#!/usr/bin/env python3
"""辩论引擎输出(top_k×3 + debate_records)→ 本仓 rca.json 四档契约。

诚实映射(v2:评分排序为体,事实闭环升档):
- 辩论引擎产的是**评分排序的假设**,不是证明 → 默认进 `probable_causes`。
- **事实闭环升档**:一条路径若被**证据引擎的确定性事实**完整确认——路径上每个带判据节点
  都实测「成立」、叶子是真叶子且实测成立、无一节点被证伪——则进 `rca_result`(确证档)。
  升档依据是 execute 算出的事实,**不是辩论得分**(得分只管排序):与手工 fault-tree-rca 的
  确证语义同源,validate_rca 的"成立必须有事实/路径无不成立节点"机械检查天然通过。
  任一带判据节点数据不足/被证伪/未覆盖,或叶子非真叶(如 L6 截断)→ 不升,留在 probable。
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
_ST_CN = {"held": "成立", "refuted": "不成立", "insufficient": "数据不足"}


def _facts_index(rca_input: dict | None) -> dict:
    """rca_input → {node_id: {"st": held|refuted|insufficient, "obs": 实测摘要}}。

    与 facts_to_question 同一塌缩优先级(held>refuted>insufficient)。这是回程事实感知的
    地基:证据引擎的确定性结论此前只用于渲染 question,产出侧全弃——confidence 校准、
    证伪旗标、blocked_at 回填、弃权垫片都从这张表读。"""
    from facts_to_question import _classify
    idx: dict = {}
    rank = {"held": 2, "refuted": 1, "insufficient": 0}

    def _put(nid, held, failed, insuf, obs):
        st = "held" if held else ("refuted" if failed else ("insufficient" if insuf else None))
        if not nid or st is None:
            return
        prev = idx.get(nid)
        if prev is None or rank[st] > rank[prev["st"]]:
            idx[nid] = {"st": st, "obs": "; ".join(obs) if obs else ""}

    ft = (rca_input or {}).get("fault_tree")
    if isinstance(ft, dict) and ft.get("pruned_nodes"):
        for nid, node in ft["pruned_nodes"].items():
            facts = (node or {}).get("facts")
            if facts:
                held, failed, insuf, obs = _classify(facts)
                _put(nid, held, failed, insuf, obs)
    else:
        for res in ((rca_input or {}).get("indicator_results") or {}).values():
            for c in (res or {}).get("conditions", []) if isinstance(res, dict) else []:
                held, failed, insuf, obs = _classify([c])
                _put(c.get("node_id"), held, failed, insuf, obs)
    return idx


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


def _judgment(n: dict) -> str:
    mt, sc = n.get("match_type"), n.get("node_score")
    sc_s = f"{float(sc):.2f}" if isinstance(sc, (int, float)) else "?"
    if mt == "full":
        return f"条件匹配(full,得分{sc_s})"
    if mt == "partial":
        return f"部分匹配(得分{sc_s})"
    if mt == "debate":
        return f"辩论评分(得分{sc_s})"
    return f"结构节点(得分{sc_s})"


def to_rca_json(vendor_result: dict, mechanism: str, tree_index: dict,
                rca_input: dict | None = None) -> dict:
    if mechanism not in _MECH_KEY:
        raise ValueError(f"mechanism 应为 {list(_MECH_KEY)},收到 {mechanism!r}")
    nodes_idx = (tree_index or {}).get("nodes") or {}
    dbg = {r.get("node_code"): r for r in vendor_result.get("debate_records") or [] if isinstance(r, dict)}
    fx = _facts_index(rca_input)          # 证据引擎的确定性结论(回程事实感知的地基)
    # 弃权垫片:全场景没有任何"成立"事实(excluded/honest 情形,正确答案="别报根因")时,
    # 排序候选降入 undetermined 而非硬吐 probable——补上评分制引擎的弃权能力缺口。
    abstain = bool(fx) and not any(v["st"] == "held" for v in fx.values())

    def _has_cond(ti_n: dict) -> bool:
        """与 validate_rca._holding_map 同口径:非空判据、且不是"无条件/无判断"占位。"""
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
        held_cond_nodes, closure_break = 0, False   # 事实闭环核验:带判据节点是否全部实测成立
        for n in nodes:
            nid = n.get("code")
            ti_n = nodes_idx.get(nid) or {}
            f = fx.get(nid)
            if _has_cond(ti_n) and not ti_n.get("pass_through"):
                if f and f["st"] == "held":
                    held_cond_nodes += 1
                else:                          # 数据不足/被证伪/无事实,任一即破闭环
                    closure_break = True
            if f:                              # actual 回填实测事实(老契约语义);判据词不进 judgment,防伪造检查误触
                actual = f"证据引擎实测[{_ST_CN[f['st']]}]" + (f":{f['obs']}" if f["obs"] else "")
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
                       "judgment": _judgment(n)})
            if nid in dbg:
                r = dbg[nid]
                debate_caveats.append(
                    f"「{ev[-1]['node']}」经辩论评分:均分{r.get('mean_score')}、std {r.get('std_score')}"
                    f"(评委分歧越大越该存疑)")

        orig = tail.get("original_path_score")
        leaf_st = (fx.get(leaf.get("code")) or {}).get("st")
        # confidence 校准(修对抗审查实锤的失真):引擎给 debate 节点的分上限 0.5,连乘让
        # "叶子已实证成立、只是祖先数据不足"的路径掉到 low——叶子实证成立且路径无证伪时
        # 托底 medium;叶子被数据证伪则封顶 low(评分再高也不该高于被证伪的置信)。
        conf = _confidence(orig)
        if leaf_st == "refuted":
            conf = "low"
        elif leaf_st == "held" and not refuted_nodes and conf == "low":
            conf = "medium"

        conflict = ([f"⚠ 与证据引擎冲突:「{'、'.join(refuted_nodes)}」的判据实测不成立(已被数据排除),"
                     f"本条仅为评分排序结果"] if refuted_nodes else [])
        entry = {
            "Root_cause": leaf_name,
            "Fault_tree_path": [e["node"] for e in ev],
            "confidence": conf,
            "blocked_at": (f"受阻于「{blocked_node}」(数据不足,判据未验证);评分式引擎输出,未做逐节点事实闭环"
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
        # 事实闭环升档:整条路径被证据引擎实测确认 → 确证档(依据是事实,不是辩论得分)
        ti_leaf = nodes_idx.get(leaf.get("code")) or {}
        if (not abstain and not closure_break and held_cond_nodes > 0
                and leaf_st == "held" and not (ti_leaf.get("children") or [])
                and _has_cond(ti_leaf) and not ti_leaf.get("pass_through")):
            ev2 = []
            for e in ev:
                tn = nodes_idx.get(e["node_id"]) or {}
                e2 = dict(e)
                if _has_cond(tn) and not tn.get("pass_through"):
                    e2["judgment"] = "成立(证据引擎实测)"   # 有事实背书,合法使用保留字
                ev2.append(e2)
            confirmed.append({
                "Root_cause": leaf_name,
                "Fault_tree_path": entry["Fault_tree_path"],
                "support": "事实闭环:路径上所有带判据节点均经证据引擎实测成立(确定性代码判定,"
                           f"非模型/辩论打分);辩论引擎仅提供排序(机制={mechanism},"
                           f"路径得分 {tail.get(_SCORE_KEY[mechanism])},详见 rca_debate_full.json)",
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
        print(f"  [弃权] 证据引擎无任何'成立'事实——{len(undetermined)} 条排序候选降入 undetermined"
              f"(评分制引擎的弃权垫片,防止无据硬报根因)")
    if confirmed:
        print(f"  [升档] {len(confirmed)} 条路径经证据引擎事实闭环核验(带判据节点全部实测成立)"
              f"→ 进入确证档 rca_result")

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
