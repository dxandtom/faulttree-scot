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
    "rca_result,否则降 probable_causes;**祖先的『数据不足/不成立/冲突』都不拦确证也不构成降档理由**"
    "(确证闸只看候选自身;祖先带伤见候选的 ancestor_insufficient/ancestor_refuted/ancestor_conflict "
    "注记,finalize 自动落 caveats 标出留痕——冲突细节在 evidence 该条目的 conflict_actual/conflict_how 里;"
    "**候选是中间节点且下级无一成立**也确证——按本层命中交付,叶子因待定见 leaf_pending/children_status "
    "注记,同样自动落 caveats);probable_candidate 补 confidence/caveats/support;"
    "③冲突条目权衡后归档;④排除候选/待确认骨架照对应档写。**任何候选不许无声消失**:不采纳就写进"
    "excluded_note(点名+理由);降档写理由。数据是有的就不许写'数据不足/证据不足'——语义怀疑请写"
    "'语义近似/存疑'。裁决写成一份小 decisions.json(一个候选一条 verdict),"
    "然后跑 finalize_rca.py <本骨架> <裁决单> 机械合成最终 rca.json——不要手写 rca.json。"
)


def _weak_neg(f) -> bool:
    """存在类(C/G 非 absent)的 ok+不成立 = **弱阴性**:只说明"这份数据里没找到",不构成反证——
    别的源找到了,存在就是被证实了(实测:告警源命中 2 行、日志源 0 命中,被错标"真冲突")。
    强反证 = A 阈值/P 成对/absent 缺失类/D 组合的不成立(两边都是硬话,才配叫冲突)。"""
    return str(f.get("class") or "").upper() in ("C", "G") and not f.get("absent")


def _tri(facts) -> str:
    """一个节点的机械三态:成立/不成立/冲突/数据不足(未覆盖=没 facts,由调用方判)。
    成立 + 仅存在类弱阴性 → 成立(弱阴性注记进 evidence);成立 + 强反证并存 → 冲突。"""
    ok_t = any(f.get("status") == "ok" and f.get("predicate_satisfied") is True for f in facts)
    ok_f = [f for f in facts if f.get("status") == "ok" and f.get("predicate_satisfied") is False]
    if ok_t and any(not _weak_neg(f) for f in ok_f):
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
        else:
            misses = [x for x in facts if x.get("status") == "ok" and x.get("predicate_satisfied") is False]
            if misses:                                   # 存在类弱阴性:如实记一笔,但不拉低判定
                e["other_source_note"] = (f"另有 {len(misses)} 条存在类绑定未命中"
                                          f"(如:{(misses[0].get('explain') or '')[:80]})"
                                          "——存在类的未命中=该份数据里没看见,不构成反证,caveat 提一笔即可")
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


