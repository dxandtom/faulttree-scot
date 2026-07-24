#!/usr/bin/env python3
"""裁决单 → rca.json 机械合成器:模型只写小裁决单,几百行 JSON 由代码拼,取代 validate 后置校验。

实测病根:draft 治了"忘候选",但模型仍要手打整份 rca.json(几百行,写半天还会抄错)。
与绑定产线同配方——模型只交判定,机械字段代码填。诚实性从"事后校验"改成"结构上做不出假的":
- 骨架里的候选**一条都不许漏裁决**(漏了响亮报错,候选想消失必须显式给 verdict+理由);
- verdict=confirmed 只收"路径全干净"的候选(路径带 数据不足/不成立/冲突 → 拒收,只能 probable/excluded);
- 有成立事实的候选 verdict=undetermined → 拒收(有证据的不收待确认);
- verdict=excluded 必须给 reason(排除无理由拒收);
- evidence_path/Fault_tree_path 逐字来自骨架(代码抓的原文),模型改不了数字。

用法:
    finalize_rca.py <rca_draft.json> <decisions.json> -o <rca.json>
        [--rca-input <rca_input.json>]   # 抄 tree_issues(不给则空)
        [--solutions <solutions.json>]   # 按根因节点点查 recommend_solution

裁决单 decisions.json:list(或 {"decisions": [...]}),一个候选一条:
    {"node_id": "n16", "verdict": "confirmed"}
    {"node_id": "n77", "verdict": "probable", "confidence": "medium",
     "caveats": "…", "blocked_at": "…", "support": "…"}
    {"node_id": "n51", "verdict": "excluded", "reason": "Isolate 命中系例行隔离日志,非故障"}
    可选 "overrides": {"nX": "语义近似:单位口径不同"} —— 把路径上某节点的 judgment 改成存疑落字。
verdict 同义词均认:confirmed/确证、probable/疑似、excluded/排除、undetermined/待确认。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

_VERDICT = {"confirmed": "confirmed", "rca_result": "confirmed", "确证": "confirmed", "确证根因": "confirmed",
            "probable": "probable", "probable_causes": "probable", "疑似": "probable", "疑似根因": "probable",
            "excluded": "excluded", "排除": "excluded",
            "undetermined": "undetermined", "待确认": "undetermined"}
_CLEAN = ("成立", "无独立条件")


def _load(path: Path):
    """json 优先,ast 兜底(弱模型爱写 Python 风格单引号/True)。"""
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError) as e:
            raise SystemExit(f"裁决单/骨架解析失败({path}): {e} —— 修文件格式,别绕过本脚本手写 rca.json")


def _ev_final(entry, overrides):
    """骨架预填 evidence → 最终 evidence_path(逐字照搬;overrides 只许把 judgment 改成存疑落字)。"""
    out = []
    for e in entry.get("evidence_prefilled") or [entry]:
        item = {k: e[k] for k in ("node", "node_id", "condition", "actual", "how", "judgment",
                                  "conflict_actual", "conflict_how", "other_source_note") if k in e}
        ov = (overrides or {}).get(str(e.get("node_id")))
        if ov:
            item["judgment"] = str(ov)
        out.append(item)
    return out


def _dirty(ev):
    """路径上"不干净"的条目(数据不足/不成立/冲突/存疑…——凡不是 成立/无独立条件 的都算)。"""
    return [e for e in ev if not any(c in str(e.get("judgment", "")) for c in _CLEAN)
            or "不成立" in str(e.get("judgment", ""))]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="裁决单 → rca.json 机械合成(模型只交判定,拼装归代码)")
    ap.add_argument("draft")
    ap.add_argument("decisions")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--rca-input", help="抄 tree_issues 用;不给则输出空 tree_issues")
    ap.add_argument("--solutions", help="tree/solutions.json,按根因节点点查 recommend_solution")
    a = ap.parse_args()

    draft = _load(Path(a.draft))
    dec_raw = _load(Path(a.decisions))
    dec_list = dec_raw.get("decisions") if isinstance(dec_raw, dict) else dec_raw
    if not isinstance(dec_list, list):
        raise SystemExit("裁决单须是 list(或 {\"decisions\": [...]}),一个候选一条")
    decisions = {}
    for d in dec_list:
        if not isinstance(d, dict) or not d.get("node_id"):
            raise SystemExit(f"裁决条目缺 node_id:{json.dumps(d, ensure_ascii=False)[:120]}")
        v = _VERDICT.get(str(d.get("verdict", "")).strip().lower()) or _VERDICT.get(str(d.get("verdict", "")).strip())
        if not v:
            raise SystemExit(f"节点 {d['node_id']} 的 verdict「{d.get('verdict')}」不认识"
                             f"(只认 confirmed/probable/excluded/undetermined 及中文同义词)")
        d["_v"] = v
        decisions[str(d["node_id"])] = d

    # 必裁决集 = 确证候选 + 疑似候选 + 冲突条目(md 形态为 holding_candidates + 冲突)
    adjudicables = {str(e["node_id"]): e for e in
                    (draft.get("confirmed_candidates", []) + draft.get("probable_candidates", [])
                     + draft.get("holding_candidates", []) + draft.get("conflict_nodes", []))}
    missing = [f"{nid}「{e.get('node')}」" for nid, e in adjudicables.items() if nid not in decisions]
    if missing:
        raise SystemExit("不合格:以下候选没有裁决(候选不许无声消失,逐条给 verdict;"
                         "不采纳=excluded+reason):\n  - " + "\n  - ".join(missing))
    skel_ids = {str(e.get("node_id")) for e in draft.get("undetermined_skeleton", [])}
    excl_ids = {str(e.get("node_id")) for e in draft.get("excluded_candidates", [])}
    unknown = [nid for nid in decisions if nid not in adjudicables and nid not in skel_ids and nid not in excl_ids]
    if unknown:
        raise SystemExit(f"不合格:裁决单里的节点不在骨架中:{', '.join(unknown)}(别编节点)")

    rca_result, probable, undetermined, excl_notes = [], [], [], []
    hold_ids = set(adjudicables)                          # 这些都有成立事实(候选/冲突)
    for nid, e in adjudicables.items():
        d = decisions[nid]
        v = d["_v"]
        ev = _ev_final(e, d.get("overrides"))
        path = e.get("path_names") or [e.get("node")]
        if v == "confirmed":
            bad = _dirty(ev)
            if bad:
                raise SystemExit(f"不合格:候选 {nid}「{e.get('node')}」verdict=confirmed,但路径不干净:"
                                 + "、".join(f"{x.get('node')}({x.get('judgment')})" for x in bad[:3])
                                 + " —— 确证要路径全成立;改 probable(带 blocked_at/caveats)或 excluded(带 reason)")
            rca_result.append({"Root_cause": e.get("node"), "Fault_tree_path": path,
                               "node_id": nid,
                               "support": d.get("support") or e.get("support_hint") or "",
                               "evidence_path": ev})
        elif v == "probable":
            probable.append({"Root_cause": e.get("node"), "Fault_tree_path": path,
                             "node_id": nid,
                             "confidence": d.get("confidence") or "medium",
                             "blocked_at": d.get("blocked_at") or e.get("blocked_at_hint") or "",
                             "caveats": d.get("caveats") or e.get("note") or "",
                             "support": d.get("support") or e.get("support_hint") or "",
                             "evidence_path": ev})
        elif v == "excluded":
            if not (d.get("reason") or "").strip():
                raise SystemExit(f"不合格:候选 {nid}「{e.get('node')}」verdict=excluded 但没给 reason——"
                                 "排除有成立事实的候选,理由只能是反证或绑定质量问题,必须落字")
            excl_notes.append(f"「{e.get('node')}」({nid}):{d['reason']}")
        else:                                             # undetermined:有成立事实的不收
            if nid in hold_ids:
                raise SystemExit(f"不合格:候选 {nid}「{e.get('node')}」有'成立'事实,不收 undetermined"
                                 "(待确认=既无正向证据又缺数据)——升 probable 或有据 excluded")

    # 待确认骨架(真缺数节点)自动带出;模型给了裁决的按裁决走(如补充 reason)
    for e in draft.get("undetermined_skeleton", []):
        nid = str(e.get("node_id"))
        d = decisions.get(nid, {})
        if d and d.get("_v") == "excluded":
            if not (d.get("reason") or "").strip():
                raise SystemExit(f"不合格:{nid} verdict=excluded 但没给 reason")
            excl_notes.append(f"「{e.get('node')}」({nid}):{d['reason']}")
            continue
        undetermined.append({"Fault_tree_path": [e.get("node")],
                             "blocked_at": f"{e.get('node')}({nid})",
                             "reason": d.get("reason") or e.get("actual") or "数据不足",
                             "observed": []})

    doc = {"rca_result": rca_result, "probable_causes": probable,
           "undetermined": undetermined, "tree_issues": {}}
    if excl_notes:
        doc["excluded_note"] = ";".join(excl_notes)
    if a.rca_input:
        try:
            ri = json.loads(Path(a.rca_input).read_text(encoding="utf-8"))
            doc["tree_issues"] = ri.get("tree_issues") or {}
        except Exception as e:
            print(f"[warn] 读 rca_input 失败({e}),tree_issues 置空")
    if a.solutions:
        try:
            sol = json.loads(Path(a.solutions).read_text(encoding="utf-8"))
            for rc in rca_result + probable:
                s = sol.get(str(rc.get("node_id")))
                if isinstance(s, dict) and (s.get("zh") or s.get("en")):
                    rc["recommend_solution"] = s.get("zh") or s.get("en")
        except Exception as e:
            print(f"[warn] 读 solutions.json 失败({e}),不附解决方案")

    out = Path(a.out)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已机械合成 → {out}")
    print(f"  确证 {len(rca_result)} 条、疑似 {len(probable)} 条、待确认 {len(undetermined)} 条"
          f"、排除留痕 {len(excl_notes)} 条。evidence 逐字来自骨架(代码抓的原文)。")
    print("  按 execute SKILL.md 的固定模板写文字报告即可(数字与 rca.json 严格一致)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
