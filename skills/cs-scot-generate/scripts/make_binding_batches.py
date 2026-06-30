#!/usr/bin/env python3
"""把"待绑定的节点"切成大小适中的批,供 step 2.5 并行分发给子 agent 填 CONDITIONS。

为什么:把整棵 400 节点丢给一个 agent,它注意力被摊薄 → 抓显眼、漏长尾(欠绑定的病根)。
切批后每个子 agent 只 focus 一小撮节点,漏的少;批间并行,墙钟时间从"一个 agent 啃 400 个"
变成"最慢那一批";中央汇总后再跑 覆盖度 / 反代理 校验兜底。

待绑节点 = **带非空判断条件、且非 pass_through 的节点**(叶子或内部都算 —— 有的内部节点自己
也带条件,只挑叶子会漏)。切批策略:按"分支"(根的直接子)聚合,同一子树的节点尽量同批
(保住兄弟上下文、减少跨批"一值多挂"),再贪心打包到每批 ≤ --batch-size;单分支超限就切块;
总数 ≤ batch-size 则只出 1 批(不必并行)。

输出:<out>/batch_001.json … 每个自包含(本批节点 + 树条件 + 根→叶路径 + data_source 提示)+ manifest.json。
用法:make_binding_batches.py <tree_index.json> [--batch-size 20] [--out <dir>]
"""
import argparse
import json
from collections import OrderedDict
from pathlib import Path


def _needs_binding(n):
    # 带非空判断条件且非 pass_through 即需绑 —— 不限叶子(内部节点也可能自带条件)
    return bool(n.get("condition") and str(n["condition"]).strip()) and not n.get("pass_through")


def main():
    ap = argparse.ArgumentParser(description="把待绑节点切批,供并行子 agent 填 CONDITIONS")
    ap.add_argument("tree_index")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--out")
    a = ap.parse_args()
    idx = json.loads(Path(a.tree_index).read_text(encoding="utf-8"))
    nodes = idx.get("nodes") or {}

    _pc = {}
    def path_ids(nid):
        if nid in _pc:
            return _pc[nid]
        out, cur, seen = [nid], nodes[nid].get("parent"), set()
        while cur and cur in nodes and cur not in seen:
            seen.add(cur); out.append(cur); cur = nodes[cur].get("parent")
        _pc[nid] = list(reversed(out))
        return _pc[nid]

    # 待绑节点,按 tree_index 出现顺序(稳定、可复现)
    need = [nid for nid, n in nodes.items() if _needs_binding(n)]
    def branch_of(nid):
        p = path_ids(nid)
        return p[1] if len(p) > 1 else p[0]          # 根的直接子 = 顶层分支

    branches = OrderedDict()
    for nid in need:
        branches.setdefault(branch_of(nid), []).append(nid)

    N = max(1, a.batch_size)
    batches, cur = [], []
    def flush():
        nonlocal cur
        if cur:
            batches.append(cur); cur = []
    for _, ls in branches.items():
        if len(ls) > N:                              # 大分支:单独成批,超大再切块
            flush()
            for i in range(0, len(ls), N):
                batches.append(ls[i:i + N])
        elif len(cur) + len(ls) > N:
            flush(); cur = ls[:]
        else:
            cur += ls
    flush()

    out = Path(a.out) if a.out else Path(a.tree_index).resolve().parent / "batches"
    out.mkdir(parents=True, exist_ok=True)

    def spec(nid):
        n = nodes[nid]
        return {"node_id": nid, "name": n.get("name"), "level": n.get("level"),
                "path": [nodes[p].get("name") for p in path_ids(nid)],
                "condition": n.get("condition"), "data_source": n.get("data_source") or {}}

    man = {"scenario": idx.get("scenario"), "batch_size": N, "total_to_bind": len(need),
           "num_batches": len(batches), "batches": []}
    for i, b in enumerate(batches, 1):
        bid = f"batch_{i:03d}"
        (out / f"{bid}.json").write_text(json.dumps(
            {"batch_id": bid, "count": len(b), "leaves": [spec(x) for x in b]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        man["batches"].append({"batch_id": bid, "count": len(b)})
    (out / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"待绑节点 {len(need)} 个 → 切成 {len(batches)} 批(每批 ≤ {N});写到 {out}/")
    for m in man["batches"]:
        print(f"  {m['batch_id']}: {m['count']} 个节点")
    if len(batches) <= 1:
        print("(节点不多,1 批即可,不必并行 fan-out)")


if __name__ == "__main__":
    main()
