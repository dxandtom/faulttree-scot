#!/usr/bin/env python3
"""
Excel层级数据转树状结构 - 从Ln(Chinese)列提取数据构建树并输出到md
"""

import re
import sys
from pathlib import Path
from typing import List, Dict, Optional, Any

try:
    import pandas as pd
except ImportError:
    print("请安装pandas: pip install pandas openpyxl")
    sys.exit(1)


def find_chinese_columns(df: pd.DataFrame) -> List[tuple]:
    """查找所有Ln(Chinese)格式的列，返回(列名, 层级)元组列表"""
    pattern = re.compile(r'^L(\d+)\(Chinese\)$')
    chinese_cols = []

    for col in df.columns:
        match = pattern.match(str(col))
        if match:
            level = int(match.group(1))
            chinese_cols.append((col, level))

    # 按层级排序
    chinese_cols.sort(key=lambda x: x[1])
    return chinese_cols


def find_judgment_condition_column(df: pd.DataFrame) -> Optional[str]:
    """查找 Judgment Condition(Chinese) 列"""
    for col in df.columns:
        if str(col).strip() == "Judgment Condition(Chinese)":
            return col
    return None


def build_tree(df: pd.DataFrame, chinese_cols: List[tuple], start_level: int = 1,
               start_node: Optional[str] = None, judgment_col: Optional[str] = None,
               exclude_pattern: Optional[str] = None) -> Dict[str, Any]:
    """构建树状结构

    Args:
        df: Excel数据
        chinese_cols: Ln(Chinese)列列表，格式为(列名, 层级)，应包含从L1开始的所有列
        start_level: 起始层级，1开始
        start_node: 起始节点名称，如果指定则只读取该节点下的数据
        judgment_col: Judgment Condition(Chinese) 列名
        exclude_pattern: 正则表达式，用于过滤掉任意层级下Ln的值满足该表达式的行
    """
    tree: Dict[str, Any] = {}

    # 编译正则表达式
    exclude_regex = None
    if exclude_pattern:
        try:
            exclude_regex = re.compile(exclude_pattern)
        except re.error as e:
            raise ValueError(f"无效的正则表达式: {exclude_pattern}, 错误: {e}")

    # 提取从L1开始的所有列名
    all_col_names = [col for col, _ in chinese_cols]

    for _, row in df.iterrows():
        # 先读取从L1开始的完整路径
        full_path = []
        for col in all_col_names:
            val = row.get(col)
            if pd.notna(val) and str(val).strip():
                full_path.append(str(val).strip())

        if not full_path:
            continue

        # 如果指定了exclude_pattern，检查任意Ln列的值是否匹配
        if exclude_regex:
            matched = False
            for col in all_col_names:
                val = row.get(col)
                if pd.notna(val) and str(val).strip():
                    if exclude_regex.search(str(val).strip()):
                        matched = True
                        break
            if matched:
                continue

        # 获取判断条件
        judgment_condition = None
        if judgment_col:
            jc_val = row.get(judgment_col)
            if pd.notna(jc_val) and str(jc_val).strip():
                jc_str = str(jc_val).strip()
                # 跳过"不涉及"
                if jc_str != "不涉及":
                    judgment_condition = jc_str

        # 根据start_level截取路径
        if start_level > 1:
            start_idx = start_level - 1
            if start_idx >= len(full_path):
                continue
            path = full_path[start_idx:]
        else:
            path = full_path

        # 根据start_node过滤路径
        if start_node:
            if not path or path[0] != start_node:
                continue

        if not path:
            continue

        # 将路径添加到树中，同时存储判断条件
        # 判断条件附加到路径的最后一个节点（不管是否是叶子节点）
        current = tree
        path_len = len(path)

        for i, node in enumerate(path):
            is_last = (i == path_len - 1)

            if node not in current:
                # 创建新节点
                current[node] = {}

            # 如果是最后一个节点，存储判断条件
            if is_last and judgment_condition:
                if "_judgment_" not in current[node]:
                    current[node]["_judgment_"] = []
                # 合并判断条件，避免重复
                if judgment_condition not in current[node]["_judgment_"]:
                    current[node]["_judgment_"].append(judgment_condition)

            current = current[node]

    return tree


