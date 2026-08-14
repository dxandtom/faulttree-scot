#!/usr/bin/env python3
"""classify.py - 分类层：按0→5顺序查错，命中即停

基于对账结果 + 执行状态 + 树结构检查结果，执行错误分类。

用法:
    python classify.py --run <run.json> --rca-input <rca_input.json> --rca <rca.json>
                       --tree-index <tree_index.json> --expected <expected.json>
                       [--binding-check <binding_check.json>] [--tree-check <tree_check.json>]
                       [--reconcile <reconcile.json>]
                       [--out <output.json>]

错误分类（按序命中即停）:
    0: ENV_FAILURE - execution_status.*.ok == false 或 run.json不存在
    1: INPUT_GAP - missing_inputs非空
    2: BINDING_GAP - 有判据但无绑定
    3: KNOWLEDGE_GAP - tree_index不存在该节点
    4: CRITERIA_GAP - predicate vs expected_facts不相等
    5: VERDICT_GAP - 裁决≠期望 或 orphan非空
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# 错误类别定义
ERROR_CLASSES = {
    0: "ENV_FAILURE",
    1: "INPUT_GAP",
    2: "BINDING_GAP",
    3: "KNOWLEDGE_GAP",
    4: "CRITERIA_GAP",
    5: "VERDICT_GAP"
}

# 错误类别对应的 verdict
VERDICTS = {
    "ENV_FAILURE": "run_invalid",
    "INPUT_GAP": "needs_data",
    "BINDING_GAP": "needs_binding",
    "KNOWLEDGE_GAP": "needs_knowledge",
    "CRITERIA_GAP": "needs_criteria_review",
    "VERDICT_GAP": "needs_adjudication"
}


def load_json(path: Path) -> Dict[str, Any]:
    """通用 JSON 加载"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] 无法读取 {path}: {e}", file=sys.stderr)
        return {}


