#!/usr/bin/env python3
"""
fault_check_LDP会话震荡.py

故障树节点：LDP会话震荡
判断逻辑：
  1. 解析 display mpls ldp session 获取所有 PeerID 列表
  2. 解析 display mpls ldp event session-down 获取所有 PeerID 列表
  3. PeerID 同时出现在两个列表中 → 故障

命中条件：PeerID 在 Session 表 且 session-down 事件中同时出现

数据来源：CMD/ 目录
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


def parse_ldp_session(lines):
    """
    解析 display mpls ldp session 命令回显

    返回格式：
    {
        "peerids": ["1.1.1.1:0", "3.3.3.9:0"],
        "sessions": [
            {"peerid": "1.1.1.1:0", "status": "Operational", "ssnrole": "Passive", "ssnage": "0001:02:53"},
            ...
        ]
    }
    """
    result = {
        "peerids": [],
        "sessions": []
    }

    in_session_section = False

    for line in lines:
        stripped = line.strip()

        # 跳过空行和表头
        if not stripped:
            continue
        if "LDP Session(s) in Public Network" in stripped:
            in_session_section = True
            continue
        if "TOTAL:" in stripped:
            in_session_section = False
            continue
        if stripped.startswith("PeerID"):
            continue
        if stripped.startswith("-"):
            continue

        if not in_session_section:
            continue

        # 匹配 session 行：1.1.1.1:0 Operational DU Passive 0001:02:53 1676/1676
        match = re.match(r'^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+/\S+\s*$', stripped)
        if match:
            peerid = match.group(1)
            status = match.group(2)
            ssnrole = match.group(4)
            ssnage = match.group(5)

            result["peerids"].append(peerid)
            result["sessions"].append({
                "peerid": peerid,
                "status": status,
                "ssnrole": ssnrole,
                "ssnage": ssnage
            })

    return result


def parse_ldp_event_session_down(lines):
    """
    解析 display mpls ldp event session-down 命令回显

    返回格式：
    {
        "peerids": ["1.1.1.1:0"],
        "events": [
            {"peerid": "1.1.1.1:0", "down_date": "2026-07-29", "down_time": "21:38:57", "reason": "Receive notification"},
            ...
        ]
    }
    """
    result = {
        "peerids": [],
        "events": []
    }

    in_event_section = False

    for line in lines:
        stripped = line.strip()

        # 跳过空行和表头
        if not stripped:
            continue
        if "LDP Session Down Information" in stripped:
            in_event_section = True
            continue
        if stripped.startswith("-"):
            continue

        if not in_event_section:
            continue

        # 匹配事件行：1.1.1.1:0         2026-07-29 21:38:57+07:00        1 days, 22:50:38 L    Receive notification
        # 格式：PeerID Date Time Duration Flag Reason
        match = re.match(r'^(\S+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\S*\s+(\S+)\s+(\S+)\s+(.+)$', stripped)
        if match:
            peerid = match.group(1)
            down_date = match.group(2)
            down_time = match.group(3)
            duration = match.group(4)
            flag = match.group(5)
            reason = match.group(6)

            result["peerids"].append(peerid)
            result["events"].append({
                "peerid": peerid,
                "down_date": down_date,
                "down_time": down_time,
                "duration": duration,
                "flag": flag,
                "reason": reason
            })

    return result


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

    # 合并所有行
    all_lines = []
    for cmd_file in cmd_files:
        try:
            lines = read_file_lines(str(cmd_file))
            all_lines.extend(lines)
        except Exception as e:
            continue

    if not all_lines:
        result["details"]["error"] = "Failed to read files"
        return result

    # 解析两个命令的输出
    session_data = parse_ldp_session(all_lines)
    event_data = parse_ldp_event_session_down(all_lines)

    result["details"]["session_peerids"] = session_data["peerids"]
    result["details"]["event_peerids"] = event_data["peerids"]

    # 找出同时出现在两个列表中的 PeerID
    common_peerids = set(session_data["peerids"]) & set(event_data["peerids"])

    result["details"]["common_peerids"] = list(common_peerids)

    if common_peerids:
        result["matched"] = "true"
        # 构建证据
        for peerid in common_peerids:
            # 找到对应的 session 和 event 信息
            session_info = None
            event_info = None
            for s in session_data["sessions"]:
                if s["peerid"] == peerid:
                    session_info = s
                    break
            for e in event_data["events"]:
                if e["peerid"] == peerid:
                    event_info = e
                    break

            evidence = {"peerid": peerid}
            if session_info:
                evidence["session"] = session_info
            if event_info:
                evidence["event"] = event_info
            result["evidence"].append(evidence)

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
        print("Usage: python fault_check_LDP会话震荡.py <data_dir>")
        sys.exit(1)

    data_dir = sys.argv[1]
    results = main(data_dir)
    print(json.dumps(results, ensure_ascii=False, indent=2))