def tree_to_markdown(tree: Dict, lines: List[str] = None, current_level: int = 1) -> List[str]:
    """将树转换为Markdown标题层级格式

    使用 # ## ### 等标题表示层级关系
    例如：
    # 节点1
    判断方法：XXXX
    如果该条件成立，则可以继续判断该条件下面的子标题对应的条件
    ## 节点2
    ### 节点3
    ### 节点4
    ## 节点5
    """
    if lines is None:
        lines = []

    items = list(tree.items())
    if not items:
        return lines

    # 根节点可能有多个，每棵树独立输出
    for i, (node_name, node_data) in enumerate(items):
        if not node_name:
            continue

        if i > 0:
            # 多棵树之间用空行分隔
            lines.append("")

        # 检查节点是否有判断条件
        has_judgment = isinstance(node_data, dict) and "_judgment_" in node_data

        # 输出节点标题
        lines.append("#" * current_level + " " + node_name)

        # 如果有判断条件，直接输出判断条件内容
        if has_judgment:
            judgments = node_data["_judgment_"]
            # 支持单个字符串或列表
            if isinstance(judgments, list):
                for j in judgments:
                    lines.append(j)
            else:
                lines.append(judgments)

        # 递归处理子节点，层级+1
        # 如果是叶子节点（没有子节点），则不递归
        if isinstance(node_data, dict) and node_data:
            # 过滤掉 _judgment_ 键，只处理子节点
            children = {k: v for k, v in node_data.items() if k != "_judgment_"}
            if children:
                tree_to_markdown(children, lines, current_level + 1)

    return lines


def excel_to_tree_markdown(excel_path: str, output_path: Optional[str] = None,
                          start_level: Optional[int] = None, start_node: Optional[str] = None,
                          exclude_pattern: Optional[str] = None) -> str:
    """主函数：Excel转树状markdown

    Args:
        excel_path: Excel文件路径
        output_path: 输出md文件路径
        start_level: 起始层级，从1开始。如2表示从L2开始
        start_node: 起始节点名称，配合start_level使用
        exclude_pattern: 正则表达式，用于过滤掉任意层级下Ln的值满足该表达式的行
    """
    # 读取Excel
    df = pd.read_excel(excel_path)

    # 查找Chinese列
    all_chinese_cols = find_chinese_columns(df)
    if not all_chinese_cols:
        raise ValueError("未找到Ln(Chinese)格式的列")

    # 查找判断条件列
    judgment_col = find_judgment_condition_column(df)

    # 确定使用的列范围（需要从L1开始的所有列，以便正确截取路径）
    chinese_cols = all_chinese_cols

    if start_level:
        # 验证起始层级是否存在
        max_level = max(level for _, level in all_chinese_cols)
        if start_level > max_level:
            raise ValueError(f"起始层级L{start_level}不存在，最大层级为L{max_level}")

    # 构建树，传入判断条件列和排除模式
    tree = build_tree(df, chinese_cols, start_level or 1, start_node, judgment_col, exclude_pattern)

    # 转换为markdown
    lines = tree_to_markdown(tree)

    if not lines:
        raise ValueError("未找到匹配的数据")

    # 确定输出路径
    if not output_path:
        excel_path_obj = Path(excel_path)
        output_path = str(excel_path_obj.with_suffix('.md'))

    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return output_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Excel层级数据转树状结构')
    parser.add_argument('excel_path', help='Excel文件路径')
    parser.add_argument('-o', '--output', help='输出md文件路径')
    parser.add_argument('--start-level', type=int, help='起始层级，从1开始。如2表示从L2开始')
    parser.add_argument('--start-node', help='起始节点名称，配合start_level使用')
    parser.add_argument('--exclude-pattern', help='正则表达式，用于过滤掉任意层级下Ln的值满足该表达式的行')

    args = parser.parse_args()

    try:
        result = excel_to_tree_markdown(args.excel_path, args.output, args.start_level, args.start_node, args.exclude_pattern)
        print(f"成功输出到: {result}")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
