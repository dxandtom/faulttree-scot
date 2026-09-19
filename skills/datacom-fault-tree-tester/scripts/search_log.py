#!/usr/bin/env python3
"""
search_log.py - 解析华为设备日志文件（logbuffer/trapbuffer），支持按时间、模块、关键字过滤

支持 .txt 和 .log 后缀的日志文件。

【优化点】
1. 添加编码自动检测（chardet），支持 GBK/GB2312/UTF-8 等多种编码
2. 添加模块化接口（parse_log_file, filter_logs 等函数可被外部调用）
3. 添加批量文件解析支持
4. 添加端口/接口提取功能，方便统计 UP/DOWN 次数
5. 输出格式优化，支持 JSON 格式返回

用法:
    # 命令行
    python search_log.py <文件路径> [--keyword <关键字>] [--module <模块名>]

    # 作为模块调用
    from search_log import parse_log_file, filter_logs, count_port_events
    logs = parse_log_file("logbuffer.txt")
    filtered = filter_logs(logs, keyword="linkDown")
    port_stats = count_port_events(logs, "25GE1/0/43")
"""

import argparse
import os
import re
import sys
import json
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict

try:
    import chardet
    HAS_CHARDET = True
except ImportError:
    HAS_CHARDET = False


# 日志格式正则
# Jul 23 2020 10:13:15-05:00 Switch01 %%01MSTP/4/PORT_STATE_FORWARDING(l):...
LOG_PATTERN = re.compile(
    r'^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2})\s+'  # 时间戳
    r'(\S+)\s+'  # 设备名
    r'%%(\w+)/(\d+)/([\w_]+)\((\w)\):'  # 模块/级别/信息名(类型)
    r'(.*)$'  # 详细信息
)

# 陷阱格式正则（带 OID）
TRAP_PATTERN = re.compile(
    r'^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2})\s+'  # 时间戳
    r'(\S+)\s+'  # 设备名
    r'%%(\w+)/(\d+)/([\w_]+)\((\w)\):'  # 模块/级别/信息名(类型)
    r'(?:OID=[\d.]+;)?'  # 可选的 OID
    r'(.*)$'  # 详细信息
)

# 端口名提取正则
PORT_PATTERN = re.compile(r'(?:ifName|PortName|Port|Interface)=([^\s,)]+)')


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


def parse_log_line(line):
    """
    解析单条日志行

    Args:
        line: 日志行内容

    Returns:
        dict: 解析后的日志信息，解析失败返回 None
    """
    for pattern in [LOG_PATTERN, TRAP_PATTERN]:
        match = pattern.match(line.strip())
        if match:
            return {
                'timestamp': match.group(1),
                'device': match.group(2),
                'module': match.group(3),
                'level': match.group(4),
                'info_name': match.group(5),
                'log_type': match.group(6),
                'detail': match.group(7) if len(match.groups()) > 6 else match.group(6),
                'raw': line.rstrip()
            }
    return None


def parse_log_file(file_path):
    """
    解析日志文件，返回日志条目列表（模块化接口）

    Args:
        file_path: 日志文件路径

    Returns:
        list: 日志条目列表
    """
    lines = read_file_lines(file_path)

    # 跳过文件头（display logbuffer/trapbuffer 命令输出）
    start_idx = 0
    for i, line in enumerate(lines):
        if 'Logging buffer configuration' in line or 'Trapping buffer configuration' in line:
            start_idx = i
            break

    logs = []
    for line in lines[start_idx:]:
        parsed = parse_log_line(line)
        if parsed:
            logs.append(parsed)

    return logs


def parse_time(time_str):
    """
    解析日志时间字符串

    Args:
        time_str: 时间字符串（如 "Jul 23 2020 10:13:15-05:00"）

    Returns:
        datetime: 解析后的时间对象，解析失败返回 None
    """
    try:
        # 去掉时区信息
        time_part = time_str.split('-')[0].strip()
        return datetime.strptime(time_part, '%b %d %Y %H:%M:%S')
    except ValueError:
        return None


def filter_logs(logs, keyword=None, module=None, severity=None,
                start_time=None, end_time=None, log_type=None, info_name=None):
    """
    过滤日志条目（模块化接口）

    Args:
        logs: 日志条目列表
        keyword: 关键字（搜索 detail 和 info_name）
        module: 模块名
        severity: 级别（如 4=warning, 3=event）
        start_time: 开始时间字符串
        end_time: 结束时间字符串
        log_type: 日志类型 (l=logbuffer, t=trapbuffer)
        info_name: 信息名称（如 linkDown, PORT_STATE_FORWARDING）

    Returns:
        list: 过滤后的日志列表
    """
    results = []

    # 解析时间条件
    start_dt = parse_time(start_time) if start_time else None
    end_dt = parse_time(end_time) if end_time else None

    for log in logs:
        if _log_matches(log, keyword, module, info_name, severity, log_type, start_dt, end_dt):
            results.append(log)

    return results


