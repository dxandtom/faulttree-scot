#!/usr/bin/env python3
"""
search_diag_txt.py —— 在 DIAG 类型的 TXT/LOG 文件中根据命令行和关键字查找回显结果

支持 DIAG 类型的 .txt 和 .log 后缀文件。

用法:
    python search_diag_txt.py <文件路径> <命令行> [--keyword <关键字>] [--max-lines <最大行数>]

示例:
    # 查找 display interface 命令的回显（.log 文件）
    python search_diag_txt.py "EHYI001-CO-01_13JAN.log" "display interface"

    # 查找 display interface 命令的回显（.txt 文件）
    python search_diag_txt.py "EHYI001-CO-01_13JAN.txt" "display interface"

    # 查找 display interface 中包含 "GigabitEthernet" 的回显
    python search_diag_txt.py "EHYI001-CO-01_13JAN.log" "display interface" --keyword "GigabitEthernet"

    # 查找多条命令回显
    python search_diag_txt.py "EHYI001-CO-01_13JAN.log" "display alarm active verbose" --keyword "mplsLdpSessionDown"
"""

import argparse
import os
import re
import sys
from pathlib import Path


def find_cmd_output(lines, cmd_pattern, max_lines=1000):
    """
    在文本行中查找命令及其输出

    支持4种格式:
    1. 上下分隔线: =========display xxx======== + 输出
    2. 内嵌命令: ===display xxx=== + 输出
    3. Command: 格式: Command: display xxx + User/Time
    4. 命令提示符: <Switch>display xxx + 输出

    Args:
        lines: 文件的所有行
        cmd_pattern: 命令行匹配模式（支持模糊匹配和正则）
        max_lines: 单次命令输出的最大行数

    Returns:
        [(起始行号, 命令行, 输出内容), ...]
    """
    results = []
    n = len(lines)

    # 编译命令匹配正则
    try:
        cmd_regex = re.compile(cmd_pattern, re.IGNORECASE)
    except re.error:
        # 如果是无效的正则，当作普通字符串模糊匹配
        cmd_regex = re.compile(re.escape(cmd_pattern), re.IGNORECASE)

    sep_pattern = re.compile(r'^=+$')
    cmd_standalone_pattern = re.compile(r'^\s*(display\s+[^=]+?)\s*$', re.I)
    inline_cmd_pattern = re.compile(r'^=+\s*(display\s+[^=]+?)\s*=+$', re.I)
    prompt_pattern = re.compile(r'^<(\S+)>\s*(display\s+\S+)', re.I)
    patterns = (sep_pattern, cmd_standalone_pattern, inline_cmd_pattern, prompt_pattern)

    i = 0
    while i < n:
        hit = _match_cmd_at(lines, i, n, cmd_regex, patterns)
        if hit is None:
            i += 1
            continue
        cmd_match, start, out_start, stop_sep, stop_prompt = hit
        # 收集输出
        output_lines, j = _read_output(lines, out_start, n, max_lines, sep_pattern, stop_sep, stop_prompt)

        results.append({
            'start_line': start + 1,
            'cmd': cmd_match,
            'output': ''.join(output_lines),
            'output_lines': len(output_lines)
        })
        i = j

    return results


def _read_output(lines, start, n, max_lines, sep_pattern, stop_sep, stop_prompt):
    """从 start 行起收集回显,最多 max_lines 行;遇到新段落起点就停(Command: 一律认,
    分隔线/提示符按 stop_sep/stop_prompt 决定是否认)。返回 (回显行列表, 停止位置)。"""
    output_lines = []
    j = start
    while j < n and j < start + max_lines:
        next_line = lines[j].strip()
        # 遇到新命令/分隔线/提示符就停止
        if (stop_sep and sep_pattern.match(next_line)) or \
           (stop_prompt and next_line.startswith('<')) or \
           next_line.startswith('Command:'):
            break
        output_lines.append(lines[j].rstrip('\n'))
        j += 1
    return output_lines, j


def _match_cmd_at(lines, i, n, cmd_regex, patterns):
    """在第 i 行按四种格式依次识别匹配 cmd_regex 的命令;命中返回
    (命令文本, 命令所在行, 回显起始行, 回显是否遇分隔线停, 是否遇提示符停),否则 None。"""
    sep_pattern, cmd_standalone_pattern, inline_cmd_pattern, prompt_pattern = patterns
    line = lines[i].strip()

    # 格式1: 上下分隔线 =========display xxx======== (命令在分隔线之后)
    if i + 2 < n:
        next_line1 = lines[i + 1].strip()
        next_line2 = lines[i + 2].strip() if i + 2 < n else ''

        if sep_pattern.match(line) and sep_pattern.match(next_line2):
            # line1 = 等号分隔线, line2 = 命令, line3 = 等号分隔线
            cmd_m = cmd_standalone_pattern.match(next_line1)
            if cmd_m and cmd_regex.search(cmd_m.group(1)):
                # 命令在第二行;跳过分隔线和命令,从第 i+3 行开始读输出
                return next_line1.strip(), i + 1, i + 3, True, True

    # 格式2: 内嵌命令格式 ===display xxx=== (同一行)
    if sep_pattern.match(line):
        cmd_m = inline_cmd_pattern.match(line)
        if cmd_m and cmd_regex.search(cmd_m.group(1)):
            return cmd_m.group(1).strip(), i, i + 1, True, True

    # 格式3: Command: display xxx 格式
    if line.startswith('Command:'):
        # 提取命令部分
        cmd_part = line[len('Command:'):].strip()
        if cmd_regex.search(cmd_part):
            # 回显只在遇到新命令(Command:)时停止
            return line, i, i + 1, False, False

    # 格式4: 命令提示符格式 <Switch>display xxx
    if line.startswith('<'):
        cmd_m = prompt_pattern.match(line)
        if cmd_m and cmd_regex.search(cmd_m.group(2)):
            return line, i, i + 1, False, True

    return None


def filter_by_keyword(results, keyword):
    """过滤包含关键字的结果"""
    if not keyword:
        return results

    keyword_lower = keyword.lower()
    filtered = []

    for item in results:
        if keyword_lower in item['output'].lower():
            filtered.append(item)

    return filtered


def main():
    parser = argparse.ArgumentParser(
        description='在 TXT 文件中根据命令行和关键字查找回显结果'
    )
    parser.add_argument('txt_file', help='TXT 文件路径')
    parser.add_argument('cmd_pattern', help='命令行匹配模式（支持模糊匹配和正则）')
    parser.add_argument('--keyword', '-k', help='关键字过滤（可选）')
    parser.add_argument('--max-lines', '-m', type=int, default=1000,
                        help='单次命令输出的最大行数（默认1000）')
    parser.add_argument('--json', action='store_true', help='以 JSON 格式输出')

    args = parser.parse_args()

    if not os.path.exists(args.txt_file):
        print(f'错误: 文件不存在: {args.txt_file}', file=sys.stderr)
        sys.exit(1)

    with open(args.txt_file, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    results = find_cmd_output(lines, args.cmd_pattern, args.max_lines)

    if args.keyword:
        results = filter_by_keyword(results, args.keyword)

    if args.json:
        import json
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

    return 0


if __name__ == '__main__':
    sys.exit(main())
