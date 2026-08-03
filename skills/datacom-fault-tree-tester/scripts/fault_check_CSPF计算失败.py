#!/usr/bin/env python3
"""
fault_check_CSPF计算失败.py

故障树节点：CSPF计算失败
判断逻辑：
  1. 解析 display mpls te tunnel-interface last-error 命令回显
  2. 筛选 Error Code = 1 且 Occur Time 为最新的记录
  3. 命中条件：Error Code == 1 且该记录的时间戳是最新的

数据来源：CMD/ 目录
"""

import os
import sys
import json
import re
from datetime import datetime
from pathlib import Path


def detect_encoding(file_path):
    """
    自动检测文件编码
    """
    try:
        import chardet
        with open(file_path, 'rb') as f:
            raw_data = f.read()
            result = chardet.detect(raw_data)
            return result.get('encoding', 'utf-8') or 'utf-8'
    except Exception:
        return 'utf-8'


def find_cmd_in_file(file_path, cmd_keyword):
    """
    在文件中查找指定命令的输出（直接解析）

    Args:
        file_path: 文件路径
        cmd_keyword: 命令关键词（支持模糊匹配）

    Returns:
        list: 命令输出行列表
    """
    encoding = detect_encoding(file_path)
    with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
        lines = f.readlines()

    results = []
    n = len(lines)
    i = 0

    while i < n:
        line = lines[i].strip()

        # 检查是否是命令提示符行
        # 格式: <设备名> display xxx
        prompt_match = re.match(r'^<(\S+)>\s*(.*)', line)
        if prompt_match:
            device_name = prompt_match.group(1)
            cmd = prompt_match.group(2).strip()

            # 检查命令是否包含关键词
            if cmd_keyword.lower() in cmd.lower():
                # 找到了匹配的命令，收集后续行作为输出
                output_lines = []
                j = i + 1
                while j < n:
                    next_line = lines[j].strip()
                    # 检查是否到达下一个命令
                    if re.match(r'^<\S+>\s*', next_line):
                        break
                    output_lines.append(lines[j].rstrip('\n'))
                    j += 1

                if output_lines:
                    results.append({
                        "device": device_name,
                        "cmd": cmd,
                        "output_lines": output_lines
                    })
                i = j - 1
        i += 1

    return results


def parse_last_error(output_lines):
    """
    解析 display mpls te tunnel-interface last-error 命令回显

    返回格式：
    [
        {
            "tunnel": "Tunnel2",
            "error_no": 1,
            "error_code": 1,
            "occur_time": "2018-11-17 14:06:03.750",
            "error_info": "..."
        },
        ...
    ]
    """
    records = []
    current_tunnel = None
    current_error = {}

    for line in output_lines:
        line = line.strip()

        # 跳过空行
        if not line:
            continue

        # 匹配 Tunnel Name 行
        tunnel_match = re.match(r'Tunnel Name:\s*(\S+)', line)
        if tunnel_match:
            # 保存上一个 error record（如果存在）
            if current_error and current_tunnel:
                current_error["tunnel"] = current_tunnel
                records.append(current_error)
            current_tunnel = tunnel_match.group(1)
            current_error = {}
            continue

        # 匹配 Error No. 行
        error_no_match = re.match(r'Error No\.\s*:\s*(\d+)', line)
        if error_no_match:
            current_error = {"error_no": int(error_no_match.group(1))}
            continue

        # 匹配 Error Code 和 Occur Time 行
        # Error Code : 1                        Occur Time : 2018-11-17 14:06:03.750
        code_time_match = re.match(
            r'Error Code\s*:\s*(\d+).*Occur Time\s*:\s*([\d\-:.\s]+)',
            line
        )
        if code_time_match:
            current_error["error_code"] = int(code_time_match.group(1))
            current_error["occur_time"] = code_time_match.group(2).strip()
            continue

        # 匹配 Error Info 行
        info_match = re.match(r'Error Info\s*:\s*(.+)', line)
        if info_match:
            current_error["error_info"] = info_match.group(1).strip()
            continue

    # 保存最后一个 error record
    if current_error and current_tunnel:
        current_error["tunnel"] = current_tunnel
        records.append(current_error)

    return records