def _severity_ok(log, severity):
    """级别过滤:纯数字级别按值比对;数字转换失败时按名称查表,查不到就不过滤。"""
    try:
        sev_level = int(severity) if severity.isdigit() else None
        if sev_level is not None and log['level'] != str(sev_level):
            return False
    except ValueError:
        level_map = {'emergency': 0, 'alert': 1, 'critical': 2, 'error': 3,
                     'warning': 4, 'notice': 5, 'info': 6, 'debug': 7}
        if severity.lower() in level_map:
            if log['level'] != str(level_map[severity.lower()]):
                return False
    return True


def _time_ok(log, start_dt, end_dt):
    """时间过滤:日志时间解析得出才比对区间,解析不出的放行。"""
    log_dt = parse_time(log['timestamp'])
    if log_dt:
        if start_dt and log_dt < start_dt:
            return False
        if end_dt and log_dt > end_dt:
            return False
    return True


def _log_matches(log, keyword, module, info_name, severity, log_type, start_dt, end_dt):
    """一条日志是否通过全部过滤条件(按关键字→模块→信息名→级别→类型→时间顺序短路)。"""
    # 关键字过滤
    if keyword:
        keyword_lower = keyword.lower()
        if keyword_lower not in log['detail'].lower() and \
           keyword_lower not in log['info_name'].lower():
            return False

    # 模块过滤
    if module and module.lower() not in log['module'].lower():
        return False

    # 信息名过滤
    if info_name and info_name.lower() not in log['info_name'].lower():
        return False

    # 级别过滤
    if severity and not _severity_ok(log, severity):
        return False

    # 日志类型过滤
    if log_type and log['log_type'] != log_type:
        return False

    # 时间过滤
    if (start_dt or end_dt) and not _time_ok(log, start_dt, end_dt):
        return False

    return True


def extract_port_name(log):
    """
    从日志详情中提取端口名

    Args:
        log: 日志条目

    Returns:
        str: 端口名，未找到返回 None
    """
    match = PORT_PATTERN.search(log.get('detail', ''))
    return match.group(1) if match else None


def count_events(logs, field='module'):
    """
    统计日志中各字段的出现次数（模块化接口）

    Args:
        logs: 日志条目列表
        field: 统计字段（module/level/info_name/device/log_type）

    Returns:
        Counter: 各字段值的计数
    """
    return Counter(log.get(field, 'unknown') for log in logs)


def count_port_events(logs, port_name=None):
    """
    统计端口相关事件（模块化接口）

    Args:
        logs: 日志条目列表
        port_name: 端口名，不指定则统计所有端口

    Returns:
        dict: {端口名: [事件列表]}
    """
    port_events = defaultdict(list)

    for log in logs:
        port = extract_port_name(log)
        if port:
            if port_name is None or port == port_name:
                port_events[port].append(log)

    return dict(port_events)


def count_port_updown(logs, port_name=None):
    """
    统计端口 UP/DOWN 切换次数（模块化接口）

    Args:
        logs: 日志条目列表
        port_name: 端口名，不指定则统计所有端口

    Returns:
        dict: {端口名: {'up': N, 'down': N, 'total': N}}
    """
    port_stats = defaultdict(lambda: {'up': 0, 'down': 0, 'up_events': [], 'down_events': []})

    for log in logs:
        port = extract_port_name(log)
        if not port:
            continue
        if port_name and port != port_name:
            continue

        info = log['info_name'].lower()
        if 'linkdown' in info or 'link_down' in info or 'down_active' in info:
            port_stats[port]['down'] += 1
            port_stats[port]['down_events'].append(log)
        elif 'linkup' in info or 'link_up' in info or 'down_clear' in info or 'up' in info:
            port_stats[port]['up'] += 1
            port_stats[port]['up_events'].append(log)

    # 计算总切换次数
    result = {}
    for port, stats in port_stats.items():
        result[port] = {
            'up': stats['up'],
            'down': stats['down'],
            'total': stats['up'] + stats['down'],
            'up_events': stats['up_events'],
            'down_events': stats['down_events']
        }

    return result


