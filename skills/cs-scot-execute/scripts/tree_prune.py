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


def _facts_by_node(results: dict, alias: dict) -> dict:
    """{规范node_id: [该节点的所有事实条目]};node_id 用 alias 解析(认 nN key 或 code 两种);
    没填 node_id 的归 __no_node_id__,解析不到真实节点的留原值(后续判为孤儿)。"""
    m = defaultdict(list)
    for ind_name, res in (results or {}).items():
        for c in (res or {}).get("conditions", []):
            entry = {"indicator": ind_name, "id": c.get("id"), "class": c.get("class"),
                     "condition_text": c.get("condition_text"),
                     "mapping_reason": c.get("mapping_reason"), "fact": c.get("fact")}
            raw = c.get("node_id")
            nid = alias.get(raw, raw) if raw else "__no_node_id__"
            m[nid].append(entry)
    return m


_FACT_KEEP = ("class", "status", "predicate_satisfied", "resolved_column", "value",
              "matched", "total", "ratio", "top", "count_satisfied", "top1", "top1_ratio",
              "top1_count", "valid", "n", "n_rows", "filtered_n", "distinct",
              "matched_sessions", "lacks_never_seen", "nulls", "op", "threshold")


def _slim_fact(entry: dict) -> dict:
    """精简一条事实:去掉与节点重复的 condition_text、截断 mapping_reason,只留关键结果字段。"""
    f = entry.get("fact") or {}
    out = {"id": entry.get("id"), "indicator": entry.get("indicator")}
    if entry.get("mapping_reason"):
        out["mapping_reason"] = entry["mapping_reason"][:100]
    out.update({k: f[k] for k in _FACT_KEEP if k in f})
    return out


def _coarse(entry_list, pass_through) -> str:
    """给 outline 用的粗状态(仅导航,RCA 仍重判):pass/hold/fail/insufficient/uncovered。"""
    if pass_through:
        return "pass"
    if not entry_list:
        return "uncovered"
    pairs = [((e.get("fact") or {}).get("status"), (e.get("fact") or {}).get("predicate_satisfied"))
             for e in entry_list]
    if any(s == "ok" and p is True for s, p in pairs):
        return "hold"
    if any(s == "ok" and p is False for s, p in pairs):
        return "fail"
    if any(s in ("insufficient_data", "manual") for s, _ in pairs):
        return "insufficient"
    return "?"


def prune(index: dict, results: dict) -> dict:
    """index + 各源事实 → 剪枝后的局部子树 + 覆盖报告。瘦身:outline(导航)+ 精简事实。"""
    nodes = index.get("nodes", {})
    # 别名表:节点 key(nN)和 code 都指向同一节点 → 绑定填哪个 node_id 都能挂上
    alias = {}
    for nid, n in nodes.items():
        alias[nid] = nid
        if n.get("code"):
            alias[n["code"]] = nid
    facts = _facts_by_node(results, alias)

    keep = set()
    for nid in [k for k in facts if k in nodes]:        # 有事实的真实节点
        cur = nid
        while cur and cur in nodes and cur not in keep:  # 连同祖先一路留到根
            keep.add(cur)
            cur = nodes[cur].get("parent")
    if index.get("root"):
        keep.add(index["root"])

    pruned, outline = {}, {}
    for nid in keep:
        n = nodes[nid]
        ent = facts.get(nid, [])
        rec = {"name": n.get("name"), "parent": n.get("parent"), "level": n.get("level")}
        ch = n.get("children") or []
        if ch:                                                      # 非叶子:RCA 据此知道"这不是根因、下面还有更深的因"
            unc = [c for c in ch if c in nodes and not nodes[c].get("pass_through")
                   and c not in facts and nodes[c].get("condition")]
            if unc:                                                 # 带条件却没人绑的子节点 = 还没查到的叶子因
                rec["uncovered_children"] = unc
        else:
            rec["is_leaf"] = True
        for k in ("condition", "combine"):
            if n.get(k):                                            # 稀疏:有才写
                rec[k] = n[k]
        if n.get("pass_through"):
            rec["pass_through"] = True
        if ent:
            rec["facts"] = [_slim_fact(e) for e in ent]
        pruned[nid] = rec
        outline[nid] = _coarse(ent, n.get("pass_through"))          # outline 只存状态(name/parent 去 pruned_nodes 取)

    uncovered = [{"id": nid, "name": n.get("name"), "condition": (n.get("condition") or "")[:60]}
                 for nid, n in nodes.items() if not n.get("pass_through") and nid not in facts]
    orphans = sorted(k for k in facts if k != "__no_node_id__" and k not in nodes)
    unmapped = list(facts.get("__no_node_id__", []))
    for oid in orphans:
        unmapped.extend(facts[oid])

    return {"scenario": index.get("scenario"), "root": index.get("root"),
            "outline": outline, "pruned_nodes": pruned, "unmapped": unmapped,
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
