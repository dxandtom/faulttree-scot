#!/usr/bin/env python3
"""场景产物 → 单页签「调优表」(<短名>.tuning.xlsx):树 + 判据 + 各源绑定 + 解决方案,一行一节点。

为什么要它:产物的"树+绑定"散在 tree_index.json 和各 <指标>_analyzer.py 的 CONDITIONS 里,
业务同事改不动——加节点动不了 tree_index,改绑定要读 Python。本脚本把它们汇成一张人可编辑的
Excel(调优的唯一事实源);改完用 import_tree_excel.py 机械编译回产物,全程零模型参与。

表格式(单页签「树」,一行 = 一个节点,父先子后、兄弟按行序;上级各层名字逐行照抄):
  node_id | L<n>(Chinese)… | [L<n>(English)…] | 判据:<源标签>…/判据:原文 | [Judgment Condition(English)]
  | 绑定:<指标>… | Recommend Solution(Chinese/English) | meta(勿改)
- node_id:现有节点已填(nN);**新增节点这格留空**(导入时自动分配),**禁止改动/复制已有 id**;
- 英文列(方括号的两组)只在树里有英文名/英文判据时才导出:L<n>(English) 与中文层级列同位置对照,
  一行的英文名填在该节点所在层的英文格(上级各层的英文名也照抄,便于阅读);英文判据整段一格。
  导入时英文名取本行最深层的英文格,英文判据随中文判据走:中文判据清空则英文判据一并清空;
- 判据:**以节点 condition 为准**——能按源标签拆回去的落各 `判据:<标签>` 列;拆不回去的
  (老格式 v1 树:condition 有值但无标签结构)整段落 `判据:原文` 列逐字保真,绝不丢判据;
- 绑定列:JSON;与默认值一致的自动字段(id=<源>-<nN>、source=<源>、condition_text=节点判据)
  已省略,**不一致的保留**(自定义 id/页签路由 source/逐字 condition_text 往返不丢);
  同一源多条绑定 = JSON 数组;"本源查不了" = {"decision": "insufficient_data", "mapping_reason": "…"}。
- 覆盖保护:目标调优表已存在且比 tree_index 新(可能有没导入的人工修改)→ 先备份 .bak 再覆盖,明确提示。

用法:export_tree_excel.py --out <场景产物目录>
依赖:openpyxl(仅**写** xlsx 用;读表已零依赖走同目录 xlsx_reader.py)。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import sys
from pathlib import Path

META_COL = "meta(勿改)"
RAW_TAG = "原文"
COND_EN_COL = "Judgment Condition(English)"


def read_conditions(path: Path):
    """ast 读出 analyzer 里的 CONDITIONS 字面量列表(与 merge_conditions 的写入互为逆操作)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CONDITIONS" for t in node.targets):
            return ast.literal_eval(node.value)
    return []


def _read_index(out_dir: Path):
    """读产物 tree_index(须恰一份、有根),返回 (tree_index 路径, index, 短名, nodes, root)。"""
    tis = sorted((out_dir / "tree").glob("*.tree_index.json"))
    if len(tis) != 1:
        raise SystemExit(f"产物 tree/ 下应恰有一份 tree_index,现有 {len(tis)} 份:{[t.name for t in tis]}")
    index = json.loads(tis[0].read_text(encoding="utf-8"))
    stem = tis[0].name[: -len(".tree_index.json")]
    nodes = index.get("nodes") or {}
    root = index.get("root")
    if not root or root not in nodes:
        raise SystemExit("tree_index 无根节点,产物不完整")
    return tis[0], index, stem, nodes, root


def _binding_cell(c: dict, src: str, rnid: str, nodes: dict) -> dict:
    """一条 CONDITIONS 条目 → 调优表绑定格:去掉 node/node_id;与默认值一致的 id/source/condition_text 省略,不一致的保留。"""
    cell = {k: v for k, v in c.items() if k not in ("node", "node_id")}
    cond_ref = nodes[rnid].get("condition") or nodes[rnid].get("name")
    if cell.get("id") == f"{src}-{rnid}":
        cell.pop("id", None)                     # 默认 id 省略;自定义 id(如 CUSTOM 引用)保留
    if cell.get("source") == src:
        cell.pop("source", None)                 # 默认源省略;页签路由(source=<页签名>)保留
    if cell.get("condition_text") == cond_ref:
        cell.pop("condition_text", None)         # 与节点判据一致省略;逐字子句保留
    return cell