def main():
    parser = argparse.ArgumentParser(
        description='解析华为设备日志文件（logbuffer/trapbuffer）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python search_log.py logbuffer.txt --keyword "linkDown"
  python search_log.py logbuffer.txt --module "MSTP"
  python search_log.py trapbuffer.txt --severity "Critical"
  python search_log.py logbuffer.txt --count --group-by module
  python search_log.py logbuffer.txt --port-stats 25GE1/0/43
  python search_log.py logbuffer.txt --updown 25GE1/0/43 --json
        '''
    )
    parser.add_argument('file_path', help='日志文件路径')
    parser.add_argument('--keyword', '-k', help='搜索关键字')
    parser.add_argument('--module', '-m', help='模块名（如 MSTP、LLDP、IFNET）')
    parser.add_argument('--info-name', '-n', help='信息名称（如 linkDown、PORT_STATE_FORWARDING）')
    parser.add_argument('--severity', '-s', help='日志级别（0-7 或名称如 Critical）')
    parser.add_argument('--start-time', help='开始时间（如 "Jul 22 2020"）')
    parser.add_argument('--end-time', help='结束时间（如 "Jul 23 2020"）')
    parser.add_argument('--type', '-t', choices=['l', 't'], help='日志类型：l=logbuffer, t=trapbuffer')
    parser.add_argument('--count', '-c', action='store_true', help='统计匹配数量')
    parser.add_argument('--group-by', choices=['module', 'level', 'info_name', 'device'],
                        help='按指定字段分组统计')
    parser.add_argument('--context', type=int, default=0,
                        help='显示匹配行的上下文行数')
    parser.add_argument('--port-stats', help='统计指定端口的事件')
    parser.add_argument('--updown', help='统计指定端口的 UP/DOWN 切换次数')
    parser.add_argument('--json', '-j', action='store_true', help='以 JSON 格式输出')

    args = parser.parse_args()

    if not os.path.exists(args.file_path):
        print(f"错误：文件不存在: {args.file_path}", file=sys.stderr)
        sys.exit(1)

    logs = parse_log_file(args.file_path)
    print(f"解析到 {len(logs)} 条日志记录")

    # 过滤
    filtered = filter_logs(
        logs,
        keyword=args.keyword,
        module=args.module,
        severity=args.severity,
        start_time=args.start_time,
        end_time=args.end_time,
        log_type=args.type,
        info_name=args.info_name
    )

    print(f"匹配 {len(filtered)} 条记录")

    # UP/DOWN 统计
    if args.updown:
        _print_updown(args, logs)
        return

    # 端口统计
    if args.port_stats:
        _print_port_stats(args, logs)
        return

    # 统计模式
    if args.count:
        _print_count(args, filtered)
        return

    # JSON 输出
    if args.json:
        print(json.dumps(filtered, ensure_ascii=False, indent=2))
        return

    # 显示结果
    _print_matches(args, logs, filtered)


def _print_updown(args, logs):
    """--updown:打印指定端口的 UP/DOWN 切换统计(--json 时整份 JSON)。"""
    stats = count_port_updown(logs, args.updown)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        if args.updown in stats:
            s = stats[args.updown]
            print(f"\n端口 {args.updown} 的 UP/DOWN 统计:")
            print(f"  UP 事件: {s['up']} 次")
            print(f"  DOWN 事件: {s['down']} 次")
            print(f"  总切换次数: {s['total']} 次")
            if s['down'] > 0:
                print(f"\n  最近 DOWN 事件:")
                for e in s['down_events'][:3]:
                    print(f"    {e['timestamp']} - {e['info_name']}")
        else:
            print(f"未找到端口 {args.updown} 的事件记录")


def _print_port_stats(args, logs):
    """--port-stats:打印指定端口的事件记录(最多 10 条;--json 时整份 JSON)。"""
    stats = count_port_events(logs, args.port_stats)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        if args.port_stats in stats:
            events = stats[args.port_stats]
            print(f"\n端口 {args.port_stats} 共有 {len(events)} 条事件记录")
            for e in events[:10]:
                print(f"  {e['timestamp']} {e['module']}/{e['info_name']}")
        else:
            print(f"未找到端口 {args.port_stats} 的事件记录")


def _print_count(args, filtered):
    """--count:按 --group-by 字段分组统计(前 20 项),没给分组字段就只打总数。"""
    if args.group_by:
        counter = count_events(filtered, args.group_by)
        print(f"\n按 {args.group_by} 统计:")
        for key, cnt in counter.most_common(20):
            print(f"  {key}: {cnt}")
    else:
        print(f"\n总计: {len(filtered)} 条")


def _print_matches(args, logs, filtered):
    """默认展示:--context>0 带上下文列前 20 条,否则逐条列前 100 条;没有匹配则报错退出。"""
    if filtered:
        if args.context > 0:
            raw_lines = [log['raw'] for log in logs]
            for log in filtered[:20]:
                orig_idx = logs.index(log)
                start = max(0, orig_idx - args.context)
                end = min(len(raw_lines), orig_idx + args.context + 1)
                print(f"\n--- 行 {orig_idx+1} ---")
                for j in range(start, end):
                    marker = ">>> " if j == orig_idx else "    "
                    print(f"{marker}{raw_lines[j].rstrip()}")
        else:
            for i, log in enumerate(filtered[:100]):
                print(f"\n[{i+1}] {log['timestamp']} {log['device']} "
                      f"%%{log['module']}/{log['level']}/{log['info_name']}({log['log_type']})")
                print(f"    {log['detail']}")

            if len(filtered) > 100:
                print(f"\n... 还有 {len(filtered) - 100} 条记录未显示")
    else:
        print("未找到匹配的日志记录")
        sys.exit(1)


if __name__ == '__main__':
    main()
