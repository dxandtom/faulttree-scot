#!/usr/bin/env python3
"""故障树 Excel → 层级 Markdown 子树(自包含,无本仓自定义依赖)。

输入 Excel 的约定(沿用现网故障树导出格式):
- 层级列:L1(Chinese)、L2(Chinese) … Ln(Chinese),每列是该层的节点名;
- 判断条件列:Judgment Condition(Chinese),挂在每行最深的那个节点上;
- 值为"不涉及"的判断条件会被跳过。

输出 Markdown:用 #/##/### 表示层级,标题下面紧跟该节点的判断条件正文。

用法(当命令行跑,不要 import):
    python excel_to_tree.py "<故障树.xlsx>" -o "<输出.md>" --start-node "<场景名>"

要点:
- 背景业务知识:场景节点通常在 **L2**,所以 `--start-level` 默认 2,`--start-node` 在该层选具体场景;
  万一不在 L2,会自动在其它层找到该场景名并以它为根截取(兜底)。
- `--exclude-pattern` 默认排除噪声节点 `萃取新增节点-.*`;传 `--exclude-pattern ""` 可保留全部。
读 xlsx 需要 openpyxl(pip install openpyxl);csv 用标准库即可。
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_LEVEL_RE = re.compile(r"^L(\d+)\(Chinese\)$")
_JUDGMENT_HEADER = "Judgment Condition(Chinese)"
_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")
# 现网导出里常见的"自动萃取"噪声节点,默认排除(与同事产出 69KB 子树的做法一致)
DEFAULT_EXCLUDE = r"萃取新增节点-.*"
# 背景业务知识:场景节点通常位于第 2 层(L2)
DEFAULT_START_LEVEL = 2


def _read_table(path: str) -> Tuple[List[str], List[Dict[str, str]]]:
    """读 xlsx/csv → (表头, 行dict列表);自动编码检测。自包含。"""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        try:
            from openpyxl import load_workbook
        except ImportError as e:
            raise RuntimeError("读取 xlsx 需要 openpyxl,请先 pip install openpyxl") from e
        wb = load_workbook(p, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = [("" if h is None else str(h)).strip() for h in next(it, [])]
        rows: List[Dict[str, str]] = []
        for r in it:
            rows.append({header[i]: ("" if i >= len(r) or r[i] is None else str(r[i]))
                         for i in range(len(header))})
        wb.close()
        return header, rows
    raw = p.read_bytes()
    text = None
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "ignore")
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    return headers, [{(k or ""): ("" if v is None else str(v)) for k, v in row.items()} for row in reader]


def find_level_columns(headers: List[str]) -> List[Tuple[str, int]]:
    """从表头里找出所有 Ln(Chinese) 层级列,按层级升序返回 (列名, 层级)。"""
    cols: List[Tuple[str, int]] = []
    for h in headers:
        m = _LEVEL_RE.match((h or "").strip())
        if m:
            cols.append((h, int(m.group(1))))
    cols.sort(key=lambda x: x[1])
    return cols


def find_judgment_column(headers: List[str]) -> Optional[str]:
    """找到判断条件列名。"""
    for h in headers:
        if (h or "").strip() == _JUDGMENT_HEADER:
            return h
    return None


def build_tree(rows: List[Dict[str, str]], level_cols: List[Tuple[str, int]],
               start_level: Optional[int] = None, start_node: Optional[str] = None,
               judgment_col: Optional[str] = None,
               exclude_pattern: Optional[str] = None) -> Dict[str, Any]:
    """把扁平的行数据按层级列拼成嵌套树;判断条件挂到每行最深的节点上。

    路径截取规则(给了 start_node 时):
    - 优先在 start_level 层(默认 L2,背景业务知识)匹配场景名;
    - 若该层不是,则在该行任意层找到 start_node 并以它为根(兜底);
    - 都没有则跳过该行。
    """
    tree: Dict[str, Any] = {}
    exclude_re = re.compile(exclude_pattern) if exclude_pattern else None
    col_names = [c for c, _ in level_cols]

    for row in rows:
        full = [(row.get(c) or "").strip() for c in col_names]
        full = [v for v in full if v]
        if not full:
            continue
        if exclude_re and any(exclude_re.search(v) for v in full):
            continue
        judgment = None
        if judgment_col:
            jv = (row.get(judgment_col) or "").strip()
            if jv and jv != "不涉及":
                judgment = jv

        if start_node:
            sl = start_level or 1
            if len(full) >= sl and full[sl - 1] == start_node:
                path = full[sl - 1:]                    # 优先:场景名在 L2
            elif start_node in full:
                path = full[full.index(start_node):]    # 兜底:在别的层找到也认
            else:
                continue
        elif start_level and start_level > 1:
            path = full[start_level - 1:]
        else:
            path = full
        if not path:
            continue

        cur = tree
        for i, node in enumerate(path):
            cur.setdefault(node, {})
            if i == len(path) - 1 and judgment:
                cur[node].setdefault("_judgment_", [])
                if judgment not in cur[node]["_judgment_"]:
                    cur[node]["_judgment_"].append(judgment)
            cur = cur[node]
    return tree


def tree_to_markdown(tree: Dict[str, Any], lines: Optional[List[str]] = None,
                     level: int = 1) -> List[str]:
    """把嵌套树渲染成 #/##/### 层级 Markdown,判断条件紧跟在标题下。"""
    if lines is None:
        lines = []
    items = [(k, v) for k, v in tree.items() if k]
    for i, (name, data) in enumerate(items):
        if i > 0 and level == 1:
            lines.append("")
        lines.append("#" * level + " " + name)
        if isinstance(data, dict) and "_judgment_" in data:
            for j in data["_judgment_"]:
                lines.append(j)
        children = {k: v for k, v in data.items() if k != "_judgment_"} if isinstance(data, dict) else {}
        if children:
            tree_to_markdown(children, lines, level + 1)
    return lines


