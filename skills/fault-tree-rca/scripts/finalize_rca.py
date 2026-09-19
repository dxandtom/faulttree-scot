#!/usr/bin/env python3
"""裁决单 → rca.json 机械合成器:模型只写小裁决单,几百行 JSON 由代码拼,取代 validate 后置校验。

实测发现的问题:draft 解决了"忘候选",但模型仍要手打整份 rca.json(几百行,写半天还会抄错)。
与绑定产线同配方——模型只交判定,机械字段代码填。诚实性从"事后校验"改成"结构上做不出假的":
- 候选清单里的候选**一条都不许漏裁决**(漏了明确报错,候选想消失必须显式给 verdict+理由);
- verdict=confirmed 的检查:**只看候选自身**——候选(叶子,或下级无一成立的中间节点)必须成立
  (被改成 存疑/语义近似/不成立 → 拒收);中间节点确证的「叶子因待定」自动落 caveats;
  **祖先的规则**:「未绑定/数据不足/存疑」=默认放行(零信息不当作反证),落 caveats、
  报告提示"此处无判断"——判断方法写错的中间节点走调优表清绑定即归此类;
  上级实测「不成立/冲突」=层级矛盾 → **确证拒收**,只收 probable(blocked_at=该上级,
  note 写清矛盾)——留着绑定又实测不成立就是有效反证;带 auto_verdict 的候选不给裁决
  自动收,给了相反裁决则明确拒收;
- 有成立事实的候选 verdict=undetermined → 拒收(有证据的不收待确认);
- verdict=excluded 必须给 reason(排除无理由拒收);
- evidence_path/Fault_tree_path 逐字来自候选清单(代码抓的原文),模型改不了数字。

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

机械说明中英并列:本脚本写进 rca.json 的说明(祖先注记、叶子因待定、卡点、待确认原因、排除留痕)
同时给英文版本,放在同名加 _en 的字段里(caveats_en、blocked_at_en、reason_en、excluded_note_en 等),
中文字段逐字不变。裁决单里模型写的理由、注意、卡点按当前对话的语言写,原样进两种版本。
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
# 祖先注记与叶子因待定的中英模板(zh 逐字等于原有输出)
_L = {
    "insuf": ("祖先节点未验证(无绑定/数据不足,默认放行——此处未做判断):{names}——由本节点成立事实自下而上蕴含",
              "Ancestor nodes not verified (no binding / insufficient data; passed by default, not judged here): {names}; "
              "implied bottom-up by this node's holding facts"),
    "refuted": ("⚠祖先节点判定不成立(层级矛盾):{names}——与本叶子命中矛盾;请复核该祖先绑定(可能假阴)或叶子命中(可能背景)",
                "⚠Ancestor nodes judged not holding (hierarchy contradiction): {names}; contradicts this leaf hit; "
                "review the ancestor binding (possible false negative) or the leaf hit (possible background)"),
    "conflict": ("⚠祖先节点证据冲突(成立与强反证并存):{names}——冲突两边实测值见 evidence_path 该条目的 conflict_actual/conflict_how",
                 "⚠Ancestor nodes with conflicting evidence (holds and strong counter-evidence coexist): {names}; "
                 "both observed values are in that evidence_path entry's conflict_actual / conflict_how"),
    "doubt": ("祖先节点存疑:{names}", "Ancestor nodes in doubt: {names}"),
    "leaf": ("已定位到本层,叶子因待定(下级无一成立){kids}——数据补齐后重跑可细化到叶子",
             "Located at this level; the leaf cause is pending (no child holds){kids}; rerun after data is supplied to refine to a leaf"),
    "leaf_kids": (";子节点:{ks}{more}", "; children: {ks}{more}"),
    "insufficient": ("数据不足", "insufficient data"),
}


def _t(key: str, lang: str = "zh", **kw) -> str:
    return _L[key][0 if lang == "zh" else 1].format(**kw)


def _nm(x: dict, lang: str) -> str:
    return str((x.get("node_en") if lang == "en" else None) or x.get("node") or "")


def _load(path: Path):
    """json 优先,解析失败再用 ast(弱模型爱写 Python 风格单引号/True)。"""
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError) as e:
            raise SystemExit(f"裁决单/候选清单解析失败({path}): {e} —— 修文件格式,别绕过本脚本手写 rca.json")


def _ev_final(entry, overrides):
    """候选清单预填的 evidence → 最终 evidence_path(逐字照搬;overrides 只许把 judgment 改成存疑落字)。"""
    out = []
    for e in entry.get("evidence_prefilled") or [entry]:
        item = {k: e[k] for k in ("node", "node_en", "node_id", "condition", "actual", "actual_en", "how", "judgment",
                                  "conflict_actual", "conflict_actual_en", "conflict_how",
                                  "other_source_note", "other_source_note_en") if k in e}
        ov = (overrides or {}).get(str(e.get("node_id")))
        if ov:
            item["judgment"] = str(ov)
        out.append(item)
    return out


def _dirty(ev):
    """确证检查里的"不干净"条目:**只看候选自身(路径末位)**——不是 成立/无独立条件(含被 overrides
    改成 存疑/语义近似)就拒收。祖先一律不拦:数据不足=零信息(查不了不当作反证);不成立/冲突=
    层级矛盾(祖先绑定漏判或叶子命中只是背景?)——由确证检查另行拒收,这里只管候选自身;
    存疑=对祖先证据的怀疑,同样不否定叶子自身的事实——全部由 _anc_notes 机械落 caveats 留痕。"""
    if not ev:
        return []
    e = ev[-1]
    j = str(e.get("judgment", ""))
    if any(c in j for c in _CLEAN) and "不成立" not in j:
        return []
    return [e]


def _anc_notes(ev, lang: str = "zh"):
    """路径祖先里所有"带伤"条目 → caveats 注记文案列表(确证时机械写进 caveats,诚实留痕:
    豁免不等于隐身,每类矛盾/缺口都点名+说清复核方向)。zh 与 en 两个列表条数、顺序一致。"""
    insuf, refuted, conflict, doubt = [], [], [], []
    for e in ev[:-1]:
        j = str(e.get("judgment", ""))
        if any(c in j for c in _CLEAN) and "不成立" not in j:
            continue
        {"数据不足": insuf, "不成立": refuted, "冲突": conflict}.get(j, doubt).append(e)
    sep = "、" if lang == "zh" else ", "
    notes = []
    for key, group in (("insuf", insuf), ("refuted", refuted), ("conflict", conflict)):
        if group:
            notes.append(_t(key, lang, names=sep.join(f"{_nm(x, lang)}({x.get('node_id')})" for x in group[:5])))
    if doubt:
        notes.append(_t("doubt", lang, names=sep.join(f"{_nm(x, lang)}({x.get('node_id')}:{x.get('judgment')})"
                                                      for x in doubt[:5])))
    return notes


def _leaf_note(kids, lang: str = "zh") -> str:
    sep = "、" if lang == "zh" else ", "
    ks = sep.join(f"{_nm(k, lang)}({k.get('node_id')}:{(k.get('status_en') if lang == 'en' else None) or k.get('状态')})"
                  for k in kids[:6])
    more = "…" if len(kids) > 6 else ""
    return _t("leaf", lang, kids=(_t("leaf_kids", lang, ks=ks, more=more) if ks else ""))


def _put_en(item: dict, key: str, en: str) -> None:
    """英文版与中文版不同才写 <key>_en。"""
    if en and en != item.get(key):
        item[key + "_en"] = en


def _read_decisions(path):
    """读裁决单并把 verdict 归一为四档:{node_id: 裁决条目(带 _v)};全自动定档时可不给裁决单。"""
    if path:
        dec_raw = _load(Path(path))
        dec_list = dec_raw.get("decisions") if isinstance(dec_raw, dict) else dec_raw
        if not isinstance(dec_list, list):
            raise SystemExit("裁决单须是 list(或 {\"decisions\": [...]}),一个候选一条")
    else:
        dec_list = []                                    # 候选清单全自动定档时允许不给裁决单
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
    return decisions


def _adjudicables(draft: dict) -> dict:
    """必裁决集 = 确证候选 + 疑似候选 + 冲突条目(md 形态为 holding_candidates + 冲突)。"""
    return {str(e["node_id"]): e for e in
            (draft.get("confirmed_candidates", []) + draft.get("probable_candidates", [])
             + draft.get("holding_candidates", []) + draft.get("conflict_nodes", []))}


def _apply_auto_verdicts(adjudicables: dict, decisions: dict) -> int:
    """自动定档合并:候选清单已机械定档的(auto_verdict)不给裁决自动收;给了相反裁决明确拒收
    (机械定档不容改写)。返回自动收的条数。"""
    auto_cnt = 0
    for nid, e in adjudicables.items():
        av = _VERDICT.get(str(e.get("auto_verdict") or "").strip().lower())
        if not av:
            continue
        if nid in decisions:
            if decisions[nid]["_v"] != av:
                raise SystemExit(
                    f"不合格:候选 {nid}「{e.get('node')}」已由候选清单机械定档为 {av}"
                    f"(定档条件全是机械可判的),裁决单不得改写成 {decisions[nid]['_v']}——"
                    "对机械档有异议:修数据/绑定后重跑,或在报告补充说明里点名请人复核")
            continue                                     # 同档:模型补充的 caveats/support 照收
        decisions[nid] = {"node_id": nid, "verdict": av, "_v": av}
        auto_cnt += 1
    return auto_cnt


def _check_coverage(draft: dict, adjudicables: dict, decisions: dict) -> None:
    """漏裁决检查:必裁决集一条都不许漏;裁决单里也不许冒出候选清单之外的节点。"""
    missing = [f"{nid}「{e.get('node')}」" for nid, e in adjudicables.items() if nid not in decisions]
    if missing:
        raise SystemExit("不合格:以下候选没有裁决(候选清单未机械定档,需要权衡;候选不许无声消失,"
                         "逐条给 verdict;不采纳=excluded+reason):\n  - " + "\n  - ".join(missing))
    skel_ids = {str(e.get("node_id")) for e in draft.get("undetermined_skeleton", [])}
    excl_ids = {str(e.get("node_id")) for e in draft.get("excluded_candidates", [])}
    unknown = [nid for nid in decisions if nid not in adjudicables and nid not in skel_ids and nid not in excl_ids]
    if unknown:
        raise SystemExit(f"不合格:裁决单里的节点不在候选清单中:{', '.join(unknown)}(别编节点)")


def _check_confirmable(nid: str, e: dict, ev: list) -> None:
    """确证前置检查:候选自身须干净(_dirty);路径上级不许有实测不成立/冲突(层级矛盾只收 probable)。"""
    bad = _dirty(ev)
    if bad:
        raise SystemExit(f"不合格:候选 {nid}「{e.get('node')}」verdict=confirmed,但候选自身不干净:"
                         + "、".join(f"{x.get('node')}({x.get('judgment')})" for x in bad[:3])
                         + " —— 确证要求:候选自身成立(未绑定/数据不足祖先与叶子因待定不拦,"
                           "自动落 caveats);对候选自身有疑,改 probable(带 blocked_at/caveats)"
                           "或 excluded(带 reason)")
    bad_anc = [x for x in ev[:-1]
               if "不成立" in str(x.get("judgment") or "") or "冲突" in str(x.get("judgment") or "")]
    if bad_anc:
        raise SystemExit(f"不合格:候选 {nid}「{e.get('node')}」verdict=confirmed,但路径上级 "
                         + "、".join(f"{x.get('node')}({x.get('judgment')})" for x in bad_anc[:3])
                         + " —— 规则:层级矛盾(候选成立+上级实测不成立/冲突)只收 probable"
                           "(blocked_at=该上级,note 写清矛盾);上级判断方法确认写错的,"
                           "调优表清其绑定(未绑定默认放行)后重跑")


def _confirmed_caveats(e: dict, d: dict, ev: list) -> tuple:
    """确证条目的 caveats (zh, en):裁决单的 caveats + 祖先注记 + 叶子因待定注记(中间节点确证必须落痕)。"""
    notes, notes_en = _anc_notes(ev), _anc_notes(ev, "en")
    kids = e.get("children_status") or []
    if e.get("leaf_pending") or kids:            # 中间节点确证:叶子因待定必须落痕,不许无痕
        notes.append(_leaf_note(kids))
        notes_en.append(_leaf_note(kids, "en"))
    cav = ";".join(x for x in ([str(d.get("caveats") or "").strip()] + notes) if x)
    cav_en = ";".join(x for x in ([str(d.get("caveats") or "").strip()] + notes_en) if x)
    return cav, cav_en


def _confirmed_item(nid: str, e: dict, d: dict, ev: list, path: list) -> dict:
    """确证条目合成:先做前置检查,再拼机械字段;祖先注记与叶子因待定机械落 caveats。"""
    _check_confirmable(nid, e, ev)
    item = {"Root_cause": e.get("node"), "Fault_tree_path": path,
            "node_id": nid,
            "support": d.get("support") or e.get("support_hint") or "",
            "evidence_path": ev}
    _put_en(item, "support", d.get("support") or e.get("support_hint_en") or e.get("support_hint") or "")
    cav, cav_en = _confirmed_caveats(e, d, ev)
    if cav:
        item["caveats"] = cav
        _put_en(item, "caveats", cav_en)
    return item


def _probable_caveats(e: dict, d: dict, ev: list) -> tuple:
    """疑似条目的 caveats (zh, en):裁决单 caveats(其次候选清单预填的 note)+ 层级矛盾之外的祖先缺数注记。"""
    cav_bits = [str(d.get("caveats") or e.get("note") or "").strip()]
    cav_bits_en = [str(d.get("caveats") or e.get("note_en") or e.get("note") or "").strip()]
    notes, notes_en = _anc_notes(ev), _anc_notes(ev, "en")
    keep = [i for i, x in enumerate(notes) if "数据不足" in x]   # 层级矛盾之外的祖先缺数也留痕
    cav_bits += [notes[i] for i in keep]
    cav_bits_en += [notes_en[i] for i in keep]
    return ";".join(x for x in cav_bits if x), ";".join(x for x in cav_bits_en if x)


def _probable_item(nid: str, e: dict, d: dict, ev: list, path: list) -> dict:
    """疑似条目合成:卡点/注意/support 优先取裁决单,其次候选清单预填(含英文预填)。"""
    cav, cav_en = _probable_caveats(e, d, ev)
    item = {"Root_cause": e.get("node"), "Fault_tree_path": path,
            "node_id": nid,
            "confidence": d.get("confidence") or "medium",
            "blocked_at": d.get("blocked_at") or e.get("blocked_at_hint") or "",
            "caveats": cav,
            "support": d.get("support") or e.get("support_hint") or "",
            "evidence_path": ev}
    _put_en(item, "blocked_at", d.get("blocked_at") or e.get("blocked_at_hint_en") or e.get("blocked_at_hint") or "")
    _put_en(item, "caveats", cav_en)
    _put_en(item, "support", d.get("support") or e.get("support_hint_en") or e.get("support_hint") or "")
    return item


def _excl_note(e: dict, nid: str, reason) -> tuple:
    """排除留痕一条的 (zh, en) 文案。"""
    return f"「{e.get('node')}」({nid}):{reason}", f"「{_nm(e, 'en')}」({nid}):{reason}"


def _synthesize(adjudicables: dict, decisions: dict) -> tuple:
    """按裁决逐条合成:确证/疑似条目、排除留痕(zh/en);excluded 无 reason、有成立事实却裁 undetermined 都拒收。"""
    rca_result, probable, excl_notes, excl_notes_en = [], [], [], []
    hold_ids = set(adjudicables)                          # 这些都有成立事实(候选/冲突)
    for nid, e in adjudicables.items():
        d = decisions[nid]
        v = d["_v"]
        ev = _ev_final(e, d.get("overrides"))
        path = e.get("path_names") or [e.get("node")]
        if v == "confirmed":
            rca_result.append(_confirmed_item(nid, e, d, ev, path))
        elif v == "probable":
            probable.append(_probable_item(nid, e, d, ev, path))
        elif v == "excluded":
            if not (d.get("reason") or "").strip():
                raise SystemExit(f"不合格:候选 {nid}「{e.get('node')}」verdict=excluded 但没给 reason——"
                                 "排除有成立事实的候选,理由只能是反证或绑定质量问题,必须落字")
            zh, en = _excl_note(e, nid, d["reason"])
            excl_notes.append(zh)
            excl_notes_en.append(en)
        else:                                             # undetermined:有成立事实的不收
            if nid in hold_ids:
                raise SystemExit(f"不合格:候选 {nid}「{e.get('node')}」有'成立'事实,不收 undetermined"
                                 "(待确认=既无正向证据又缺数据)——升 probable 或有据 excluded")
    return rca_result, probable, excl_notes, excl_notes_en


def _undetermined_items(draft: dict, decisions: dict, excl_notes: list, excl_notes_en: list) -> list:
    """待确认条目(真缺数据的节点)自动带出;模型给了裁决的按裁决走(如补充 reason,或 excluded+reason 转排除留痕)。"""
    undetermined = []
    for e in draft.get("undetermined_skeleton", []):
        nid = str(e.get("node_id"))
        d = decisions.get(nid, {})
        if d and d.get("_v") == "excluded":
            if not (d.get("reason") or "").strip():
                raise SystemExit(f"不合格:{nid} verdict=excluded 但没给 reason")
            zh, en = _excl_note(e, nid, d["reason"])
            excl_notes.append(zh)
            excl_notes_en.append(en)
            continue
        item = {"Fault_tree_path": [e.get("node")],
                "blocked_at": f"{e.get('node')}({nid})",
                "reason": d.get("reason") or e.get("actual") or _t("insufficient"),
                "observed": [],
                **({"gap_kind": e["gap_kind"]} if e.get("gap_kind") else {})}
        _put_en(item, "blocked_at", f"{_nm(e, 'en')}({nid})")
        _put_en(item, "reason", d.get("reason") or e.get("actual_en") or e.get("actual") or _t("insufficient", "en"))
        undetermined.append(item)
    return undetermined


def _attach_extras(doc: dict, rca_input, solutions) -> None:
    """附加项:tree_issues 从 rca_input 抄(不给则空);recommend_solution 按根因节点点查 solutions.json;读失败只警告。"""
    if rca_input:
        try:
            ri = json.loads(Path(rca_input).read_text(encoding="utf-8"))
            doc["tree_issues"] = ri.get("tree_issues") or {}
        except Exception as e:
            print(f"[warn] 读 rca_input 失败({e}),tree_issues 置空")
    if solutions:
        try:
            sol = json.loads(Path(solutions).read_text(encoding="utf-8"))
            for rc in doc["rca_result"] + doc["probable_causes"]:
                s = sol.get(str(rc.get("node_id")))
                if isinstance(s, dict) and (s.get("zh") or s.get("en")):
                    rc["recommend_solution"] = s.get("zh") or s.get("en")
                    _put_en(rc, "recommend_solution", s.get("en") or "")
        except Exception as e:
            print(f"[warn] 读 solutions.json 失败({e}),不附解决方案")


def _write_doc(doc: dict, out: Path, n_excl: int, auto_cnt: int) -> None:
    """写盘并打印合成统计与下一步。"""
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已机械合成 → {out}")
    print(f"  确证 {len(doc['rca_result'])} 条、疑似 {len(doc['probable_causes'])} 条、待确认 {len(doc['undetermined'])} 条"
          f"、排除留痕 {n_excl} 条(其中 {auto_cnt} 条由候选清单机械定档,零模型裁决)。")
    print("  下一步:python render_report.py <本 rca.json> 机械渲染报告(证据/命中原文/卡点全从"
          " rca.json 拼,模型只在报告末尾追加「补充说明」段,数字不许改)。")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="裁决单 → rca.json 机械合成(模型只交判定,拼装归代码)")
    ap.add_argument("draft")
    ap.add_argument("decisions", nargs="?", default=None,
                    help="模型裁决单(只需含候选清单里没定档的条目;全自动定档时可省略)")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--rca-input", help="抄 tree_issues 用;不给则输出空 tree_issues")
    ap.add_argument("--solutions", help="tree/solutions.json,按根因节点点查 recommend_solution")
    a = ap.parse_args()

    draft = _load(Path(a.draft))
    decisions = _read_decisions(a.decisions)
    adjudicables = _adjudicables(draft)
    auto_cnt = _apply_auto_verdicts(adjudicables, decisions)
    _check_coverage(draft, adjudicables, decisions)

    rca_result, probable, excl_notes, excl_notes_en = _synthesize(adjudicables, decisions)
    undetermined = _undetermined_items(draft, decisions, excl_notes, excl_notes_en)

    doc = {"rca_result": rca_result, "probable_causes": probable,
           "undetermined": undetermined, "tree_issues": {}}
    if excl_notes:
        doc["excluded_note"] = ";".join(excl_notes)
        _put_en(doc, "excluded_note", ";".join(excl_notes_en))
    _attach_extras(doc, a.rca_input, a.solutions)
    _write_doc(doc, Path(a.out), len(excl_notes), auto_cnt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
