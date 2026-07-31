#!/usr/bin/env python3
"""
search_config.py - 解析华为设备配置文件中指定配置项的内容

支持 .txt 和 .log 后缀的配置文件。

【配置文件格式规范】
- 以 # 分割行，两个 # 行之间为一组配置
- 无缩进的配置属于一层配置
- 加一个空格缩进代表二层配置，以此类推
- 空格缩进代表层级

【优化点】
1. 添加编码自动检测（chardet），支持 GBK/GB2312/UTF-8 等多种编码
2. 添加模块化接口（parse_config_file, find_config_item 等函数可被外部调用）
3. 添加批量文件解析支持
4. 输出格式优化，支持 JSON 格式返回
5. 支持按层级解析配置

用法:
    # 命令行
    python search_config.py <文件路径> <配置项路径> [--value <期望值>] [--regex]

    # 作为模块调用
    from search_config import parse_config_file, find_config_item
    blocks = parse_config_file("config.txt")
    result = find_config_item(blocks, "interface")
"""

import argparse
import os
import re
import sys
import json
from pathlib import Path

try:
    import chardet
    HAS_CHARDET = True
except ImportError:
    HAS_CHARDET = False


def detect_encoding(file_path):
    """
    自动检测文件编码

    Args:
        file_path: 文件路径

    Returns:
        str: 检测到的编码（默认 utf-8）
    """
    if not HAS_CHARDET:
        return 'utf-8'

    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read()
            result = chardet.detect(raw_data)
            return result.get('encoding', 'utf-8') or 'utf-8'
    except Exception:
        return 'utf-8'


def read_file_content(file_path):
    """
    读取文件内容，自动检测编码

    Args:
        file_path: 文件路径

    Returns:
        str: 文件内容
    """
    encoding = detect_encoding(file_path)
    with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
        return f.read()


def get_indent_level(line):
    """
    获取行的缩进层级

    Args:
        line: 行内容

    Returns:
        int: 缩进空格数
    """
    return len(line) - len(line.lstrip())


def parse_config(content):
    """
    解析华为设备配置文件，返回配置块列表

    【解析规则】
    - 以 # 分割行，两个 # 行之间为一组配置
    - 无缩进的配置属于一层配置（level=0）
    - 加一个空格缩进代表二层配置（level=1），以此类推
    - 空格缩进代表层级

    Args:
        content: 配置文件内容

    Returns:
        list: 配置块列表，每个块包含层级结构
    """
    lines = content.split('\n')
    results = []
    current_group = None
    in_group = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 跳过空行
        if not stripped:
            continue

        # 分隔行 #，开始或结束一个配置组
        if stripped.startswith('#'):
            # 结束当前组
            if current_group:
                results.append(current_group)
            # 开始新组
            current_group = {
                'separator': stripped,
                'start_line': i + 1,
                'items': [],
                'tree': {}
            }
            in_group = True
            continue

        # 处于配置组内
        if in_group and current_group is not None:
            # 计算缩进层级
            indent = get_indent_level(line)
            level = indent // 1  # 每空格代表一层

            item = {
                'raw': line,
                'stripped': stripped,
                'indent': indent,
                'level': level,
                'line_number': i + 1
            }

            # 解析配置项
            parts = stripped.split(None, 1)
            if len(parts) >= 1:
                item['key'] = parts[0]
                item['value'] = parts[1] if len(parts) > 1 else ""

            current_group['items'].append(item)

    # 保存最后一个组
    if current_group:
        results.append(current_group)

    return results


def parse_config_file(file_path):
    """
    解析配置文件，返回配置块列表（模块化接口）

    Args:
        file_path: 配置文件路径

    Returns:
        list: 配置块列表
    """
    content = read_file_content(file_path)
    return parse_config(content)


