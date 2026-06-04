#!/usr/bin/env python3
"""把 tree_index + 各源逐条件事实 → 压缩成"相关局部子树"(给 RCA),并产覆盖报告。

只做两件确定性的事,**不下任何判断**(成立/不成立/疑似仍由 fault-tree-rca 判):
1. **剪枝**:保留"有事实(被绑定)的节点 + 它们到根的祖先",其余分支砍掉;每个保留节点把它的事实挂上。
   —— RCA 因此既有**结构(路径/父子/AND-OR)**又有**事实**,而不必读整棵 69KB 子树。
2. **覆盖报告(= tree_issues 的一部分)**:
   - `uncovered_condition_nodes`:有判断条件、却没有任何绑定的节点(模型漏绑 / 无可用数据源);
   - `orphan_node_ids`:事实里引用了 index 中**不存在**的 node_id(typo 或模型造假信号)。

用法(也可被 runner import):
    tree_prune.py --index <tree_index.json> --results kpi=<kpi.json> --results chr=<chr.json> -o pruned.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def _facts_by_node(results: dict) -> dict:
    """{node_id: [该节点的所有事实条目]};没有 node_id 的归到 __no_node_id__。"""
    m = defaultdict(list)
    for ind_name, res in (results or {}).items():
        for c in (res or {}).get("conditions", []):
            entry = {"indicator": ind_name, "id": c.get("id"), "class": c.get("class"),
                     "condition_text": c.get("condition_text"),
                     "mapping_reason": c.get("mapping_reason"), "fact": c.get("fact")}
            m[c.get("node_id") or "__no_node_id__"].append(entry)
    return m


def prune(index: dict, results: dict) -> dict:
    """index + 各源事实 → 剪枝后的局部子树 + 覆盖报告。"""
    nodes = index.get("nodes", {})
    facts = _facts_by_node(results)

    keep = set()
    for nid in [k for k in facts if k in nodes]:        # 有事实的真实节点
        cur = nid
        while cur and cur in nodes and cur not in keep:  # 连同祖先一路留到根
            keep.add(cur)
            cur = nodes[cur].get("parent")
    if index.get("root"):
        keep.add(index["root"])

    pruned = {}
    for nid in keep:
        n = nodes[nid]
        rec = {k: n.get(k) for k in
               ("name", "parent", "level", "condition", "combine", "data_source", "pass_through")}
        if facts.get(nid):
            rec["facts"] = facts[nid]
        pruned[nid] = rec

    uncovered = [{"id": nid, "name": n.get("name"), "condition": (n.get("condition") or "")[:100]}
                 for nid, n in nodes.items() if not n.get("pass_through") and nid not in facts]
    orphans = sorted(k for k in facts if k != "__no_node_id__" and k not in nodes)
    # 没挂上任何真实节点的事实(没填 node_id 的 + 引用了不存在节点的)——别静默丢,带给 RCA
    unmapped = list(facts.get("__no_node_id__", []))
    for oid in orphans:
        unmapped.extend(facts[oid])

    return {"scenario": index.get("scenario"), "root": index.get("root"),
            "pruned_nodes": pruned, "unmapped": unmapped,
            "coverage": {"total_nodes": len(nodes), "kept_nodes": len(pruned),
                         "uncovered_condition_nodes": uncovered,
                         "orphan_node_ids": orphans,
                         "facts_without_node_id": len(facts.get("__no_node_id__", []))}}


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="tree_index + 事实 → 局部子树 + 覆盖报告")
    ap.add_argument("--index", required=True)
    ap.add_argument("--results", action="append", default=[], help="name=path(可多次)")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    results = {}
    for item in args.results:
        if "=" not in item:
            print(f"--results 应为 name=path: {item}", file=sys.stderr)
            sys.exit(2)
        k, v = item.split("=", 1)
        results[k.strip()] = _load(v.strip())
    out = prune(_load(args.index), results)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    cov = out["coverage"]
    print(f"[tree_prune] 保留 {cov['kept_nodes']}/{cov['total_nodes']} 节点;"
          f"未覆盖条件节点 {len(cov['uncovered_condition_nodes'])};孤儿 node_id {len(cov['orphan_node_ids'])} → {args.output}")


if __name__ == "__main__":
    main()
