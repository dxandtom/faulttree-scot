#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DCGW_VBDIF_IPv6_地址不一致 故障诊断脚本
非SDN二层网络架构中，本端和对端DCGW上相同VBDIF接口的IPv6地址应一致

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


def extract_nve_source_ip(config_groups):
    """从配置中提取 interface Nve1 下的 source IP（复用 search_config）"""
    # 查找所有 interface Nve1 配置组（大小写不敏感）
    nve_groups = find_config_item(config_groups, 'interface Nve1', use_regex=True)
    for group in nve_groups:
        for item in get_group_items(group):
            if 'source' in item.get('key', '').lower():
                return item.get('value', '').strip()
    return None


def extract_vbdif_ipv6_addresses(config_groups):
    """提取所有配置了 ipv6 enable 的 Vbdif 接口及其 IPv6 地址（复用 search_config）"""
    vbdif_dict = {}

    # 查找所有 interface Vbdif 配置组（大小写不敏感）
    vbdif_groups = find_config_item(config_groups, 'interface Vbdif', use_regex=True)

    for group in vbdif_groups:
        items = get_group_items(group)
        if not items:
            continue

        # 第一个配置项是 interface Vbdifxxx
        first_item = items[0]
        iface_match = re.match(r'interface\s+(Vbdif\d+)', first_item.get('stripped', ''), re.IGNORECASE)
        if not iface_match:
            continue
        iface_name = iface_match.group(1)

        # 检查是否配置了 ipv6 enable
        has_ipv6_enable = False
        ipv6_address = None

        for item in items[1:]:  # 从第二个开始检查
            stripped = item.get('stripped', '')
            if re.match(r'ipv6\s+enable', stripped, re.IGNORECASE):
                has_ipv6_enable = True
            ipv6_match = re.match(r'ipv6\s+address\s+([0-9a-fA-F:]+/\d+)', stripped, re.IGNORECASE)
            if ipv6_match:
                ipv6_address = ipv6_match.group(1)

        if has_ipv6_enable and ipv6_address:
            vbdif_dict[iface_name] = ipv6_address

    return vbdif_dict

def diagnose(data_dir):
    """执行故障诊断"""
    results = {}
    data_path = Path(data_dir)

    # 步骤1：遍历所有网元，提取 nve1 source IP
    ne_list = []
    for subdir in data_path.iterdir():
        if subdir.is_dir() and not subdir.name.startswith('.'):
            config_file = subdir / 'CONFIG'
            if config_file.exists():
                files = list(config_file.glob('*.txt'))
                if files:
                    # 使用通用配置解析模块（复用 search_config）
                    config_groups = parse_config_file(str(files[0]))
                    ne_name = subdir.name
                    nve_ip = extract_nve_source_ip(config_groups)
                    vbdif_dict = extract_vbdif_ipv6_addresses(config_groups)
                    ne_list.append({
                        'name': ne_name,
                        'nve_ip': nve_ip,
                        'vbdif': vbdif_dict
                    })
                    results[ne_name] = {
                        'nve_source_ip': nve_ip,
                        'vbdif_interfaces': vbdif_dict
                    }

    # 步骤2：按 nve1 source IP 配对设备
    ip_to_devices = {}
    for ne in ne_list:
        if ne['nve_ip']:
            if ne['nve_ip'] not in ip_to_devices:
                ip_to_devices[ne['nve_ip']] = []
            ip_to_devices[ne['nve_ip']].append(ne)

    # 步骤3：比较配对设备间的 Vbdif IPv6 地址
    fault_details = []
    for ip, devices in ip_to_devices.items():
        if len(devices) < 2:
            continue

        # 两两配对比较
        for i in range(len(devices)):
            for j in range(i + 1, len(devices)):
                dev_a = devices[i]
                dev_b = devices[j]

                # 获取双方的 Vbdif 接口列表
                vbdif_a = set(dev_a['vbdif'].keys())
                vbdif_b = set(dev_b['vbdif'].keys())

                # 找出共同存在的接口
                common_vbdif = vbdif_a & vbdif_b
                # 找出仅在一侧存在的接口
                only_a = vbdif_a - vbdif_b
                only_b = vbdif_b - vbdif_a

                # 检查共同接口的 IPv6 地址是否一致
                for iface in sorted(common_vbdif):
                    ip_a = dev_a['vbdif'][iface]
                    ip_b = dev_b['vbdif'][iface]
                    if ip_a != ip_b:
                        fault_details.append({
                            'type': 'address_mismatch',
                            'device_a': dev_a['name'],
                            'device_b': dev_b['name'],
                            'interface': iface,
                            'ip_a': ip_a,
                            'ip_b': ip_b
                        })

                # 记录单侧缺失
                for iface in sorted(only_a):
                    fault_details.append({
                        'type': 'missing_on_peer',
                        'device_a': dev_a['name'],
                        'device_b': dev_b['name'],
                        'interface': iface,
                        'ip_a': dev_a['vbdif'][iface],
                        'ip_b': None
                    })
                for iface in sorted(only_b):
                    fault_details.append({
                        'type': 'missing_on_peer',
                        'device_a': dev_a['name'],
                        'device_b': dev_b['name'],
                        'interface': iface,
                        'ip_a': None,
                        'ip_b': dev_b['vbdif'][iface]
                    })

    # 构建最终结果
    matched = len(fault_details) > 0
    evidence = []
    for fault in fault_details:
        if fault['type'] == 'address_mismatch':
            evidence.append(
                f"{fault['device_a']} 与 {fault['device_b']} 的 {fault['interface']} IPv6地址不一致："
                f"{fault['ip_a']} vs {fault['ip_b']}"
            )
        elif fault['type'] == 'missing_on_peer':
            if fault['ip_a']:
                evidence.append(
                    f"{fault['device_b']} 侧缺少 {fault['interface']}，"
                    f"{fault['device_a']} 侧配置为 {fault['ip_a']}"
                )
            else:
                evidence.append(
                    f"{fault['device_a']} 侧缺少 {fault['interface']}，"
                    f"{fault['device_b']} 侧配置为 {fault['ip_b']}"
                )

    return {
        'matched': matched,
        'evidence': evidence,
        'details': {
            'device_count': len(ne_list),
            'pairs': {ip: [d['name'] for d in devs] for ip, devs in ip_to_devices.items() if len(devs) >= 2},
            'fault_count': len(fault_details),
            'faults': fault_details
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
