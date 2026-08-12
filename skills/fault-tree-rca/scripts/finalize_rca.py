#!/usr/bin/env python3
"""裁决单 → rca.json 机械合成器:模型只写小裁决单,几百行 JSON 由代码拼,取代 validate 后置校验。

实测病根:draft 治了"忘候选",但模型仍要手打整份 rca.json(几百行,写半天还会抄错)。
与绑定产线同配方——模型只交判定,机械字段代码填。诚实性从"事后校验"改成"结构上做不出假的":
- 骨架里的候选**一条都不许漏裁决**(漏了响亮报错,候选想消失必须显式给 verdict+理由);
- verdict=confirmed 的闸:**只看候选自身**——候选(叶子,或下级无一成立的中间节点)必须成立
  (被改成 存疑/语义近似/不成立 → 拒收);中间节点确证的「叶子因待定」自动落 caveats;
  **祖先口径 v3:一律不拦档**——「数据不足/存疑」零信息不冒充反证;「不成立/冲突」=层级矛盾,
  照样确证(现场父节点绑定常未优化,不成立多为假阴——用户拍板),但 hierarchy_hint/⚠注记
  必须落 caveats 显著晾出(豁免不隐身);带 auto_verdict 的候选不给裁决自动收,
  给了相反裁决则响亮拒收;
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
    """确证闸的"不干净"条目:**只看候选自身(路径末位)**——不是 成立/无独立条件(含被 overrides
    改成 存疑/语义近似)就拒收。祖先一律不拦:数据不足=零信息(查不了不冒充反证);不成立/冲突=
    层级矛盾(祖先绑定假阴或叶子命中系背景?)——口径 v2 由确证闸另行拒收,这里只管候选自身;
    存疑=对祖先证据的怀疑,同样不否定叶子自身的事实——全部由 _anc_notes 机械落 caveats 留痕。"""
    if not ev:
        return []
    e = ev[-1]
    j = str(e.get("judgment", ""))
    if any(c in j for c in _CLEAN) and "不成立" not in j:
        return []
    return [e]


def _anc_notes(ev):
    """路径祖先里所有"带伤"条目 → caveats 注记文案列表(确证时机械写进 caveats,诚实留痕:
    豁免不等于隐身,每类矛盾/缺口都点名+说清复核方向)。"""
    insuf, refuted, conflict, doubt = [], [], [], []
    for e in ev[:-1]:
        j = str(e.get("judgment", ""))
        if any(c in j for c in _CLEAN) and "不成立" not in j:
            continue
        {"数据不足": insuf, "不成立": refuted, "冲突": conflict}.get(j, doubt).append(e)
    notes = []
    if insuf:
        notes.append("祖先节点自身判据未独立验证(数据不足):"
                     + "、".join(f"{x.get('node')}({x.get('node_id')})" for x in insuf[:5])
                     + "——由本节点成立事实自下而上蕴含")
    if refuted:
        notes.append("⚠祖先节点判定不成立(层级矛盾):"
                     + "、".join(f"{x.get('node')}({x.get('node_id')})" for x in refuted[:5])
                     + "——与本叶子命中矛盾;请复核该祖先绑定(可能假阴)或叶子命中(可能背景)")
    if conflict:
        notes.append("⚠祖先节点证据冲突(成立与强反证并存):"
                     + "、".join(f"{x.get('node')}({x.get('node_id')})" for x in conflict[:5])
                     + "——冲突两边实测值见 evidence_path 该条目的 conflict_actual/conflict_how")
    if doubt:
        notes.append("祖先节点存疑:"
                     + "、".join(f"{x.get('node')}({x.get('node_id')}:{x.get('judgment')})" for x in doubt[:5]))
    return notes


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="裁决单 → rca.json 机械合成(模型只交判定,拼装归代码)")
    ap.add_argument("draft")
    ap.add_argument("decisions", nargs="?", default=None,
                    help="模型裁决单(只需含骨架没定档的条目;骨架全自动定档时可省略)")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--rca-input", help="抄 tree_issues 用;不给则输出空 tree_issues")
    ap.add_argument("--solutions", help="tree/solutions.json,按根因节点点查 recommend_solution")
    a = ap.parse_args()

    draft = _load(Path(a.draft))
    if a.decisions:
        dec_raw = _load(Path(a.decisions))
        dec_list = dec_raw.get("decisions") if isinstance(dec_raw, dict) else dec_raw
        if not isinstance(dec_list, list):
            raise SystemExit("裁决单须是 list(或 {\"decisions\": [...]}),一个候选一条")
    else:
        dec_list = []                                    # 骨架全自动定档时允许不给裁决单
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
    # 骨架已机械定档的(auto_verdict):不给裁决自动收;给了相反裁决响亮拒收(机械档不容改写)
    auto_cnt = 0
    for nid, e in adjudicables.items():
        av = _VERDICT.get(str(e.get("auto_verdict") or "").strip().lower())
        if not av:
            continue
        if nid in decisions:
            if decisions[nid]["_v"] != av:
                raise SystemExit(
                    f"不合格:候选 {nid}「{e.get('node')}」已由骨架机械定档为 {av}"
                    f"(定档条件全是机械可判的),裁决单不得改写成 {decisions[nid]['_v']}——"
                    "对机械档有异议:修数据/绑定后重跑,或在报告补充说明里点名请人复核")
            continue                                     # 同档:模型补充的 caveats/support 照收
        decisions[nid] = {"node_id": nid, "verdict": av, "_v": av}
        auto_cnt += 1
    missing = [f"{nid}「{e.get('node')}」" for nid, e in adjudicables.items() if nid not in decisions]
    if missing:
        raise SystemExit("不合格:以下候选没有裁决(骨架未机械定档,需要权衡;候选不许无声消失,"
                         "逐条给 verdict;不采纳=excluded+reason):\n  - " + "\n  - ".join(missing))
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
                raise SystemExit(f"不合格:候选 {nid}「{e.get('node')}」verdict=confirmed,但候选自身不干净:"
                                 + "、".join(f"{x.get('node')}({x.get('judgment')})" for x in bad[:3])
                                 + " —— 确证要求:候选自身成立(祖先带伤/叶子因待定不拦,自动落 caveats);"
                                   "对候选自身有疑,改 probable(带 blocked_at/caveats)或 excluded(带 reason)")
            item = {"Root_cause": e.get("node"), "Fault_tree_path": path,
                    "node_id": nid,
                    "support": d.get("support") or e.get("support_hint") or "",
                    "evidence_path": ev}
            hint = e.get("hierarchy_hint")
            notes = ([hint] if hint else []) + [
                n for n in _anc_notes(ev)
                if not (hint and ("判定不成立" in n or "证据冲突" in n))]   # hint 已含矛盾详情,免重复
            kids = e.get("children_status") or []
            if e.get("leaf_pending") or kids:            # 中间节点确证:叶子因待定必须落痕,不许无痕
                ks = "、".join(f"{k.get('node')}({k.get('node_id')}:{k.get('状态')})" for k in kids[:6])
                notes.append("已定位到本层,叶子因待定(下级无一成立)"
                             + (f";子节点:{ks}{'…' if len(kids) > 6 else ''}" if ks else "")
                             + "——数据补齐后重跑可细化到叶子")
            cav = ";".join(x for x in ([str(d.get("caveats") or "").strip()] + notes) if x)
            if cav:
                item["caveats"] = cav
            rca_result.append(item)
        elif v == "probable":
            cav_bits = [str(d.get("caveats") or e.get("note") or "").strip()]
            cav_bits += [x for x in _anc_notes(ev) if "数据不足" in x]   # 层级矛盾之外的祖先缺数也留痕
            probable.append({"Root_cause": e.get("node"), "Fault_tree_path": path,
                             "node_id": nid,
                             "confidence": d.get("confidence") or "medium",
                             "blocked_at": d.get("blocked_at") or e.get("blocked_at_hint") or "",
                             "caveats": ";".join(x for x in cav_bits if x),
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
          f"、排除留痕 {len(excl_notes)} 条(其中 {auto_cnt} 条由骨架机械定档,零模型裁决)。")
    print("  下一步:python render_report.py <本 rca.json> 机械渲染报告(证据/命中原文/卡点全从"
          " rca.json 拼,模型只在报告末尾追加「补充说明」段,数字不许改)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