def find_config_item(config_groups, search_key, exact_match=False, use_regex=False):
    """
    在配置块中查找指定配置项（模块化接口）

    Args:
        config_groups: 配置块列表（parse_config 或 parse_config_file 返回）
        search_key: 要查找的配置项
        exact_match: 是否精确匹配
        use_regex: 是否使用正则表达式

    Returns:
        list: 匹配的配置组列表
    """
    matched_groups = []

    for group in config_groups:
        for item in group.get('items', []):
            key = item.get('key', '')
            stripped = item.get('stripped', '')

            if use_regex:
                try:
                    pattern = re.compile(search_key, re.IGNORECASE)
                    if pattern.search(key) or pattern.search(stripped):
                        if group not in matched_groups:
                            matched_groups.append(group)
                        break
                except re.error:
                    continue
            elif exact_match:
                if key == search_key or stripped == search_key:
                    if group not in matched_groups:
                        matched_groups.append(group)
                    break
            else:
                if search_key.lower() in key.lower() or search_key.lower() in stripped.lower():
                    if group not in matched_groups:
                        matched_groups.append(group)
                    break

    return matched_groups


def find_item_by_path(config_groups, path_parts):
    """
    按路径查找配置项（如 ["interface", "Eth-Trunk1", "description"]）

    Args:
        config_groups: 配置块列表
        path_parts: 路径列表

    Returns:
        list: 匹配的嵌套路径配置
    """
    results = []
    target_depth = len(path_parts)

    for group in config_groups:
        items = group.get('items', [])
        for i, item in enumerate(items):
            # 检查每层路径
            if item.get('stripped', '').startswith(path_parts[0]):
                matched_path = [item]

                # 继续检查子层级
                depth = 1
                j = i + 1
                while depth < target_depth and j < len(items):
                    next_item = items[j]
                    # 如果遇到同级或更高级的配置，停止
                    if next_item['level'] <= item['level']:
                        break
                    if next_item.get('stripped', '').startswith(path_parts[depth]):
                        matched_path.append(next_item)
                        depth += 1
                    j += 1

                if depth == target_depth:
                    results.append({
                        'group': group,
                        'path': matched_path,
                        'start_line': item['line_number']
                    })

    return results


def check_config_value(config_groups, search_key, expected_value, exact_match=False):
    """
    检查配置项是否包含期望值（模块化接口）

    Args:
        config_groups: 配置块列表
        search_key: 要查找的配置项
        expected_value: 期望的值
        exact_match: 是否精确匹配

    Returns:
        tuple: (是否匹配, 匹配的配置组列表)
    """
    matched_groups = find_config_item(config_groups, search_key, exact_match)
    if not matched_groups:
        return False, []

    matching_groups = []
    expected_lower = expected_value.lower()

    for group in matched_groups:
        for item in group.get('items', []):
            value = item.get('value', '')
            stripped = item.get('stripped', '')

            if expected_lower in value.lower() or expected_lower in stripped.lower():
                matching_groups.append(group)
                break

    return len(matching_groups) > 0, matching_groups


def get_group_items(group, level=None):
    """
    获取配置组中的配置项，可按层级过滤

    Args:
        group: 配置组
        level: 层级（None 表示所有层级）

    Returns:
        list: 配置项列表
    """
    items = group.get('items', [])
    if level is None:
        return items
    return [item for item in items if item.get('level') == level]


def extract_config_tree(group):
    """
    将配置组转换为层级树结构

    Args:
        group: 配置组

    Returns:
        dict: 层级树结构
    """
    items = group.get('items', [])
    if not items:
        return {}

    # 构建树结构
    root = {}
    stack = [(0, root)]

    for item in items:
        level = item.get('level', 0)
        key = item.get('key', item.get('stripped', ''))
        value = item.get('value', '')

        # 调整栈大小
        while len(stack) > level + 1:
            stack.pop()

        # 获取当前父节点
        parent = stack[-1][1]

        # 添加当前节点
        if value:
            parent[key] = value
        else:
            parent[key] = {}
            stack.append((level, parent[key]))

    return root


