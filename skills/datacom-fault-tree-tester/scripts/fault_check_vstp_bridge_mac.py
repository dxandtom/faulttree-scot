# -*- coding: utf-8 -*-
"""
V-STP根桥桥MAC配置异常 检查脚本

判断逻辑：
1. 检查 stp v-stp enable 是否配置
2. 检查是否为根桥（CIST Root/ERPC 包含 "This bridge is the root"）
3. 对比配置的桥 MAC 与 display stp v-stp 中的 Active MAC
   - 不一致 → 故障

输出格式：
{
    "网元名": {
        "matched": true/false,  # true=故障，false=正常
        "evidence": [...],
        "details": {...}
    }
}
"""

import json
import os
import re
from pathlib import Path


def extract_configured_mac(config_content):
    """从配置中提取 stp bridge-address 的 MAC 地址"""
    pattern = r'stp\s+bridge-address\s+([0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4})'
    match = re.search(pattern, config_content)
    if match:
        return match.group(1).lower()
    return None


def check_vstp_enabled(config_content):
    """检查 stp v-stp enable 是否配置"""
    return bool(re.search(r'stp\s+v-stp\s+enable', config_content))


def check_is_root_bridge(cmd_content):
    """检查是否为根桥"""
    # 查找 display stp 输出中的 CIST Root/ERPC
    pattern = r'CIST\s+Root/ERPC\s*:[^\n]*\(([^)]*This bridge is the root[^)]*)\)'
    match = re.search(pattern, cmd_content)
    return match is not None


def extract_active_mac(cmd_content):
    """从 display stp v-stp 输出中提取 Active MAC"""
    # 查找 Bridge Mac :Config=xxxx / Active=xxxx 格式
    pattern = r'Bridge\s+Mac\s*:\s*\S+\s*/\s*Active=([0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4})'
    match = re.search(pattern, cmd_content, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return None


def check_vstp_bridge_mac_fault(config_file, cmd_file):
    """
    检查 V-STP 根桥桥 MAC 配置异常
    
    Args:
        config_file: display current-configuration 文件路径
        cmd_file: CMD 目录下的文件路径（包含 display stp 和 display stp v-stp 输出）
    
    Returns:
        dict: 包含 matched, evidence, details
    """
    result = {
        "matched": False,
        "evidence": [],
        "details": {
            "step1_vstp_enabled": None,
            "step2_is_root_bridge": None,
            "step3_configured_mac": None,
            "step3_active_mac": None,
            "step3_comparison": None
        }
    }
    
    # 读取配置
    with open(config_file, 'r', encoding='utf-8', errors='ignore') as f:
        config_content = f.read()
    
    # 读取命令输出
    with open(cmd_file, 'r', encoding='utf-8', errors='ignore') as f:
        cmd_content = f.read()
    
    # 步骤1：检查 V-STP 是否使能
    vstp_enabled = check_vstp_enabled(config_content)
    result["details"]["step1_vstp_enabled"] = vstp_enabled
    result["evidence"].append(f"步骤1 - V-STP使能: {'是' if vstp_enabled else '否'}")
    
    if not vstp_enabled:
        # V-STP 未使能，不匹配
        return result
    
    # 步骤2：检查是否为根桥
    is_root = check_is_root_bridge(cmd_content)
    result["details"]["step2_is_root_bridge"] = is_root
    result["evidence"].append(f"步骤2 - 是否根桥: {'是' if is_root else '否'}")
    
    if not is_root:
        # 不是根桥，不匹配
        return result
    
    # 步骤3：对比配置的 MAC 与 Active MAC
    configured_mac = extract_configured_mac(config_content)
    active_mac = extract_active_mac(cmd_content)
    
    result["details"]["step3_configured_mac"] = configured_mac
    result["details"]["step3_active_mac"] = active_mac
    
    if configured_mac:
        result["evidence"].append(f"步骤3 - 配置MAC: {configured_mac}")
    else:
        result["evidence"].append("步骤3 - 配置MAC: 未配置")
    
    result["evidence"].append(f"步骤3 - Active MAC: {active_mac}")
    
    # 判断不一致
    if configured_mac is None:
        # 未配置桥MAC，与 Active 不一致 → 故障
        result["details"]["step3_comparison"] = "config_none_vs_active"
        result["matched"] = True
        result["evidence"].append("结论: 配置未设置桥MAC，与Active MAC不一致 → 故障")
    elif active_mac and configured_mac.lower() != active_mac.lower():
        # 配置的 MAC 与 Active MAC 不一致 → 故障
        result["details"]["step3_comparison"] = "mismatch"
        result["matched"] = True
        result["evidence"].append(f"结论: 配置MAC({configured_mac}) ≠ Active MAC({active_mac}) → 故障")
    else:
        # 一致 → 正常
        result["details"]["step3_comparison"] = "match"
        result["evidence"].append("结论: 配置MAC与Active MAC一致 → 正常")
    
    return result


def main(data_dir, output_file=None):
    """
    主函数：遍历网元目录并检查
    
    Args:
        data_dir: 数据目录（包含多个网元子目录）
        output_file: 可选，输出 JSON 结果的文件路径
    
    Returns:
        dict: 所有网元的检查结果
    """
    results = {}
    data_path = Path(data_dir)
    
    # 遍历所有网元目录
    for ne_dir in sorted(data_path.iterdir()):
        if not ne_dir.is_dir():
            continue
        
        ne_name = ne_dir.name
        results[ne_name] = {"error": "目录结构不正确"}
        
        config_dir = ne_dir / "CONFIG"
        cmd_dir = ne_dir / "CMD"
        
        if not config_dir.exists() or not cmd_dir.exists():
            results[ne_name] = {"error": "缺少 CONFIG 或 CMD 目录"}
            continue
        
        # 查找配置文件（display current-configuration）
        config_files = list(config_dir.glob("*.txt"))
        if not config_files:
            results[ne_name] = {"error": "CONFIG 目录下没有文件"}
            continue
        
        # 查找 CMD 文件（包含 display stp 和 display stp v-stp）
        cmd_files = list(cmd_dir.glob("*.txt"))
        if not cmd_files:
            results[ne_name] = {"error": "CMD 目录下没有文件"}
            continue
        
        # 使用第一个文件（通常只有一个）
        config_file = config_files[0]
        cmd_file = cmd_files[0]
        
        try:
            results[ne_name] = check_vstp_bridge_mac_fault(str(config_file), str(cmd_file))
        except Exception as e:
            results[ne_name] = {"error": str(e)}
    
    # 输出结果
    print(json.dumps(results, ensure_ascii=False, indent=2))
    
    # 保存到文件
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {output_file}")
    
    return results


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python fault_check_vstp_bridge_mac.py <数据目录> [输出文件]")
        print("示例: python fault_check_vstp_bridge_mac.py D:\\test_data\\output result.json")
        sys.exit(1)
    
    data_dir = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    main(data_dir, output_file)
