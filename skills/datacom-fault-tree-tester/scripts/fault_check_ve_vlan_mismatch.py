#!/usr/bin/env python3
"""
fault_check_ve_vlan_mismatch.py - Virtual-Ethernet子接口VLAN配置不一致故障诊断

故障判断逻辑：
1. 从 CONFIG 目录的 display current-configuration 中提取所有 Virtual-Ethernet 子接口
2. 按子接口 ID（如 .700）分组，检查是否存在来自不同父接口的配对接口
3. 检查配对接口的 vlan-type dot1q 配置是否一致

命中条件：
- 子接口 ID 相同，但父接口不同（如 Virtual-Ethernet1/1/1.x 和 Virtual-Ethernet1/2/1.x）
- 且至少有一个接口没有配置 vlan-type，或两个接口的 VLAN ID 不一致

输出说明：
- matched = true: 成功检测到符合条件的情况（success）
- matched = false: 未检测到符合条件的情况（ok）
"""
import os
import re
import json
import sys

def get_ne_files(ne_dir):
    """获取网元目录下的所有数据文件"""
    files = {"CONFIG": [], "CMD": [], "LOG": []}
    for subdir in ["CONFIG", "CMD", "LOG"]:
        subdir_path = os.path.join(ne_dir, subdir)
        if os.path.exists(subdir_path):
            for f in os.listdir(subdir_path):
                if f.endswith(('.txt', '.log')):
                    files[subdir].append(os.path.join(subdir_path, f))
    return files

def extract_interface_blocks(content):
    """
    从配置文件中提取所有 interface Virtual-Ethernet 块
    返回格式：[{'name': 'Virtual-Ethernet1/1/1.700', 'raw_text': '...'}, ...]
    """
    # 匹配 interface Virtual-Ethernet 开头的配置块
    # 从 interface 开始到下一个 interface 或文件结尾
    pattern = re.compile(
        r'(?:^> )?interface Virtual-Ethernet(\d+/\d+/\d+\.\d+)\s*\n(.*?)(?=(?:^> )?interface Virtual-Ethernet|\Z)',
        re.MULTILINE | re.DOTALL
    )
    
    interfaces = []
    for match in pattern.finditer(content):
        full_name = 'Virtual-Ethernet' + match.group(1)
        raw_text = match.group(0)
        interfaces.append({
            'name': full_name,
            'raw_text': raw_text
        })
    
    return interfaces

def parse_vlan_config(interface_block):
    """
    解析单个接口的 VLAN 配置
    返回：{'has_vlan': True/False, 'vlan_id': int or None}
    """
    raw_text = interface_block['raw_text']
    
    # 匹配 vlan-type dot1q <vlan_id>
    vlan_match = re.search(r'vlan-type\s+dot1q\s+(\d+)', raw_text, re.IGNORECASE)
    
    if vlan_match:
        return {'has_vlan': True, 'vlan_id': int(vlan_match.group(1))}
    else:
        return {'has_vlan': False, 'vlan_id': None}

def parse_interface_name(if_name):
    """
    解析接口名，提取父接口和子接口 ID
    例如：Virtual-Ethernet1/1/1.700 -> {'parent': '1/1/1', 'sub_id': 700}
    """
    match = re.match(r'Virtual-Ethernet\d+/(\d+/\d+)\.(\d+)', if_name)
    if match:
        return {'parent': match.group(1), 'sub_id': int(match.group(2))}
    return None