def find_latest_records(records):
    """
    从记录中找到最新时间的记录

    Args:
        records: 所有解析出的记录

    Returns:
        tuple: (latest_records, latest_time)
    """
    if not records:
        return [], None

    # 找到最新时间
    latest_time = None
    for r in records:
        if "occur_time" in r:
            try:
                t = datetime.strptime(r["occur_time"], "%Y-%m-%d %H:%M:%S.%f")
                if latest_time is None or t > latest_time:
                    latest_time = t
            except ValueError:
                try:
                    t = datetime.strptime(r["occur_time"], "%Y-%m-%d %H:%M:%S")
                    if latest_time is None or t > latest_time:
                        latest_time = t
                except ValueError:
                    pass

    # 筛选最新时间的记录
    latest_records = []
    for r in records:
        if "occur_time" in r:
            try:
                t = datetime.strptime(r["occur_time"], "%Y-%m-%d %H:%M:%S.%f")
                if t == latest_time:
                    latest_records.append(r)
            except ValueError:
                try:
                    t = datetime.strptime(r["occur_time"], "%Y-%m-%d %H:%M:%S")
                    if t == latest_time:
                        latest_records.append(r)
                except ValueError:
                    pass

    return latest_records, latest_time


def check_ne(ne_dir):
    """
    检查单个网元的数据

    Args:
        ne_dir: 网元目录路径

    Returns:
        dict: 检查结果
    """
    result = {
        "matched": "false",
        "evidence": [],
        "details": {}
    }

    # 查找 CMD 目录
    cmd_dir = ne_dir / "CMD"
    if not cmd_dir.exists():
        cmd_dir = ne_dir / "cmd"  # 尝试小写

    if not cmd_dir.exists():
        result["details"]["error"] = "CMD directory not found"
        return result

    # 遍历 CMD 目录下的所有文件
    cmd_files = list(cmd_dir.glob("*.txt")) + list(cmd_dir.glob("*.log"))
    if not cmd_files:
        result["details"]["error"] = "No cmd files found"
        return result

    all_records = []

    for cmd_file in cmd_files:
        # 查找 display mpls te tunnel-interface last-error 命令输出
        try:
            cmd_results = find_cmd_in_file(str(cmd_file), "display mpls te tunnel-interface last-error")
        except Exception as e:
            continue

        for cmd_result in cmd_results:
            output_lines = cmd_result.get("output_lines", [])
            records = parse_last_error(output_lines)
            all_records.extend(records)

    if not all_records:
        result["details"]["error"] = "No last-error records found"
        return result

    result["details"]["total_records"] = len(all_records)

    # 筛选 Error Code = 1 的记录
    error_code_1_records = [r for r in all_records if r.get("error_code") == 1]

    if not error_code_1_records:
        result["details"]["error_code_1_count"] = 0
        result["details"]["message"] = "No records with Error Code = 1"
        return result

    result["details"]["error_code_1_count"] = len(error_code_1_records)

    # 找到最新时间的记录
    latest_records, latest_time = find_latest_records(error_code_1_records)

    if not latest_records:
        result["details"]["message"] = "No latest records found"
        return result

    result["details"]["latest_time"] = latest_time.strftime("%Y-%m-%d %H:%M:%S.%f") if latest_time else None
    result["details"]["latest_records_count"] = len(latest_records)

    # 检查是否有 Error Code = 1 且时间最新的记录
    # 如果有，则 matched = True
    if latest_records:
        result["matched"] = "true"
        result["evidence"] = latest_records

    return result


def main(data_dir):
    """
    主函数：遍历所有网元目录，执行故障检查

    Args:
        data_dir: 测试数据根目录

    Returns:
        dict: 检查结果
    """
    data_path = Path(data_dir)
    results = {}

    # 遍历所有网元目录（data_dir 下的每个子目录为一个网元）
    if not data_path.exists():
        return {"error": f"Data directory not found: {data_dir}"}

    for ne_dir in data_path.iterdir():
        if ne_dir.is_dir() and not ne_dir.name.startswith('.'):
            ne_name = ne_dir.name
            results[ne_name] = check_ne(ne_dir)

    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fault_check_CSPF计算失败.py <data_dir>")
        sys.exit(1)

    data_dir = sys.argv[1]
    results = main(data_dir)
    print(json.dumps(results, ensure_ascii=False, indent=2))
