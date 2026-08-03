#!/usr/bin/env python3
"""
fault_check_BFD震荡.py

故障树节点：BFD震荡
判断逻辑：
  在 display bfd session all verbose 命令回显中：
  1. State = Down
  2. Bind Interface 匹配 Tunnel(x/x/x) 格式

命中条件：State == Down 且 Bind Interface 匹配 Tunnel(x/x/x)

数据来源：CMD/ 目录
"""

import os
import sys
import json
import re
from pathlib import Path

# 添加 scripts 目录到路径以复用通用能力
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from search_cmd import find_cmd_output


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


def find_bfd_session_blocks(lines):
    """
    查找 BFD session 块

    返回格式：
    [
        {
            "state": "Down",
            "name": "TELSPToAcc1",
            "bind_interface": "Tunnel0/0/1",
            "bfd_bind_type": "TE_LSP",
            ...
        },
        ...
    ]
    """
    blocks = []
    current_block = {}
    in_block = False
    after_header = False  # 标记是否在头部后的分隔线之后

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 检查是否是分隔线
        if "----" in stripped and len(stripped) > 10:
            if after_header:
                # 第二个分隔线，块结束
                if current_block and in_block:
                    blocks.append(current_block)
                    current_block = {}
                    in_block = False
                after_header = False
            else:
                # 第一个分隔线（头部后的分隔），标记并继续
                after_header = True
            continue

        # 检查是否是新的 session 块开始 (State + Name 在同一行)
        if "State : " in stripped and "Name :" in stripped:
            # 保存上一个 block
            if current_block and in_block:
                blocks.append(current_block)
            current_block = {}
            in_block = True
            after_header = False
            # 立即解析这行的字段
            parts = stripped.split("Name :")
            if len(parts) >= 2:
                state_part = parts[0].replace("State :", "").strip()
                current_block["state"] = state_part
                current_block["name"] = parts[1].strip()
            continue

        if not in_block:
            continue

        # 解析各字段 - 使用正则因为 strip() 后中间空格保留
        if re.search(r'Bind Interface\s+:', stripped):
            # Bind Interface : Tunnel0/0/1      TE LSP Type            : Primary
            parts = stripped.split("TE LSP Type")
            if len(parts) >= 1:
                interface_part = re.sub(r'Bind Interface\s+:', '', parts[0]).strip()
                current_block["bind_interface"] = interface_part
        elif "BFD Bind Type" in stripped:
            # BFD Bind Type          : TE_LSP
            value = stripped.replace("BFD Bind Type", "").strip()
            if ":" in value:
                value = value.split(":")[1].strip()
            current_block["bfd_bind_type"] = value
        elif "BFD Bind Type" in stripped:
            # BFD Bind Type          : TE_LSP
            value = stripped.replace("BFD Bind Type", "").strip()
            if ":" in value:
                value = value.split(":")[1].strip()
            current_block["bfd_bind_type"] = value

    # 保存最后一个 block
    if current_block and in_block:
        blocks.append(current_block)

    return blocks


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
        cmd_dir = ne_dir / "cmd"

    if not cmd_dir.exists():
        result["details"]["error"] = "CMD directory not found"
        return result

    # 遍历 CMD 目录下的所有文件
    cmd_files = list(cmd_dir.glob("*.txt")) + list(cmd_dir.glob("*.log"))
    if not cmd_files:
        result["details"]["error"] = "No cmd files found"
        return result

    all_sessions = []

    for cmd_file in cmd_files:
        try:
            lines = read_file_lines(str(cmd_file))
            sessions = find_bfd_session_blocks(lines)
            all_sessions.extend(sessions)
        except Exception as e:
            continue

    result["details"]["total_sessions"] = len(all_sessions)

    # 筛选符合条件的 session
    matched_sessions = []
    for session in all_sessions:
        state = session.get("state", "").lower()
        bind_interface = session.get("bind_interface", "")

        # 条件A：State = Down
        state_match = state == "down"

        # 条件B：Bind Interface 匹配 Tunnel(x/x/x)
        interface_match = bool(re.match(r'Tunnel\d+/\d+/\d+', bind_interface))

        if state_match and interface_match:
            matched_sessions.append(session)

    result["details"]["matched_count"] = len(matched_sessions)

    if matched_sessions:
        result["matched"] = "true"
        result["evidence"] = matched_sessions

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

    if not data_path.exists():
        return {"error": f"Data directory not found: {data_dir}"}

    for ne_dir in data_path.iterdir():
        if ne_dir.is_dir() and not ne_dir.name.startswith('.'):
            ne_name = ne_dir.name
            results[ne_name] = check_ne(ne_dir)

    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fault_check_BFD震荡.py <data_dir>")
        sys.exit(1)

    data_dir = sys.argv[1]
    results = main(data_dir)
    print(json.dumps(results, ensure_ascii=False, indent=2))
