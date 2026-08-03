#!/usr/bin/env python3
"""
fault_check_LSP不存在.py

故障树节点：LSP不存在
判断逻辑：
  1. 用户输入期望的 FEC 地址（如 3.3.3.3/32）
  2. 解析 display mpls lsp 命令回显，获取实际 FEC 地址列表
  3. 比较：期望 FEC 不在 实际 FEC 列表中 → 故障

命中条件：期望 FEC 地址不在 LSP 表中

数据来源：CMD/ 目录

用法：
  python fault_check_LSP不存在.py <data_dir> <expected_fec>
  例如：python fault_check_LSP不存在.py "D:/data" "3.3.3.3/32"
"""

import os
import sys
import json
import re
from pathlib import Path


def detect_encoding(file_path):
    """自动检测文件编码"""
    try:
        import chardet
        with open(file_path, 'rb') as f:
            raw_data = f.read()
            result = chardet.detect(raw_data)
            return result.get('encoding', 'utf-8') or 'utf-8'
    except Exception:
        return 'utf-8'


def read_file_lines(file_path):
    """读取文件行"""
    encoding = detect_encoding(file_path)
    with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
        return f.readlines()


def parse_mpls_lsp(lines):
    """
    解析 display mpls lsp 命令回显

    返回格式：
    [
        {"fec": "2.2.2.2/32", "label_in": "NULL", "label_out": "300", "interface": "GE1/0/0"},
        ...
    ]
    """
    records = []

    for line in lines:
        stripped = line.strip()

        # 跳过空行和表头
        if not stripped:
            continue
        if "FEC In/Out" in stripped or "LSP Information" in stripped:
            continue
        if stripped.startswith("-" * 20):
            continue

        # 匹配 FEC 行：2.2.2.2/32 NULL/300 -/GE1/0/0
        # 格式：FEC LABEL_IN/LABEL_OUT IF VRF
        match = re.match(r'^(\S+)\s+(\S+)\s+(\S+)\s*$', stripped)
        if match:
            fec = match.group(1)
            label_in_out = match.group(2)
            interface = match.group(3)

            # 分割 label in/out
            if '/' in label_in_out:
                label_in, label_out = label_in_out.split('/', 1)
            else:
                label_in = label_in_out
                label_out = ""

            # 分割 interface（如有前缀）
            if interface.startswith('-/'):
                interface = interface[2:]
            elif '/' in interface:
                interface = interface.split('/', 1)[1] if interface.startswith('-') else interface

            records.append({
                "fec": fec,
                "label_in": label_in,
                "label_out": label_out,
                "interface": interface
            })

    return records


def check_ne(ne_dir, expected_fec):
    """
    检查单个网元的数据

    Args:
        ne_dir: 网元目录路径
        expected_fec: 用户期望的 FEC 地址

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
        cmd_dir = ne_dir / "cmd"

    if not cmd_dir.exists():
        result["details"]["error"] = "CMD directory not found"
        return result

    # 遍历 CMD 目录下的所有文件
    cmd_files = list(cmd_dir.glob("*.txt")) + list(cmd_dir.glob("*.log"))
    if not cmd_files:
        result["details"]["error"] = "No cmd files found"
        return result

    # 解析所有 LSP 记录
    all_lsp_records = []
    for cmd_file in cmd_files:
        try:
            lines = read_file_lines(str(cmd_file))
            # 查找 display mpls lsp 命令输出
            in_lsp_section = False
            lsp_lines = []
            for line in lines:
                if "display mpls lsp" in line:
                    in_lsp_section = True
                    continue
                if in_lsp_section:
                    lsp_lines.append(line)
                    # 遇到下一个命令提示符则停止
                    if line.strip().startswith('<'):
                        break
            if lsp_lines:
                records = parse_mpls_lsp(lsp_lines)
                all_lsp_records.extend(records)
        except Exception as e:
            continue

    # 提取实际 FEC 列表
    actual_fecs = [r["fec"] for r in all_lsp_records]

    result["details"]["total_fec_count"] = len(actual_fecs)
    result["details"]["actual_fecs"] = actual_fecs
    result["details"]["expected_fec"] = expected_fec

    # 检查期望的 FEC 是否在 LSP 表中
    if expected_fec not in actual_fecs:
        # 期望 FEC 不在 LSP 表中 -> 故障
        result["matched"] = "true"
        result["evidence"].append({
            "expected_fec": expected_fec,
            "actual_fecs": actual_fecs,
            "message": f"期望的 FEC {expected_fec} 不在 LSP 表中"
        })

    return result


def main(data_dir, expected_fec):
    """
    主函数：遍历所有网元目录，执行故障检查

    Args:
        data_dir: 测试数据根目录
        expected_fec: 用户期望的 FEC 地址

    Returns:
        dict: 检查结果
    """
    data_path = Path(data_dir)
    results = {}

    if not data_path.exists():
        return {"error": f"Data directory not found: {data_dir}"}

    for ne_dir in data_path.iterdir():
        if ne_dir.is_dir() and not ne_dir.name.startswith('.'):
            ne_name = ne_dir.name
            results[ne_name] = check_ne(ne_dir, expected_fec)

    return results


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python fault_check_LSP不存在.py <data_dir> <expected_fec>")
        print("例如：python fault_check_LSP不存在.py \"D:/data\" \"3.3.3.3/32\"")
        sys.exit(1)

    data_dir = sys.argv[1]
    expected_fec = sys.argv[2]
    results = main(data_dir, expected_fec)
    print(json.dumps(results, ensure_ascii=False, indent=2))