def draft_pruned(data: dict) -> dict:
    ft = data.get("fault_tree") or {}
    nodes = ft.get("pruned_nodes") or {}
    # 未覆盖节点不在 pruned_nodes 里,名字从覆盖报告回填(uncovered_children 只存 id)
    unc_names = {u.get("id"): u.get("name") for u in
                 ((data.get("tree_issues") or {}).get("uncovered_condition_nodes") or [])
                 if isinstance(u, dict)}
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
        # 确证闸只看叶子候选自身——祖先的状态一律不拦、只注记(finalize 机械落 caveats 留痕):
        # ①「数据不足」=零信息,查不了不冒充反证;②「不成立/冲突」=树逻辑与叶子命中矛盾,通常是
        # 祖先绑定假阴或叶子命中系背景——按叶子事实确证(树的判据就是标准,叶子脚本/绑定是人验过的),
        # 矛盾点名给人复核,不再压成疑似逼模型逐条权衡(实测:压档后弱模型权衡半天还权衡错,诊断变慢)。
        anc_insuf = [e for e in ev[:-1] if e["judgment"] == "数据不足"]
        gaps = [e for e in ev[:-1] if e["judgment"] == "冲突"]
        refutes = [e for e in ev[:-1] if e["judgment"] == "不成立"]
        entry = {"node": n.get("name"), "node_id": nid,
                 "path_names": [nodes[p].get("name") for p in _path(nid, nodes)],
                 "evidence_prefilled": ev}
        f = _pick(n.get("facts") or [], True)
        if f:
            entry["support_hint"] = _actual(f)
        if anc_insuf:
            entry["ancestor_insufficient"] = (
                "祖先大类自身判据未独立验证(数据不足):"
                + "、".join(e["node"] + "(" + e["node_id"] + ")" for e in anc_insuf[:5])
                + "——由本节点成立事实自下而上蕴含,不拦确证、不构成降档理由;finalize 会自动写进 caveats")
        if refutes:
            entry["ancestor_refuted"] = (
                "祖先节点判定不成立:"
                + "、".join(e["node"] + "(" + e["node_id"] + ")" for e in refutes[:5])
                + "——与本叶子命中矛盾(通常是该祖先绑定假阴、或叶子命中系背景),不拦确证、"
                  "不构成降档理由;finalize 会自动写进 caveats,报告里提醒复核该祖先绑定")
        if gaps:
            entry["ancestor_conflict"] = (
                "祖先节点证据冲突(成立与强反证并存):"
                + "、".join(e["node"] + "(" + e["node_id"] + ")" for e in gaps[:5])
                + "——不拦确证、不构成降档理由;冲突两边的实测值已摆在 evidence 该条目的 "
                  "conflict_actual/conflict_how,finalize 会自动写进 caveats,报告里把冲突点标出来")
        unc = n.get("uncovered_children") or []
        if not n.get("is_leaf") or unc:
            # 中间节点候选:候选=最深成立(下面没有任何成立后代)。用户拍板:叶子没有一个成立时,
            # 中间层命中就按本层确证——压疑似只会逼模型空转权衡"叶子因是谁"(它也没证据)。
            # 叶子因待定机械注记落 caveats;直接子节点三态摆出来,数据补齐后重跑可细化到叶子。
            kids = []
            for cid, c in nodes.items():
                if c.get("parent") != nid:
                    continue
                ccond = c.get("condition") or ""
                if c.get("pass_through") or not ccond or "无条件" in ccond or "无判断" in ccond:
                    continue
                cf = c.get("facts") or []
                kids.append({"node": c.get("name"), "node_id": cid,
                             "状态": _tri(cf) if cf else "未覆盖(没人绑/缺数据)"})
            for u in unc:
                uid = u.get("id") if isinstance(u, dict) else u
                if uid and not any(k["node_id"] == uid for k in kids):
                    nm = (u.get("name") if isinstance(u, dict) else "") or unc_names.get(uid, "")
                    kids.append({"node": nm, "node_id": uid, "状态": "未覆盖(没人绑/缺数据)"})
            if kids:
                entry["children_status"] = kids
            entry["leaf_pending"] = ("非叶子/有未覆盖子因:叶子因待定(下级无一成立)——按本层命中确证,"
                                     "finalize 自动落 caveats;子节点三态见 children_status,数据补齐后重跑可细化")
        entry["draft_tier"] = ("confirmed_candidate(机械建议:最深成立节点;祖先带伤/叶子因待定"
                               "均不拦,已各自注记;语义复核后定档)")
        confirmed.append(entry)

    return {
        "_readme": _README,
        "confirmed_candidates": confirmed,
        "probable_candidates": probable,
        "conflict_nodes": [{"node": nodes[nid].get("name"), "node_id": nid,
                            "path_names": [nodes[p].get("name") for p in _path(nid, nodes)],
                            "evidence_prefilled": [_ev(p, nodes[p]) for p in _path(nid, nodes)],
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
    draft = draft_pruned(data) if isinstance(ft, dict) and ft.get("pruned_nodes") else draft_md(data)
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
    print("  逐条裁决(确认/降档都要落字理由,候选不许无声消失):写一份小裁决单 decisions.json"
          "(格式见骨架 _readme 或 finalize_rca.py 文件头),然后"
          "python finalize_rca.py <本骨架> <裁决单> -o <结果目录>/rca.json 机械合成——**不要手写 rca.json**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