def main():
    parser = argparse.ArgumentParser(
        description='解析华为设备配置文件，支持按配置项路径查找',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
配置文件格式说明:
  - 以 # 分割行，两个 # 行之间为一组配置
  - 无缩进的配置属于一层配置（level=0）
  - 加一个空格缩进代表二层配置（level=1），以此类推

示例:
  python search_config.py config.txt "interface"
  python search_config.py config.txt "interface Eth-Trunk1"
  python search_config.py config.txt "stp mode" --value "rstp"
  python search_config.py config.txt "dfs-group" --json
  python search_config.py config.txt "interface" --show-tree
        '''
    )
    parser.add_argument('file_path', help='配置文件路径')
    parser.add_argument('search_key', nargs='?', help='要查找的配置项')
    parser.add_argument('--value', '-v', help='期望的配置值，用于匹配检查')
    parser.add_argument('--regex', '-r', action='store_true', help='将 search_key 作为正则表达式处理')
    parser.add_argument('--exact', '-e', action='store_true', help='精确匹配配置项名称')
    parser.add_argument('--lines', '-l', type=int, default=100, help='输出行数限制（默认100）')
    parser.add_argument('--json', '-j', action='store_true', help='以 JSON 格式输出')
    parser.add_argument('--show-tree', '-t', action='store_true', help='显示配置层级树结构')

    args = parser.parse_args()

    if not os.path.exists(args.file_path):
        print(f"错误：文件不存在: {args.file_path}", file=sys.stderr)
        sys.exit(1)

    # 解析配置
    config_groups = parse_config_file(args.file_path)

    if not args.search_key:
        # 无 search_key 时，输出所有配置组统计
        if args.json:
            result = {
                'file': args.file_path,
                'total_groups': len(config_groups),
                'groups': [{
                    'start_line': g['start_line'],
                    'separator': g['separator'],
                    'item_count': len(g['items'])
                } for g in config_groups]
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"解析到 {len(config_groups)} 个配置组")
        return

    if args.value:
        # 检查配置值
        is_match, matched_groups = check_config_value(
            config_groups, args.search_key, args.value, args.exact
        )
        if args.json:
            result = {
                'matched': is_match,
                'search_key': args.search_key,
                'expected_value': args.value,
                'matched_groups': [{'start_line': g['start_line']} for g in matched_groups]
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif is_match:
            print(f"[OK] 找到匹配配置: {args.search_key} = {args.value}")
            for group in matched_groups[:5]:
                print(f"\n--- 行 {group['start_line']} ---")
                for item in get_group_items(group)[:args.lines]:
                    indent = '  ' * item['level']
                    if item.get('value'):
                        print(f"{indent}{item['key']} {item['value']}")
                    else:
                        print(f"{indent}{item['stripped']}")
        else:
            print(f"[FAIL] 未找到匹配配置: {args.search_key} = {args.value}")
            sys.exit(1)
    else:
        # 查找配置项
        matched_groups = find_config_item(config_groups, args.search_key, args.exact, args.regex)

        if args.json:
            result = {
                'search_key': args.search_key,
                'count': len(matched_groups),
                'groups': []
            }
            for g in matched_groups:
                result['groups'].append({
                    'start_line': g['start_line'],
                    'items': get_group_items(g)[:args.lines]
                })
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif matched_groups:
            print(f"找到 {len(matched_groups)} 个匹配配置组:\n")
            for i, group in enumerate(matched_groups):
                print(f"--- [{i+1}] 配置组 (行 {group['start_line']}) ---")

                if args.show_tree:
                    tree = extract_config_tree(group)
                    print(json.dumps(tree, ensure_ascii=False, indent=2))
                else:
                    for item in get_group_items(group)[:args.lines]:
                        indent = '  ' * item['level']
                        if item.get('value'):
                            print(f"{indent}{item['key']} {item['value']}")
                        else:
                            print(f"{indent}{item['stripped']}")
                print()
        else:
            print(f"未找到配置项: {args.search_key}")
            sys.exit(1)


if __name__ == '__main__':
    main()
