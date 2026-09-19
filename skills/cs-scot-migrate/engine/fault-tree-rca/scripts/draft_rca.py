#!/usr/bin/env python3
"""RCA 候选清单(rca_draft.json)机械生成器 —— 枚举是机械活,模型只做裁决。

实测发现的问题:让模型自己"发现候选"(挨个 rca_query --node 翻),弱模型会把成立的节点整个忘掉
(n25/n26 事实 predicate_satisfied=True 却通篇消失),或把成立写成"binding证据不足"。
本脚本从 rca_input.json 机械枚举:所有成立节点→候选(根→叶路径+证据全预填),
不成立→排除候选,数据不足→待确认条目,成立+不成立并存→冲突条目。

**自动定档(树形态)**:定档条件全是机械可判的,代码直接写 auto_verdict,模型不参与——
- 候选自身成立 + 路径上**有判定的**节点无「不成立/冲突」→ auto_verdict=confirmed
  (**未绑定/数据不足的上级=默认放行**:零信息不当作反证,落 caveats、报告提示"此处无判断";
  判断方法写错的中间节点走调优表**清绑定**即归此类,停验放行);
- 候选自身成立 + 某上级实测「不成立/冲突」→ auto_verdict=probable(**层级矛盾降疑似**:
  留着绑定又实测不成立=有效反证,blocked_at/note 预填卡点与实际值给人复核);
- 冲突条目(候选自身成立+强反证并存)→ **唯一交模型权衡的**,清单不定档。
**模型的工作收窄为:只裁冲突条目**(md 形态无路径信息,退回逐条裁决)。

机械说明中英并列:候选清单与 rca.json 里由本脚本生成的说明文字(卡点、层级矛盾、缺口分型、
弱阴性注记等)同时给出英文版本,放在同名加 _en 的字段里;中文字段逐字不变(平台与评估按它解析)。
节点英文名来自 rca_input 的 pruned_nodes.name_en(树里有才有),没有英文名的节点回退中文名。

用法:python draft_rca.py <rca_input.json> [-o <rca_draft.json>]   # 缺省写在输入同目录
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_README = (
    "本候选清单由代码从 rca_input 机械枚举:候选=有'成立'事实的节点(路径+证据已预填,how 逐字来自 explain)。"
    "**带 auto_verdict 的条目已由代码机械定档,不需要也不许改写**(finalize 自动收;确证=候选自身成立"
    "且有判定的上级无不成立/冲突——未绑定/数据不足上级默认放行落 caveats;层级矛盾=机械降 probable,"
    "blocked_at/note 已预填;**候选是中间节点且下级无一成立**同样机械确证——叶子因待定见 "
    "leaf_pending/children_status 注记,自动落 caveats)。**你的工作只剩:①裁决 conflict_nodes**(候选自身成立+强反证并存,权衡两面证据"
    "定档并写 caveat——冲突细节在该条目的 conflict_actual/conflict_how);②(仅 md 形态,无路径信息)"
    "逐条裁决 holding_candidates。语义复核仍是你的责任:发现 mapping_reason 与判据不真等,"
    "在报告补充说明里点名请人复核——不改机械档。**任何候选不许无声消失**:冲突条目不采纳就 "
    "excluded+reason。数据是有的就不许写'数据不足/证据不足'。裁决写成小 decisions.json"
    "(只含需要你裁的条目;全自动时可不写),然后跑 finalize_rca.py <本清单> [裁决单] 机械合成 "
    "rca.json——不要手写 rca.json。"
)


# 机械说明的中英模板:zh 逐字等于原有输出,en 供 render_report / render_tree --lang en 使用
_L = {
    "absent_ok": ("absent=true(缺失类:未出现=成立)", "absent=true (absence class: not present = holds)"),
    "hit": ("命中原文:「{h}」", "matched text: 「{h}」"),
    "no_facts": ("无任何事实(没人绑/缺数据源)", "no facts at all (nothing bound / data source missing)"),
    "other_source": ("另有 {n} 条存在类绑定未命中(如:{ex})——存在类的未命中=该份数据里没看见,不构成反证,caveat 提一笔即可",
                     "{n} other presence-class binding(s) did not match (e.g. {ex}); a presence-class miss only means "
                     "not seen in this data and is not counter-evidence; mention it in the caveat"),
    "gap_no_data": ("数据不足(缺数据)", "insufficient data (data missing)"),
    "gap_cannot": ("数据在,无法判断", "data present, cannot judge"),
    "gap_mixed": ("部分缺数据/部分无法判断", "partly missing data / partly cannot judge"),
    "uncovered": ("未覆盖(没人绑/缺数据)", "not covered (nothing bound / data missing)"),
    "kind_refuted": ("实测不成立", "observed not holding"),
    "kind_conflict": ("证据冲突(成立与强反证并存)", "conflicting evidence (holds and strong counter-evidence coexist)"),
    "blocked_hint": ("上级「{node}」({nid}){kind}", "ancestor 「{node}」({nid}) {kind}"),
    "note_actual": ("(实际值:{a})", " (observed: {a})"),
    "note": ("层级矛盾:候选「{cand}」实测成立,但上级「{node}」{kind}{actual}——可能该上级绑定假阴,或候选命中系背景;"
             "人复核前不升确证(该上级判断方法确认写错的,走调优表清其绑定→未绑定默认放行,下轮即确证)",
             "Hierarchy contradiction: candidate 「{cand}」 holds as observed, but ancestor 「{node}」 {kind}{actual}; "
             "the ancestor binding may be a false negative, or the candidate hit may be background; not promoted to "
             "confirmed before human review (if the ancestor's judgment method is confirmed wrong, clear its binding "
             "in the tuning table; an unbound ancestor passes by default and the next run confirms)"),
    "conflict_note": ("同节点成立与不成立并存(真冲突),权衡后归档并写 caveat",
                      "The same node both holds and does not hold (genuine conflict); weigh both sides, file it and write a caveat"),
}
_TRI_EN = {"成立": "holds", "不成立": "does not hold", "冲突": "conflict", "数据不足": "insufficient data"}


def _t(key: str, lang: str = "zh", **kw) -> str:
    return _L[key][0 if lang == "zh" else 1].format(**kw)


def _name(n: dict, lang: str) -> str:
    """节点显示名:en 且有 name_en / node_en 用英文,否则中文。"""
    if lang == "en":
        return str(n.get("name_en") or n.get("node_en") or n.get("name") or n.get("node") or "")
    return str(n.get("name") or n.get("node") or "")


def _put(d: dict, key: str, zh: str, en: str) -> None:
    """写中文字段;英文与中文不同才写 <key>_en(纯数字/原文的说明两边一样,不重复存)。"""
    d[key] = zh
    if en != zh:
        d[key + "_en"] = en


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


def _actual(f: dict, lang: str = "zh") -> str:
    """事实 → 简短的机械 actual 串(数字逐字,不加工;只有固定提示语按语言取)。"""
    if not f:
        return ""
    bits = []
    for k in ("matched", "total", "ratio", "value", "op", "threshold", "count_satisfied",
              "valid", "groups", "matched_groups", "local", "peer", "equal"):
        if f.get(k) is not None:
            bits.append(f"{k}={f[k]}")
    if f.get("absent"):
        bits.append(_t("absent_ok", lang))
    if f.get("evidence"):                                # S 脚本证据标签:实体+命中原文+来源文件,比裸 hits 全
        bits.append(";".join(str(x)[:220] for x in f["evidence"][:2]))
    elif f.get("hits"):
        bits.append(_t("hit", lang, h=f['hits'][0]))
    if not bits and f.get("reason"):
        bits.append(str(f["reason"])[:120])
    return ";".join(str(b) for b in bits)[:400]


def _put_actual(e: dict, f: dict, key: str = "actual") -> None:
    _put(e, key, _actual(f), _actual(f, "en"))


def _fill_fact(e: dict, f, actual_key: str, how_key: str) -> None:
    """代表事实 → evidence 条目:actual(中英)+ explain(键名按正向侧/反证侧传入)。"""
    _put_actual(e, f, actual_key)
    if f and f.get("explain"):
        e[how_key] = f["explain"]


def _ev_holding(e: dict, facts, tri: str) -> None:
    """成立/冲突节点的证据:正向事实作代表;冲突另记反证一侧,成立则把存在类弱阴性如实记一笔。"""
    _fill_fact(e, _pick(facts, True), "actual", "how")
    if tri == "冲突":
        _fill_fact(e, _pick(facts, False), "conflict_actual", "conflict_how")
        return
    misses = [x for x in facts if x.get("status") == "ok" and x.get("predicate_satisfied") is False]
    if misses:                                   # 存在类弱阴性:如实记一笔,但不拉低判定
        ex = (misses[0].get('explain') or '')[:80]
        _put(e, "other_source_note", _t("other_source", "zh", n=len(misses), ex=ex),
             _t("other_source", "en", n=len(misses), ex=ex))


def _ev_insufficient(e: dict, facts) -> None:
    """数据不足节点的证据:没 facts 写"无任何事实",有 reason 用 reason 原文,再按 gap_kind 分型加前缀。"""
    _put(e, "actual", "" if facts else _t("no_facts"), "" if facts else _t("no_facts", "en"))
    rs = [f.get("reason") for f in facts if f.get("reason")]
    if rs:
        e["actual"] = str(rs[0])[:160]
        e.pop("actual_en", None)
    _apply_gap(e, facts)


def _ev(nid: str, n: dict) -> dict:
    """一个路径节点 → 预填的 evidence 条目(judgment 是机械三态,模型可降不可无据升)。"""
    cond = n.get("condition") or ""
    has_cond = bool(cond) and "无条件" not in cond and "无判断" not in cond
    e = {"node": n.get("name"), "node_id": nid, "condition": cond}
    if n.get("name_en"):
        e["node_en"] = n["name_en"]
    facts = n.get("facts") or []
    if n.get("pass_through") or not has_cond:
        e["judgment"] = "无独立条件"
        return e
    tri = _tri(facts) if facts else "数据不足"
    e["judgment"] = tri
    if tri in ("成立", "冲突"):
        _ev_holding(e, facts, tri)
    elif tri == "不成立":
        _fill_fact(e, _pick(facts, False), "actual", "how")
    else:
        _ev_insufficient(e, facts)
    return e


def _apply_gap(e: dict, facts) -> None:
    """四态约定里"判不了"的两种分型(通用适配函数的 gap_kind):缺数据(补采可解) vs 数据在判不动(判据质量信号)。
    事实 reason 可能已带适配函数加的分型前缀(字面略长),按前缀族判重,避免双前缀。"""
    kinds = {f.get("gap_kind") for f in facts if isinstance(f, dict) and f.get("gap_kind")}
    if not kinds:
        return
    gk = ("cannot_judge" if kinds == {"cannot_judge"}
          else "no_data" if kinds == {"no_data"} else "mixed")
    e["gap_kind"] = gk
    key = {"no_data": "gap_no_data", "cannot_judge": "gap_cannot", "mixed": "gap_mixed"}[gk]
    if not str(e.get("actual") or "").startswith(("数据不足", "数据在,", "部分缺")):
        base_en = e.get("actual_en") or e.get("actual")
        e["actual_en"] = (_t(key, "en") + (":" + base_en if base_en else ""))[:200]
        e["actual"] = (_t(key) + (":" + e["actual"] if e.get("actual") else ""))[:200]


def _path(nid: str, nodes: dict, limit=400):
    """root→nid 的节点 id 链(沿 parent 上溯;剪枝保证祖先全保留)。"""
    chain, cur, seen = [], nid, set()
    while cur and cur in nodes and cur not in seen and len(chain) < limit:
        seen.add(cur)
        chain.append(cur)
        cur = nodes[cur].get("parent")
    return list(reversed(chain))


def _no_own_cond(n: dict) -> bool:
    """透传节点或无真条件(空/「无条件」/「无判断」)的节点:不进候选清单、不算子节点三态。"""
    cond = n.get("condition") or ""
    return bool(n.get("pass_through") or not cond or "无条件" in cond or "无判断" in cond)


def _bucket_nodes(nodes: dict) -> tuple:
    """三态分桶:有事实的真条件节点按 成立/不成立/数据不足/冲突 分四组 id 列表(未覆盖=没 facts 不进清单)。"""
    holding, refuted, insuf, conflict = [], [], [], []
    for nid, n in nodes.items():
        if _no_own_cond(n):
            continue
        facts = n.get("facts") or []
        if not facts:
            continue                                    # 未覆盖:归 tree_issues,不进候选清单
        tri = _tri(facts)
        {"成立": holding, "不成立": refuted, "数据不足": insuf, "冲突": conflict}[tri].append(nid)
    return holding, refuted, insuf, conflict


def _deepest_holding(holding: list, nodes: dict) -> list:
    """候选=成立节点中"没有成立后代"的(最深的成立);成立的祖先在候选的路径里体现,不重复开条。"""
    hold_set = set(holding)
    deepest = []
    for nid in holding:
        others = hold_set - {nid}
        if not any(nid in _path(o, nodes)[:-1] for o in others):
            deepest.append(nid)
    return deepest


def _ancestor_notes(entry: dict, ev: list) -> tuple:
    """路径上级三类注记(未验证/不成立/冲突)写进候选条目;返回 (refutes, gaps) 供机械定档。
    定档规则(机械,模型不参与):
    ①未绑定/数据不足的上级=默认放行(零信息不当作反证)落 caveats,报告提示"此处无判断"
      ——判断方法写错的中间节点,正路是调优表清绑定停验,归到这类;
    ②上级实测「不成立/冲突」=层级矛盾 → 机械降疑似:留着绑定又实测不成立就是有效反证,
      blocked_at/note 预填卡点与实际值,人复核(上级绑定假阴?候选命中系背景?)前不升确证。"""
    anc_insuf = [e for e in ev[:-1] if e["judgment"] == "数据不足"]
    gaps = [e for e in ev[:-1] if e["judgment"] == "冲突"]
    refutes = [e for e in ev[:-1] if e["judgment"] == "不成立"]
    if anc_insuf:
        entry["ancestor_insufficient"] = (
            "祖先节点未验证(无绑定/数据不足/无法判断):"
            + "、".join(e["node"] + "(" + e["node_id"] + ")" for e in anc_insuf[:5])
            + "——默认放行(零信息不当作反证),由本节点成立事实自下而上蕴含;"
              "finalize 落 caveats,报告提示'此处无判断'")
    if refutes:
        entry["ancestor_refuted"] = (
            "祖先节点判定不成立:"
            + "、".join(e["node"] + "(" + e["node_id"] + ")" for e in refutes[:5])
            + "——层级矛盾,已机械降 probable(blocked_at/note 已预填);复核该上级绑定(可能假阴)"
              "或叶子命中(可能背景)")
    if gaps:
        entry["ancestor_conflict"] = (
            "祖先节点证据冲突(成立与强反证并存):"
            + "、".join(e["node"] + "(" + e["node_id"] + ")" for e in gaps[:5])
            + "——层级矛盾,已机械降 probable;冲突两边的实测值在 evidence 该条目的 "
              "conflict_actual/conflict_how,报告里把冲突点标出来")
    return refutes, gaps


def _children_status(nid: str, nodes: dict, unc, unc_names: dict) -> list:
    """中间节点候选的直接子节点三态列表(未覆盖子因补在后面,名字从覆盖报告回填)。"""
    kids = []
    for cid, c in nodes.items():
        if c.get("parent") != nid:
            continue
        if _no_own_cond(c):
            continue
        cf = c.get("facts") or []
        st = _tri(cf) if cf else _t("uncovered")
        kid = {"node": c.get("name"), "node_id": cid, "状态": st,
               "status_en": _TRI_EN.get(st) or _t("uncovered", "en")}
        if c.get("name_en"):
            kid["node_en"] = c["name_en"]
        kids.append(kid)
    for u in unc:
        uid = u.get("id") if isinstance(u, dict) else u
        if uid and not any(k["node_id"] == uid for k in kids):
            nm = (u.get("name") if isinstance(u, dict) else "") or unc_names.get(uid, "")
            kids.append({"node": nm, "node_id": uid, "状态": _t("uncovered"),
                         "status_en": _t("uncovered", "en")})
    return kids


def _auto_tier(entry: dict, n: dict, refutes: list, gaps: list) -> None:
    """机械定档:层级矛盾(上级不成立/冲突)→ probable 并预填卡点与 note(中英);否则 confirmed。"""
    if refutes or gaps:
        # 层级矛盾 → 机械降疑似:卡点=离候选最近的带反证上级(最可指认)
        worst = (refutes or gaps)[-1]
        kind_key = "kind_refuted" if refutes else "kind_conflict"
        entry["auto_verdict"] = "probable"
        for lang in ("zh", "en"):
            kind = _t(kind_key, lang)
            wa = worst.get("actual_en") if lang == "en" and worst.get("actual_en") else worst.get("actual")
            hint = _t("blocked_hint", lang, node=_name(worst, lang), nid=worst["node_id"], kind=kind)
            note = _t("note", lang, cand=_name(n, lang), node=_name(worst, lang), kind=kind,
                      actual=(_t("note_actual", lang, a=str(wa)[:120]) if worst.get("actual") else ""))
            entry["blocked_at_hint" if lang == "zh" else "blocked_at_hint_en"] = hint
            entry["note" if lang == "zh" else "note_en"] = note
        entry["draft_tier"] = "probable_auto(机械降档:候选成立+上级实测不成立/冲突=层级矛盾)"
        return
    entry["auto_verdict"] = "confirmed"
    entry["draft_tier"] = ("confirmed_auto(机械定档:候选自身成立+有判定的上级全成立;"
                           "未绑定/数据不足上级默认放行落 caveats,报告提示未验证)")


def _candidate_entry(nid: str, nodes: dict, unc_names: dict) -> dict:
    """一个最深成立节点 → 候选条目:路径+证据预填、祖先注记、子节点三态(中间节点)、机械定档。"""
    n = nodes[nid]
    ev = [_ev(p, nodes[p]) for p in _path(nid, nodes)]
    entry = {"node": n.get("name"), "node_id": nid,
             "path_names": [nodes[p].get("name") for p in _path(nid, nodes)],
             "evidence_prefilled": ev}
    f = _pick(n.get("facts") or [], True)
    if f:
        _put(entry, "support_hint", _actual(f), _actual(f, "en"))
    refutes, gaps = _ancestor_notes(entry, ev)
    unc = n.get("uncovered_children") or []
    if not n.get("is_leaf") or unc:
        # 中间节点候选:候选=最深成立(下面没有任何成立后代)。用户拍板:叶子没有一个成立时,
        # 中间层命中就按本层确证——压疑似只会逼模型空转权衡"叶子因是谁"(它也没证据)。
        # 叶子因待定机械注记落 caveats;直接子节点三态摆出来,数据补齐后重跑可细化到叶子。
        kids = _children_status(nid, nodes, unc, unc_names)
        if kids:
            entry["children_status"] = kids
        entry["leaf_pending"] = ("非叶子/有未覆盖子因:叶子因待定(下级无一成立)——按本层命中确证,"
                                 "finalize 自动落 caveats;子节点三态见 children_status,数据补齐后重跑可细化")
    _auto_tier(entry, n, refutes, gaps)
    return entry


def _conflict_entry(nid: str, nodes: dict) -> dict:
    """冲突条目(候选自身成立+强反证并存):路径+证据预填,不定档,交模型权衡。"""
    return {"node": nodes[nid].get("name"), "node_id": nid,
            **({"node_en": nodes[nid]["name_en"]} if nodes[nid].get("name_en") else {}),
            "path_names": [nodes[p].get("name") for p in _path(nid, nodes)],
            "evidence_prefilled": [_ev(p, nodes[p]) for p in _path(nid, nodes)],
            "note": _t("conflict_note"), "note_en": _t("conflict_note", "en")}


def draft_pruned(data: dict) -> dict:
    ft = data.get("fault_tree") or {}
    nodes = ft.get("pruned_nodes") or {}
    # 未覆盖节点不在 pruned_nodes 里,名字从覆盖报告回填(uncovered_children 只存 id)
    unc_names = {u.get("id"): u.get("name") for u in
                 ((data.get("tree_issues") or {}).get("uncovered_condition_nodes") or [])
                 if isinstance(u, dict)}
    holding, refuted, insuf, conflict = _bucket_nodes(nodes)
    deepest = _deepest_holding(holding, nodes)

    confirmed, probable = [], []
    for nid in sorted(deepest, key=lambda x: int(x[1:]) if x[1:].isdigit() else 10 ** 9):
        entry = _candidate_entry(nid, nodes, unc_names)
        (probable if entry["auto_verdict"] == "probable" else confirmed).append(entry)

    return {
        "_readme": _README,
        "confirmed_candidates": confirmed,
        "probable_candidates": probable,
        "conflict_nodes": [_conflict_entry(nid, nodes) for nid in sorted(conflict)],
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
                 "condition": (c.get("condition_text") or "")[:200]}
            _put_actual(e, f)
            if f.get("explain"):
                e["how"] = f["explain"]
            if tri == "数据不足":                          # md 形态同样带四态分型(与 _ev 同款)
                _apply_gap(e, [f])
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
    ap = argparse.ArgumentParser(description="RCA 候选清单机械生成(候选枚举交给代码,模型只裁决)")
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
    print(f"候选清单已生成 → {out}")
    if "holding_candidates" in draft:                    # md 形态:无路径,只有三态分拣
        print(f"  成立候选 {len(draft['holding_candidates'])} 条、冲突 {len(draft.get('conflict_nodes', []))} 条、"
              f"排除候选 {len(draft.get('excluded_candidates', []))} 条、"
              f"待确认条目 {len(draft.get('undetermined_skeleton', []))} 条(md 形态:路径按 fault_tree_md 回溯)。")
    else:
        print(f"  确证候选 {len(draft.get('confirmed_candidates', []))} 条、"
              f"疑似候选 {len(draft.get('probable_candidates', []))} 条、"
              f"冲突 {len(draft.get('conflict_nodes', []))} 条、排除候选 {len(draft.get('excluded_candidates', []))} 条、"
              f"待确认条目 {len(draft.get('undetermined_skeleton', []))} 条。")
    print("  逐条裁决(确认/降档都要落字理由,候选不许无声消失):写一份小裁决单 decisions.json"
          "(格式见候选清单的 _readme 或 finalize_rca.py 文件头),然后"
          "python finalize_rca.py <本清单> <裁决单> -o <结果目录>/rca.json 机械合成——**不要手写 rca.json**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
