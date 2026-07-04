#!/usr/bin/env python3
"""tree_index.json → 辩论引擎期望的故障树 Excel(vendor 格式)。

为什么从 tree_index 生成、而不是直接喂原始 Excel:
- tree_index 是本仓的**归一化拓扑**——老格式(excel_to_tree)/新格式(v2 多页签)都会产它,
  从它出发,辩论引擎对上游 Excel 格式零感知;
- **`Ln Code` 列填我们的 node_id(nN)**:引擎输出的路径因此天然携带全链主键,
  结果适配回 rca.json 时能挂回树、能被 validate_rca / cs-scot-eval 消费。

生成的列(按 vendor 文档的必填集):`L1 Code / L1 / L1(Chinese)` … + `Judgment Condition(Chinese)`。
层级**重定基**:树根记 L1(我们的 tree_index 根常在 L2 起,引擎按 L1 起读)。

条件的挂法:每个叶子一行(条件=叶子的);**带条件的中间节点另出一行**(路径止于该节点,
条件挂它)——若 vendor loader 对"短行"处理有异(把中间节点当叶子),用 --leaf-only 关掉,
并见 INTEGRATION.md 核对项 ②。

用法:tree_to_vendor_xlsx.py <tree_index.json> -o <out.xlsx> [--leaf-only]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _depth_map(nodes: dict, root: str) -> dict:
    """{nid: 重定基深度(根=1)};按 parent 链算,断链的节点跳过。"""
    depth = {root: 1}
    changed = True
    while changed:                       # 层层扩散,避免递归深度问题
        changed = False
        for nid, n in nodes.items():
            p = n.get("parent")
            if nid not in depth and p in depth:
                depth[nid] = depth[p] + 1
                changed = True
    return depth


def _path(nodes: dict, nid: str) -> list:
    out, cur, seen = [], nid, set()
    while cur and cur in nodes and cur not in seen:
        seen.add(cur); out.append(cur); cur = nodes[cur].get("parent")
    return list(reversed(out))


def enrich_conditions(index: dict, rca_input: dict | None = None,
                      binding_question: dict | None = None) -> dict:
    """把判据文本合回树(只填空缺,不覆盖已有)。

    为什么需要:评测树(cs-scot-eval 的 make_trees 产物)节点 condition **全为 null**——判据文本
    住在 rca_input(pruned_nodes[].condition / indicator_results[].condition_text)和
    binding_question(to_bind[].condition_text)里。不合回去,xlsx 全空判据,引擎对每个节点
    白给满分、排名无意义。生产树(excel_to_tree 产)自带判据,本函数是无害空操作。"""
    nodes = index.get("nodes") or {}
    fill = {}
    for b in (binding_question or {}).get("to_bind") or []:
        nid, ct = b.get("node_id"), str(b.get("condition_text") or "").strip()
        if nid and ct:
            fill.setdefault(nid, ct)
    if rca_input:
        pn = ((rca_input.get("fault_tree") or {}).get("pruned_nodes")) or {}
        for nid, n in pn.items():
            ct = str((n or {}).get("condition") or "").strip()
            if ct:
                fill.setdefault(nid, ct)
        for res in (rca_input.get("indicator_results") or {}).values():
            for c in (res.get("conditions") or []) if isinstance(res, dict) else []:
                nid, ct = c.get("node_id"), str(c.get("condition_text") or "").strip()
                if nid and ct:
                    fill.setdefault(nid, ct)
    if not fill:
        return index
    out_nodes, changed = {}, 0
    for nid, n in nodes.items():
        if nid in fill and not str(n.get("condition") or "").strip():
            n = {**n, "condition": fill[nid]}
            changed += 1
        out_nodes[nid] = n
    if changed:
        print(f"  [tree补判据] 从 rca_input/binding_question 合回 {changed} 个节点的判据文本(该树 condition 原为空)")
    return {**index, "nodes": out_nodes}


def build_rows(index: dict, leaf_only: bool = False):
    """→ (max_depth, rows, dropped);row = [(nid, name), ...](根→端点), 端点条件文本。

    **只收带判据的端点**:无判据/pass_through 的叶子一律不进 xlsx——辩论引擎对"空判据节点"
    默认给满分 1.0(full match),这些叶子会以 1.0×1.0 的路径分屠榜 Top-K,把带判据的真路径
    全部挤出去、且永远轮不到触发辩论(实测:真引擎与 mock 因此跑出一模一样的假排名)。"""
    nodes = index.get("nodes") or {}
    root = index.get("root")
    if not nodes or root not in nodes:
        raise ValueError("tree_index 缺 nodes/root")
    depth = _depth_map(nodes, root)

    def _has_cond(n):
        return bool(str(n.get("condition") or "").strip()) and not n.get("pass_through")

    leaves = [nid for nid, n in nodes.items() if not (n.get("children") or [])]
    ends = [nid for nid in leaves if _has_cond(nodes[nid])]            # 带判据的真叶子
    dropped = len(leaves) - len(ends)
    if not leaf_only:                                                   # + 带条件的中间节点
        ends += [nid for nid, n in nodes.items()
                 if (n.get("children") or []) and _has_cond(n)]
    if not ends:
        raise ValueError("该 tree_index 没有任何带判据的节点,无法给辩论引擎出题")
    rows, maxd = [], 0
    for nid in sorted(set(ends), key=lambda x: (depth.get(x, 99), x)):
        if nid not in depth:
            continue
        path = _path(nodes, nid)
        maxd = max(maxd, len(path))
        cond = str(nodes[nid].get("condition") or "").strip()
        rows.append(([(p, nodes[p].get("name") or p) for p in path], cond))
    return maxd, rows, dropped


def write_xlsx(index: dict, out_path: Path, leaf_only: bool = False) -> int:
    from openpyxl import Workbook
    maxd, rows, dropped = build_rows(index, leaf_only)
    if dropped:
        print(f"  [tree→xlsx] 已排除 {dropped} 个无判据/pass_through 叶子(空判据会被引擎白给满分、屠榜 Top-K)")
    wb = Workbook()
    ws = wb.active
    ws.title = "fault_tree"
    header = []
    for i in range(1, maxd + 1):
        header += [f"L{i} Code", f"L{i}", f"L{i}(Chinese)"]
    header.append("Judgment Condition(Chinese)")
    ws.append(header)
    for path, cond in rows:
        row = []
        for i in range(maxd):
            if i < len(path):
                nid, name = path[i]
                row += [nid, name, name]          # Code=node_id(主键载体);中英名都填中文名(无英文源)
            else:
                row += ["", "", ""]
        row.append(cond)
        ws.append(row)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="tree_index → vendor 故障树 xlsx")
    ap.add_argument("tree_index")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--leaf-only", action="store_true", help="只出叶子行(中间节点条件不单独成行)")
    a = ap.parse_args()
    index = json.loads(Path(a.tree_index).read_text(encoding="utf-8"))
    n = write_xlsx(index, Path(a.output), a.leaf_only)
    print(f"已生成 vendor 格式故障树:{a.output}({n} 行;Code 列=node_id)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
