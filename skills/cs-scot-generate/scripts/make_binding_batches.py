#!/usr/bin/env python3
"""把"待绑定的节点"切成大小适中的批,供 step 2.5 并行分发给子 agent 填 CONDITIONS。

为什么:把整棵 400 节点丢给一个 agent,它注意力被摊薄 → 抓显眼、漏长尾(欠绑定的病根)。
切批后每个子 agent 只 focus 一小撮节点,漏的少;批间并行,墙钟时间从"一个 agent 啃 400 个"
变成"最慢那一批";中央汇总后再跑 覆盖度 / 反代理 校验兜底。

待绑节点 = **带非空判断条件、且非 pass_through 的节点**(叶子或内部都算 —— 有的内部节点自己
也带条件,只挑叶子会漏)。切批策略 = **按分支递归切、兄弟成组、超限再下钻**(批大小随分支而变,
不是固定 N):一棵子树整支装得下(≤N)就当一批,保住兄弟/兜底上下文、少跨批"一值多挂";
装不下就把它的子分支(兄弟)贪心攒到 ≤N 一组,某个子分支自己还超 N 就递归下钻它。
**每批永远落在"兄弟分支"内,不跨不相关的支乱掺**;总数 ≤ N 则只出 1 批(不必并行)。

输出:<out>/batch_001.json … 每个自包含(本批节点 + 覆盖的分支 covers + 树条件 + 根→叶路径
+ data_source 提示)+ manifest.json。
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

    def _children(nid):
        return [c for c in (nodes[nid].get("children") or []) if c in nodes]

    _sc = {}
    def subtree_need(nid):
        """nid 子树里所有待绑节点(含自身,DFS 树序)——稳定可复现。"""
        if nid in _sc:
            return _sc[nid]
        r = [nid] if _needs_binding(nodes[nid]) else []
        for c in _children(nid):
            r += subtree_need(c)
        _sc[nid] = r
        return r

    N = max(1, a.batch_size)

    def batch_node(nid):
        """切 nid 子树:整支装得下(≤N)→ 一批;装不下 → 本节点 + 把子分支(兄弟)成组打包到 ≤N,
        某子分支自己超 N 再递归下钻。**每批永远是'兄弟(子)分支',不跨不相关的支乱掺。**"""
        ids = subtree_need(nid)
        if not ids:
            return []
        if len(ids) <= N:
            return [ids]                              # 整支放一批(保住兄弟/兜底上下文)
        out, cur = [], ([nid] if _needs_binding(nodes[nid]) else [])
        for c in _children(nid):
            cids = subtree_need(c)
            if not cids:
                continue
            if len(cids) > N:                         # 这个子分支自己就超 N → 先封存当前组,再下钻它
                if cur:
                    out.append(cur); cur = []
                out += batch_node(c)
            elif len(cur) + len(cids) > N:            # 加上它会超 → 当前组封存,它另起一组
                out.append(cur); cur = cids[:]
            else:                                     # 兄弟成组,继续攒
                cur += cids
        if cur:
            out.append(cur)
        return out

    root = idx.get("root")
    batches = batch_node(root) if root in nodes else []
    branch_of = lambda nid: (path_ids(nid)[1] if len(path_ids(nid)) > 1 else nid)  # 顶层分支(供 covers 标注)

    out = Path(a.out) if a.out else Path(a.tree_index).resolve().parent / "batches"
    out.mkdir(parents=True, exist_ok=True)

    def spec(nid):
        n = nodes[nid]
        return {"node_id": nid, "name": n.get("name"), "level": n.get("level"),
                "path": [nodes[p].get("name") for p in path_ids(nid)],
                "condition": n.get("condition"), "data_source": n.get("data_source") or {}}

    def covers(b):
        """本批触及的顶层分支名(去重、保序)—— 让人一眼看出这批是哪几支。"""
        seen, names = set(), []
        for nid in b:
            br = branch_of(nid)
            if br not in seen:
                seen.add(br)
                names.append(nodes[br].get("name") if br in nodes else br)
        return names

    need = subtree_need(root) if root in nodes else []
    man = {"scenario": idx.get("scenario"), "batch_size": N, "total_to_bind": len(need),
           "num_batches": len(batches), "batches": []}
    for i, b in enumerate(batches, 1):
        bid = f"batch_{i:03d}"
        cov = covers(b)
        (out / f"{bid}.json").write_text(json.dumps(
            {"batch_id": bid, "count": len(b), "covers": cov, "nodes": [spec(x) for x in b]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        man["batches"].append({"batch_id": bid, "count": len(b), "covers": cov})
    (out / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"待绑节点 {len(need)} 个 → 切成 {len(batches)} 批(每批 ≤ {N});写到 {out}/")
    for m in man["batches"]:
        print(f"  {m['batch_id']}: {m['count']} 个节点  ←  {' / '.join(m['covers']) or '—'}")
    if len(batches) <= 1:
        print("(节点不多,1 批即可,不必并行 fan-out)")


if __name__ == "__main__":
    main()
