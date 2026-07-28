#!/usr/bin/env python3
"""场景产物 → 单页签「调优表」(<短名>.tuning.xlsx):树 + 判据 + 各源绑定 + 解决方案,一行一节点。

为什么要它:产物的"树+绑定"散在 tree_index.json 和各 <指标>_analyzer.py 的 CONDITIONS 里,
业务同事改不动——加节点动不了 tree_index,改绑定要读 Python。本脚本把它们汇成一张人可编辑的
Excel(调优的唯一事实源);改完用 import_tree_excel.py 机械编译回产物,全程零模型参与。

表格式(单页签「树」,一行 = 一个节点,父先子后、兄弟按行序;上级各层名字逐行照抄):
  node_id | L<n>(Chinese)… | 判据:<源标签>…/判据:原文 | 绑定:<指标>… | Recommend Solution(Chinese/English) | meta(勿改)
- node_id:现有节点已填(nN);**新增节点这格留空**(导入时自动分配),**禁止改动/复制已有 id**;
- 判据:**以节点 condition 为准**——能按源标签拆回去的落各 `判据:<标签>` 列;拆不回去的
  (老格式 v1 树:condition 有值但无标签结构)整段落 `判据:原文` 列逐字保真,绝不丢判据;
- 绑定列:JSON;与默认值一致的自动字段(id=<源>-<nN>、source=<源>、condition_text=节点判据)
  已省略,**不一致的保留**(自定义 id/页签路由 source/逐字 condition_text 往返不丢);
  同一源多条绑定 = JSON 数组;"本源查不了" = {"decision": "insufficient_data", "mapping_reason": "…"}。
- 覆盖保护:目标调优表已存在且比 tree_index 新(可能有没导入的人工修改)→ 先备份 .bak 再覆盖,响亮提示。

用法:export_tree_excel.py --out <场景产物目录>
依赖:openpyxl(读 xlsx 本就需要)。
"""
from __future__ import annotations

import argparse
import ast
import json
import shutil
import sys
from pathlib import Path

