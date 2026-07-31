#!/usr/bin/env python3
"""
search_cmd.py - 在 CMD 类型的命令输出文件中查找指定命令的回显结果

支持 .txt 和 .log 后缀的命令输出文件，包含 display version、display interface 等命令的回显。

【优化点】
1. 添加编码自动检测（chardet），支持 GBK/GB2312/UTF-8 等多种编码
2. 添加模块化接口（find_cmd_output, find_all_cmds 等函数可被外部调用）
3. 添加批量文件解析支持
4. 优化输出格式，支持 JSON 格式返回
5. 添加命令提取辅助函数

用法:
    # 命令行
    python search_cmd.py <文件路径> <命令行> [--keyword <关键字>] [--max-lines <最大行数>]

    # 作为模块调用
    from search_cmd import find_cmd_output, find_all_cmds
    results = find_cmd_output("cmd.txt", "display interface")
    all_cmds = find_all_cmds("cmd.txt")
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
        str: 检测到的编码
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


def read_file_lines(file_path):
    """
    读取文件行，自动检测编码

    Args:
        file_path: 文件路径

    Returns:
        list: 文件行列表
    """
    encoding = detect_encoding(file_path)
    with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
        return f.readlines()


def find_cmd_output(lines_or_file, cmd_pattern, max_lines=1000):
    """
    在文本行中查找命令及其输出（模块化接口）

    支持4种格式:
    1. 上下分隔线: =========display xxx======== + 输出
    2. 内嵌命令: ===display xxx=== + 输出
    3. Command: 格式: Command: display xxx + User/Time
    4. 命令提示符: <Switch>display xxx + 输出

    Args:
        lines_or_file: 文件路径或行列表
        cmd_pattern: 命令行匹配模式（支持模糊匹配和正则）
        max_lines: 单次命令输出的最大行数

    Returns:
        list: [{start_line, cmd, output, output_lines}, ...]
    """
    # 如果是文件路径，先读取文件
    if isinstance(lines_or_file, (str, Path)):
        lines = read_file_lines(lines_or_file)
    else:
        lines = lines_or_file

    results = []
    n = len(lines)

    # 编译命令匹配正则
    try:
        cmd_regex = re.compile(cmd_pattern, re.IGNORECASE)
    except re.error:
        cmd_regex = re.compile(re.escape(cmd_pattern), re.IGNORECASE)

    sep_pattern = re.compile(r'^=+$')
    cmd_standalone_pattern = re.compile(r'^\s*(display\s+[^=]+?)\s*$', re.I)
    inline_cmd_pattern = re.compile(r'^=+\s*(display\s+[^=]+?)\s*=+$', re.I)
    prompt_pattern = re.compile(r'^<(\S+)>\s*(display\s+\S+)', re.I)

    i = 0
    while i < n:
        line = lines[i].strip()
        original_line = lines[i].rstrip('\n')

        cmd_match = None
        start = i

        # 格式1: 上下分隔线 =========display xxx========
        if i + 2 < n:
            next_line1 = lines[i + 1].strip()
            next_line2 = lines[i + 2].strip() if i + 2 < n else ''

            if sep_pattern.match(line) and sep_pattern.match(next_line2):
                cmd_m = cmd_standalone_pattern.match(next_line1)
                if cmd_m and cmd_regex.search(cmd_m.group(1)):
                    cmd_match = next_line1.strip()
                    start = i + 1
                    i = i + 3

                    output_lines = []
                    j = i
                    while j < n and j < i + max_lines:
                        next_line = lines[j].strip()
                        if sep_pattern.match(next_line) or \
                           next_line.startswith('<') or \
                           next_line.startswith('Command:'):
                            break
                        output_lines.append(lines[j].rstrip('\n'))
                        j += 1

                    results.append({
                        'start_line': start + 1,
                        'cmd': cmd_match,
                        'output': ''.join(output_lines),
                        'output_lines': len(output_lines)
                    })
                    i = j
                    continue

        # 格式2: 内嵌命令格式 ===display xxx===
        if sep_pattern.match(line):
            cmd_m = inline_cmd_pattern.match(line)
            if cmd_m and cmd_regex.search(cmd_m.group(1)):
                cmd_match = cmd_m.group(1).strip()
                start = i
                i += 1

                output_lines = []
                j = i
                while j < n and j < i + max_lines:
                    next_line = lines[j].strip()
                    if sep_pattern.match(next_line) or \
                       next_line.startswith('<') or \
                       next_line.startswith('Command:'):
                        break
                    output_lines.append(lines[j].rstrip('\n'))
                    j += 1

                results.append({
                    'start_line': start + 1,
                    'cmd': cmd_match,
                    'output': ''.join(output_lines),
                    'output_lines': len(output_lines)
                })
                i = j
                continue

        # 格式3: Command: display xxx 格式
        if line.startswith('Command:'):
            cmd_part = line[len('Command:'):].strip()
            if cmd_regex.search(cmd_part):
                cmd_match = line
                start = i
                i += 1

                output_lines = []
                j = i
                while j < n and j < i + max_lines:
                    next_line = lines[j].strip()
                    if next_line.startswith('Command:'):
                        break
                    output_lines.append(lines[j].rstrip('\n'))
                    j += 1

                results.append({
                    'start_line': start + 1,
                    'cmd': cmd_match,
                    'output': ''.join(output_lines),
                    'output_lines': len(output_lines)
                })
                i = j
                continue

        # 格式4: 命令提示符格式 <Switch>display xxx
        if line.startswith('<'):
            cmd_m = prompt_pattern.match(line)
            if cmd_m and cmd_regex.search(cmd_m.group(2)):
                cmd_match = line
                start = i
                i += 1

                output_lines = []
                j = i
                while j < n and j < i + max_lines:
                    next_line = lines[j].strip()
                    if next_line.startswith('<') or next_line.startswith('Command:'):
                        break
                    output_lines.append(lines[j].rstrip('\n'))
                    j += 1

                results.append({
                    'start_line': start + 1,
                    'cmd': cmd_match,
                    'output': ''.join(output_lines),
                    'output_lines': len(output_lines)
                })
                i = j
                continue

        i += 1

    return results


def find_all_cmds(file_path):
    """
    提取文件中所有命令及其输出（模块化接口）

    Args:
        file_path: 文件路径

    Returns:
        list: 所有命令及其输出
    """
    lines = read_file_lines(file_path)

    results = []
    n = len(lines)

    sep_pattern = re.compile(r'^=+$')
    cmd_standalone_pattern = re.compile(r'^\s*(display\s+[^\s=]+(?:\s+[^\s=]+)*)\s*$', re.I)
    inline_cmd_pattern = re.compile(r'^=+\s*(display\s+[^\s=]+(?:\s+[^\s=]+)*)\s*=+$', re.I)
    prompt_pattern = re.compile(r'^<(\S+)>\s*(display\s+\S+)', re.I)

    i = 0
    while i < n:
        line = lines[i].strip()
        cmd_match = None
        start = i

        # 格式1: 上下分隔线
        if i + 2 < n:
            next_line1 = lines[i + 1].strip()
            next_line2 = lines[i + 2].strip() if i + 2 < n else ''

            if sep_pattern.match(line) and sep_pattern.match(next_line2):
                cmd_m = cmd_standalone_pattern.match(next_line1)
                if cmd_m:
                    cmd_match = cmd_m.group(1).strip()
                    start = i + 1
                    i = i + 3

                    output_lines = []
                    j = i
                    while j < n:
                        next_line = lines[j].strip()
                        if sep_pattern.match(next_line) or \
                           next_line.startswith('<') or \
                           next_line.startswith('Command:'):
                            break
                        output_lines.append(lines[j].rstrip('\n'))
                        j += 1

                    results.append({'start_line': start + 1, 'cmd': cmd_match, 'output': ''.join(output_lines)})
                    i = j
                    continue

        # 格式2: 内嵌命令
        if sep_pattern.match(line):
            cmd_m = inline_cmd_pattern.match(line)
            if cmd_m:
                cmd_match = cmd_m.group(1).strip()
                start = i
                i += 1

                output_lines = []
                j = i
                while j < n:
                    next_line = lines[j].strip()
                    if sep_pattern.match(next_line) or \
                       next_line.startswith('<') or \
                       next_line.startswith('Command:'):
                        break
                    output_lines.append(lines[j].rstrip('\n'))
                    j += 1

                results.append({'start_line': start + 1, 'cmd': cmd_match, 'output': ''.join(output_lines)})
                i = j
                continue

        # 格式4: 命令提示符
        if line.startswith('<'):
            cmd_m = prompt_pattern.match(line)
            if cmd_m:
                cmd_match = cmd_m.group(2).strip()
                start = i
                i += 1

                output_lines = []
                j = i
                while j < n:
                    next_line = lines[j].strip()
                    if next_line.startswith('<') or next_line.startswith('Command:'):
                        break
                    output_lines.append(lines[j].rstrip('\n'))
                    j += 1

                results.append({'start_line': start + 1, 'cmd': cmd_match, 'output': ''.join(output_lines)})
                i = j
                continue

        i += 1

    return results


def filter_by_keyword(results, keyword):
    """
    过滤包含关键字的结果（模块化接口）

    Args:
        results: find_cmd_output 返回的结果
        keyword: 关键字

    Returns:
        list: 过滤后的结果
    """
    if not keyword:
        return results

    keyword_lower = keyword.lower()
    return [item for item in results if keyword_lower in item['output'].lower()]


def extract_interface_info(cmd_output):
    """
    从 display interface 命令输出中提取接口信息

    Args:
        cmd_output: 命令输出文本

    Returns:
        list: 接口信息字典列表
    """
    interfaces = []
    current_if = None
    current_info = {}

    for line in cmd_output.split('\n'):
        line = line.strip()

        # 接口名行
        if line and not line.startswith(' ') and not line.startswith('\t'):
            if current_if and current_info:
                interfaces.append(current_info)

            current_if = line.rstrip(':')
            current_info = {'name': current_if}
        elif current_if and ':' in line:
            # 配置项行
            parts = line.split(':', 1)
            if len(parts) == 2:
                key = parts[0].strip()
                value = parts[1].strip()
                current_info[key] = value

    if current_if and current_info:
        interfaces.append(current_info)

    return interfaces


def main():
    parser = argparse.ArgumentParser(
        description='在命令输出文件中查找指定命令的回显结果',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python search_cmd.py "cmd.txt" "display interface"
  python search_cmd.py "cmd.txt" "display alarm active verbose" --keyword "linkDown"
  python search_cmd.py "cmd.txt" "display" --json
  python search_cmd.py "cmd.txt" "display" --all  # 列出所有命令
        '''
    )
    parser.add_argument('file_path', help='TXT 文件路径')
    parser.add_argument('cmd_pattern', nargs='?', help='命令行匹配模式（支持模糊匹配和正则）')
    parser.add_argument('--keyword', '-k', help='关键字过滤（可选）')
    parser.add_argument('--max-lines', '-m', type=int, default=1000,
                        help='单次命令输出的最大行数（默认1000）')
    parser.add_argument('--json', '-j', action='store_true', help='以 JSON 格式输出')
    parser.add_argument('--all', '-a', action='store_true', help='列出所有命令')

    args = parser.parse_args()

    if not os.path.exists(args.file_path):
        print(f'错误: 文件不存在: {args.file_path}', file=sys.stderr)
        sys.exit(1)

    if args.all:
        # 列出所有命令
        results = find_all_cmds(args.file_path)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print(f'共找到 {len(results)} 条命令:\n')
            for i, item in enumerate(results):
                preview = item['output'][:100].replace('\n', ' ')
                if len(item['output']) > 100:
                    preview += '...'
                print(f'[{i+1}] 行{item["start_line"]}: {item["cmd"]}')
                print(f'    {preview}')
            return

    if not args.cmd_pattern:
        print('错误: 请指定命令匹配模式，或使用 --all 列出所有命令', file=sys.stderr)
        sys.exit(1)

    results = find_cmd_output(args.file_path, args.cmd_pattern, args.max_lines)

    if args.keyword:
        results = filter_by_keyword(results, args.keyword)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        if not results:
            print('未找到匹配的命令', file=sys.stderr)
            sys.exit(1)

        for i, item in enumerate(results):
            print(f'=== 结果 {i+1} (行号: {item["start_line"]}) ===')
            print(f'命令: {item["cmd"]}')
            print(f'输出 ({item["output_lines"]} 行):')
            print(item['output'][:500] if len(item['output']) > 500 else item['output'])
            if len(item['output']) > 500:
                print(f'... (省略 {len(item["output"]) - 500} 字符)')
            print()


if __name__ == '__main__':
    main()
