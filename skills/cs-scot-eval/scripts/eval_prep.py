#!/usr/bin/env python3
"""Mode A 评测准备 —— 把每个合成场景的【金标准绑定 + 数据】确定性地变成 RCA 输入。

跑金标准绑定(evaluate_binding,跳过模型绑定 → 隔离测纯 RCA)→ 各源逐条件事实 →
tree_prune.prune 剪成结构化局部子树(含 is_leaf/uncovered_children/事实)→ 写 <场景>/result/rca_input.json。
之后只剩"模型做 RCA 判断"这一步,正是被打分的对象。

用法: python eval_prep.py [--suite <目录,默认 ../suite>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS_ROOT = HERE.parent.parent
DEFAULT_SUITE = HERE.parent / "suite"


def _imports():
    sys.path.insert(0, str(SKILLS_ROOT / "cs-scot-generate" / "scripts"))
    sys.path.insert(0, str(SKILLS_ROOT / "cs-scot-execute" / "scripts"))
    from condition_kernels import evaluate_binding, load_table  # noqa: E402
    from tree_prune import prune                                 # noqa: E402
    return evaluate_binding, load_table, prune


def facts_from_gold(scn_dir: Path):
    """金标准绑定 + data/ → {源: 分析脚本同形输出};与真实 <ind>_analyzer.py 产物结构一致。"""
    evaluate_binding, load_table, _ = _imports()
    gold = json.loads((scn_dir / "gold.json").read_text(encoding="utf-8"))
    idx = json.loads(next((scn_dir / "tree").glob("*.tree_index.json")).read_text(encoding="utf-8"))
    nodes = idx.get("nodes", {})
    sources = {}
    for csvf in (scn_dir / "data").glob("*.csv"):
        try:
            sources[csvf.stem] = load_table(str(csvf))
        except Exception:
            sources[csvf.stem] = ([], [])
    results = {}
    for b in gold["bindings"]:
        src = b.get("source", "kpi")
        fact = evaluate_binding(b, sources, src)
        meta = {k: b[k] for k in ("id", "node_id", "condition_text", "class", "mapping_reason") if k in b}
        nid = b.get("node_id")
        if nid in nodes:
            meta["node"] = nodes[nid].get("name")
        results.setdefault(src, {"indicator": src, "source": src, "conditions": [], "summary": {}})
        results[src]["conditions"].append({**meta, "fact": fact})
    for src, r in results.items():
        conds = r["conditions"]
        r["summary"] = {"total": len(conds),
                        "ok": sum(1 for c in conds if c["fact"].get("status") == "ok"),
                        "insufficient_data": sum(1 for c in conds if c["fact"].get("status") == "insufficient_data"),
                        "manual": sum(1 for c in conds if c["fact"].get("status") == "manual")}
    return idx, results


def prep_one(scn_dir: Path) -> dict:
    """造该场景的结构化 RCA 输入(局部子树,不走 floor —— 评测要的是 RCA 判断的真实形态)。"""
    _, _, prune = _imports()
    idx, results = facts_from_gold(scn_dir)
    pr = prune(idx, results)
    payload = {"fault_tree": {"scenario": pr["scenario"], "root": pr["root"],
                              "outline": pr["outline"], "pruned_nodes": pr["pruned_nodes"]},
               "unmapped_facts": pr["unmapped"], "tree_issues": pr["coverage"]}
    res = scn_dir / "result"
    res.mkdir(exist_ok=True)
    (res / "rca_input.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"scenario": scn_dir.name, "kept": pr["coverage"]["kept_nodes"],
            "total": pr["coverage"]["total_nodes"], "input": str(res / "rca_input.json")}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Mode A:金标准绑定 → RCA 输入(逐场景)")
    ap.add_argument("--suite", default=str(DEFAULT_SUITE))
    args = ap.parse_args()
    suite = Path(args.suite)
    scns = sorted(d for d in suite.iterdir() if d.is_dir() and (d / "gold.json").exists())
    if not scns:
        print(f"未在 {suite} 找到场景(需含 gold.json)", file=sys.stderr)
        sys.exit(2)
    print(f"已为 {len(scns)} 个场景准备 RCA 输入(金标准绑定 → 事实 → 局部子树):")
    for d in scns:
        info = prep_one(d)
        print(f"  {info['scenario']:<32} 保留 {info['kept']}/{info['total']} → {Path(info['input']).relative_to(suite)}")
    print("\n下一步:对每个场景,用 fault-tree-rca 技能读其 result/rca_input.json 做判断,"
          "把结果写到同目录 result/rca.json;全部写完后跑 score_accuracy.py 出准确率表。")


if __name__ == "__main__":
    main()
