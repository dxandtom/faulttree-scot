#!/usr/bin/env python3
"""check_tree.py - 检查 tree_index 中是否存在指定节点

用于 KNOWLEDGE_GAP 检测：expected.json 中的节点是否在 tree_index 中存在。

用法:
    python check_tree.py --tree-index <tree_index.json> --node-ids <node_id1,node_id2,...>
                        [--out <output.json>]

输出:
    检查结果 JSON，包含:
    - found: 在 tree_index 中存在的节点列表
    - missing: 在 tree_index 中不存在的节点列表（KNOWLEDGE_GAP 候选）
"""
from __future__ import annotations

import argparse
import json
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


def collect_tree_node_ids(tree_index: Dict[str, Any]) -> Set[str]:
    """收集 tree_index 中所有节点的 node_id"""
    node_ids: Set[str] = set()
    nodes = tree_index.get("nodes", {})

    if isinstance(nodes, dict):
        for node in nodes.values():
            nid = node.get("node_id") or node.get("id")
            if nid:
                node_ids.add(nid)
    elif isinstance(nodes, list):
        for node in nodes:
            nid = node.get("node_id") or node.get("id")
            if nid:
                node_ids.add(nid)

    return node_ids


def check_nodes_exist(tree_index: Dict[str, Any], target_ids: List[str]) -> Dict[str, Any]:
    """检查目标节点是否在 tree_index 中存在

    返回:
        {
            "found": [node_id, ...],
            "missing": [{node_id, reason}, ...]
        }
    """
    existing_ids = collect_tree_node_ids(tree_index)

    found = []
    missing = []

    for node_id in target_ids:
        if node_id in existing_ids:
            found.append(node_id)
        else:
            missing.append({
                "node_id": node_id,
                "reason": f"节点{node_id}在tree_index中不存在"
            })

    return {
        "total_checked": len(target_ids),
        "found_count": len(found),
        "missing_count": len(missing),
        "found": found,
        "missing": missing
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="检查节点是否在 tree_index 中存在")
    parser.add_argument("--tree-index", required=True, help="tree_index.json 路径")
    parser.add_argument("--node-ids", required=True, help="要检查的节点ID，逗号分隔")
    parser.add_argument("--out", help="输出 JSON 路径（可选）")
    args = parser.parse_args()

    tree_index_path = Path(args.tree_index)
    if not tree_index_path.exists():
        print(f"[error] tree_index 不存在: {tree_index_path}", file=sys.stderr)
        sys.exit(1)

    target_ids = [n.strip() for n in args.node_ids.split(",") if n.strip()]
    if not target_ids:
        print("[error] --node-ids 不能为空", file=sys.stderr)
        sys.exit(1)

    tree_index = load_tree_index(tree_index_path)
    result = check_nodes_exist(tree_index, target_ids)

    print(f"[check_tree] 检查完成")
    print(f"  总检查节点数: {result['total_checked']}")
    print(f"  存在节点数: {result['found_count']}")
    print(f"  缺失节点数: {result['missing_count']}")

    if result['missing']:
        print(f"\n[warn] 缺失节点（KNOWLEDGE_GAP 候选）:")
        for n in result['missing']:
            print(f"  - {n['node_id']}: {n['reason']}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[output] 结果已写入: {out_path}")

    # 返回退出码：有缺失节点则返回 1（警告），用于自动化检测
    sys.exit(1 if result['missing'] else 0)


if __name__ == "__main__":
    main()
