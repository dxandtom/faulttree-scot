#!/usr/bin/env python3
"""
fault_check_配置问题.py

故障树节点：配置问题
判断逻辑：
  A. OSPF下 opaque-capability enable 和 mpls-te enable 两个没有同时配置 = 故障
  B. ISIS下 traffic-eng 未配置 = 故障（与 cost-style wide 无关）
  C. 接口下未配置 mpls te = 故障
  D. Tunnel接口下未配置 destination = 故障

命中条件：A 或 B 或 C 或 D

数据来源：CONFIG/ 目录
"""

import os
import sys
import json
import re
from pathlib import Path

# 添加 scripts 目录到路径以复用通用能力
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from search_config import parse_config_file, find_config_item, get_group_items


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


def read_config_lines(file_path):
    """读取配置文件行"""
    encoding = detect_encoding(file_path)
    with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
        return f.readlines()


def parse_simple_config(lines):
    """
    解析简单的纯文本配置文件（无#分隔符）

    返回格式：
    {
        "ospf": [...items...],
        "isis": [...items...],
        "interface": [...items...],
        "interface Tunnel": [...items...],
    }
    """
    result = {
        "ospf": [],
        "isis": [],
        "interface": [],
        "interface Tunnel": []
    }

    current_section = None
    current_key = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 检查是否是一级配置项（协议名、interface等）
        # 一级配置项没有前导空格或只有很少空格
        indent = len(line) - len(line.lstrip())

        if indent == 0:
            # 一级配置
            parts = stripped.split(None, 1)
            key = parts[0] if parts else ""

            if key == "ospf":
                current_section = "ospf"
                current_key = key
            elif key == "isis":
                current_section = "isis"
                current_key = key
            elif key.startswith("interface"):
                current_section = "interface"
                current_key = key
            else:
                current_section = None
                current_key = None
        elif indent > 0 and current_section:
            # 子配置项
            parts = stripped.split(None, 1)
            sub_key = parts[0] if parts else ""
            sub_value = parts[1] if len(parts) > 1 else ""

            if current_section == "ospf":
                result["ospf"].append({"key": sub_key, "value": sub_value})
            elif current_section == "isis":
                result["isis"].append({"key": sub_key, "value": sub_value})
            elif current_section == "interface":
                result["interface"].append({"key": sub_key, "value": sub_value})

    return result


def check_ospf_config_simple(config_data):
    """
    检查条件A：OSPF下 opaque-capability enable 和 mpls-te enable 是否都配置

    故障条件：两个没有同时配置（配置了任意一个或都没配置）
    """
    ospf_items = config_data.get("ospf", [])
    if not ospf_items:
        # OSPF 配置不存在，两个都没配置 -> 故障
        return True, {"ospf_found": False, "opaque_capability_enable": False, "mpls_te_enable": False}

    result = {"ospf_found": True}

    # 检查 opaque-capability enable
    opaque_found = False
    mpls_te_found = False

    for item in ospf_items:
        key = item.get("key", "").lower()
        if "opaque-capability enable" in key:
            opaque_found = True
        if "mpls-te enable" in key:
            mpls_te_found = True

    result["opaque_capability_enable"] = opaque_found
    result["mpls_te_enable"] = mpls_te_found

    # 故障条件：两个没有同时配置
    is_fault = not (opaque_found and mpls_te_found)
    return is_fault, result


def check_isis_config_simple(config_data):
    """
    检查条件B：ISIS下 traffic-eng 是否配置

    故障条件：traffic-eng 未配置
    """
    isis_items = config_data.get("isis", [])
    if not isis_items:
        # ISIS 配置不存在，没有 traffic-eng 配置 -> 故障
        return True, {"isis_found": False, "traffic_eng": False, "cost_style_wide": False}

    result = {"isis_found": True}

    # 检查 traffic-eng 和 cost-style wide
    traffic_eng_found = False
    cost_style_wide_found = False

    for item in isis_items:
        key = item.get("key", "").lower()
        if "traffic-eng" in key:
            traffic_eng_found = True
        if "cost-style wide" in key:
            cost_style_wide_found = True

    result["traffic_eng"] = traffic_eng_found
    result["cost_style_wide"] = cost_style_wide_found

    # 故障条件：traffic-eng 未配置
    is_fault = not traffic_eng_found
    return is_fault, result


