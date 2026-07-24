#!/usr/bin/env python3
"""RCA 诊断骨架(rca_draft.json)机械生成器 —— 枚举是机械活,模型只做裁决。

实测病根:让模型自己"发现候选"(挨个 rca_query --node 翻),弱模型会把成立的节点整个忘掉
(n25/n26 事实 predicate_satisfied=True 却通篇消失),或把成立写成"binding证据不足"。
本脚本从 rca_input.json 机械枚举:所有成立节点→候选(根→叶路径+证据全预填),
不成立→排除候选,数据不足→待确认骨架,成立+不成立并存→冲突条目。
**模型的工作收窄为逐条裁决**:确认/降档/合并(都要落字理由),补 confidence/caveats/support
的人话,写成最终 rca.json——骨架字段名与最终契约刻意不同,抄一遍是抄不动的,必须裁决。

用法:python draft_rca.py <rca_input.json> [-o <rca_draft.json>]   # 缺省写在输入同目录
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_README = (
    "本骨架由代码从 rca_input 机械枚举:候选=有'成立'事实的节点(路径+证据已预填,how 逐字来自 explain)。"
    "你的工作=逐条裁决,不是重新发现:①语义复核(mapping_reason 与判据是否真等,近似就降档并把该节点 "
    "judgment 改'语义近似/存疑');②定档:draft_tier 只是机械建议——confirmed_candidate 语义过硬才进 "
    "rca_result,否则降 probable_causes;probable_candidate 补 confidence/caveats/support;"
    "③冲突条目权衡后归档;④排除候选/待确认骨架照对应档写。**任何候选不许无声消失**:不采纳就写进"
    "excluded_note(点名+理由);降档写理由。数据是有的就不许写'数据不足/证据不足'——语义怀疑请写"
    "'语义近似/存疑'。产出后跑 validate_rca.py 自检。"
)


def _tri(facts) -> str:
    """一个节点的机械三态:成立/不成立/冲突/数据不足(未覆盖=没 facts,由调用方判)。"""
    ok_t = any(f.get("status") == "ok" and f.get("predicate_satisfied") is True for f in facts)
    ok_f = any(f.get("status") == "ok" and f.get("predicate_satisfied") is False for f in facts)
    if ok_t and ok_f:
        return "冲突"
    if ok_t:
        return "成立"
    if ok_f:
        return "不成立"
    return "数据不足"


def _pick(facts, want: bool):
    """取一条 ok 且 predicate_satisfied=want 的事实(证据代表);没有则 None。"""
    for f in facts:
        if f.get("status") == "ok" and f.get("predicate_satisfied") is want:
            return f
    return None


def _actual(f: dict) -> str:
    """事实 → 简短的机械 actual 串(数字逐字,不加工)。"""
    if not f:
        return ""
    bits = []
    for k in ("matched", "total", "ratio", "value", "op", "threshold", "count_satisfied",
              "valid", "groups", "matched_groups", "local", "peer", "equal"):
        if f.get(k) is not None:
            bits.append(f"{k}={f[k]}")
    if f.get("absent"):
        bits.append("absent=true(缺失类:未出现=成立)")
    if f.get("hits"):
        bits.append(f"命中原文:「{f['hits'][0]}」")
    if not bits and f.get("reason"):
        bits.append(str(f["reason"])[:120])
    return ";".join(str(b) for b in bits)[:400]


def _ev(nid: str, n: dict) -> dict:
    """一个路径节点 → 预填的 evidence 条目(judgment 是机械三态,模型可降不可无据升)。"""
    cond = n.get("condition") or ""
    has_cond = bool(cond) and "无条件" not in cond and "无判断" not in cond
    e = {"node": n.get("name"), "node_id": nid, "condition": cond}
    facts = n.get("facts") or []
    if n.get("pass_through") or not has_cond:
        e["judgment"] = "无独立条件"
        return e
    tri = _tri(facts) if facts else "数据不足"
    e["judgment"] = tri
    if tri in ("成立", "冲突"):
        f = _pick(facts, True)
        e["actual"] = _actual(f)
        if f and f.get("explain"):
            e["how"] = f["explain"]
        if tri == "冲突":
            g = _pick(facts, False)
            e["conflict_actual"] = _actual(g)
            if g and g.get("explain"):
                e["conflict_how"] = g["explain"]
    elif tri == "不成立":
        f = _pick(facts, False)
        e["actual"] = _actual(f)
        if f and f.get("explain"):
            e["how"] = f["explain"]
    else:
        e["actual"] = "" if facts else "无任何事实(没人绑/缺数据源)"
        rs = [f.get("reason") for f in facts if f.get("reason")]
        if rs:
            e["actual"] = str(rs[0])[:160]
    return e


def _path(nid: str, nodes: dict, limit=400):
    """root→nid 的节点 id 链(沿 parent 上溯;剪枝保证祖先全保留)。"""
    chain, cur, seen = [], nid, set()
    while cur and cur in nodes and cur not in seen and len(chain) < limit:
        seen.add(cur)
        chain.append(cur)
        cur = nodes[cur].get("parent")
    return list(reversed(chain))


def draft_pruned(ft: dict) -> dict:
    nodes = ft.get("pruned_nodes") or {}
    holding, refuted, insuf, conflict = [], [], [], []
    for nid, n in nodes.items():
        cond = n.get("condition") or ""
        if n.get("pass_through") or not cond or "无条件" in cond or "无判断" in cond:
            continue
        facts = n.get("facts") or []
        if not facts:
            continue                                    # 未覆盖:归 tree_issues,不进骨架
        tri = _tri(facts)
        {"成立": holding, "不成立": refuted, "数据不足": insuf, "冲突": conflict}[tri].append(nid)

    # 候选=成立节点中"没有成立后代"的(最深的成立);成立的祖先在候选的路径里体现,不重复开条
    hold_set = set(holding)
    deepest = []
    for nid in holding:
        others = hold_set - {nid}
        if not any(nid in _path(o, nodes)[:-1] for o in others):
            deepest.append(nid)

    confirmed, probable = [], []
    for nid in sorted(deepest, key=lambda x: int(x[1:]) if x[1:].isdigit() else 10 ** 9):
        n = nodes[nid]
        ev = [_ev(p, nodes[p]) for p in _path(nid, nodes)]
        gaps = [e for e in ev if e["judgment"] in ("数据不足", "冲突")]
        entry = {"node": n.get("name"), "node_id": nid,
                 "path_names": [nodes[p].get("name") for p in _path(nid, nodes)],
                 "evidence_prefilled": ev}
        f = _pick(n.get("facts") or [], True)
        if f:
            entry["support_hint"] = _actual(f)
        unc = n.get("uncovered_children") or []
        if not gaps and n.get("is_leaf") and not unc:
            entry["draft_tier"] = "confirmed_candidate(机械建议:叶子+路径全干净;语义复核后定档)"
            confirmed.append(entry)
        else:
            why = []
            if gaps:
                why.append(f"路径缺口:{'、'.join(e['node'] + '(' + e['node_id'] + ')' for e in gaps[:3])}")
            if not n.get("is_leaf") or unc:
                why.append("非叶子/有未覆盖子因,叶子因待定")
            entry["draft_tier"] = "probable_candidate"
            entry["blocked_at_hint"] = ";".join(why)
            probable.append(entry)

    return {
        "_readme": _README,
        "confirmed_candidates": confirmed,
        "probable_candidates": probable,
        "conflict_nodes": [{**_ev(nid, nodes[nid]),
                            "note": "同节点成立与不成立并存(真冲突),权衡后归档并写 caveat"}
                           for nid in sorted(conflict)],
        "excluded_candidates": [_ev(nid, nodes[nid]) for nid in sorted(refuted)],
        "undetermined_skeleton": [_ev(nid, nodes[nid]) for nid in sorted(insuf)],
    }


def draft_md(data: dict) -> dict:
    """完整 md 形态(无剪枝树):没有路径可拼,退化为按三态分拣的候选清单。"""
    buckets = {"成立": [], "不成立": [], "数据不足": [], "冲突": []}
    for ind, res in (data.get("indicator_results") or {}).items():
        for c in (res or {}).get("conditions", []) or []:
            f = c.get("fact") or {}
            tri = _tri([f]) if f else "数据不足"
            e = {"node": c.get("node"), "node_id": c.get("node_id"), "indicator": ind,
                 "condition": (c.get("condition_text") or "")[:200], "actual": _actual(f)}
            if f.get("explain"):
                e["how"] = f["explain"]
            buckets[tri].append(e)
    return {
        "_readme": _README + "(本输入为完整 md 形态,无剪枝树:路径请按 fault_tree_md 的层级自行回溯,"
                             "但候选枚举以下面清单为准,不许漏)",
        "holding_candidates": buckets["成立"],
        "conflict_nodes": buckets["冲突"],
        "excluded_candidates": buckets["不成立"],
        "undetermined_skeleton": buckets["数据不足"],
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="RCA 诊断骨架机械生成(候选枚举交给代码,模型只裁决)")
    ap.add_argument("rca_input")
    ap.add_argument("-o", "--out", help="输出路径,缺省=输入同目录 rca_draft.json")
    a = ap.parse_args()
    src = Path(a.rca_input)
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"无法读取/解析 {src}: {e}", file=sys.stderr)
        return 2
    ft = data.get("fault_tree")
    draft = draft_pruned(ft) if isinstance(ft, dict) and ft.get("pruned_nodes") else draft_md(data)
    out = Path(a.out) if a.out else src.parent / "rca_draft.json"
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"骨架已生成 → {out}")
    if "holding_candidates" in draft:                    # md 形态:无路径,只有三态分拣
        print(f"  成立候选 {len(draft['holding_candidates'])} 条、冲突 {len(draft.get('conflict_nodes', []))} 条、"
              f"排除候选 {len(draft.get('excluded_candidates', []))} 条、"
              f"待确认骨架 {len(draft.get('undetermined_skeleton', []))} 条(md 形态:路径按 fault_tree_md 回溯)。")
    else:
        print(f"  确证候选 {len(draft.get('confirmed_candidates', []))} 条、"
              f"疑似候选 {len(draft.get('probable_candidates', []))} 条、"
              f"冲突 {len(draft.get('conflict_nodes', []))} 条、排除候选 {len(draft.get('excluded_candidates', []))} 条、"
              f"待确认骨架 {len(draft.get('undetermined_skeleton', []))} 条。")
    print("  逐条裁决(确认/降档/合并都要落字理由,候选不许无声消失),写成最终 rca.json,再跑 validate_rca.py。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
