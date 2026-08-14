#!/usr/bin/env python3
"""check_binding.py - 静态检查 tree_index 中节点绑定情况

检测 tree_index 中有判据的节点是否在 analyzer 脚本中有绑定。

用法:
    python check_binding.py --tree-index <tree_index.json> --scripts-dir <scripts_dir>
                            [--out <output.json>]

输出:
    检查结果 JSON，包含:
    - fully_bound: 完全绑定的节点列表
    - partially_bound: 部分绑定的节点列表（多条件节点部分有绑定）
    - unbound: 有判据但完全无绑定的节点列表（可能是 BINDING_GAP）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set


def load_tree_index(path: Path) -> Dict[str, Any]:
    """加载 tree_index.json"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[error] 无法读取 tree_index: {e}", file=sys.stderr)
        sys.exit(1)


def find_analyzer_scripts(scripts_dir: Path) -> List[Path]:
    """查找所有 analyzer 脚本"""
    if not scripts_dir.exists():
        return []
    return sorted(scripts_dir.glob("*_analyzer.py"))


def extract_bindings_from_script(script_path: Path) -> Set[str]:
    """从 analyzer 脚本中提取已绑定的 node_id 集合

    分析脚本中 CONDITIONS 的结构，提取所有有 binding 配置的 node_id。
    """
    bound_nodes: Set[str] = set()
    try:
        content = script_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[warn] 无法读取脚本 {script_path}: {e}", file=sys.stderr)
        return bound_nodes

    # 提取所有 node_id 引用
    # 匹配形如 "node_id": "n6" 或 'node_id': 'n6' 的模式
    node_id_pattern = re.compile(r'''["']node_id["']\s*:\s*["']([nv]\d+)["']''')
    for match in node_id_pattern.finditer(content):
        bound_nodes.add(match.group(1))

    # 同时检查以 node_id 为 key 的字典字面量
    # 形如 {"node_id": "n6", ...} 或 {'node_id': 'n6', ...}
    # 这个正则匹配 CONDITIONS 列表中的各条件条目
    condition_entry_pattern = re.compile(
        r'''\{[^}]*?["']node_id["']\s*:\s*["']([nv]\d+)["'][^}]*\}'''
    )
    for match in condition_entry_pattern.finditer(content):
        node_id = match.group(1)
        bound_nodes.add(node_id)

    return bound_nodes


def collect_all_bindings(scripts_dir: Path) -> Set[str]:
    """收集所有 analyzer 脚本中的绑定"""
    all_bound: Set[str] = set()
    for script in find_analyzer_scripts(scripts_dir):
        all_bound.update(extract_bindings_from_script(script))
    return all_bound


def check_binding_gaps(tree_index: Dict[str, Any], bound_nodes: Set[str]) -> Dict[str, Any]:
    """检查绑定缺口

    返回:
        {
            "total_condition_nodes": int,
            "fully_bound": [node_id, ...],
            "unbound": [{node_id, name, condition, reasons}, ...],
            "binding_rate": float
        }
    """
    nodes = tree_index.get("nodes", {})
    if isinstance(nodes, dict):
        # 执行链的 tree_index:nodes 是字典,node_id 就是字典的 key(节点体内没有 node_id 字段)
        node_items = [(node.get("node_id") or node.get("id") or nid, node)
                      for nid, node in nodes.items()]
    elif isinstance(nodes, list):
        node_items = [(node.get("node_id") or node.get("id"), node) for node in nodes]
    else:
        node_items = []

    condition_nodes = []
    for node_id, node in node_items:
        # 跳过 pass_through 节点
        if node.get("pass_through"):
            continue
        # 检查是否有判据
        cond = node.get("condition") or node.get("criteria")
        if cond:
            condition_nodes.append((node_id, node))

    fully_bound = []
    unbound = []

    for node_id, node in condition_nodes:
        if not node_id:
            continue

        if node_id in bound_nodes:
            fully_bound.append(node_id)
        else:
            unbound.append({
                "node_id": node_id,
                "name": node.get("name", ""),
                "condition": node.get("condition") or node.get("criteria", ""),
                "reason": f"节点{node_id}有判据但无任何绑定"
            })

    total = len(condition_nodes)
    binding_rate = len(fully_bound) / total if total > 0 else 0.0

    return {
        "total_condition_nodes": total,
        "fully_bound_count": len(fully_bound),
        "unbound_count": len(unbound),
        "binding_rate": binding_rate,
        "unbound": unbound
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="检查 tree_index 中节点的绑定情况")
    parser.add_argument("--tree-index", required=True, help="tree_index.json 路径")
    parser.add_argument("--scripts-dir", required=True, help="analyzer 脚本目录")
    parser.add_argument("--out", help="输出 JSON 路径（可选）")
    args = parser.parse_args()

    tree_index_path = Path(args.tree_index)
    scripts_dir = Path(args.scripts_dir)

    if not tree_index_path.exists():
        print(f"[error] tree_index 不存在: {tree_index_path}", file=sys.stderr)
        sys.exit(1)

    tree_index = load_tree_index(tree_index_path)
    bound_nodes = collect_all_bindings(scripts_dir)
    result = check_binding_gaps(tree_index, bound_nodes)

    print(f"[check_binding] 检查完成")
    print(f"  总条件节点数: {result['total_condition_nodes']}")
    print(f"  已绑定节点数: {result['fully_bound_count']}")
    print(f"  未绑定节点数: {result['unbound_count']}")
    print(f"  绑定率: {result['binding_rate']:.1%}")

    if result['unbound']:
        print(f"\n[warn] 未绑定节点（BINDING_GAP 候选）:")
        for n in result['unbound'][:5]:  # 只显示前5个
            print(f"  - {n['node_id']}: {n['name']}")
        if len(result['unbound']) > 5:
            print(f"  ... 还有 {len(result['unbound']) - 5} 个")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[output] 结果已写入: {out_path}")

    # 返回退出码：有未绑定节点则返回 1（警告），用于自动化检测
    sys.exit(1 if result['unbound'] else 0)


if __name__ == "__main__":
    main()
