#!/usr/bin/env python3
"""造多种大小的故障树拓扑,供准确率评测注入用——别只在一棵 178 节点树上测。

两类来源,都不带业务语义(condition=null,语义由 inject_on_tree 的人设池叠加):
  1) 真实树的子树:从已提交的真实树(trees/real_*.tree_index.json)切几个内部节点当根,
     重定级 → 得到 ~小/中 号的【真实拓扑】树(最可信);
  2) 合成树:按目标节点数确定性生成不同规模(含比 178 更大的),覆盖"规模曲线"上更宽的点。

输出到 trees/(确定性、可反复;真实源树不动)。用法: python make_trees.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREES = HERE.parent / "trees"
REAL = TREES / "real_SA接入失败.tree_index.json"

# 真实树切子树的根 + 目标名(节点数由结构决定)
SUBTREES = [("n47", "real_sub_接入_小"), ("n3", "real_sub_上下文_中")]
# 合成树:目标节点数(含比真实 178 更大的,验证规模上探)
SYNTH = [("synth_中", 95, 11), ("synth_特大", 320, 23)]


def subtree(tree: dict, root_id: str, name: str) -> dict:
    nodes = tree["nodes"]
    keep, stack = set(), [root_id]
    while stack:
        x = stack.pop()
        keep.add(x)
        stack.extend(nodes[x]["children"])
    shift = 2 - nodes[root_id]["level"]
    new = {}
    for k in keep:
        n = dict(nodes[k])
        n["children"] = [c for c in n["children"] if c in keep]
        n["level"] = n["level"] + shift
        if k == root_id:
            n["parent"], n["pass_through"], n["combine"] = None, True, None
        new[k] = n
    return {"scenario": name, "root": root_id, "nodes": new}


def synthetic(name: str, target_n: int, seed: int) -> dict:
    rng = random.Random(seed)
    cnt = [0]

    def nid():
        cnt[0] += 1
        return f"s{cnt[0]}"

    root = nid()
    nodes = {root: {"name": "L1节点_001", "parent": None, "level": 2, "code": f"CODE_{root}",
                    "children": [], "condition": None, "combine": None, "pass_through": True}}
    frontier = [root]
    fan_by_level = {2: [4, 5, 6], 3: [3, 4, 5], 4: [3, 4, 5], 5: [2, 3, 4], 6: [0, 2, 3]}
    while frontier and len(nodes) < target_n:
        p = frontier.pop(0)
        pl = nodes[p]["level"]
        if pl >= 7:
            continue
        for _ in range(rng.choice(fan_by_level.get(pl, [0, 2]))):
            if len(nodes) >= target_n:
                break
            c = nid()
            lvl = pl + 1
            nodes[c] = {"name": f"L{lvl - 1}节点_{cnt[0]:03d}", "parent": p, "level": lvl,
                        "code": f"CODE_{c}", "children": [], "condition": None,
                        "combine": rng.choice(["OR", "AND", "unknown", "unknown"]),
                        "pass_through": rng.random() < 0.07}
            nodes[p]["children"].append(c)
            frontier.append(c)
    return {"scenario": name, "root": root, "nodes": nodes}


def main():
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    real = json.loads(REAL.read_text(encoding="utf-8"))
    made = [("real_SA接入失败", len(real["nodes"]))]
    for root_id, name in SUBTREES:
        t = subtree(real, root_id, name)
        (TREES / f"{name}.tree_index.json").write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
        made.append((name, len(t["nodes"])))
    for name, n, seed in SYNTH:
        t = synthetic(name, n, seed)
        (TREES / f"{name}.tree_index.json").write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
        made.append((name, len(t["nodes"])))
    print("trees/ 现有树(供注入):")
    for name, n in sorted(made, key=lambda x: x[1]):
        print(f"  {name:<28} {n:>4} 节点")


if __name__ == "__main__":
    main()