def excel_to_tree_markdown(excel_path: str, output_path: Optional[str] = None,
                           start_level: Optional[int] = None, start_node: Optional[str] = None,
                           exclude_pattern: Optional[str] = None) -> str:
    """主流程:读 Excel → 建树 → 渲染 md,可选写出文件,返回 md 文本。"""
    headers, rows = _read_table(excel_path)
    level_cols = find_level_columns(headers)
    if not level_cols:
        raise ValueError("未找到 Ln(Chinese) 格式的层级列,请检查 Excel 表头")
    judgment_col = find_judgment_column(headers)
    tree = build_tree(rows, level_cols, start_level, start_node, judgment_col, exclude_pattern)
    lines = tree_to_markdown(tree)
    if not lines:
        raise ValueError(f"未构建出任何节点。请确认场景名是否准确(--start-node「{start_node}」是否在故障树里)")
    md = "\n".join(lines) + "\n"
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(md, encoding="utf-8")
    return md


def main() -> None:
    try:  # Windows 控制台中文路径不乱码
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="故障树 Excel → 层级 Markdown 子树")
    ap.add_argument("excel_path", help="故障树 Excel 路径(.xlsx)")
    ap.add_argument("-o", "--output", help="输出 md 路径")
    ap.add_argument("--start-node", help="场景名;在 start-level 层选定该场景,截取以它为根的子树(强烈建议传)")
    ap.add_argument("--start-level", type=int, default=DEFAULT_START_LEVEL,
                    help=f"场景所在层级,默认 {DEFAULT_START_LEVEL}(背景业务知识:场景通常在 L2)")
    ap.add_argument("--exclude-pattern", default=DEFAULT_EXCLUDE,
                    help=f"正则;任一层级值命中则整行排除。默认 {DEFAULT_EXCLUDE!r};传 \"\" 可保留全部")
    args = ap.parse_args()
    if not args.start_node:
        print("[warn] 未传 --start-node,将导出整棵故障树(可能很大)。建议传场景名只截子树。",
              file=sys.stderr)
    try:
        md = excel_to_tree_markdown(args.excel_path, args.output, args.start_level,
                                    args.start_node, args.exclude_pattern)
    except Exception as e:  # noqa: BLE001 — CLI 边界统一兜底
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    if args.output:
        print(f"已输出: {args.output}")
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
