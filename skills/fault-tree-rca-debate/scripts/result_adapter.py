#!/usr/bin/env python3
"""辩论引擎输出(top_k×3 + debate_records)→ 本仓 rca.json 四档契约。

诚实映射(评分排序为主体,候选自身的事实决定升档——与手工 fault-tree-rca 的确证条件同一规则):
- 辩论引擎产的是**评分排序的假设**,不是证明 → 默认进 `probable_causes`。
- **升档凭事实,规则=候选自身+有判定的上级(与手工路径同步)**:路径末端的叶子
  (候选)被**证据引擎实测「成立」**、是带判据的真叶子、**且路径无实测「不成立/冲突」的
  上级** → 进 `rca_result`(确证档)。未绑定/数据不足的上级=**默认放行**(零信息不当作
  反证,caveats 提示"此处无判断";判断方法写错的中间节点走调优表清绑定即归此类);
  上级实测不成立/冲突=层级矛盾 → 降疑似,blocked_at 指认该上级,机械落
  caveats 逐类点名;整条路径全实测成立的在 caveats 注明"全路径实测闭环"(最强成色)。
  升档依据是 execute 算出的事实,**不是辩论得分**(得分只管排序);validate_rca 的
  新规则(假根因只查候选自身)天然一致。叶子非真叶(如 L6 截断)/叶子非成立 → 留在
  probable(叶子数据不足的路径由 blocked_at 指认卡点;"叶子待定确证中间节点"那半条
  手工路径有、本适配层不做——诚实留疑似)。
- `confidence` 一律按 **original 路径得分**分档(softmax/ratio 是连乘概率、数值天然极小,
  不适合做绝对阈值);--mechanism 只决定**取哪份 top-k 排序**。
- evidence_path 的 judgment:升档条目的带判据节点写「成立(证据引擎实测)」(有事实背书,
  合法使用保留字);未升档条目仍用中性词(条件匹配/部分匹配/辩论评分),**不冒用**
  「成立/不成立/数据不足」——评分制结果冒用会触发(或骗过)validate_rca 的诚实性机械检查。
- tree_issues 从 rca_input 透传(覆盖度/孤儿信息不因换引擎而丢)。

供 run_debate_rca.py import;也可单独跑:
result_adapter.py <vendor_result.json> --tree-index <tree_index.json> [--rca-input <rca_input.json>]
                  [--mechanism original|softmax|ratio] -o <rca.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_MECH_KEY = {"original": "top_k_paths_original", "softmax": "top_k_paths_softmax",
             "ratio": "top_k_paths_ratio"}
_SCORE_KEY = {"original": "original_path_score", "softmax": "softmax_path_score",
              "ratio": "ratio_path_score"}
_ST_CN = {"held": "成立", "refuted": "不成立", "insufficient": "数据不足",
          "conflict": "冲突(成立与强反证并存)"}
# judgment 里按实测态附加的字眼——与 validate_rca 的 NOT_PROVEN 疑虑词表对齐:
# 疑似档的降档理由必须落在具体节点的 judgment 上,否则"全成立却居疑似"会被硬性检查打回(实测)。
_J_ST = {"held": ";证据引擎实测成立", "refuted": ";证据引擎实测不成立",
         "insufficient": ";证据引擎实测数据不足",
         "conflict": ";证据引擎实测冲突(成立与强反证并存,存疑)"}


def _facts_index(rca_input: dict | None) -> dict:
    """rca_input → {node_id: {"st": 四态, "obs", "fail", "insuf"}}。四态规则(含 conflict,
    与手工 draft_rca._tri 同源)统一定义在 facts_to_question.facts_index——回程事实感知的
    地基:confidence 校准、证伪/冲突旗标、blocked_at 回填、弃权处理都从这张表读。"""
    from facts_to_question import facts_index
    return facts_index(rca_input or {})


def _split_path(path: list) -> tuple:
    """一条 vendor 路径 → (节点列表, 路径得分尾巴 dict)。"""
    nodes = [x for x in path if isinstance(x, dict) and x.get("code")]
    tail = next((x for x in reversed(path) if isinstance(x, dict) and not x.get("code")), {})
    return nodes, tail


def _confidence(orig_score) -> str:
    try:
        s = float(orig_score)
    except (TypeError, ValueError):
        return "low"
    return "high" if s >= 0.6 else ("medium" if s >= 0.3 else "low")


def _judgment(n: dict, f: dict | None = None, unbound: bool = False) -> str:
    """节点 judgment=引擎侧评语 + **实测态字眼**(有 fx 的节点)。实测态不是装饰:
    validate 对疑似档的硬性检查要求降档理由落在具体节点上(不成立/数据不足/未判定这类疑虑词),
    只写中性词会被当"全成立却居疑似"误杀;有事实背书写实测态,既诚实又与手工路径用词一致。"""
    mt, sc = n.get("match_type"), n.get("node_score")
    sc_s = f"{float(sc):.2f}" if isinstance(sc, (int, float)) else "?"
    if mt == "full":
        base = f"条件匹配(full,得分{sc_s})"
    elif mt == "partial":
        base = f"部分匹配(得分{sc_s})"
    elif mt == "debate":
        base = f"辩论评分(得分{sc_s})"
    else:
        base = f"结构节点(得分{sc_s})"
    if f:
        return base + _J_ST[f["st"]]
    if unbound:
        return base + ";判据未绑定,未判定"
    return base


def to_rca_json(vendor_result: dict, mechanism: str, tree_index: dict,
                rca_input: dict | None = None) -> dict:
    if mechanism not in _MECH_KEY:
        raise ValueError(f"mechanism 应为 {list(_MECH_KEY)},收到 {mechanism!r}")
    nodes_idx = (tree_index or {}).get("nodes") or {}
    dbg = {r.get("node_code"): r for r in vendor_result.get("debate_records") or [] if isinstance(r, dict)}
    fx = _facts_index(rca_input)          # 证据引擎的确定性结论(回程事实感知的地基)
    # 弃权垫片:全场景没有任何"成立"事实(excluded/honest 情形,正确答案="别报根因")时,
    # 排序候选降入 undetermined 而非硬吐 probable——补上评分制引擎的弃权能力缺口。
    # 冲突=有成立侧证据,不算"零成立",不触发弃权。
    abstain = bool(fx) and not any(v["st"] in ("held", "conflict") for v in fx.values())

    def _has_cond(ti_n: dict) -> bool:
        """与 validate_rca._holding_map 同一规则:非空判据、且不是"无条件/无判断"占位。"""
        c = str(ti_n.get("condition") or "").strip()
        return bool(c) and "无条件" not in c and "无判断" not in c

    confirmed, probable, undetermined = [], [], []
    for path in vendor_result.get(_MECH_KEY[mechanism]) or []:
        nodes, tail = _split_path(path)
        if not nodes:
            continue
        leaf = nodes[-1]
        idx_n = nodes_idx.get(leaf.get("code")) or {}
        leaf_name = idx_n.get("name") or leaf.get("chinese_name") or leaf.get("name") or leaf.get("code")

        ev, debate_caveats = [], []
        blocked_node = None                    # 路径上第一个"数据不足"节点 = 最可指认的阻塞点
        refuted_nodes = []
        conflict_anc = []                      # 实测「冲突」的祖先(与不成立同为层级矛盾,拦升档)
        anc_notes = []                         # 祖先(非候选)事实状态:机械落 caveats
        path_closed = True                     # 全部带判据节点实测成立=全路径闭环(最强成色)
        for n in nodes:
            nid = n.get("code")
            ti_n = nodes_idx.get(nid) or {}
            f = fx.get(nid)
            if _has_cond(ti_n) and not ti_n.get("pass_through"):
                st = f["st"] if f else None
                if st != "held":
                    path_closed = False
                if nid != leaf.get("code"):    # 候选自身另行判;祖先只记状态
                    nm = ti_n.get("name") or nid
                    if st == "refuted":
                        anc_notes.append(f"⚠ 路径节点「{nm}」判据实测不成立(层级矛盾,拦升档待人审)")
                    elif st == "conflict":
                        anc_notes.append(f"⚠ 路径节点「{nm}」证据冲突(成立与强反证并存,拦升档存疑)")
                        conflict_anc.append(nm)
                    elif st == "insufficient":
                        if (fx.get(nid) or {}).get("gap") == "cannot_judge":
                            anc_notes.append(f"路径节点「{nm}」数据在但无法判断"
                                             "(默认放行,此处无判断;判据/脚本判不动,补数据无用)")
                        else:
                            anc_notes.append(f"路径节点「{nm}」数据不足(默认放行,此处无判断;补数据可闭环)")
                    elif st is None:
                        anc_notes.append(f"路径节点「{nm}」未验证(判据无绑定——默认放行,此处无判断)")
            unbound = _has_cond(ti_n) and not ti_n.get("pass_through") and not f
            if f:                              # actual 回填实测事实(老契约语义)
                actual = f"证据引擎实测[{_ST_CN[f['st']]}]" + (f":{f['obs']}" if f["obs"] else "")
                if f["st"] == "conflict" and f.get("fail"):
                    actual += f";反证:{f['fail']}"
                if f["st"] == "insufficient" and blocked_node is None:
                    blocked_node = ti_n.get("name") or nid
                if f["st"] == "refuted":
                    refuted_nodes.append(ti_n.get("name") or nid)
            else:
                actual = f"引擎评分 {n.get('node_score')}(match_type={n.get('match_type', '?')})"
            ev.append({"node": ti_n.get("name") or n.get("chinese_name") or n.get("name") or nid,
                       "node_id": nid,
                       "condition": str(ti_n.get("condition") or "").strip() or "(无独立判据)",
                       "actual": actual,
                       "judgment": _judgment(n, f, unbound)})
            if nid in dbg:
                r = dbg[nid]
                debate_caveats.append(
                    f"「{ev[-1]['node']}」经辩论评分:均分{r.get('mean_score')}、std {r.get('std_score')}"
                    f"(评委分歧越大越该存疑)")

        orig = tail.get("original_path_score")
        leaf_st = (fx.get(leaf.get("code")) or {}).get("st")
        ti_leaf = nodes_idx.get(leaf.get("code")) or {}
        # 路径末端非真叶/无独立判据(截断):引擎路径在此止步,整支其实没判完——把这一点
        # 落到末端节点的 judgment 上(validate 认"未判定"疑虑词),否则全成立的截断路径会被
        # "全成立却居疑似"的硬性检查误杀
        truncated = bool(ti_leaf.get("children")) or not _has_cond(ti_leaf) \
            or bool(ti_leaf.get("pass_through"))
        if truncated and ev:
            ev[-1]["judgment"] += ";路径在此截断(下级未展开/无独立判据),整支未判定"
        # confidence 校准(修对抗审查实锤的失真):引擎给 debate 节点的分上限 0.5,连乘让
        # "叶子已实证成立、只是祖先数据不足"的路径掉到 low——叶子实证成立且路径无证伪时
        # 托底 medium;叶子被数据证伪则封顶 low(评分再高也不该高于被证伪的置信)。
        conf = _confidence(orig)
        if leaf_st == "refuted":
            conf = "low"
        elif leaf_st == "held" and not refuted_nodes and conf == "low":
            conf = "medium"

        hier = None                            # 层级矛盾:候选实测成立、上级实测不成立/冲突(拦升档)
        if leaf_st == "held" and (refuted_nodes or conflict_anc):
            worst = (refuted_nodes or conflict_anc)[-1]
            kind = "实测不成立" if refuted_nodes else "证据冲突(成立与强反证并存)"
            hier = (f"⚠ 层级矛盾:候选自身实测成立,但上级「{worst}」{kind}——可能该上级绑定假阴,"
                    "或候选命中系背景;与手工路径同一规则不升确证,需人工复核"
                    "(该上级判断方法确认写错的,调优表清其绑定→未绑定默认放行)")
        conflict = ([f"⚠ 与证据引擎冲突:「{'、'.join(refuted_nodes)}」的判据实测不成立(已被数据排除),"
                     f"本条仅为评分排序结果"] if refuted_nodes and leaf_st != "held" else [])
        if hier:
            conflict.append(hier)
        if leaf_st == "conflict":
            conflict.append("⚠ 候选自身证据冲突(成立与强反证并存)——不升确证,须人工裁决两面证据")
        entry = {
            "Root_cause": leaf_name,
            "Fault_tree_path": [e["node"] for e in ev],
            "confidence": conf,
            "blocked_at": (f"受阻于上级「{(refuted_nodes or conflict_anc)[-1] if hier else ''}」"
                           "(实测与候选命中矛盾,层级矛盾待人裁)" if hier else
                           f"受阻于「{blocked_node}」(数据不足,判据未验证);评分式引擎输出,未做逐节点事实闭环"
                           if blocked_node else
                           "评分式引擎输出:节点由文本匹配/辩论打分,未做逐节点事实闭环验证"),
            "caveats": "; ".join(
                conflict
                + [f"排序机制={mechanism}(路径得分 {tail.get(_SCORE_KEY[mechanism])});"
                   f"original 路径得分 {orig}(confidence 依据"
                   + (",叶子判据经证据引擎实测成立,已托底 medium" if leaf_st == "held" and conf == "medium" else "")
                   + ")"]
                + debate_caveats)
                + ";得分机制孰优待评测确认"
                + (";⚠ 本次运行 LLM 曾连接失败、引擎降级为关键词匹配续跑,结果可信度受损,建议重跑"
                   if vendor_result.get("llm_degraded") else ""),
            "support": f"辩论引擎 Top-K 路径(k 内排名按 {mechanism});"
                       f"路径节点匹配/辩论详情见 evidence_path 与 result/rca_debate_full.json",
            "evidence_path": ev,
            "_engine": {"name": "fault-tree-rca-debate", "mechanism": mechanism},
        }
        # 升档(与手工路径的确证条件同一规则):候选=路径末端叶子,自身实测成立、
        # 且路径无实测「不成立/冲突」上级(层级矛盾降疑似,hier 已写清卡点);
        # 未绑定/数据不足上级默认放行,anc_notes 落 caveats 提示"此处无判断";
        # conflict 候选不升(手工 finalize 对冲突候选同样拒收确证)
        if not abstain and leaf_st == "held" and not truncated \
                and not refuted_nodes and not conflict_anc:
            ev2 = []
            for e in ev:
                tn = nodes_idx.get(e["node_id"]) or {}
                e2 = dict(e)
                if _has_cond(tn) and not tn.get("pass_through") \
                        and (fx.get(e["node_id"]) or {}).get("st") == "held":
                    e2["judgment"] = "成立(证据引擎实测)"   # 只有实测成立的节点配用保留字
                ev2.append(e2)
            closure = ("事实闭环:路径上所有带判据节点均经证据引擎实测成立" if path_closed
                       else "候选自身经证据引擎实测成立(与手工 RCA 的确证条件同一规则,祖先状态见 caveats)")
            confirmed.append({
                "Root_cause": leaf_name,
                "Fault_tree_path": entry["Fault_tree_path"],
                "support": f"{closure}(确定性代码判定,非模型/辩论打分);"
                           f"辩论引擎仅提供排序(机制={mechanism},"
                           f"路径得分 {tail.get(_SCORE_KEY[mechanism])},详见 rca_debate_full.json)",
                "caveats": "; ".join(anc_notes) if anc_notes else "全路径实测闭环(无带伤节点)",
                "evidence_path": ev2,
                "_engine": entry["_engine"],
            })
            continue
        if abstain:
            undetermined.append({
                "node": leaf_name,
                "Fault_tree_path": entry["Fault_tree_path"],
                "reason": f"弃权:证据引擎在本场景没有任何'成立'事实,评分排序候选不足以立为疑似"
                          f"(排序机制={mechanism},路径得分 {tail.get(_SCORE_KEY[mechanism])};"
                          f"卡点:{entry['blocked_at']})",
                "support": entry["support"],
                "evidence_path": ev,
            })
        else:
            probable.append(entry)

    if abstain and undetermined:
        print(f"  [弃权|机制={mechanism}] 证据引擎无任何'成立'事实——{len(undetermined)} 条排序候选"
              f"降入 undetermined(评分制引擎的弃权垫片,防止无据硬报根因)")
    if confirmed:
        print(f"  [升档|机制={mechanism}] {len(confirmed)} 条路径的候选经证据引擎实测成立"
              f"(与手工路径的确证条件同一规则,祖先状态记入 caveats)→ 进入确证档 rca_result")

    tree_issues = {}
    if rca_input:
        tree_issues = dict(rca_input.get("tree_issues") or {})
    out = {"rca_result": confirmed,                # 仅事实闭环核验通过的路径(见模块 docstring);其余诚实留在 probable
           "probable_causes": probable,
           "undetermined": undetermined,
           "tree_issues": {"uncovered_condition_nodes": tree_issues.get("uncovered_condition_nodes", []),
                           "orphan_node_ids": tree_issues.get("orphan_node_ids", [])}}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="vendor 输出 → rca.json(四档)")
    ap.add_argument("vendor_result")
    ap.add_argument("--tree-index", required=True)
    ap.add_argument("--rca-input")
    ap.add_argument("--mechanism", default="original", choices=list(_MECH_KEY))
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()
    vr = json.loads(Path(a.vendor_result).read_text(encoding="utf-8"))
    ti = json.loads(Path(a.tree_index).read_text(encoding="utf-8"))
    ri = json.loads(Path(a.rca_input).read_text(encoding="utf-8")) if a.rca_input else None
    doc = to_rca_json(vr, a.mechanism, ti, ri)
    Path(a.output).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写 {a.output}(确证 {len(doc['rca_result'])} 条 / probable {len(doc['probable_causes'])} 条,机制={a.mechanism})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
