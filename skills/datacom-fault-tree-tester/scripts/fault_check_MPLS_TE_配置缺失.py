#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MPLS_TE_配置缺失 故障诊断脚本
检查OSPF/ISIS/接口/Tunnel配置，4个条件满足任意一个即命中

【通用能力复用】
- 使用 search_config.py 的 parse_config() 进行配置解析（支持 # 分隔、大小写不敏感）
- 使用 search_config.py 的 find_config_item() 进行配置查找
"""

import os
import re
import json
from pathlib import Path

# 复用通用配置解析模块
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from search_config import parse_config_file, find_config_item, get_group_items


def check_ospf_config(config_groups):
    """
    检查OSPF配置：是否同时配置了 opaque-capability enable 和 mpls-te enable
    返回：True表示正常（两个都配置了），False表示故障（配置了任意一个或都没配置）
    """
    # 查找所有 OSPF 进程配置
    ospf_groups = find_config_item(config_groups, 'ospf', use_regex=True)
    for group in ospf_groups:
        items=  get_group_items(group)
        has_opaque = False
        has_mpls_te = False
        for item in items:
            stripped = item.get('stripped', '')
            if 'opaque-capability enable' in stripped.lower():
                has_opaque = True
            if 'mpls-te enable' in stripped.lower():
                has_mpls_te = True
        # 只要有OSPF配置，就检查它
        if has_opaque and has_mpls_te:
            return True  # 正常
    # 没有找到同时满足两个条件的OSPF配置 → 故障
    return False


def check_isis_config(config_groups):
    """
    检查ISIS配置：是否同时配置了 cost-style wide 和 traffic-eng
    返回：True表示正常（两个都配置了），False表示故障（配置了任意一个或都没配置）
    """
    # 查找所有 ISIS 进程配置
    isis_groups = find_config_item(config_groups, 'isis', use_regex=True)
    for group in isis_groups:
        items = get_group_items(group)
        has_cost_style = False
        has_traffic_eng = False
        for item in items:
            stripped = item.get('stripped', '')
            if 'cost-style wide' in stripped.lower():
                has_cost_style = True
            if 'traffic-eng' in stripped.lower():
                has_traffic_eng = True
        # 只要有ISIS配置，就检查它
        if has_cost_style and has_traffic_eng:
            return True  # 正常
    # 没有找到同时满足两个条件的ISIS配置 → 故障
    return False


def check_interface_mpls_te(config_groups):
    """
    检查接口配置：是否有接口配置了 mpls te
    返回：True表示正常（有接口配置了mpls te），False表示故障（没有接口配置mpls te）
    """
    # 查找所有接口配置
    for group in config_groups:
        items = get_group_items(group)
        if not items:
            continue
        first_item = items[0].get('stripped', '')
        # 跳过非接口配置块（如 OSPF、ISIS 等进程配置）
        if not re.match(r'interface\s+', first_item, re.IGNORECASE):
            continue
        # 检查该接口是否配置了 mpls te
        for item in items[1:]:
            stripped = item.get('stripped', '')
            if 'mpls te' in stripped.lower() or 'mpls-te' in stripped.lower():
                return True  # 正常
    # 没有找到配置了 mpls te 的接口 → 故障
    return False


def check_tunnel_destination(config_groups):
    """
    检查Tunnel接口配置：是否有Tunnel接口配置了 destination
    返回：True表示正常（有Tunnel接口配置了destination），False表示故障
    """
    # 查找所有 Tunnel 接口配置
    for group in config_groups:
        items = get_group_items(group)
        if not items:
            continue
        first_item = items[0].get('stripped', '')
        # 只检查 Tunnel 接口
        if not re.match(r'interface\s+Tunnel\d*', first_item, re.IGNORECASE):
            continue
        # 检查该 Tunnel 接口是否配置了 destination
        for item in items[1:]:
            stripped = item.get('stripped', '')
            if 'destination' in stripped.lower():
                return True  # 正常
    # 没有找到配置了 destination 的 Tunnel 接口 → 故障
    return False


def diagnose(data_dir):
    """执行故障诊断"""
    data_path = Path(data_dir)
    results = {}

    # 检测数据目录结构：标准结构（网元子目录）还是扁平结构（直接是网元）
    config_dir = data_path / 'CONFIG'
    if config_dir.exists():
        # 扁平结构：数据目录直接包含 CONFIG/ 目录
        ne_name = data_path.name
        config_files = list(config_dir.glob('*.txt')) + list(config_dir.glob('*.log'))
        if config_files:
            results[ne_name] = diagnose_single_ne(str(config_files[0]))
    else:
        # 标准结构：遍历网元子目录
        for subdir in data_path.iterdir():
            if not subdir.is_dir() or subdir.name.startswith('.'):
                continue

            config_dir = subdir / 'CONFIG'
            if not config_dir.exists():
                continue

            config_files = list(config_dir.glob('*.txt')) + list(config_dir.glob('*.log'))
            if not config_files:
                continue

            results[subdir.name] = diagnose_single_ne(str(config_files[0]))

    # 汇总结果
    total_matched = sum(1 for r in results.values() if r['matched'])
    all_evidence = []
    for ne_name, result in results.items():
        if result['matched']:
            for evidence in result['evidence']:
                all_evidence.append(f'{ne_name}: {evidence}')

    return {
        'matched': total_matched > 0,
        'evidence': all_evidence,
        'details': {
            'device_count': len(results),
            'fault_count': total_matched,
            'devices': results
        }
    }


def diagnose_single_ne(config_file_path):
    """诊断单个网元"""
    config_groups = parse_config_file(config_file_path)
    if not config_groups:
        # 如果解析失败，尝试直接读取文件内容
        content = Path(config_file_path).read_text(encoding='utf-8', errors='ignore')
        # 使用简单的文本搜索作为后备
        has_opaque = 'opaque-capability enable' in content.lower()
        has_mpls_te = 'mpls-te enable' in content.lower()
        has_cost_style = 'cost-style wide' in content.lower()
        has_traffic_eng = 'traffic-eng' in content.lower()
        has_interface_mpls_te = re.search(r'interface\s+\S+', content, re.IGNORECASE) and 'mpls te' in content.lower()
        has_tunnel_dest = re.search(r'interface\s+Tunnel\d*', content, re.IGNORECASE) and 'destination' in content.lower()

        fault_conditions = []
        if not (has_opaque and has_mpls_te):
            fault_conditions.append('OSPF未同时配置opaque-capability enable和mpls-te enable')
        if not (has_cost_style and has_traffic_eng):
            fault_conditions.append('ISIS未同时配置cost-style wide和traffic-eng')
        if not has_interface_mpls_te:
            fault_conditions.append('接口下未配置mpls te')
        if not has_tunnel_dest:
            fault_conditions.append('Tunnel接口下未配置destination')

        return {
            'matched': len(fault_conditions) > 0,
            'evidence': fault_conditions,
            'details': {
                'ospf_ok': has_opaque and has_mpls_te,
                'isis_ok': has_cost_style and has_traffic_eng,
                'interface_mpls_te_ok': has_interface_mpls_te,
                'tunnel_dest_ok': has_tunnel_dest
            }
        }

    # 检查各项配置
    ospf_ok = check_ospf_config(config_groups)
    isis_ok = check_isis_config(config_groups)
    interface_ok = check_interface_mpls_te(config_groups)
    tunnel_ok = check_tunnel_destination(config_groups)

    fault_conditions = []
    if not ospf_ok:
        fault_conditions.append('OSPF未同时配置opaque-capability enable和mpls-te enable')
    if not isis_ok:
        fault_conditions.append('ISIS未同时配置cost-style wide和traffic-eng')
    if not interface_ok:
        fault_conditions.append('接口下未配置mpls te')
    if not tunnel_ok:
        fault_conditions.append('Tunnel接口下未配置destination')

    matched = not (ospf_ok and isis_ok and interface_ok and tunnel_ok)

    return {
        'matched': matched,
        'evidence': fault_conditions,
        'details': {
            'ospf_ok': ospf_ok,
            'isis_ok': isis_ok,
            'interface_mpls_te_ok': interface_ok,
            'tunnel_dest_ok': tunnel_ok
        }
    }


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        data_dir = sys.argv[1]
    else:
        data_dir = '.'

    result = diagnose(data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