def check_interface_mpls_te_simple(config_data):
    """
    检查条件C：接口下是否配置了 mpls te

    故障条件：接口下未配置 mpls te
    """
    interface_items = config_data.get("interface", [])
    if not interface_items:
        # 没有接口配置 -> 故障
        return True, {"interface_count": 0, "mpls_te_found": False}

    result = {"interface_count": len(interface_items)}

    # 在接口配置块中查找 mpls te
    mpls_te_found = False
    for item in interface_items:
        key = item.get("key", "").lower()
        if "mpls te" in key:
            mpls_te_found = True
            break

    result["mpls_te_found"] = mpls_te_found

    # 故障条件：未配置 mpls te
    is_fault = not mpls_te_found
    return is_fault, result


def check_tunnel_destination_simple(config_data):
    """
    检查条件D：Tunnel接口下是否配置了 destination

    故障条件：Tunnel接口下未配置 destination

    注：由于简单解析器不区分 Tunnel 和普通接口，
    如果有 interface Tunnel 配置应该在 interface 中，
    这里通过检查是否存在 Tunnel 接口来判断
    """
    # 简单解析器把 Tunnel 接口归为普通 interface，
    # 需要在 interface 中查找包含 "Tunnel" 的项
    interface_items = config_data.get("interface", [])
    tunnel_items = [item for item in interface_items if "Tunnel" in item.get("key", "")]

    if not tunnel_items:
        # 没有 Tunnel 配置 -> 故障
        return True, {"tunnel_count": 0, "destination_found": False}

    result = {"tunnel_count": len(tunnel_items)}

    # 在 Tunnel 接口配置块中查找 destination
    destination_found = False
    for item in tunnel_items:
        key = item.get("key", "").lower()
        if "destination" in key:
            destination_found = True
            break

    result["destination_found"] = destination_found

    # 故障条件：未配置 destination
    is_fault = not destination_found
    return is_fault, result


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

    # 查找 CONFIG 目录
    config_dir = ne_dir / "CONFIG"
    if not config_dir.exists():
        config_dir = ne_dir / "config"  # 尝试小写

    if not config_dir.exists():
        result["details"]["error"] = "CONFIG directory not found"
        return result

    # 遍历 CONFIG 目录下的所有文件
    config_files = list(config_dir.glob("*.txt")) + list(config_dir.glob("*.log"))
    if not config_files:
        result["details"]["error"] = "No config files found"
        return result

    # 合并所有配置行
    all_lines = []
    for config_file in config_files:
        try:
            lines = read_config_lines(str(config_file))
            all_lines.extend(lines)
        except Exception as e:
            continue

    if not all_lines:
        result["details"]["error"] = "Failed to read config files"
        return result

    # 使用简单解析器解析配置
    config_data = parse_simple_config(all_lines)

    result["details"]["files_parsed"] = len(config_files)
    result["details"]["ospf_items"] = len(config_data["ospf"])
    result["details"]["isis_items"] = len(config_data["isis"])
    result["details"]["interface_items"] = len(config_data["interface"])

    # 检查各条件
    conditions = {}

    # 条件A：OSPF配置
    fault_a, details_a = check_ospf_config_simple(config_data)
    conditions["A"] = {"fault": fault_a, "details": details_a}

    # 条件B：ISIS配置
    fault_b, details_b = check_isis_config_simple(config_data)
    conditions["B"] = {"fault": fault_b, "details": details_b}

    # 条件C：接口配置
    fault_c, details_c = check_interface_mpls_te_simple(config_data)
    conditions["C"] = {"fault": fault_c, "details": details_c}

    # 条件D：Tunnel配置
    fault_d, details_d = check_tunnel_destination_simple(config_data)
    conditions["D"] = {"fault": fault_d, "details": details_d}

    result["details"]["conditions"] = conditions

    # 判断是否命中
    matched = fault_a or fault_b or fault_c or fault_d
    result["matched"] = "true" if matched else "false"

    # 收集证据
    if fault_a:
        result["evidence"].append({
            "condition": "A",
            "type": "OSPF配置缺失",
            "details": details_a
        })
    if fault_b:
        result["evidence"].append({
            "condition": "B",
            "type": "ISIS配置缺失",
            "details": details_b
        })
    if fault_c:
        result["evidence"].append({
            "condition": "C",
            "type": "接口配置缺失",
            "details": details_c
        })
    if fault_d:
        result["evidence"].append({
            "condition": "D",
            "type": "Tunnel配置缺失",
            "details": details_d
        })

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
        print("Usage: python fault_check_配置问题.py <data_dir>")
        sys.exit(1)

    data_dir = sys.argv[1]
    results = main(data_dir)
    print(json.dumps(results, ensure_ascii=False, indent=2))
