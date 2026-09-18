#!/usr/bin/env python3
"""route_feedback.py - 反馈分类:评估反思的输出 → 按类给出每条问题的处理方式(只给出处理方式,不执行)

职责边界(与反馈处理线设计一致):
- 判断交代码:六类错误的真因分支全部机械判定(台账反查/名字匹配/orphan 定位/证据拼装);
- 执行交既有工具:处理项上只写"用哪个工具+命令/草稿",真正动手的是 migrate、调优表+import、
  tester、人工——本脚本不改任何资产;
- 两档执行:自动迭代档(auto)在优化循环内**直接按处理项执行**,同节点同类 3 轮不过由熔断
  自动转人工;人工档(fallback)=自动化本来就无能为力的情况(环境故障/缺数据/
  绑定清除冲突/缺节点/结论争议/熔断),汇总交人。模型按处理项办事,不自行发挥。

用法:
    分类并给出处理方式:
      python route_feedback.py --feedback <evaluation_report.json 或 feedback_to_generate.json>
                               --product <产物目录或打包技能目录> [--out <tickets.json>]
    执行留痕(人确认并按处理项执行后记录):
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
    - evaluation_report.json:取 failure_signatures(空则按 verdict 没有待处理项);
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
            "level": level,              # auto=自动迭代(循环内直接执行) / fallback=人工处理
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
            add(cat, "熔断", "fallback", "人工处理(迭代上限)",
                "该节点同类错误已连续3轮未修复,停止自动迭代。**第一步由模型定性**(对照判断"
                "方法文档原文与三轮修复记录,这是机械分类测不出、必须模型判断的环节):"
                "①判据描述不充足,脚本怎么写都判不对→转 KNOWLEDGE_GAP,给 tester 出重写"
                "判断方法的需求单;②判据没问题但用例期望可疑→修用例并反馈测试数据质量;"
                "③都不是→结论归档规则争议,出争议单交人", issue)
            continue

        if cat == "ENV_FAILURE":
            steps = issue.get("failed_steps") or []
            txt = json.dumps(steps, ensure_ascii=False) + issue.get("reason", "")
            if "custom" in txt or "fault_check" in txt:
                owner = next((k for k, v in ledger.items()
                              if v.get("script", "") and Path(v["script"]).name in txt), "")
                pkg = ledger.get(owner, {}).get("package_node", "")
                add(cat, "脚本运行崩溃", "auto", "migrate(重迁+限定范围修复)",
                    (f"重迁该节点交付包(格式检查与限定范围的修复随迁自动跑):migrate --only \"{pkg}\""
                     f" --node {owner}" if owner else
                     "按 traceback 定位 custom/ 脚本,重迁其交付包(管道修复随迁);清单外的实现"
                     "问题记退回单,退回 tester"), issue)
            else:
                add(cat, "环境故障", "fallback", "人工处理",
                    "环境级失败(依赖/权限/数据损坏),修复后重跑 execute;报错原文见 reason", issue)
            continue

        if cat == "INPUT_GAP":
            missing = issue.get("missing_inputs") or []
            impact = {}
            for m in missing:
                impact[m] = sum(1 for n in nodes.values()
                                if m in str(n.get("data_source") or ""))
            add(cat, "数据缺失", "fallback", "人工处理(使用方补数据)",
                "补齐后重跑 execute,不动任何资产。缺口影响(按 tree_index data_source 统计):"
                + ", ".join(f"{m}(约{c}个节点)" for m, c in impact.items()), issue)
            continue

        if cat == "BINDING_GAP":
            orphans = issue.get("orphan_nodes") or []
            if issue.get("binding_form_mismatch") or "绑定形态不匹配" in blob:
                # 类1 检测改判来的:缺的源在用例 raw 数据里有内容,现绑定(表类)吃不了
                pkg = ledger.get(nid, {}).get("package_node", "")
                add(cat, "绑定形态不匹配", "auto", "migrate(重迁换 S 绑定)",
                    (f"节点 {nid}「{node.get('name', '')}」的绑定要表源,而用例数据只有 raw 形态"
                     "(内容在场)。有该节点交付包→migrate 重迁,fault_check 脚本直接吃 raw"
                     + (f"(台账记过包「{pkg}」,可 --only \"{pkg}\" 重迁)" if pkg else "")
                     + ";确无包→zip2csv 把 raw 转三表后在用例 data_map.json 映射,"
                       "或给 tester 出脚本需求单(3 轮内熔断转人工)"), issue)
            elif orphans or "孤儿绑定" in blob or "孤立节点" in blob:
                locs = {o: grep_binding_files(product, o) for o in orphans}
                add(cat, "孤儿绑定", "auto", "调优表+import(产物自带)",
                    "清残留绑定:导出调优表→删这些幽灵节点的绑定引用→import 编译回产物。"
                    f"绑定所在脚本:{json.dumps(locs, ensure_ascii=False)}", issue)
            elif nid in ledger:
                pkg = ledger[nid].get("package_node", "")
                add(cat, "绑定清除冲突", "fallback", "人工处理(二选一,无默认)",
                    f"该节点曾由包「{pkg}」迁入(台账有记录),现在绑定为空=人清过;而用例期望它是"
                    f"根因。二选一:①恢复绑定:migrate --only \"{pkg}\" --node {nid} 重迁;"
                    "②改用例期望(该节点确实该跳过)", issue)
            else:
                add(cat, "绑定缺失", "auto", "migrate 或 调优表+import",
                    f"节点 {nid}「{node.get('name', '')}」从未绑定过(台账无记录)。有交付包→"
                    f"migrate 迁入(判据+脚本+绑定一体到位);没有→在调优表该节点的绑定列按"
                    "判据文档起草机械匹配规则(简单判据不需要脚本;语法见 generate 导入导出"
                    "手册)→import。判据复杂到写不出绑定的,3 轮内熔断转 tester 脚本需求单", issue)
            continue

        if cat == "KNOWLEDGE_GAP":
            name = issue.get("node_name", "")
            by_name = [k for k, n in nodes.items() if name and n.get("name") == name]
            if by_name:
                add(cat, "用例库过期", "auto", "case_gen 重新生成",
                    f"节点「{name}」在树上(现 node_id={by_name[0]},用例记的 {nid} 已过期"
                    "——结构调整后 id 重排)。重跑 case_gen 全库刷新即可,不用麻烦 tester", issue)
            else:
                add(cat, "故障树缺节点", "fallback", "人工处理(tester 需求单)",
                    f"树上确实没有「{name or nid}」。给 tester 出增节点需求单(节点名/建议父路径/"
                    "来源用例/期望证据),产出判断方法后走优化线导入,或走增删节点线增节点", issue)
            continue

        if cat == "CRITERIA_GAP":
            add(cat, "判定偏差修复", "auto", "调优表 或 migrate(按证据自动修复)",
                f"节点 {nid}「{node.get('name', '')}」期望 predicate="
                f"{issue.get('expected_predicate')} vs 实测={issue.get('actual_predicate')}。"
                f"判据原文:{str(node.get('condition') or '')[:160]}。自动修复范围="
                "使实现与判断方法文档一致:①绑定/列映射错→调优表改行→import;"
                "②脚本实现偏离文档→migrate 限定范围修复,清单外记退回。判定标准本身不许改;"
                "若疑为用例期望标错,在处理项备注里留证——连续修不过由熔断转人工复核", issue)
            continue

        if cat == "VERDICT_GAP":
            add(cat, "结论归档争议", "fallback", "人工处理(可加辩论引擎)",
                "0~4 全排除仍漏判/假阳=纯裁决规则争议。出争议单交人;绝不自动改 RCA 引擎的判定规则"
                "(两引擎同步的核心资产)。SCOT_DEBATE=auto 时可对争议候选跑辩论引擎出辩论记录"
                "辅助裁决", issue)
            continue

        add(cat or "UNKNOWN", "未识别", "fallback", "人工处理",
            f"未识别的错误类别,原样转人工。原始条目:{blob[:300]}", issue)

    return tickets


def record_execution(tickets_path: Path, done: str, product: Path, by: str) -> None:
    tickets = load_json(tickets_path)
    items = tickets.get("tickets", [])
    done_ids = [t["id"] for t in items] if done.strip().lower() == "all" \
        else [x.strip() for x in done.split(",") if x.strip()]
    unknown = [d for d in done_ids if d not in {t["id"] for t in items}]
    if unknown:
        raise SystemExit(f"--done 里有编号不在处理清单里:{unknown}")
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
    print(f"[route_feedback] 已记录 {len(done_ids)} 条处理项 → {log_p}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="反馈分类:评估输出→每条问题的处理方式(只给出处理方式,不执行)")
    ap.add_argument("--feedback", help="evaluation_report.json 或 feedback_to_generate.json")
    ap.add_argument("--product", required=True, help="场景产物目录(或打包技能目录)")
    ap.add_argument("--out", help="处理清单输出路径(默认与 feedback 同目录 feedback_tickets.json)")
    ap.add_argument("--record", help="执行留痕模式:处理清单 json 路径")
    ap.add_argument("--done", help="执行留痕模式:已执行的处理项编号,逗号分隔或 all")
    ap.add_argument("--by", default="user", help="执行留痕模式:谁点的头(默认 user)")
    a = ap.parse_args()

    product = locate_product(Path(a.product).expanduser().resolve())

    if a.record:
        if not a.done:
            raise SystemExit("--record 需要配 --done(处理项编号列表或 all)")
        record_execution(Path(a.record).expanduser().resolve(), a.done, product, a.by)
        return

    if not a.feedback:
        raise SystemExit("给 --feedback(分类并给出处理方式)或 --record(执行留痕),二选一")
    fb_path = Path(a.feedback).expanduser().resolve()
    feedback = load_json(fb_path)
    nodes = load_tree(product)
    ledger_p = product / "tree" / "migrations.json"
    ledger = load_json(ledger_p) if ledger_p.exists() else {}

    issues = extract_issues(feedback)
    if not issues:
        print("[route_feedback] 没有需要处理的条目(PASS/PASS_WITH_GAPS 或空反馈)")
        return

    tickets = build_tickets(issues, product, nodes, ledger)
    out_p = Path(a.out).expanduser().resolve() if a.out \
        else fb_path.parent / "feedback_tickets.json"
    out_p.write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "feedback_file": str(fb_path), "product": str(product), "tickets": tickets},
        ensure_ascii=False, indent=2), encoding="utf-8")

    by_level = {"auto": "自动迭代", "fallback": "人工处理"}
    print(f"[route_feedback] 生成 {len(tickets)} 条处理项 → {out_p}\n")
    for t in tickets:
        print(f"  [{t['id']}] 类{t['error_category']}·{t['branch']}"
              f"({by_level.get(t['level'], t['level'])}) → {t['channel']}")
        print(f"      节点:{t['node_id'] or '-'}  用例:{t['case_id'] or '-'}")
        print(f"      动作:{t['action']}\n")
    n_auto = sum(1 for t_ in tickets if t_["level"] == "auto")
    n_fb = len(tickets) - n_auto
    print(f"自动迭代档 {n_auto} 条:循环内直接执行(同节点同类 3 轮不过自动熔断转人工);"
          f"人工档 {n_fb} 条:汇总交人处理。执行完记录:"
          f"\n  python {Path(__file__).name} --record \"{out_p}\" --done <处理项编号|all> --product \"{product}\"")


if __name__ == "__main__":
    main()
