#!/usr/bin/env python3
"""故障树 Excel → 层级 Markdown 子树。

输入 Excel 的约定(沿用现网故障树导出格式):
- 层级列:L1(Chinese)、L2(Chinese) … Ln(Chinese),每列是该层的节点名;
- 判断条件列:Judgment Condition(Chinese),挂在每行最深的那个节点上;
- 值为"不涉及"的判断条件会被跳过。

输出 Markdown:用 #/##/### 表示层级,标题下面紧跟该节点的判断条件正文。
依赖统一的 data_loader,xlsx/csv 都能读(本脚本主要用于 xlsx)。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_loader import load_table  # noqa: E402

_LEVEL_RE = re.compile(r"^L(\d+)\(Chinese\)$")
_JUDGMENT_HEADER = "Judgment Condition(Chinese)"


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
               start_level: int = 1, start_node: Optional[str] = None,
               judgment_col: Optional[str] = None,
               exclude_pattern: Optional[str] = None) -> Dict[str, Any]:
    """把扁平的行数据按层级列拼成嵌套树;判断条件挂到每行最深的节点上。"""
    tree: Dict[str, Any] = {}
    exclude_re = re.compile(exclude_pattern) if exclude_pattern else None
    col_names = [c for c, _ in level_cols]

    for row in rows:
        # 读出本行从 L1 起的完整路径(跳过空层)
        full = [(row.get(c) or "").strip() for c in col_names]
        full = [v for v in full if v]
        if not full:
            continue
        # 命中排除正则的行整行跳过
        if exclude_re and any(exclude_re.search(v) for v in full):
            continue
        # 取判断条件("不涉及"视为无)
        judgment = None
        if judgment_col:
            jv = (row.get(judgment_col) or "").strip()
            if jv and jv != "不涉及":
                judgment = jv
        # 按 start_level 截断,再按 start_node 过滤
        path = full[start_level - 1:] if start_level > 1 else full
        if not path:
            continue
        if start_node and path[0] != start_node:
            continue
        # 写入树
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
            lines.append("")  # 多棵根树之间空行分隔
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
    headers, rows = load_table(excel_path)
    level_cols = find_level_columns(headers)
    if not level_cols:
        raise ValueError("未找到 Ln(Chinese) 格式的层级列,请检查 Excel 表头")
    judgment_col = find_judgment_column(headers)
    tree = build_tree(rows, level_cols, start_level or 1, start_node, judgment_col, exclude_pattern)
    lines = tree_to_markdown(tree)
    if not lines:
        raise ValueError("未构建出任何节点,请检查 --start-level / --start-node / --exclude-pattern")
    md = "\n".join(lines) + "\n"
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(md, encoding="utf-8")
    return md


def main() -> None:
    ap = argparse.ArgumentParser(description="故障树 Excel → 层级 Markdown 子树")
    ap.add_argument("excel_path", help="故障树 Excel 路径(.xlsx)")
    ap.add_argument("-o", "--output", help="输出 md 路径")
    ap.add_argument("--start-level", type=int, help="起始层级,从 1 开始;如 2 表示从 L2 起")
    ap.add_argument("--start-node", help="起始节点名(配合 start-level)")
    ap.add_argument("--exclude-pattern", help="正则;任一层级值命中则整行排除")
    args = ap.parse_args()
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