def _collect_bindings(out_dir: Path, nodes: dict):
    """各 analyzer 的 CONDITIONS → {(node_id, 源): [绑定格…]},node_id 别名解析回 nN;孤儿绑定明确提示、绝不无声丢。
    返回 (源列表[cross 放最后], 绑定表)。"""
    # node_id 别名:老产物绑定可能用 code 当 node_id,统一解析回 nN;解析不到的明确提示,绝不无声丢
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
            bind.setdefault((rnid, src), []).append(_binding_cell(c, src, rnid, nodes))
    sources = [s for s in sources if s != "cross"] + (["cross"] if "cross" in sources else [])  # cross 列放最后
    if dropped:
        print("⚠ 以下绑定的 node_id 在树里找不到,未能进调优表(孤儿绑定,请先修产物):", file=sys.stderr)
        for d in dropped:
            print(f"  - {d}", file=sys.stderr)
    return sources, bind


def _dfs_order(nodes: dict, root: str):
    """DFS(父先子后)+ 相对深度。返回 (节点序, {id: 深度}, 出现过的深度升序)。
    列位用**相对深度**(根=L1,由 parent 链推导),不用节点存的母表层级号——多页签合并的树
    层级号可能内部不自洽(父子同号/兄弟异号),按它摆列会同列互相覆盖、导入按列重建父链必炸
    (实测:零改动纯往返报 73 处"上级和上文对不上"+83 个根)。深度永远自洽。"""
    order, stack = [], [root]
    while stack:
        nid = stack.pop()
        order.append(nid)
        stack.extend(reversed(nodes[nid].get("children") or []))
    depth = {}
    for nid in order:                                    # order 父先子后,父的深度必已算出
        p = nodes[nid].get("parent")
        depth[nid] = 1 if not p else depth[p] + 1
    return order, depth, sorted({depth[n] for n in order})


def _condition_cells(nodes: dict, order: list):
    """判据以 condition 为准:能由 data_source 逐字拼回 condition 的按标签拆列;拼不回的走「原文」列。
    返回 (标签列表[出现序], 是否需要原文列, {id: ("tags", data_source) | ("raw", condition)})。"""
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
    return tags, need_raw, cond_cells


def _build_header(levels, has_en_name: bool, tag_headers, has_en_cond: bool, sources):
    """表头列序:node_id | 中文层级列 | [英文层级列] | 判据列 | [英文判据列] | 绑定列 | 解决方案中/英 | meta。"""
    return (["node_id"] + [f"L{l}(Chinese)" for l in levels]
            + ([f"L{l}(English)" for l in levels] if has_en_name else [])
            + tag_headers + ([COND_EN_COL] if has_en_cond else [])
            + [f"绑定:{s}" for s in sources]
            + ["Recommend Solution(Chinese)", "Recommend Solution(English)", META_COL])


def _column_layout(col: dict, levels, tags, need_raw: bool, sources, has_en_name: bool, has_en_cond: bool) -> dict:
    """表头名 → 各类列的 1 起列号表:lcol(中文层级)/ecol(英文层级)/tcol(标签判据)/raw_col(原文)/
    scol(绑定)/cond_en(英文判据)。"""
    return {"lcol": {l: col[f"L{l}(Chinese)"] for l in levels},
            "ecol": {l: col[f"L{l}(English)"] for l in levels} if has_en_name else {},
            "tcol": {t: col[f"判据:{t}"] for t in tags},
            "raw_col": col[f"判据:{RAW_TAG}"] if need_raw else None,
            "scol": {s: col[f"绑定:{s}"] for s in sources},
            "cond_en": col[COND_EN_COL] if has_en_cond else None}


def _path_ids(nodes: dict, nid: str) -> list:
    """根→本节点的 id 链。"""
    chain, cur = [], nid
    while cur:
        chain.append(cur)
        cur = nodes[cur].get("parent")
    return list(reversed(chain))


def _node_row(nid: str, nodes: dict, depth: dict, header: list, layout: dict, cond_cells: dict,
              bind: dict, sources: list, solutions: dict) -> list:
    """一个节点 → 一行格值:祖先各层中/英文名逐行照抄、英文判据、判据(标签列或原文列)、各源绑定 JSON、
    解决方案、meta。"""
    n = nodes[nid]
    row = [""] * len(header)
    row[0] = nid
    for anc in _path_ids(nodes, nid):
        li = layout["lcol"].get(depth[anc])
        if li:
            row[li - 1] = nodes[anc].get("name") or ""
        ei = layout["ecol"].get(depth[anc])
        if ei and nodes[anc].get("name_en"):
            row[ei - 1] = nodes[anc]["name_en"]
    if layout["cond_en"] and n.get("condition_en"):
        row[layout["cond_en"] - 1] = n["condition_en"]
    kind_ds = cond_cells.get(nid)
    if kind_ds:
        kind, payload = kind_ds
        if kind == "tags":
            for t, v in payload.items():
                row[layout["tcol"][t] - 1] = v
        else:
            row[layout["raw_col"] - 1] = payload
    for s in sources:
        bl = bind.get((nid, s))
        if bl:
            row[layout["scol"][s] - 1] = json.dumps(bl[0] if len(bl) == 1 else bl, ensure_ascii=False)
    sol = solutions.get(nid) or {}
    row[-3], row[-2] = sol.get("zh", ""), sol.get("en", "")
    if n.get("meta"):
        row[-1] = json.dumps(n["meta"], ensure_ascii=False)
    return row


