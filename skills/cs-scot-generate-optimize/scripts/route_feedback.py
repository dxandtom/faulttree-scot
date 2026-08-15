#!/usr/bin/env python3
"""route_feedback.py - 回流分拣:评估反思的输出 → 按类开处置票(只开票,不执行)

职责边界(与回流线设计一致):
- 判断交代码:六类错误的真因分支全部机械判定(台账反查/名字对时/orphan 定位/证据拼装);
- 执行交既有通道:票上只写"该走哪条通道+命令/草稿",真正动手的是 migrate、调优表+import、
  tester、人工——本脚本不改任何资产;
- 确认门:全部票默认待人过目;模型拿到票后照票办事,禁止越票发挥。

用法:
    分拣开票:
      python route_feedback.py --feedback <evaluation_report.json 或 feedback_to_generate.json>
                               --product <产物目录或打包技能目录> [--out <tickets.json>]
    执行留痕(人点头照单执行后记账):
      python route_feedback.py --record <tickets.json> --done all|t1,t2,...
                               --product <产物目录> [--by <谁点的头>]
      → 追加到 产物/tree/feedback_log.json(评估反思的"改了什么"历史,与评估目录的
        round 报告链对账;previous_attempts/熔断深查都从这里查)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CLASS_NAMES = {0: "ENV_FAILURE", 1: "INPUT_GAP", 2: "BINDING_GAP",
               3: "KNOWLEDGE_GAP", 4: "CRITERIA_GAP", 5: "VERDICT_GAP"}


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise SystemExit(f"读不动 {path}: {e}")


def locate_product(product: Path) -> Path:
    if (product / "tree").is_dir():
        return product
    if (product / "assets" / "tree").is_dir():
        return product / "assets"
    raise SystemExit(f"--product 不像场景产物目录(缺 tree/):{product}")


def load_tree(product: Path) -> Dict[str, Any]:
    tis = sorted((product / "tree").glob("*.tree_index.json"))
    if not tis:
        return {}
    nodes = load_json(tis[0]).get("nodes", {})
    if isinstance(nodes, list):
        nodes = {n.get("node_id") or n.get("id"): n for n in nodes}
    return nodes


def extract_issues(feedback: Any) -> List[Dict[str, Any]]:
    """把两种输入统一成 issue 列表:
    - evaluation_report.json:取 failure_signatures(空则按 verdict 无票);
    - feedback_to_generate.json:单对象或列表,取各自 issues。"""
    if isinstance(feedback, list):                       # feedback 列表(批量)
        out = []
        for fb in feedback:
            out += extract_issues(fb)
        return out
    if not isinstance(feedback, dict):
        return []
    if "reconciliation" in feedback or "verdict_detail" in feedback:   # evaluation_report
        verdict = feedback.get("meta", {}).get("verdict", "")
        if verdict in ("PASS", "PASS_WITH_GAPS"):
            return []
        sigs = feedback.get("failure_signatures", []) or []
        for s in sigs:
            s.setdefault("case_id", feedback.get("meta", {}).get("case_id", ""))
        return sigs
    issues = feedback.get("issues", []) or []
    for i in issues:
        i.setdefault("case_id", feedback.get("meta", {}).get("case_id", ""))
    return issues


def issue_category(issue: Dict[str, Any]) -> str:
    for key in ("error_category", "error_class"):
        v = issue.get(key)
        if isinstance(v, str) and v in CLASS_NAMES.values():
            return v
        if isinstance(v, int):
            return CLASS_NAMES.get(v, "")
    return ""


def grep_binding_files(product: Path, node_id: str) -> List[str]:
    """哪些脚本里绑着这个节点(机械定位残留绑定用)。"""
    hits = []
    for p in sorted((product / "scripts").glob("*.py")) if (product / "scripts").is_dir() else []:
        try:
            if re.search(r'''["']node_id["']\s*:\s*["']%s["']''' % re.escape(node_id),
                         p.read_text(encoding="utf-8", errors="replace")):
                hits.append(p.name)
        except OSError:
            continue
    return hits


def build_tickets(issues, product: Path, nodes: Dict[str, Any],
                  ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    tickets = []

    def add(cat, branch, level, channel, action, issue, **extra):
        tickets.append({
            "id": f"t{len(tickets) + 1}",
            "case_id": issue.get("case_id", ""),
            "error_category": cat,
            "branch": branch,
            "level": level,              # auto=可代办 / semi=草稿待点头 / manual=纯人工
            "channel": channel,
            "node_id": issue.get("node_id") or issue.get("expected_node") or "",
            "reason": issue.get("reason", ""),
            "action": action,
            **extra
        })

    for issue in issues:
        cat = issue_category(issue)
        blob = json.dumps(issue, ensure_ascii=False)
        nid = issue.get("node_id") or issue.get("expected_node") or ""
        node = nodes.get(nid, {})

        # 熔断一票否决:自动迭代到此为止
        if "熔断" in blob or int(issue.get("previous_attempts") or 0) >= 3:
            add(cat, "熔断", "manual", "人工深查",
                "该节点同类错误已连续3轮未修复,停止自动迭代。深查方向:①判据描述是否充足"
                "(不足→转 KNOWLEDGE_GAP 退 tester 重写判断方法)②期望是否标错(退用例库)"
                "③裁决口径是否需调(出争议单)", issue)
            continue

        if cat == "ENV_FAILURE":
            steps = issue.get("failed_steps") or []
            txt = json.dumps(steps, ensure_ascii=False) + issue.get("reason", "")
            if "custom" in txt or "fault_check" in txt:
                owner = next((k for k, v in ledger.items()
                              if v.get("script", "") and Path(v["script"]).name in txt), "")
                pkg = ledger.get(owner, {}).get("package_node", "")
                add(cat, "判据脚本崩", "semi", "migrate(管道修复/重迁)",
                    (f"重迁该节点交付包(体检+有界管道修复随迁自动跑):migrate --only \"{pkg}\""
                     f" --node {owner}" if owner else
                     "按 traceback 定位 custom/ 脚本,重迁其交付包(管道修复随迁);清单外的实现"
                     "问题记返厂单退 tester"), issue)
            else:
                add(cat, "环境崩", "manual", "人工",
                    "环境级失败(依赖/权限/数据损坏),修复后重跑 execute;报错原文见 reason", issue)
            continue

        if cat == "INPUT_GAP":
            missing = issue.get("missing_inputs") or []
            impact = {}
            for m in missing:
                impact[m] = sum(1 for n in nodes.values()
                                if m in str(n.get("data_source") or ""))
            add(cat, "缺数据", "manual", "使用方补数据",
                "补齐后重跑 execute,不动任何资产。缺口影响(按 tree_index data_source 统计):"
                + ", ".join(f"{m}(约{c}个节点)" for m, c in impact.items()), issue)
            continue

        if cat == "BINDING_GAP":
            orphans = issue.get("orphan_nodes") or []
            if orphans or "孤儿绑定" in blob or "孤立节点" in blob:
                locs = {o: grep_binding_files(product, o) for o in orphans}
                add(cat, "孤儿绑定", "semi", "调优表+import(产物自带)",
                    "清残留绑定:导出调优表→删这些幽灵节点的绑定引用→import 编译回产物。"
                    f"绑定所在脚本:{json.dumps(locs, ensure_ascii=False)}", issue)
            elif nid in ledger:
                pkg = ledger[nid].get("package_node", "")
                add(cat, "清绑定冲突", "manual", "人裁(二选一,无默认)",
                    f"该节点曾由包「{pkg}」迁入(台账有记录),现在绑定为空=人清过;而用例期望它是"
                    f"根因。二选一:①恢复绑定:migrate --only \"{pkg}\" --node {nid} 重迁;"
                    "②改用例期望(该节点确实该跳过)", issue)
            else:
                add(cat, "缺绑定", "semi", "migrate 或 调优表+import",
                    f"节点 {nid}「{node.get('name', '')}」从未绑定过(台账无记录)。有交付包→"
                    f"migrate 迁入;没有→调优表补绑定行(绑定语法见 generate 导入导出手册,"
                    "草稿待人审)→import;或给 tester 出脚本需求单", issue)
            continue

        if cat == "KNOWLEDGE_GAP":
            name = issue.get("node_name", "")
            by_name = [k for k, n in nodes.items() if name and n.get("name") == name]
            if by_name:
                add(cat, "用例库过期", "auto", "case_gen 重跑",
                    f"节点「{name}」在树上(现 node_id={by_name[0]},用例记的 {nid} 已过期"
                    "——结构调整后 id 重排)。重跑 case_gen 全库刷新即可,别惊动 tester", issue)
            else:
                add(cat, "真缺节点/缺知识", "manual", "tester 需求单",
                    f"树上确实没有「{name or nid}」。给 tester 出增节点需求单(节点名/建议父路径/"
                    "来源用例/期望证据),产出判断方法后走优化线收编或结构手术线增节点", issue)
            continue

        if cat == "CRITERIA_GAP":
            add(cat, "对质单", "manual", "人拍板→三选一执行",
                f"节点 {nid}「{node.get('name', '')}」期望 predicate="
                f"{issue.get('expected_predicate')} vs 实测={issue.get('actual_predicate')}。"
                f"判据原文:{str(node.get('condition') or '')[:160]}。人对照测试数据拍板:"
                "①绑定/列映射抄错→调优表改行→import;②脚本实现偏离判断方法文档→"
                "migrate 有界修复先试,清单外返厂 tester 按规范重产(判定标准一个字不许动);"
                "③期望标错(数据不含该特征)→修用例并退 tester(测试数据质量问题)", issue)
            continue

        if cat == "VERDICT_GAP":
            add(cat, "裁决争议", "manual", "人裁(+SCOT_DEBATE=auto 可拉辩论卷宗)",
                "0~4 全排除仍漏判/假阳=纯裁决口径争议。出争议单交人;绝不自动改 RCA 引擎口径"
                "(两引擎同步的核心资产)。SCOT_DEBATE=auto 时可对争议候选跑辩论引擎出对抗卷宗"
                "辅助裁决", issue)
            continue

        add(cat or "UNKNOWN", "未识别", "manual", "人工",
            f"未识别的错误类别,原样转人工。原始条目:{blob[:300]}", issue)

    return tickets


def record_execution(tickets_path: Path, done: str, product: Path, by: str) -> None:
    tickets = load_json(tickets_path)
    items = tickets.get("tickets", [])
    done_ids = [t["id"] for t in items] if done.strip().lower() == "all" \
        else [x.strip() for x in done.split(",") if x.strip()]
    unknown = [d for d in done_ids if d not in {t["id"] for t in items}]
    if unknown:
        raise SystemExit(f"--done 里有票号不在票单里:{unknown}")
    log_p = product / "tree" / "feedback_log.json"
    log = load_json(log_p) if log_p.exists() else {"entries": []}
    log["entries"].append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tickets_file": tickets_path.name,
        "executed": [{k: t[k] for k in ("id", "error_category", "branch", "node_id", "channel")}
                     for t in items if t["id"] in done_ids],
        "approved_by": by
    })
    log_p.parent.mkdir(parents=True, exist_ok=True)
    log_p.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[route_feedback] 已记账 {len(done_ids)} 张票 → {log_p}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="回流分拣:评估输出→处置票(只开票不执行)")
    ap.add_argument("--feedback", help="evaluation_report.json 或 feedback_to_generate.json")
    ap.add_argument("--product", required=True, help="场景产物目录(或打包技能目录)")
    ap.add_argument("--out", help="票单输出路径(默认与 feedback 同目录 feedback_tickets.json)")
    ap.add_argument("--record", help="执行留痕模式:票单 json 路径")
    ap.add_argument("--done", help="执行留痕模式:已执行票号,逗号分隔或 all")
    ap.add_argument("--by", default="user", help="执行留痕模式:谁点的头(默认 user)")
    a = ap.parse_args()

    product = locate_product(Path(a.product).expanduser().resolve())

    if a.record:
        if not a.done:
            raise SystemExit("--record 需要配 --done(票号列表或 all)")
        record_execution(Path(a.record).expanduser().resolve(), a.done, product, a.by)
        return

    if not a.feedback:
        raise SystemExit("给 --feedback(分拣开票)或 --record(执行留痕),二选一")
    fb_path = Path(a.feedback).expanduser().resolve()
    feedback = load_json(fb_path)
    nodes = load_tree(product)
    ledger_p = product / "tree" / "migrations.json"
    ledger = load_json(ledger_p) if ledger_p.exists() else {}

    issues = extract_issues(feedback)
    if not issues:
        print("[route_feedback] 无待处置条目(PASS/PASS_WITH_GAPS 或空反馈)——无票,收工")
        return

    tickets = build_tickets(issues, product, nodes, ledger)
    out_p = Path(a.out).expanduser().resolve() if a.out \
        else fb_path.parent / "feedback_tickets.json"
    out_p.write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "feedback_file": str(fb_path), "product": str(product), "tickets": tickets},
        ensure_ascii=False, indent=2), encoding="utf-8")

    by_level = {"auto": "可代办", "semi": "草稿待点头", "manual": "纯人工"}
    print(f"[route_feedback] 开出 {len(tickets)} 张处置票 → {out_p}\n")
    for t in tickets:
        print(f"  [{t['id']}] 类{t['error_category']}·{t['branch']}"
              f"({by_level.get(t['level'], t['level'])}) → {t['channel']}")
        print(f"      节点:{t['node_id'] or '-'}  用例:{t['case_id'] or '-'}")
        print(f"      动作:{t['action']}\n")
    print("确认门:人过目后对可执行的票逐张/照单执行;执行完记账:"
          f"\n  python {Path(__file__).name} --record \"{out_p}\" --done all --product \"{product}\"")


if __name__ == "__main__":
    main()