META_COL = "meta(勿改)"
RAW_TAG = "原文"


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

    # node_id 别名:老产物绑定可能用 code 当 node_id,统一解析回 nN;解析不到的响亮提示,绝不无声丢
    alias = {nid: nid for nid in nodes}
    for nid, n in nodes.items():
        if n.get("code"):
            alias.setdefault(str(n["code"]), nid)

    sources, bind, dropped = [], {}, []
    for ap in sorted((out_dir / "scripts").glob("*_analyzer.py")):
        src = ap.name[: -len("_analyzer.py")]
        sources.append(src)                              # cross 也进表(「绑定:cross」列,人可调优;
                                                         # 其条目自带 source=真实落点,默认剥离不会误伤)
        for c in read_conditions(ap):
            if not isinstance(c, dict):
                continue
            rnid = alias.get(str(c.get("node_id") or ""))
            if not rnid:
                dropped.append(f"{src}: id={c.get('id')} node_id={c.get('node_id')!r}(树里没有该节点)")
                continue
            cell = {k: v for k, v in c.items() if k not in ("node", "node_id")}
            cond_ref = nodes[rnid].get("condition") or nodes[rnid].get("name")
            if cell.get("id") == f"{src}-{rnid}":
                cell.pop("id", None)                     # 默认 id 省略;自定义 id(如 CUSTOM 引用)保留
            if cell.get("source") == src:
                cell.pop("source", None)                 # 默认源省略;页签路由(source=<页签名>)保留
            if cell.get("condition_text") == cond_ref:
                cell.pop("condition_text", None)         # 与节点判据一致省略;逐字子句保留
            bind.setdefault((rnid, src), []).append(cell)
    sources = [s for s in sources if s != "cross"] + (["cross"] if "cross" in sources else [])  # cross 列放最后
    if dropped:
        print("⚠ 以下绑定的 node_id 在树里找不到,未能进调优表(孤儿绑定,请先修产物):", file=sys.stderr)
        for d in dropped:
            print(f"  - {d}", file=sys.stderr)

    # DFS(父先子后)+ 层级列范围
    order, stack = [], [root]
    while stack:
        nid = stack.pop()
        order.append(nid)
        stack.extend(reversed(nodes[nid].get("children") or []))
    levels = sorted({nodes[n].get("level") or 1 for n in order})

    # 判据以 condition 为准:能由 data_source 逐字拼回 condition 的按标签拆列;拼不回的走「原文」列
    tags, need_raw, cond_cells = [], False, {}
    for nid in order:
        n = nodes[nid]
        cond, ds = n.get("condition"), n.get("data_source") or {}
        if not cond:
            continue
        if ds and "\n".join(f"[{t}] {v}" for t, v in ds.items()) == cond:
            cond_cells[nid] = ("tags", ds)
            for t in ds:
                if t not in tags:
                    tags.append(t)
        else:                                            # v1 老树/结构对不上:整段逐字进「原文」,绝不丢
            cond_cells[nid] = ("raw", cond)
            need_raw = True
    tag_headers = [f"判据:{t}" for t in tags] + ([f"判据:{RAW_TAG}"] if need_raw else [])

    header = (["node_id"] + [f"L{l}(Chinese)" for l in levels] + tag_headers
              + [f"绑定:{s}" for s in sources]
              + ["Recommend Solution(Chinese)", "Recommend Solution(English)", META_COL])

    def path_ids(nid):
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
    tcol = {t: 2 + len(levels) + i for i, t in enumerate(tags)}
    raw_col = 2 + len(levels) + len(tags) if need_raw else None
    scol = {s: 2 + len(levels) + len(tag_headers) + i for i, s in enumerate(sources)}
    for nid in order:
        n = nodes[nid]
        row = [""] * len(header)
        row[0] = nid
        for anc in path_ids(nid):
            li = lcol.get(nodes[anc].get("level") or 1)
            if li:
                row[li - 1] = nodes[anc].get("name") or ""
        kind_ds = cond_cells.get(nid)
        if kind_ds:
            kind, payload = kind_ds
            if kind == "tags":
                for t, v in payload.items():
                    row[tcol[t] - 1] = v
            else:
                row[raw_col - 1] = payload
        for s in sources:
            bl = bind.get((nid, s))
            if bl:
                row[scol[s] - 1] = json.dumps(bl[0] if len(bl) == 1 else bl, ensure_ascii=False)
        sol = solutions.get(nid) or {}
        row[-3], row[-2] = sol.get("zh", ""), sol.get("en", "")
        if n.get("meta"):
            row[-1] = json.dumps(n["meta"], ensure_ascii=False)
        ws.append(row)
    widths = [8] + [18] * len(levels) + [50] * len(tag_headers) + [60] * len(sources) + [40, 30, 20]
    for col, width in zip(ws.columns, widths):
        ws.column_dimensions[col[0].column_letter].width = width
        for cell in col:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    dst = out_dir / "tree" / f"{stem}.tuning.xlsx"
    if dst.exists() and dst.stat().st_mtime > tis[0].stat().st_mtime:
        bak = dst.with_suffix(".xlsx.bak")
        shutil.copy2(dst, bak)
        print(f"⚠ 现有调优表比 tree_index 新(可能有**尚未导入**的人工修改)——已备份到 {bak.name} 再覆盖。"
              "若确有未导入的修改,先跑 import_tree_excel.py 再导出。", file=sys.stderr)
    wb.save(dst)
    n_cross = sorted({nid for (nid, s) in bind if s == "cross"})
    if n_cross:
        print(f"  跨源绑定已进「绑定:cross」列(涉及节点 {len(n_cross)} 个:{', '.join(n_cross[:8])}"
              f"{'…' if len(n_cross) > 8 else ''})——每条必须带 source=最终查询落点的真实源名;"
              "写法见 tree/binding-handbook.md 第三节。")
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
    dst = export(Path(a.out).expanduser().resolve())
    print(f"已导出调优表: {dst}")
    print("  一行一节点;新增节点 node_id 留空;改完跑 import_tree_excel.py 编译回产物。"
          "动手说明见同目录 tuning-guide.md。")


if __name__ == "__main__":
    main()