def _new_workbook(header: list):
    """新建单页签「树」工作簿并写入加粗表头,返回 (wb, ws)。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "树"
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
    return wb, ws


def _col_width(h: str) -> int:
    """列宽按列类型定。"""
    if h == "node_id":
        return 8
    if h.startswith("L") and (h.endswith("(Chinese)") or h.endswith("(English)")):
        return 18
    if h.startswith("判据:") or h == COND_EN_COL:
        return 50
    if h.startswith("绑定:"):
        return 60
    return {"Recommend Solution(Chinese)": 40, "Recommend Solution(English)": 30, META_COL: 20}.get(h, 20)


def _style_columns(ws, header: list) -> None:
    """按列类型设列宽,所有格顶端对齐 + 自动换行。"""
    from openpyxl.styles import Alignment
    widths = [_col_width(h) for h in header]
    for col, width in zip(ws.columns, widths):
        ws.column_dimensions[col[0].column_letter].width = width
        for cell in col:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _save_with_backup(wb, dst: Path, ti_path: Path) -> None:
    """覆盖保护:目标调优表已存在且比 tree_index 新 → 先备份 .bak 再覆盖并明确提示;保存后把表 mtime 对齐 index。"""
    if dst.exists() and dst.stat().st_mtime > ti_path.stat().st_mtime:
        bak = dst.with_suffix(".xlsx.bak")
        shutil.copy2(dst, bak)
        print(f"⚠ 现有调优表比 tree_index 新(可能有**尚未导入**的人工修改)——已备份到 {bak.name} 再覆盖。"
              "若确有未导入的修改,先跑 import_tree_excel.py 再导出。", file=sys.stderr)
    wb.save(dst)
    try:                                                 # 刚导出的表内容必与产物同步:把表 mtime 对齐 index,
        st = ti_path.stat()                              # 消除"表比 index 新"的常驻假警报(迁移/打包每次都误报)。
        os.utime(dst, (st.st_atime, st.st_mtime))        # 此后人工真改过表,mtime 变新,新旧检测照常灵。
    except OSError:
        pass


def export(out_dir: Path) -> Path:
    ti_path, index, stem, nodes, root = _read_index(out_dir)

    sol_p = out_dir / "tree" / "solutions.json"
    solutions = json.loads(sol_p.read_text(encoding="utf-8")) if sol_p.exists() else {}

    sources, bind = _collect_bindings(out_dir, nodes)
    order, depth, levels = _dfs_order(nodes, root)
    tags, need_raw, cond_cells = _condition_cells(nodes, order)
    tag_headers = [f"判据:{t}" for t in tags] + ([f"判据:{RAW_TAG}"] if need_raw else [])
    has_en_name = any(nodes[n].get("name_en") for n in order)
    has_en_cond = any(nodes[n].get("condition_en") for n in order)

    header = _build_header(levels, has_en_name, tag_headers, has_en_cond, sources)
    col = {h: i + 1 for i, h in enumerate(header)}      # 表头名 → 1 起列号(导入按表头名识别,位置无关)

    wb, ws = _new_workbook(header)
    layout = _column_layout(col, levels, tags, need_raw, sources, has_en_name, has_en_cond)
    for nid in order:
        ws.append(_node_row(nid, nodes, depth, header, layout, cond_cells, bind, sources, solutions))
    _style_columns(ws, header)

    dst = out_dir / "tree" / f"{stem}.tuning.xlsx"
    _save_with_backup(wb, dst, ti_path)
    n_cross = sorted({nid for (nid, s) in bind if s == "cross"})
    if n_cross:
        print(f"  跨源绑定已进「绑定:cross」列(涉及节点 {len(n_cross)} 个:{', '.join(n_cross[:8])}"
              f"{'…' if len(n_cross) > 8 else ''})——每条必须带 source=最终查询落点的真实源名;"
              "写法见 scripts/condition_binding_schema.md 的「参数化查询与跨源判据」节。")
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
          "绑定列写法与类别模板见 scripts/condition_binding_schema.md。")


if __name__ == "__main__":
    main()