def check_vlan_mismatch(interfaces):
    """
    检查子接口 VLAN 配置是否不一致
    
    逻辑：
    1. 按子接口 ID 分组
    2. 检查是否有来自不同父接口的配对
    3. 检查配对接口的 VLAN 配置是否一致
    """
    # 按子接口 ID 分组
    sub_id_groups = {}
    for iface in interfaces:
        parsed = parse_interface_name(iface['name'])
        if parsed:
            sub_id = parsed['sub_id']
            parent = parsed['parent']
            vlan_info = parse_vlan_config(iface)
            
            if sub_id not in sub_id_groups:
                sub_id_groups[sub_id] = []
            
            sub_id_groups[sub_id].append({
                'name': iface['name'],
                'parent': parent,
                'vlan': vlan_info
            })
    
    # 查找 VLAN 配置不一致的配对
    mismatches = []
    
    for sub_id, group in sub_id_groups.items():
        # 需要至少两个不同父接口的接口才能形成配对
        parents = set(item['parent'] for item in group)
        if len(parents) < 2:
            continue
        
        # 检查 VLAN 配置
        for i, item1 in enumerate(group):
            for item2 in group[i+1:]:
                if item1['parent'] == item2['parent']:
                    continue  # 同一父接口，跳过
                
                # 检查 VLAN 是否一致
                vlan1 = item1['vlan']
                vlan2 = item2['vlan']
                
                # 不一致情况：
                # 1. 一个有 vlan，一个没有
                # 2. 两个都有 vlan，但 ID 不同
                is_mismatch = False
                reason = ""
                
                if vlan1['has_vlan'] != vlan2['has_vlan']:
                    is_mismatch = True
                    if vlan1['has_vlan']:
                        reason = f"{item1['name']} 有 vlan-type dot1q {vlan1['vlan_id']}，{item2['name']} 没有配置 vlan-type"
                    else:
                        reason = f"{item1['name']} 没有配置 vlan-type，{item2['name']} 有 vlan-type dot1q {vlan2['vlan_id']}"
                elif not vlan1['has_vlan'] and not vlan2['has_vlan']:
                    # 都没配置 vlan，可以算作不一致（取决于业务需求）
                    is_mismatch = True
                    reason = f"{item1['name']} 和 {item2['name']} 都没有配置 vlan-type"
                elif vlan1['vlan_id'] != vlan2['vlan_id']:
                    is_mismatch = True
                    reason = f"{item1['name']} vlan-type dot1q {vlan1['vlan_id']}，{item2['name']} vlan-type dot1q {vlan2['vlan_id']}，VLAN ID 不一致"
                
                if is_mismatch:
                    mismatches.append({
                        'sub_id': sub_id,
                        'interface1': item1,
                        'interface2': item2,
                        'reason': reason
                    })
    
    return mismatches

def diagnose_ne(ne_dir):
    """诊断单个网元"""
    result = {
        'matched': False,
        'evidence': [],
        'details': {}
    }
    
    # 获取网元文件
    files = get_ne_files(ne_dir)
    
    # 只从 CONFIG 目录读取
    all_interfaces = []
    
    for cfg_file in files['CONFIG']:
        try:
            with open(cfg_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            interfaces = extract_interface_blocks(content)
            all_interfaces.extend(interfaces)
        except Exception as e:
            print(f"读取文件失败 {cfg_file}: {e}", file=sys.stderr)
    
    # 检查 VLAN 配置不一致
    mismatches = check_vlan_mismatch(all_interfaces)
    
    if mismatches:
        result['matched'] = True
        for mismatch in mismatches:
            evidence = f"子接口 ID {mismatch['sub_id']}: {mismatch['reason']}"
            result['evidence'].append(evidence)
            result['details'][f"sub_{mismatch['sub_id']}"] = {
                'interface1': mismatch['interface1']['name'],
                'interface2': mismatch['interface2']['name'],
                'vlan1': mismatch['interface1']['vlan'],
                'vlan2': mismatch['interface2']['vlan'],
                'reason': mismatch['reason']
            }
    
    return result

def main():
    if len(sys.argv) < 2:
        print("用法: python fault_check_ve_vlan_mismatch.py <网元数据目录>")
        sys.exit(1)
    
    data_dir = sys.argv[1]
    
    results = {}
    
    # 遍历数据目录下的每个网元
    for ne_name in os.listdir(data_dir):
        ne_path = os.path.join(data_dir, ne_name)
        if not os.path.isdir(ne_path):
            continue
        
        print(f"正在诊断网元: {ne_name}", file=sys.stderr)
        result = diagnose_ne(ne_path)
        results[ne_name] = result
    
    # 输出 JSON 结果
    print(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
