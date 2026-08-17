#!/usr/bin/env python3
"""RCA 导航助手:**不读整个 rca_input**,按需取节点/分支(只导航,judging 仍由你/模型做)。

为什么要它:大场景的 rca_input 可能很长,一次性读进上下文会超长报错。用这个**按分支抽取**,
只把相关节点的"条件 + 事实"摆出来,你据此判 成立/不成立/数据不足、拼路径。

用法:
  rca_query.py <rca_input.json> --outline        # 全树 id→粗状态→名字(很小,先看它把握全局)
  rca_query.py <rca_input.json> --holding        # 有"成立"事实的候选节点 + 各自"根→叶"路径
  rca_query.py <rca_input.json> --node nX         # nX 及其"根→叶"整条路径上每个节点的 条件+事实
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_FACT_KEYS = ("status", "gap_kind", "predicate_satisfied", "matched", "total", "ratio", "top",
              "value", "count_satisfied", "valid", "n_rows", "filter_rows",
              "absent", "matched_groups", "local", "peer", "equal", "params",
              "resolved_column", "reason")


def _fault_tree(data: dict):
    ft = data.get("fault_tree") or {}
    return ft.get("pruned_nodes") or {}, ft.get("outline") or {}, ft.get("outline_legend") or ""


def _ff(f: dict) -> dict:
    """取事实体:兼容瘦身后的扁平结构(字段在顶层)和未瘦身的嵌套 {"fact":{...}}。"""
    inner = f.get("fact")
    return inner if isinstance(inner, dict) else f


def _path_to_root(nodes: dict, nid: str):
    path = [nid]
    while nodes.get(path[-1], {}).get("parent"):
        p = nodes[path[-1]]["parent"]
        if p in path or p not in nodes:
            break
        path.append(p)
    return path  # 叶→根;展示时 reversed 成 根→叶


def _holding(nodes: dict):
    out = []
    for nid, n in nodes.items():
        if any(_ff(f).get("status") == "ok" and _ff(f).get("predicate_satisfied") is True
               for f in n.get("facts", [])):
            out.append(nid)
    return out


def _fact_brief(f: dict) -> str:
    ff = _ff(f)
    kv = " ".join(f"{k}={ff[k]}" for k in _FACT_KEYS if k in ff)
    mr = f.get("mapping_reason")
    return f"[{f.get('indicator')}/{f.get('id')}] {kv}" + (f"  ◂{mr}" if mr else "")


def _leafmark(n: dict) -> str:
    """叶子 / 中间节点 标注——确证=最深成立节点:叶子最好;中间节点成立且下级无一成立也确证(叶子因待定落 caveats)。"""
    if n.get("is_leaf"):
        return "叶子"
    unc = n.get("uncovered_children") or []
    return "中间节点·非叶子" + (f";下有未覆盖子因 {unc}" if unc else "")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="RCA 导航助手(按分支抽取,不读整文件)")
    ap.add_argument("input")
    ap.add_argument("--outline", action="store_true")
    ap.add_argument("--holding", action="store_true")
    ap.add_argument("--node")
    args = ap.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    nodes, outline, legend = _fault_tree(data)

    if args.outline:
        if legend:
            print(f"# {legend}")
        for nid, status in outline.items():
            n = nodes.get(nid, {})
            print(f"{nid}\t{str(status):<12}\t{n.get('name')}\t(parent={n.get('parent')})")
    elif args.holding:
        hold = _holding(nodes)
        print(f"# {len(hold)} 个有'成立'事实的候选节点(逐个用 --node 看整条路径再判):")
        print("# 确证根因=【最深成立节点】:叶子最好;中间节点成立且下级无一成立 → 也确证")
        print("#   (叶子因待定由 finalize 自动落 caveats,子节点三态见骨架 children_status)。")
        for nid in hold:
            path = _path_to_root(nodes, nid)
            names = " → ".join(nodes.get(p, {}).get("name", "?") for p in reversed(path))
            print(f"{nid} | {nodes[nid].get('name')}  «{_leafmark(nodes[nid])}»"
                  f"\n  根→叶: {names}\n  ids: {list(reversed(path))}")
    elif args.node:
        nid = args.node
        if nid not in nodes:
            print(f"无此节点:{nid}(看 --outline)")
            return
        print(f"=== 节点 {nid} 的'根→叶'路径:每个节点的条件+事实(judging 你来)===")
        for p in reversed(_path_to_root(nodes, nid)):
            n = nodes[p]
            print(f"\n[{p}] {n.get('name')}  (L{n.get('level')}, {_leafmark(n)}, "
                  f"combine={n.get('combine')}, pass_through={n.get('pass_through')})")
            if n.get("condition"):
                print(f"  条件: {n['condition'][:220]}")
            for f in n.get("facts", []):
                print(f"  事实: {_fact_brief(f)}")
    else:
        print("给个动作:--outline(全局) / --holding(候选) / --node nX(某分支)")


if __name__ == "__main__":
    main()
