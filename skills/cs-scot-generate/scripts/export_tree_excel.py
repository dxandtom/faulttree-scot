#!/usr/bin/env python3
"""场景产物 → 单页签「调优表」(<短名>.tuning.xlsx):树 + 判据 + 各源绑定 + 解决方案,一行一节点。

为什么要它:产物的"树+绑定"散在 tree_index.json 和各 <指标>_analyzer.py 的 CONDITIONS 里,
业务同事改不动——加节点动不了 tree_index,改绑定要读 Python。本脚本把它们汇成一张人可编辑的
Excel(调优的唯一事实源);改完用 import_tree_excel.py 机械编译回产物,全程零模型参与。

表格式(单页签,一行 = 一个节点,父先子后、兄弟按行序;上级各层名字逐行照抄):
  node_id | L<n>(Chinese)… | 判据:<源标签>… | 绑定:<指标>… | Recommend Solution(Chinese/English) | meta(勿改)
- node_id:现有节点已填(nN);**新增节点这格留空**(导入时自动分配),**禁止改动/复制已有 id**;
- 绑定列:JSON(自动字段 id/node/node_id/condition_text/source 已去掉,导入时自动补回);
  同一源多条绑定 = JSON 数组;"本源查不了" = {"decision": "insufficient_data", "mapping_reason": "…"}。

用法:export_tree_excel.py --out <场景产物目录>
依赖:openpyxl(读 xlsx 本就需要)。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

# 绑定里由导入脚本自动生成的字段——导出时去掉,让表里只剩"人要看/要改"的内容
AUTO_FIELDS = ("id", "node", "node_id", "condition_text", "source")
META_COL = "meta(勿改)"


def read_conditions(path: Path):
    """ast 读出 analyzer 里的 CONDITIONS 字面量列表(与 merge_conditions 的写入互为逆操作)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CONDITIONS" for t in node.targets):
            return ast.literal_eval(node.value)
    return []


def export(out_dir: Path) -> Path:
    tis = sorted((out_dir / "tree").glob("*.tree_index.json"))
    if len(tis) != 1:
        raise SystemExit(f"产物 tree/ 下应恰有一份 tree_index,现有 {len(tis)} 份:{[t.name for t in tis]}")
    index = json.loads(tis[0].read_text(encoding="utf-8"))
    stem = tis[0].name[: -len(".tree_index.json")]
    nodes = index.get("nodes") or {}
    root = index.get("root")
    if not root or root not in nodes:
        raise SystemExit("tree_index 无根节点,产物不完整")

    sol_p = out_dir / "tree" / "solutions.json"
    solutions = json.loads(sol_p.read_text(encoding="utf-8")) if sol_p.exists() else {}

    # 各源绑定:{(node_id, 源): [绑定…]};源=脚本文件名前缀,顺序按文件名稳定
    sources, bind = [], {}
    for ap in sorted((out_dir / "scripts").glob("*_analyzer.py")):
        src = ap.name[: -len("_analyzer.py")]
        sources.append(src)
        for c in read_conditions(ap):
            if not isinstance(c, dict):
                continue
            cell = {k: v for k, v in c.items() if k not in AUTO_FIELDS}
            bind.setdefault((str(c.get("node_id") or ""), src), []).append(cell)

    # DFS(父先子后)+ 层级列范围 + 判据标签(按遭遇序稳定)
    order, stack = [], [root]
    while stack:
        nid = stack.pop()
        order.append(nid)
        stack.extend(reversed(nodes[nid].get("children") or []))
    levels = sorted({nodes[n].get("level") or 1 for n in order})
    tags = []
    for nid in order:
        for t in (nodes[nid].get("data_source") or {}):
            if t not in tags:
                tags.append(t)
    if not tags:
        tags = ["通用"]

    header = (["node_id"] + [f"L{l}(Chinese)" for l in levels] + [f"判据:{t}" for t in tags]
              + [f"绑定:{s}" for s in sources]
              + ["Recommend Solution(Chinese)", "Recommend Solution(English)", META_COL])

    def path_names(nid):
        chain, cur = [], nid
        while cur:
            chain.append(cur)
            cur = nodes[cur].get("parent")
        return list(reversed(chain))

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    wb = Workbook()
    ws = wb.active
    ws.title = "树"
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
    lcol = {l: 2 + i for i, l in enumerate(levels)}
    for nid in order:
        n = nodes[nid]
        row = [""] * len(header)
        row[0] = nid
        for anc in path_names(nid):
            li = lcol.get(nodes[anc].get("level") or 1)
            if li:
                row[li - 1] = nodes[anc].get("name") or ""
        ds = n.get("data_source") or {}
        for i, t in enumerate(tags):
            if ds.get(t):
                row[1 + len(levels) + i] = ds[t]
        for i, s in enumerate(sources):
            bl = bind.get((nid, s))
            if bl:
                payload = bl[0] if len(bl) == 1 else bl
                row[1 + len(levels) + len(tags) + i] = json.dumps(payload, ensure_ascii=False)
        sol = solutions.get(nid) or {}
        row[-3], row[-2] = sol.get("zh", ""), sol.get("en", "")
        if n.get("meta"):
            row[-1] = json.dumps(n["meta"], ensure_ascii=False)
        ws.append(row)
    for col, width in zip(ws.columns, [8] + [18] * len(levels) + [50] * len(tags) + [60] * len(sources) + [40, 30, 20]):
        ws.column_dimensions[col[0].column_letter].width = width
        for cell in col:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    dst = out_dir / "tree" / f"{stem}.tuning.xlsx"
    wb.save(dst)
    return dst


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="产物 → 调优表(单页签,一行一节点)")
    ap.add_argument("--out", required=True, help="场景产物目录(或打包技能的 assets/ 目录)")
    a = ap.parse_args()
    out_dir = Path(a.out).expanduser().resolve()
    dst = export(out_dir)
    print(f"已导出调优表: {dst}")
    print("  一行一节点;新增节点 node_id 留空;改完跑 import_tree_excel.py 编译回产物。"
          "动手说明见同目录 tuning-guide.md。")


if __name__ == "__main__":
    main()
