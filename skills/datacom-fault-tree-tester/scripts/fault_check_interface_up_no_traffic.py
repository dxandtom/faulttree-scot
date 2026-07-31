#!/usr/bin/env python3
"""
fault_check_interface_up_no_traffic.py - 接口UP但无流量故障诊断
故障判断逻辑：
1. 接口名为物理接口（非vlanif/eth-trunk/tunnel/子接口）
2. current state 值为 UP
3. Last * seconds input rate 或 output rate 等于 0 bits/sec

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

def parse_interface_blocks(content):
    """
    解析 display interface 命令输出，提取每个接口的完整信息块
    按 "current state" 关键字分割接口块
    """
    # 找到 display interface 输出开始的位置
    start_marker = 'display interface\n===============================================================================\n'
    start_idx = content.find(start_marker)
    if start_idx >= 0:
        content = content[start_idx + len(start_marker):]
    
    # 按 "current state" 分割，每个接口以 "current state" 开头
    parts = re.split(r'(?=^\S+\s+current state\s*:)', content, flags=re.MULTILINE)
    
    interfaces = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        # 提取接口名（行首到第一个空格）
        lines = part.split('\n')
        first_line = lines[0]
        
        # 接口名匹配：接口类型 + 槽位号（不包含空格）
        name_match = re.match(r'^(\S+(?:\d+(?:/\d+)*)?)\s+current state\s*:', first_line)
        if not name_match:
            continue
        
        interface_name = name_match.group(1)
        
        # 提取 current state 值（在同一行）
        state_line = first_line
        state_match = re.search(r'current state\s*:\s*(.+?)(?:\s*\(|$)', state_line)
        current_state = state_match.group(1).strip() if state_match else ""
        
        interfaces.append({
            'name': interface_name,
            'current_state': current_state,
            'raw_text': part
        })
    
    return interfaces

def parse_interface_rates(interface_block):
    """
    从接口块中提取 input rate 和 output rate
    """
    raw_text = interface_block['raw_text']
    
    # 匹配 Last * seconds input rate: X bits/sec, Y packets/sec
    input_pattern = re.compile(
        r'Last\s+\d+\s+seconds\s+input\s+rate\s*:\s*(\d+)\s*bits/sec',
        re.IGNORECASE
    )
    # 匹配 Last * seconds output rate: X bits/sec, Y packets/sec
    output_pattern = re.compile(
        r'Last\s+\d+\s+seconds\s+output\s+rate\s*:\s*(\d+)\s*bits/sec',
        re.IGNORECASE
    )
    
    input_match = input_pattern.search(raw_text)
    output_match = output_pattern.search(raw_text)
    
    input_rate = int(input_match.group(1)) if input_match else None
    output_rate = int(output_match.group(1)) if output_match else None
    
    return input_rate, output_rate

def is_physical_interface(interface_name):
    """
    判断是否为物理接口
    排除：vlanif、eth-trunk、tunnel、LoopBack、子接口（包含.）
    """
    name_lower = interface_name.lower()
    
    # 排除关键词
    exclude_keywords = ['vlanif', 'eth-trunk', 'tunnel', 'loopback']
    for keyword in exclude_keywords:
        if keyword in name_lower:
            return False
    
    # 排除子接口（包含.）
    if '.' in interface_name:
        return False
    
    return True

def check_ne(ne_dir, judgment_conditions):
    """
    检查单个网元的故障状态
    """
    result = {
        "matched": False,
        "evidence": [],
        "details": {},
        "total_interfaces": 0,
        "checked_interfaces": 0
    }
    
    files = get_ne_files(ne_dir)
    
    # 从 CMD 目录查找 display interface 数据
    interface_data = []
    for cmd_file in files.get("CMD", []):
        try:
            with open(cmd_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                # 检查是否包含 display interface 命令输出
                if 'display interface' in content.lower():
                    interface_data.append(content)
        except Exception as e:
            print(f"Error reading {cmd_file}: {e}")
    
    if not interface_data:
        result["details"]["error"] = "未找到 display interface 命令输出"
        return result
    
    # 解析所有接口
    all_interfaces = []
    for data in interface_data:
        blocks = parse_interface_blocks(data)
        all_interfaces.extend(blocks)
    
    result["details"]["total_interfaces"] = len(all_interfaces)
    
    # 应用过滤条件
    for interface in all_interfaces:
        name = interface['name']
        
        # 条件A：是否为物理接口
        if not is_physical_interface(name):
            continue
        
        result["checked_interfaces"] += 1
        state = interface['current_state'].strip()
        
        # 条件B：current state 是否为 UP
        if state != "UP":
            continue
        
        # 条件C：input rate 或 output rate 是否为 0
        input_rate, output_rate = parse_interface_rates(interface)
        
        if input_rate == 0 or output_rate == 0:
            result["matched"] = True
            result["evidence"].append({
                "interface": name,
                "current_state": state,
                "input_rate": input_rate,
                "output_rate": output_rate,
                "description": extract_description(interface['raw_text'])
            })
    
    return result

def extract_description(raw_text):
    """提取接口描述"""
    desc_pattern = re.compile(r'Description:\s*(.*?)(?:\n|$)', re.IGNORECASE)
    match = desc_pattern.search(raw_text)
    return match.group(1).strip() if match else ""

def format_result(all_results):
    """格式化输出诊断结果"""
    print("\n" + "="*60)
    print("诊断结果：")
    print("="*60)
    
    for ne_name, result in all_results.items():
        print(f"\n【{ne_name}】")
        
        # matched = true 表示成功检测到符合条件的情况
        if result["matched"]:
            print(f"  [SUCCESS] 检测到 {len(result['evidence'])} 个接口符合条件（UP但无流量）")
            for ev in result['evidence']:
                print(f"\n    接口: {ev['interface']}")
                print(f"    状态: {ev['current_state']}")
                print(f"    Input Rate: {ev['input_rate']} bits/sec")
                print(f"    Output Rate: {ev['output_rate']} bits/sec")
                if ev['description']:
                    print(f"    描述: {ev['description']}")
        else:
            print(f"  [OK] 未检测到符合条件的接口（UP但无流量）")
        
        if result["details"]:
            print(f"\n  统计: 总接口数={result['details'].get('total_interfaces', 0)}, "
                  f"检查物理接口={result['details'].get('checked_interfaces', result.get('checked_interfaces', 0))}")
        
        if result.get("details", {}).get("error"):
            print(f"\n  错误: {result['details']['error']}")
    
    print("\n" + "="*60)

def main():
    # 输入：网元数据目录
    base_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    
    all_results = {}
    
    for ne_name in os.listdir(base_dir):
        ne_dir = os.path.join(base_dir, ne_name)
        if os.path.isdir(ne_dir):
            result = check_ne(ne_dir, {})
            all_results[ne_name] = result
    
    # 格式化输出诊断结果
    format_result(all_results)
    
    # 输出 JSON 结果
    print("\n--- JSON 输出 ---")
    print(json.dumps(all_results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
