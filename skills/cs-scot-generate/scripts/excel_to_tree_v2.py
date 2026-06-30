#!/usr/bin/env python3
"""新格式故障树 Excel(多页签 + 判断列按数据源分列)→ 层级 md + tree_index。

与老 `excel_to_tree.py` 的差异(老脚本一行不改、老格式照常):
- **多页签**:遍历所有页签;表头里没有 `Ln` 层级列的(非树/说明页)自动跳过;
  页签名带 `old`/`旧` 的默认跳过(过期版),`--include-old` 可保留。
- **判断列按源分列**:`CHR判断方法 / X2 cause / RB日志判断方法 / 配置数据(核查/判断方法) /
  DBG日志(DEBUG) / 信令判断方法 / 话统(PFM) …` —— 表头**模糊匹配**成规范源标签
  (CHR/X2/RB/配置/DBG/信令/话统),落进节点的 `data_source`,**列头自带"查哪个源"、给绑定预路由**。
- **按场景名(L2)跨页签提取**:输入 `--start-node "NSA场景接入失败"`,把**所有树页签**里该 L2
  场景的行收集起来,按"根→叶"路径合并成**一棵**从 L2 起的子树;同一节点的判断按"源标签 + 出处页签"累积。
  (同名场景散在 NSA NR / NSA LTE 等多页 = 不同侧视角,合并成超集;喂数据时缺的源自动数据不足、自然剪掉。)
  `--sheet "<页签名>"` 可限定只用某一页(等价"按页签名选")。
- **元数据**:`和基础诊断对接KPI索引 / 解决方案 / 备注` 存为节点元数据(**不进判断条件**);
  `数据链接` 丢弃;`产品 / 制式` 不参与建树分支。
- 输出契约与老脚本一致(`<场景>.md` + `<场景>.tree_index.json`);节点多了可选 `meta`/`sheets` 字段
  (additive,老消费方忽略即可)。

用法:
    excel_to_tree_v2.py <xlsx> --list                          # 列出所有可输入的场景名(L2)+ 各自出处页签
    excel_to_tree_v2.py <xlsx> --start-node "NSA场景接入失败"   # 跨页签合并出该场景子树
    excel_to_tree_v2.py <xlsx> --start-node "NSA场景接入失败" --sheet "NSA NR"   # 只用某一页
读 xlsx 需要 openpyxl。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from excel_to_tree import _clean_text, _infer_combine, _is_skip_judgment, scenario_out_dir  # 复用老内核的通用件

_LEVEL_RE = re.compile(r"^L(\d+)(?:\(Chinese\))?$")          # 新格式 L1.. / 老格式 L1(Chinese) 都认
_OLD_RE = re.compile(r"old\b|旧", re.IGNORECASE)            # 页签名带 old/旧 = 过期版,默认跳过
DEFAULT_START_LEVEL = 2                                      # 背景业务知识:场景节点通常在 L2
# 判断列表头 → 规范源标签(按关键词模糊匹配,扛各页签命名差异)。顺序无所谓,一个表头通常只含一个源词。
_SRC_KEYWORDS: List[Tuple[str, str]] = [
    ("CHR", "CHR"), ("X2", "X2"), ("RB", "RB"), ("DBG", "DBG"), ("DEBUG", "DBG"),
    ("信令", "信令"), ("配置", "配置"), ("话统", "话统"), ("PFM", "话统"),
]


def classify(h: str) -> Tuple[str, Any]:
    """一个表头列归类:('level',层级) / ('method',源标签) / ('meta',元数据名) / ('drop',原因)。"""
    h = (h or "").strip()
    m = _LEVEL_RE.match(h)
    if m:
        return ("level", int(m.group(1)))
    if "KPI索引" in h or "基础诊断对接KPI" in h:
        return ("meta", "KPI索引")
    if "解决方案" in h:
        return ("meta", "解决方案")
    if h == "备注" or h.startswith("备注"):
        return ("meta", "备注")
    if "数据链接" in h:
        return ("drop", "链接")
    if h in ("产品", "制式"):
        return ("drop", "产品制式")
    for kw, tag in _SRC_KEYWORDS:                            # 含源关键词 → 判断方法列
        if kw.lower() in h.lower():
            return ("method", tag)
    return ("drop", None)


def sheet_schema(header: List[str]):
    """表头 → (层级列[(列名,层级)]升序, 判断列{列名:源标签}, 元数据列{列名:名})。"""
    levels, methods, metas = [], {}, {}
    for h in header:
        kind, tag = classify(h)
        if kind == "level":
            levels.append((h, tag))
        elif kind == "method":
            methods[h] = tag
        elif kind == "meta":
            metas[h] = tag
    levels.sort(key=lambda x: x[1])
    return levels, methods, metas


def is_tree_sheet(header: List[str]) -> bool:
    return any(classify(h)[0] == "level" for h in header)


def read_sheets(path: str):
    """读所有页签 → [(页签名, 表头, 行dict列表)]。"""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    out = []
    for name in wb.sheetnames:
        ws = wb[name]
        it = ws.iter_rows(values_only=True)
        header = [("" if h is None else str(h)).strip() for h in next(it, [])]
        rows = [{header[i]: ("" if i >= len(r) or r[i] is None else str(r[i])) for i in range(len(header))}
                for r in it]
        out.append((name, header, rows))
    wb.close()
    return out


def list_scenarios(tree_sheets, start_level: int) -> Dict[str, List[str]]:
    """场景名(start_level 层,默认 L2)→ 出现在哪些页签。"""
    sc: Dict[str, set] = {}
    for name, header, rows in tree_sheets:
        levels, _, _ = sheet_schema(header)
        l2 = next((c for c, l in levels if l == start_level), None)
        if not l2:
            continue
        for row in rows:
            v = (row.get(l2) or "").strip()
            if v:
                sc.setdefault(v, set()).add(name)
    return {k: sorted(v) for k, v in sorted(sc.items())}


def build_index(tree_sheets, scenario: str, start_level: int) -> Dict[str, Any]:
    """跨页签收集 scenario(L2)的行,按路径合并成一棵从 L2 起的子树(扁平权威 index)。
    同一路径节点跨页签/跨行累积:data_source 按源标签累积、meta 累积、sheets 记出处。"""
    nodes: Dict[str, Any] = {}
    root_id: Optional[str] = None
    short: Dict[str, str] = {}

    def sid(chain: str) -> str:
        if chain not in short:
            short[chain] = "n" + str(len(short) + 1)
        return short[chain]

    for name, header, rows in tree_sheets:
        levels, methods, metas = sheet_schema(header)
        if not levels:
            continue
        for row in rows:
            full = [(lvl, (row.get(c) or "").strip()) for c, lvl in levels]
            full = [(lvl, v) for lvl, v in full if v]
            if not full:
                continue
            names = [v for _, v in full]
            sl = start_level
            if len(full) >= sl and full[sl - 1][1] == scenario:   # 优先:场景名在 L2
                path = full[sl - 1:]
            elif scenario in names:                                # 兜底:在别的层找到
                path = full[names.index(scenario):]
            else:
                continue
            # 本行各源判断 + 元数据(挂到最深节点)
            mth: Dict[str, str] = {}
            for c, tag in methods.items():
                v = _clean_text(row.get(c))
                if v and not _is_skip_judgment(v):
                    mth[tag] = (mth[tag] + "\n" + v) if tag in mth else v
            meta: Dict[str, str] = {}
            for c, tag in metas.items():
                v = _clean_text(row.get(c))
                if v:
                    meta.setdefault(tag, v)
            parent_id, parts = None, []
            for j, (lvl, nm) in enumerate(path):
                parts.append(nm)
                nid = sid("/".join(parts))
                nd = nodes.setdefault(nid, {"name": nm, "parent": parent_id, "level": lvl, "code": None,
                                            "children": [], "condition": None, "combine": None,
                                            "data_source": {}, "pass_through": True, "sheets": [], "meta": {}})
                if parent_id and nid not in nodes[parent_id]["children"]:
                    nodes[parent_id]["children"].append(nid)
                if j == 0:
                    root_id = nid
                if j == len(path) - 1:
                    if name not in nd["sheets"]:
                        nd["sheets"].append(name)
                    for tag, v in mth.items():
                        prev = nd["data_source"].get(tag)
                        nd["data_source"][tag] = (prev + "\n" + v) if (prev and v not in prev) else (prev or v)
                    if nd["data_source"]:
                        cond = "\n".join(f"[{t}] {nd['data_source'][t]}" for t in nd["data_source"])
                        nd["condition"] = cond
                        nd["combine"] = _infer_combine(cond)
                        nd["pass_through"] = False
                    for tag, v in meta.items():
                        nd["meta"].setdefault(tag, v)
                parent_id = nid
    return {"scenario": scenario, "root": root_id, "nodes": nodes}


def index_to_markdown(index: Dict[str, Any]) -> str:
    """扁平 index → #/##/### 层级 md;判断条件(按源标签)+ 元数据紧跟节点。"""
    nodes = index["nodes"]
    lines: List[str] = []

    def rec(nid: str, depth: int) -> None:
        nd = nodes[nid]
        lines.append("#" * depth + " " + nd["name"])
        if nd.get("condition"):
            lines.extend(nd["condition"].split("\n"))
        for tag, v in (nd.get("meta") or {}).items():
            lines.append(f"（{tag}）{v}")
        for ch in nd["children"]:
            rec(ch, depth + 1)

    if index.get("root"):
        rec(index["root"], 1)
    return "\n".join(lines) + "\n"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="新格式(多页签)故障树 Excel → md + tree_index")
    ap.add_argument("excel_path")
    ap.add_argument("--start-node", help="场景名(L2);跨页签收集该场景、合并成子树")
    ap.add_argument("--start-level", type=int, default=DEFAULT_START_LEVEL)
    ap.add_argument("--sheet", help="只用指定页签(等价按页签名选);不给=所有树页签")
    ap.add_argument("--list", action="store_true", help="列出所有可输入场景名(L2)+ 出处页签")
    ap.add_argument("--include-old", action="store_true", help="保留名字带 old/旧 的页签(默认跳过)")
    ap.add_argument("-o", "--output")
    ap.add_argument("--out-base")
    args = ap.parse_args()

    sheets = read_sheets(args.excel_path)
    tree, nontree, old = [], [], []
    for n, h, r in sheets:
        if not is_tree_sheet(h):
            nontree.append(n)
        elif _OLD_RE.search(n) and not args.include_old:
            old.append(n)
        elif args.sheet and n != args.sheet:
            continue
        else:
            tree.append((n, h, r))

    if args.list:
        sc = list_scenarios(tree, args.start_level)
        print(f"树页签 {len(tree)} 个 | 非树跳过 {nontree or '无'} | 旧版跳过 {old or '无'}")
        print(f"可输入场景名(L{args.start_level}) 共 {len(sc)}:")
        for name, where in sc.items():
            print(f"  {name}    ←  {where}")
        return

    if not args.start_node:
        print("请用 --start-node「场景名」选场景,或 --list 看有哪些。", file=sys.stderr)
        sys.exit(2)

    index = build_index(tree, args.start_node, args.start_level)
    if not index.get("root"):
        print(f"未在任何树页签找到场景「{args.start_node}」(L{args.start_level})。用 --list 核对名字。", file=sys.stderr)
        sys.exit(1)
    md = index_to_markdown(index)

    output_path = args.output
    out_dir = None
    if not output_path:
        anchor = Path(args.out_base).expanduser() if args.out_base else Path(args.excel_path).expanduser().resolve().parent
        out_dir = scenario_out_dir(anchor, args.start_node)
        output_path = str(out_dir / "tree" / f"{args.start_node}.md")
    outp = Path(output_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(md, encoding="utf-8")
    outp.with_suffix(".tree_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已输出子树: {output_path}")
    print(f"已输出权威 index: {outp.with_suffix('.tree_index.json')}")
    src_sheets = sorted({s for nd in index["nodes"].values() for s in nd.get("sheets", [])})
    print(f"场景「{args.start_node}」合并自页签: {src_sheets};节点数 {len(index['nodes'])}")
    if out_dir is not None:
        print(f"本场景产物目录: {out_dir}")


if __name__ == "__main__":
    main()