def check_env_failure(run_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """类0: ENV_FAILURE 检测

    条件: execution_status.*.ok == false 或 run.json不存在
    """
    if not run_data:
        return {
            "class": 0,
            "error_category": "ENV_FAILURE",
            "verdict": "run_invalid",
            "reason": "run.json 不存在或无法读取"
        }

    exec_status = run_data.get("execution_status", {})
    failed_steps = []

    for step, status in exec_status.items():
        if isinstance(status, dict):
            if status.get("ok") is False:
                failed_steps.append({
                    "step": step,
                    "error": status.get("error", "unknown")
                })

    if failed_steps:
        return {
            "class": 0,
            "error_category": "ENV_FAILURE",
            "verdict": "run_invalid",
            "reason": f"执行步骤失败: {[s['step'] for s in failed_steps]}",
            "failed_steps": failed_steps
        }

    return None


def check_input_gap(run_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """类1: INPUT_GAP 检测

    条件: missing_inputs非空
    """
    missing = run_data.get("missing_inputs", [])
    if missing:
        return {
            "class": 1,
            "error_category": "INPUT_GAP",
            "verdict": "needs_data",
            "reason": f"缺失数据源: {missing}",
            "missing_inputs": missing
        }
    return None


def check_binding_gap(binding_check: Dict[str, Any], missed_nodes: List[str]) -> Optional[Dict[str, Any]]:
    """类2: BINDING_GAP 检测

    条件: 有判据但无绑定（静态查tree_index）
    """
    if not binding_check:
        return None

    unbound = binding_check.get("unbound", [])
    if unbound:
        # 取第一个未绑定节点作为代表
        first = unbound[0]
        return {
            "class": 2,
            "error_category": "BINDING_GAP",
            "verdict": "needs_binding",
            "node_id": first.get("node_id"),
            "node_name": first.get("name", ""),
            "reason": f"节点{first.get('node_id')}有判据但无任何绑定",
            "evidence": ["tree_index.json#" + first.get("node_id", "")]
        }
    return None


def check_knowledge_gap(tree_check: Dict[str, Any], expected_roots: List[Dict]) -> Optional[Dict[str, Any]]:
    """类3: KNOWLEDGE_GAP 检测

    条件: tree_index不存在该节点
    """
    if not tree_check:
        return None

    missing = tree_check.get("missing", [])
    if missing:
        first = missing[0]
        return {
            "class": 3,
            "error_category": "KNOWLEDGE_GAP",
            "verdict": "needs_knowledge",
            "node_id": first.get("node_id"),
            "reason": f"节点{first.get('node_id')}在tree_index中不存在"
        }
    return None


def check_criteria_gap(
    rca_input_data: Dict[str, Any],
    expected_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """类4: CRITERIA_GAP 检测

    条件: predicate_satisfied vs expected_facts不相等
    """
    expected_facts = expected_data.get("expected_facts", {})
    if not expected_facts:
        return None

    # 从 rca_input 中提取各节点的 predicate_satisfied
    indicator_results = rca_input_data.get("indicator_results", {})
    actual_facts = {}

    for source, result in indicator_results.items():
        conditions = result.get("conditions", [])
        for cond in conditions:
            nid = cond.get("node_id")
            if nid:
                actual_facts[nid] = {
                    "predicate_satisfied": cond.get("predicate_satisfied"),
                    "source": source
                }

    # 比对
    for node_id, expected_fact in expected_facts.items():
        expected_satisfied = expected_fact.get("predicate_satisfied")
        actual_fact = actual_facts.get(node_id)
        if actual_fact is None:
            continue

        actual_satisfied = actual_fact.get("predicate_satisfied")
        if expected_satisfied != actual_satisfied:
            return {
                "class": 4,
                "error_category": "CRITERIA_GAP",
                "verdict": "needs_criteria_review",
                "node_id": node_id,
                "expected_predicate": expected_satisfied,
                "actual_predicate": actual_satisfied,
                "reason": f"节点{node_id}的predicate_satisfied与期望不符"
            }

    return None


def check_verdict_gap(
    rca_data: Dict[str, Any],
    reconcile_data: Dict[str, Any],
    missed_nodes: List[str]
) -> Optional[Dict[str, Any]]:
    """类5: VERDICT_GAP 检测

    条件: 裁决≠期望 或 orphan非空
    """
    # 检查孤儿节点
    tree_issues = rca_data.get("tree_issues", {})
    orphan_nodes = tree_issues.get("orphan_node_ids", [])

    if orphan_nodes:
        return {
            "class": 5,
            "error_category": "VERDICT_GAP",
            "verdict": "needs_adjudication",
            "reason": f"存在孤立节点: {orphan_nodes}",
            "orphan_nodes": orphan_nodes
        }

    # 检查漏判
    reconciliation = reconcile_data.get("reconciliation", {})
    missed = reconciliation.get("missed", 0)

    if missed > 0:
        # 找出漏判的节点
        details = reconcile_data.get("details", [])
        missed_details = [d for d in details if d.get("result") == "MISSED"]
        if missed_details:
            first = missed_details[0]
            return {
                "class": 5,
                "error_category": "VERDICT_GAP",
                "verdict": "needs_adjudication",
                "node_id": first.get("node_id"),
                "node_name": first.get("node_name", ""),
                "reason": f"期望根因{first.get('node_id')}不在RCA结论中",
                "expected_in": "rca_result",
                "actual_in": "none"
            }

    return None


def classify(
    run_data: Dict[str, Any],
    rca_input_data: Dict[str, Any],
    rca_data: Dict[str, Any],
    tree_index_data: Dict[str, Any],
    expected_data: Dict[str, Any],
    binding_check: Optional[Dict[str, Any]] = None,
    tree_check: Optional[Dict[str, Any]] = None,
    reconcile_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """执行完整分类流程

    按 0→5 顺序查错，命中即停。
    """
    # 提取漏判节点列表（用于 BINDING_GAP 和 VERDICT_GAP 定位）
    missed_nodes = []
    if reconcile_data:
        details = reconcile_data.get("details", [])
        missed_nodes = [d.get("node_id") for d in details if d.get("result") == "MISSED"]

    # 提取期望根因节点列表
    expected_roots = expected_data.get("expected_root_causes", [])

    # 按顺序检测各类错误
    checks = [
        ("ENV_FAILURE", lambda: check_env_failure(run_data)),
        ("INPUT_GAP", lambda: check_input_gap(run_data)),
        ("BINDING_GAP", lambda: check_binding_gap(binding_check, missed_nodes)),
        ("KNOWLEDGE_GAP", lambda: check_knowledge_gap(tree_check, expected_roots)),
        ("CRITERIA_GAP", lambda: check_criteria_gap(rca_input_data, expected_data)),
        ("VERDICT_GAP", lambda: check_verdict_gap(rca_data, reconcile_data, missed_nodes))
    ]

    for class_num in range(6):
        name, check_fn = checks[class_num]
        result = check_fn()
        if result:
            result["error_class"] = class_num
            return {
                "classified": True,
                "error_class": class_num,
                "error_category": name,
                "details": result
            }

    # 全部通过
    return {
        "classified": False,
        "error_class": None,
        "error_category": None,
        "details": None
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="分类层：按0→5顺序查错")
    parser.add_argument("--run", required=True, help="run.json 路径")
    parser.add_argument("--rca-input", required=True, help="rca_input.json 路径")
    parser.add_argument("--rca", required=True, help="rca.json 路径")
    parser.add_argument("--tree-index", required=True, help="tree_index.json 路径")
    parser.add_argument("--expected", required=True, help="expected.json 路径")
    parser.add_argument("--binding-check", help="binding_check.json 路径（可选）")
    parser.add_argument("--tree-check", help="tree_check.json 路径（可选）")
    parser.add_argument("--reconcile", help="reconcile.json 路径（可选）")
    parser.add_argument("--out", help="输出 JSON 路径（可选）")
    args = parser.parse_args()

    run_data = load_json(Path(args.run))
    rca_input_data = load_json(Path(args.rca_input))
    rca_data = load_json(Path(args.rca))
    tree_index_data = load_json(Path(args.tree_index))
    expected_data = load_json(Path(args.expected))
    binding_check = load_json(Path(args.binding_check)) if args.binding_check else {}
    tree_check = load_json(Path(args.tree_check)) if args.tree_check else {}
    reconcile_data = load_json(Path(args.reconcile)) if args.reconcile else {}

    result = classify(
        run_data, rca_input_data, rca_data, tree_index_data, expected_data,
        binding_check, tree_check, reconcile_data
    )

    if result["classified"]:
        print(f"[classify] 分类结果: 类{result['error_class']} {result['error_category']}")
        print(f"  verdict: {result['details'].get('verdict', 'unknown')}")
        print(f"  reason: {result['details'].get('reason', '')}")
    else:
        print("[classify] 分类结果: 全部通过 (PASS)")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[output] 结果已写入: {out_path}")

    sys.exit(1 if result["classified"] else 0)


if __name__ == "__main__":
    main()
