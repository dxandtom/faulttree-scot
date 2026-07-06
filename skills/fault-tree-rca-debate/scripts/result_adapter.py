#!/usr/bin/env python3
"""辩论引擎输出(top_k×3 + debate_records)→ 本仓 rca.json 四档契约。

诚实映射(v1,刻意保守):
- 辩论引擎产的是**评分排序的假设**,不是逐节点事实闭环的证明 → 全部进 `probable_causes`,
  `rca_result` 恒为空(确证档留给"事实闭环"语义;引擎赢得评测后再讨论升档规则)。
- `confidence` 一律按 **original 路径得分**分档(softmax/ratio 是连乘概率、数值天然极小,
  不适合做绝对阈值);--mechanism 只决定**取哪份 top-k 排序**。
- evidence_path 的 judgment 用中性词(条件匹配/部分匹配/辩论评分),**不写「成立/不成立/数据不足」**
  —— 这些是本仓事实闭环语义的保留字,评分制结果冒用会触发(或骗过)validate_rca 的诚实性机械检查。
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

    probable = []
    for path in vendor_result.get(_MECH_KEY[mechanism]) or []:
        nodes, tail = _split_path(path)
        if not nodes:
            continue
        leaf = nodes[-1]
        idx_n = nodes_idx.get(leaf.get("code")) or {}
        leaf_name = idx_n.get("name") or leaf.get("chinese_name") or leaf.get("name") or leaf.get("code")

        ev, debate_caveats = [], []
        for n in nodes:
            nid = n.get("code")
            ti_n = nodes_idx.get(nid) or {}
            ev.append({"node": ti_n.get("name") or n.get("chinese_name") or n.get("name") or nid,
                       "node_id": nid,
                       "condition": str(ti_n.get("condition") or "").strip() or "(无独立判据)",
                       "actual": f"引擎评分 {n.get('node_score')}(match_type={n.get('match_type', '?')})",
                       "judgment": _judgment(n)})
            if nid in dbg:
                r = dbg[nid]
                debate_caveats.append(
                    f"「{ev[-1]['node']}」经辩论评分:均分{r.get('mean_score')}、std {r.get('std_score')}"
                    f"(评委分歧越大越该存疑)")

        orig = tail.get("original_path_score")
        probable.append({
            "Root_cause": leaf_name,
            "Fault_tree_path": [e["node"] for e in ev],
            "confidence": _confidence(orig),
            "blocked_at": "评分式引擎输出:节点由文本匹配/辩论打分,未做逐节点事实闭环验证",
            "caveats": "; ".join(
                [f"排序机制={mechanism}(路径得分 {tail.get(_SCORE_KEY[mechanism])});"
                 f"original 路径得分 {orig}(confidence 依据)"] + debate_caveats)
                 + ";得分机制孰优待 cs-scot-eval 评测确认"
                 + (";⚠ 本次运行 LLM 曾连接失败、引擎降级为关键词匹配续跑,结果可信度受损,建议重跑"
                    if vendor_result.get("llm_degraded") else ""),
            "support": f"辩论引擎 Top-K 路径(k 内排名按 {mechanism});"
                       f"路径节点匹配/辩论详情见 evidence_path 与 result/rca_debate_full.json",
            "evidence_path": ev,
            "_engine": {"name": "fault-tree-rca-debate", "mechanism": mechanism},
        })

    tree_issues = {}
    if rca_input:
        tree_issues = dict(rca_input.get("tree_issues") or {})
    out = {"rca_result": [],                       # 评分制引擎不产"事实闭环确证"——诚实置空(见模块 docstring)
           "probable_causes": probable,
           "undetermined": [],
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
    print(f"已写 {a.output}(probable_causes {len(doc['probable_causes'])} 条,机制={a.mechanism})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
