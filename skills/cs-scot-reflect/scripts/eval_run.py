#!/usr/bin/env python3
"""eval_run.py - cs-scot-reflect 主入口脚本（核心编排层）

用法:
    python eval_run.py --case <case_id> --result-dir <result-dir> --skill-dir <skill-dir>
                       [--cases-dir cases] [--out <output-dir>] [--prev-evaluation <prev-eval-dir>]

    python eval_run.py --case-dir <cases-parent-dir> --result-dir <result-base-dir>
                       --skill-dir <skill-dir> [--out <evaluation-base-dir>]
                       [--prev-evaluation <prev-eval-dir>]

v9 变更:
    - result-dir 支持两种模式：单 result 目录（直接含 rca.json）或多 result-* 子目录的父目录
    - 输出目录默认 D:\\.claude\\output\\<scenario>-eval/
    - 输出结构: <case_id>-v<round>-<时间戳>/evaluation_report.json（唯一对外出口）
    - 新增 --prev-evaluation 参数支持 loop 轮次追踪
    - verdict 只用 PASS/PASS_WITH_GAPS/PARTIAL_PASS/FAIL
    - success 字段表示评估过程正常完成（不依赖 verdict 结果）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 脚本所在目录（相对于本文件位置推导）
SCRIPT_DIR = Path(__file__).parent.resolve()
SCRIPTS_DIR = SCRIPT_DIR

# 默认目录名
DEFAULT_CASES_DIR = "cases"
DEFAULT_OUT_DIR_PREFIX = r"D:\.claude\output"  # v9: 默认输出到 output\<scenario>-eval/


def update_progress(progress_file: Path, current_task: str, status: str, message: str = "") -> None:
    """更新进度到临时文件

    Args:
        progress_file: 进度文件路径
        current_task: 当前执行的任务标识
        status: 状态 (started/completed/failed)
        message: 附加消息
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    entry = {
        "task": current_task,
        "status": status,
        "timestamp": timestamp,
        "message": message
    }
    lines = []
    if progress_file.exists():
        content = progress_file.read_text(encoding="utf-8")
        lines = content.strip().split("\n")

    # 找到并移除同类task的旧记录
    filtered = [l for l in lines if current_task not in l]
    filtered.append(json.dumps(entry, ensure_ascii=False))

    progress_file.write_text("\n".join(filtered) + "\n", encoding="utf-8")


def find_tree_indexes(skill_dir: Path) -> List[Path]:
    """在 skill-dir 中查找所有 tree_index.json

    路径模式: <skill-dir>/assets/tree/*.tree_index.json

    Returns:
        tree_index.json 文件路径列表
    """
    tree_dir = skill_dir / "assets" / "tree"
    if not tree_dir.exists():
        return list(skill_dir.glob("**/*.tree_index.json"))
    return sorted(tree_dir.glob("*.tree_index.json"))


def load_json(path: Path) -> Dict[str, Any]:
    """通用 JSON 加载"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] 无法读取 {path}: {e}", file=sys.stderr)
        return {}


def run_sub_script(script_path: Path, args: List[str], timeout: int = 60) -> Dict[str, Any]:
    """执行子脚本并返回结果

    Args:
        script_path: 脚本路径
        args: 命令行参数列表
        timeout: 超时秒数

    Returns:
        {"ok": bool, "returncode": int, "stdout": str, "stderr": str}
    """
    cmd = [sys.executable, str(script_path)] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace"
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"超时({timeout}s)"}
    except Exception as e:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": str(e)}


def detect_result_mode(result_dir: Path) -> Tuple[str, Optional[Path]]:
    """判断 result-dir 的模式

    Returns:
        (mode, effective_dir):
        - ("single", <result_dir>)  直接包含 rca.json
        - ("multi", <result_dir>)   包含多个 result-* 子目录
        - ("empty", None)           目录不存在或为空
    """
    if not result_dir.exists():
        return ("empty", None)

    # 模式1: 直接包含 rca.json → single-result 模式
    if (result_dir / "rca.json").exists():
        return ("single", result_dir)

    # 模式2: 包含 result-* 子目录 → multi-result 模式
    subdirs = [d for d in result_dir.iterdir() if d.is_dir() and d.name.startswith("result-")]
    if subdirs:
        return ("multi", result_dir)

    return ("empty", None)


def find_case_result_dir(result_dir: Path, case_id: str) -> Optional[Path]:
    """为指定 case 查找对应的 result 子目录

    优先返回包含 rca.json 的最新目录

    Returns:
        result 子目录路径，或 None
    """
    mode, effective_dir = detect_result_mode(result_dir)
    if mode == "single":
        return effective_dir

    # multi 模式：查找 result-* 子目录
    subdirs = find_result_subdirs(result_dir)
    for d in subdirs:
        if (d / "rca.json").exists():
            return d
    # 如果没找到，返回最新的（即使没有 rca.json）
    return subdirs[0] if subdirs else None


def find_result_subdirs(result_dir: Path) -> List[Path]:
    """在 result-dir 下查找所有 result-<timestamp> 格式的子目录

    Returns:
        按修改时间倒序排列的子目录列表
    """
    if not result_dir.exists():
        return []
    subdirs = sorted(
        [d for d in result_dir.iterdir() if d.is_dir() and d.name.startswith("result-")],
        key=lambda d: d.stat().st_mtime,
        reverse=True
    )
    return subdirs




def _generate_suggestion(error_category: str, details: Dict[str, Any]) -> Dict[str, Any]:
    """根据错误类别生成针对性的修复建议（SKILL.md suggestion 对象格式）

    返回结构与 SKILL.md 对齐：
    {
        "action": "ADD_BINDING",
        "target_script": "diag_analyzer.py",
        "target_node": "n4",
        "detail": "...",
        "template": "..."
    }
    """
    node_id = details.get("node_id", "unknown")
    node_name = details.get("name", "")

    # 熔断警告
    fuse_warning = ""
    if details.get("fused"):
        fuse_warning = "【熔断警告】该节点同类错误已连续多轮未修复，建议人工深查。"

    suggestions = {
        "ENV_FAILURE": {
            "action": "CHECK_ENV",
            "target_script": "run.json",
            "target_node": node_id,
            "detail": f"执行环境故障，检查 run.json 中 execution_status。节点 {node_id} 的脚本执行失败，需要人工排查环境问题后重跑 execute。{fuse_warning}",
            "template": None
        },
        "INPUT_GAP": {
            "action": "SUPPLY_DATA",
            "target_script": None,
            "target_node": None,
            "detail": f"数据源缺失：{details.get('missing_inputs', [])}。使用方补充数据后重跑 execute，不需要回流 generate。{fuse_warning}",
            "template": None
        },
        "BINDING_GAP": {
            "action": "ADD_BINDING",
            "target_script": "diag_analyzer.py",
            "target_node": node_id,
            "detail": f"节点 {node_id}（{node_name}）有判据但无任何绑定。在 skill-dir/scripts/ 的 analyzer 脚本中添加该节点的 binding 配置。{fuse_warning}",
            "template": "参考同类节点的 binding 写法"
        },
        "KNOWLEDGE_GAP": {
            "action": "ADD_NODE",
            "target_script": None,
            "target_node": node_id,
            "detail": f"节点 {node_id} 在 tree_index.json 中不存在。需要 tester 确认是否需要新增节点到故障树，然后执行 migrate 更新。{fuse_warning}",
            "template": None
        },
        "CRITERIA_GAP": {
            "action": "REVIEW_CRITERIA",
            "target_script": None,
            "target_node": node_id,
            "detail": f"节点 {node_id} 的 predicate_satisfied 与 expected_facts 不符。检查该节点的 binding 列配置和阈值设置，或确认 expected.json 是否需要修正。{fuse_warning}",
            "template": None
        },
        "VERDICT_GAP": {
            "action": "REVIEW_VERDICT",
            "target_script": None,
            "target_node": node_id,
            "detail": f"裁决与期望不符。节点 {node_id} 可能被错误地包含/排除在人造林辑中。建议人工审查 RCA 规则和裁决逻辑。{fuse_warning}",
            "template": None
        }
    }

    result = suggestions.get(error_category, {
        "action": "MANUAL_REVIEW",
        "target_script": None,
        "target_node": node_id,
        "detail": f"未知错误类型 {error_category}，需要人工检查。{fuse_warning}",
        "template": None
    })

    return result


def _update_fuse_history(
    fuse_history_file: Path,
    case_id: str,
    result: Dict[str, Any],
    fuse_history: List[Dict[str, Any]]
) -> None:
    """更新熔断历史

    检查是否有同类错误连续2轮未修复，如有则触发熔断
    """
    classify_data = result.get("classify", {})
    if not classify_data.get("classified"):
        return  # PASS 不计入

    error_class = classify_data.get("error_class")
    error_category = classify_data.get("error_category")
    details = classify_data.get("details", {})
    node_id = details.get("node_id", "unknown")

    # 检查同类错误是否已存在1轮
    same_error_count = 0
    for entry in fuse_history:
        if entry.get("case_id") == case_id:
            continue  # 不同case才计数
        if entry.get("error_class") == error_class and entry.get("node_id") == node_id:
            same_error_count += 1

    # 检查是否同case同类（当轮重复）
    same_round = [e for e in fuse_history if e.get("case_id") == case_id
                  and e.get("error_class") == error_class and e.get("node_id") == node_id]
    if same_round:
        return  # 同轮已记录，不重复

    # 记录本轮
    new_entry = {
        "case_id": case_id,
        "error_class": error_class,
        "error_category": error_category,
        "node_id": node_id,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    fuse_history.append(new_entry)

    # 熔断判断：同类错误跨case出现2次
    if same_error_count >= 1:
        fuse_entry = {
            "fused": True,
            "case_id": case_id,
            "node_id": node_id,
            "error_class": error_class,
            "error_category": error_category,
            "fuse_reason": f"同节点同类连改{2}轮不过",
            "rounds": [same_error_count, same_error_count + 1]
        }
        fuse_history.append(fuse_entry)
        print(f"[fuse] 熔断触发: {node_id} {error_category}")


def process_case(
    case_id: str,
    cases_dir: Path,
    result_dir: Path,
    skill_dir: Path,
    tree_indexes: List[Path],
    temp_dir: Path,
    progress_file: Path,
    scripts_dir: Path = SCRIPTS_DIR,
    shared_binding_check: Optional[Dict[str, Any]] = None,
    shared_tree_check: Optional[Dict[str, Any]] = None,
    fuse_history: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """处理单个 case 的评估

    流程：
    1. 定位 expected.json 和 result-dir
    2. 查找 tree_index.json
    3. 依次执行: reconcile → check_binding → check_tree → classify
    4. 收集结果到 temp 目录

    Returns:
        评估结果字典
    """
    task_prefix = f"case:{case_id}"

    # Step 1: 定位 expected.json
    expected_path = cases_dir / case_id / "expected.json"
    if not expected_path.exists():
        print(f"[error] expected.json 不存在: {expected_path}")
        update_progress(progress_file, f"{task_prefix}:expected_load", "failed", f"文件不存在: {expected_path}")
        return {"case_id": case_id, "ok": False, "error": f"expected.json 不存在: {expected_path}"}

    expected_data = load_json(expected_path)
    print(f"[{case_id}] expected.json 加载成功: {expected_path}")

    # Step 2: 定位 result 目录
    case_result_dir = find_case_result_dir(result_dir, case_id)
    if not case_result_dir:
        print(f"[warn] {case_id}: 未找到 result 子目录，跳过")
        update_progress(progress_file, f"{task_prefix}:result_locate", "failed", "未找到 result 目录")
        return {"case_id": case_id, "ok": False, "error": "未找到 result 目录"}

    run_path = case_result_dir / "run.json"
    rca_input_path = case_result_dir / "rca_input.json"
    rca_path = case_result_dir / "rca.json"

    print(f"[{case_id}] result 目录: {case_result_dir}")
    print(f"[{case_id}] run.json: {run_path.exists()}, rca_input.json: {rca_input_path.exists()}, rca.json: {rca_path.exists()}")

    # 加载执行产物
    run_data = load_json(run_path) if run_path.exists() else {}
    rca_input_data = load_json(rca_input_path) if rca_input_path.exists() else {}
    rca_data = load_json(rca_path) if rca_path.exists() else {}

    # Step 3: 执行子脚本（依次）
    # 3a: reconcile.py
    reconcile_out = temp_dir / f"{case_id}_reconcile.json"
    reconcile_args = [
        "--expected", str(expected_path),
        "--rca", str(rca_path),
        "--out", str(reconcile_out)
    ]
    print(f"[{case_id}] 执行 reconcile.py...")
    update_progress(progress_file, f"{task_prefix}:reconcile", "started")
    reconcile_result = run_sub_script(scripts_dir / "reconcile.py", reconcile_args)
    if reconcile_result["ok"]:
        print(f"[{case_id}] reconcile.py 成功")
        update_progress(progress_file, f"{task_prefix}:reconcile", "completed")
    else:
        print(f"[{case_id}] reconcile.py 失败: {reconcile_result['stderr']}")
        update_progress(progress_file, f"{task_prefix}:reconcile", "completed",
                        f"returncode={reconcile_result['returncode']}")

    # 加载 reconcile 结果
    reconcile_data = load_json(reconcile_out) if reconcile_out.exists() else {}

    # 3b: 绑定检查（使用共享结果，避免重复计算）
    # 优化: check_binding.py 是静态检查，所有 case 共用同一结果
    binding_check_data = shared_binding_check if shared_binding_check else {}
    if shared_binding_check:
        print(f"[{case_id}] 使用共享绑定检查结果，unbound_count={shared_binding_check.get('unbound_count', 'N/A')}")
        # 写入临时文件供 classify.py 读取（--binding-check 读文件）
        binding_check_path = temp_dir / f"{case_id}_binding.json"
        binding_check_path.write_text(json.dumps(shared_binding_check, ensure_ascii=False), encoding="utf-8")

    # 3c: 树结构检查（使用共享结果，避免重复计算）
    # 优化: check_tree.py 基于 tree_index，与具体 case 无关
    tree_check_data = shared_tree_check if shared_tree_check else {}
    if tree_check_data:
        print(f"[{case_id}] 使用共享树检查结果")

    # 如果需要为特定 case 单独检查 node_ids（在 case 级别执行一次）
    # 仅当 expected_root_causes 中的节点需要单独验证时执行（通常不需要）

    # 3d: classify.py
    classify_out = temp_dir / f"{case_id}_classify.json"
    tree_index_path = tree_indexes[0] if tree_indexes else ""

    classify_args = [
        "--run", str(run_path),
        "--rca-input", str(rca_input_path),
        "--rca", str(rca_path),
        "--tree-index", str(tree_index_path),
        "--expected", str(expected_path),
        "--out", str(classify_out)
    ]
    if binding_check_data:
        classify_args.extend(["--binding-check", str(temp_dir / f"{case_id}_binding.json")])
    if tree_check_data:
        classify_args.extend(["--tree-check", str(temp_dir / f"{case_id}_tree.json")])
    if reconcile_data:
        classify_args.extend(["--reconcile", str(reconcile_out)])

    print(f"[{case_id}] 执行 classify.py...")
    update_progress(progress_file, f"{task_prefix}:classify", "started")
    classify_result = run_sub_script(scripts_dir / "classify.py", classify_args)

    # classify.py 返回 exit code=1 表示"分类出非 PASS 结果"（设计如此，不是脚本错误）
    # 只有当输出文件不存在或无法读取时才真正算"失败"
    classify_data = load_json(classify_out) if classify_out.exists() else {}
    if classify_out.exists() and classify_data:
        print(f"[{case_id}] classify.py 完成（exit code={classify_result['returncode']}）")
        update_progress(progress_file, f"{task_prefix}:classify", "completed")
    else:
        # 真正失败：输出文件不存在或无法解析
        print(f"[{case_id}] classify.py 失败: {classify_result['stderr']}")
        update_progress(progress_file, f"{task_prefix}:classify", "completed",
                        f"returncode={classify_result['returncode']}")

    # Step 4: 收集结果
    print(f"[{case_id}] 收集结果完成")
    update_progress(progress_file, f"{task_prefix}:all", "completed", "处理完成")

    return {
        "case_id": case_id,
        "ok": True,
        "expected": expected_data,
        "run": run_data,
        "rca_input": rca_input_data,
        "rca": rca_data,
        "reconcile": reconcile_data,
        "binding_check": binding_check_data,
        "tree_check": tree_check_data,
        "classify": classify_data,
        "result_dir": str(case_result_dir)
    }


def build_v9_checks(
    binding_check_data: Dict[str, Any],
    tree_check_data: Dict[str, Any],
    classify_data: Dict[str, Any],
    run_data: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    """将各检查结果转换为 v9 SKILL.md 规定的 checks 结构

    SKILL.md checks 格式:
        {
          "env_failure":  {"passed": bool, "detail": any},
          "binding_gap":  {"passed": bool, "detail": any},
          "knowledge_gap":{"passed": bool, "detail": any},
          "criteria_gap": {"passed": bool, "detail": any},
          "verdict_gap":  {"passed": bool, "detail": any}
        }
    """
    # env_failure: 检查是否有执行失败的节点
    exec_failures = []
    execution_status = run_data.get("execution_status", {})
    for node_id, status in execution_status.items():
        if isinstance(status, dict) and not status.get("ok", True):
            exec_failures.append(node_id)
    env_failure_passed = len(exec_failures) == 0

    # binding_gap: 检查是否有未绑定节点
    unbound_count = binding_check_data.get("unbound_count", 0)
    unbound_nodes = binding_check_data.get("unbound_nodes", [])
    binding_gap_passed = unbound_count == 0

    # knowledge_gap: tree_check 返回 missing 节点列表
    missing_nodes = tree_check_data.get("missing", []) if tree_check_data else []
    knowledge_gap_passed = len(missing_nodes) == 0

    # criteria_gap: classify 中 CRITERIA_GAP 触发
    classified = classify_data.get("classified", False)
    error_class = classify_data.get("error_class", "")
    criteria_gap_passed = not (classified and error_class == "4")

    # verdict_gap: classify 中 VERDICT_GAP 触发
    verdict_gap_passed = not (classified and error_class == "5")

    return {
        "env_failure": {
            "passed": env_failure_passed,
            "detail": exec_failures if exec_failures else None
        },
        "binding_gap": {
            "passed": binding_gap_passed,
            "detail": unbound_nodes if unbound_nodes else None
        },
        "knowledge_gap": {
            "passed": knowledge_gap_passed,
            "detail": missing_nodes if missing_nodes else None
        },
        "criteria_gap": {
            "passed": criteria_gap_passed,
            "detail": classify_data.get("details", {}).get("node_id") if not criteria_gap_passed else None
        },
        "verdict_gap": {
            "passed": verdict_gap_passed,
            "detail": classify_data.get("details", {}).get("node_id") if not verdict_gap_passed else None
        }
    }


def _classify_to_verdict(classified: bool, error_category: str, error_class: Any) -> str:
    """将 classify 结果映射为标准 verdict

    v9 verdict 只用: PASS / PASS_WITH_GAPS / PARTIAL_PASS / FAIL

    Args:
        classified: 是否分类出错误
        error_category: 错误类别名（如 INPUT_GAP）
        error_class: 错误类别编号（来自 classify.py，是整数 0-5）
    """
    if not classified:
        return "PASS"

    # error_class 来自 classify.py，是整数类型 (0, 1, 2, 3, 4, 5)
    # 注意：classify.py 返回的 JSON 中 error_class 是整数，不是字符串
    error_class_int = int(error_class) if error_class is not None else -1

    if error_class_int in (0, 1):
        # 轻微: ENV_FAILURE(0) 已通过 checks.env_failure, INPUT_GAP(1) 有期望根因命中
        return "PASS_WITH_GAPS"
    elif error_class_int in (2, 3):
        # 中等: BINDING_GAP(2), KNOWLEDGE_GAP(3)
        return "PARTIAL_PASS"
    elif error_class_int in (4, 5):
        # 严重: CRITERIA_GAP(4), VERDICT_GAP(5)
        return "FAIL"
    else:
        # 未知错误类型
        return "FAIL"


def _build_data_gaps(
    run_data: Dict[str, Any],
    reconcile_data: Dict[str, Any],
    expected_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """从 missing_inputs 构建 data_gaps

    与 SKILL.md 格式对齐:
    {
        "missing_inputs": [...],
        "impact": "...",
        "note": "..."
    }

    Args:
        run_data: 执行数据，包含 missing_inputs
        reconcile_data: 对账数据，包含 hit_confirmed/missed 等
        expected_data: 期望数据，包含 expected_root_causes

    Returns:
        data_gaps 对象，或 None（无数据缺口时）
    """
    missing_inputs = run_data.get("missing_inputs", [])
    if not missing_inputs:
        return None

    # 获取对账结果判断数据缺口影响
    reconciliation = reconcile_data.get("reconciliation", {})
    hit_confirmed = reconciliation.get("hit_confirmed", 0)
    missed = reconciliation.get("missed", 0)
    total_expected = reconciliation.get("total_expected", 0)

    # 获取期望根因节点
    expected_roots = expected_data.get("expected_root_causes", [])
    expected_root_ids = [r.get("node_id") for r in expected_roots if r.get("node_id")]

    # 判断影响：如果所有期望根因都 HIT_CONFIRMED，则数据缺口不影响本次验证结论
    all_confirmed = (hit_confirmed == total_expected and missed == 0 and total_expected > 0)

    if all_confirmed:
        impact = f"数据缺口（{missing_inputs}）不影响本次验证结论；期望根因均已确证"
        note = "结论可靠；数据完整后可进一步验证父节点路径完整性"
    else:
        impact = f"存在漏判（missed={missed}），数据缺口可能导致根因分析不完整"
        note = "需补充数据后重新执行 evaluate"

    return {
        "missing_inputs": missing_inputs,
        "impact": impact,
        "note": note
    }


def generate_report(
    case_results: List[Dict[str, Any]],
    out_dir: Path,
    evaluation_time: str,
    prev_evaluation: Optional[Path] = None,
    skill_dir: Optional[Path] = None,
    temp_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """生成最终评估报告 (v9 格式)

    输出结构: <case_id>-v<round>-<timestamp>/evaluation_report.json
    每个 case 独立一个输出目录，evaluation_report.json 是唯一对外出口

    Args:
        case_results: 所有 case 的处理结果
        out_dir: 输出根目录
        evaluation_time: 评估时间（ISO格式）
        prev_evaluation: 上一次评估目录（用于 round 追踪）

    Returns:
        汇总报告
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # 汇总统计
    total = len(case_results)
    verdict_counts = {"PASS": 0, "PASS_WITH_GAPS": 0, "PARTIAL_PASS": 0, "FAIL": 0}
    all_case_summaries = []
    all_feedback = []
    all_failure_signatures = []

    for result in case_results:
        case_id = result.get("case_id", "unknown")
        classify_data = result.get("classify", {})
        reconcile_data = result.get("reconcile", {})
        expected_data = result.get("expected", {})
        rca_data = result.get("rca", {})
        run_data = result.get("run", {})
        binding_check_data = result.get("binding_check", {})
        tree_check_data = result.get("tree_check", {})

        # v9: scenario 必须在 case_issues 使用前先定义
        scenario = expected_data.get("scenario", "")

        # v9: 计算 round
        round_num, feedback_iteration = compute_round_and_feedback_iteration(
            prev_evaluation, case_id
        )

        # v9: 标准 verdict
        classified = classify_data.get("classified", False)
        error_category = classify_data.get("error_category", "")
        error_class = classify_data.get("error_class", "")
        verdict = _classify_to_verdict(classified, error_category, error_class)
        verdict_counts[verdict] += 1

        # v9: success = 评估过程正常完成（不依赖 verdict）
        success = result.get("ok", False)

        # 收集 failure_signatures 和 feedback_to_generate（先收集再写文件）
        case_failure_sigs = []
        case_issues = []
        if classified:
            details = classify_data.get("details", {})
            if details:
                # v9: failure_signatures 格式与 SKILL.md 对齐
                # recon_result 使用 classify verdict；priority 默认 high（中/低需人工判断）
                sig = {
                    "expected_node": (
                        expected_data.get("expected_root_causes", [{}])[0].get("node_id")
                        if expected_data.get("expected_root_causes") else None
                    ),
                    "recon_result": details.get("verdict", "MISSED"),
                    "error_class": error_class,
                    "reason": details.get("reason", ""),
                    "evidence": details.get("evidence", []),
                    "priority": "high"
                }
                case_failure_sigs.append(sig)
                all_failure_signatures.append(sig)

                # 构建 feedback_to_generate issue（与 SKILL.md 对齐）
                suggestion = _generate_suggestion(error_category, details)
                case_issues.append({
                    "error_class": error_category,  # SKILL.md 用 error_class 存类别名
                    "node_id": details.get("node_id", ""),
                    "node_name": details.get("name", ""),
                    "expected_result": "confirmed",
                    "actual_result": details.get("verdict", "unknown"),
                    "reason": details.get("reason", ""),
                    "suggestion": suggestion,
                    "priority": "high",
                    "previous_attempts": 0
                })

        # 构建 v9 格式 feedback_to_generate（每个 case 一个对象，与 SKILL.md 对齐）
        if case_issues:
            feedback = {
                "meta": {
                    "case_id": case_id,
                    "round": round_num,
                    "feedback_iteration": feedback_iteration,
                    "evaluation_time": evaluation_time,
                    "source_result_dir": result.get("result_dir", ""),
                    "scenario": scenario
                },
                "issues": case_issues,
                "next_action": "REGENERATE",
                "summary": f"第{round_num}轮评估：{case_id}存在{len(case_issues)}个问题，需generate修复后重新生成技能包"
            }
            all_feedback.append(feedback)

        # 构建 v9 格式 evaluation_report.json（与 SKILL.md 对齐）
        # reconciliation 需要合并 counts 和 details（reconcile.py 的 details 在顶层）
        reconciliation_base = reconcile_data.get("reconciliation", {})
        reconciliation_details = reconcile_data.get("details", [])
        reconciliation_full = {
            **reconciliation_base,
            "details": reconciliation_details
        }

        case_report = {
            "success": success,  # v9: 评估过程正常完成（不依赖 verdict）
            "meta": {
                "case_id": case_id,
                "scenario": scenario,
                "round": round_num,
                "feedback_iteration": feedback_iteration,
                "evaluation_time": evaluation_time,
                "verdict": verdict,  # v9: 与 verdict_detail.verdict 保持一致
                "result_dir": result.get("result_dir", ""),
                "skill_dir": str(skill_dir) if skill_dir else "",
                "source_result_dir": result.get("result_dir", "")
            },
            "reconciliation": reconciliation_full,  # 合并 counts + details
            # data_gaps: 从 run_data.missing_inputs 构建，与 SKILL.md 格式对齐
            "data_gaps": _build_data_gaps(run_data, reconcile_data, expected_data),
            "failure_signatures": case_failure_sigs,
            "verdict_detail": {
                "verdict": verdict,
                "error_class": error_class if classified else None,
                "reason": classify_data.get("details", {}).get("reason", "") if classified else ""
            },
            "feedback_to_generate": None,  # 由根目录统一生成
            "checks": build_v9_checks(binding_check_data, tree_check_data, classify_data, run_data)
        }

        # 创建 case 独立输出目录: <case_id>-v<round>-<timestamp>/
        case_out_dir = out_dir / f"{case_id}-v{round_num}-{timestamp}"
        case_out_dir.mkdir(parents=True, exist_ok=True)

        # 写入 evaluation_report.json（唯一对外出口）
        report_path = case_out_dir / "evaluation_report.json"
        report_path.write_text(
            json.dumps(case_report, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"[eval_run] {case_id}: 报告已写入 {report_path}")

        # 写入 eval_run.json（本次评估元数据，内部用）
        eval_run_meta = {
            "case_id": case_id,
            "round": round_num,
            "feedback_iteration": feedback_iteration,
            "evaluation_time": evaluation_time,
            "verdict": verdict,
            "success": success,
            "result_dir": result.get("result_dir", ""),
            "temp_dir": str(temp_dir)
        }
        eval_run_path = case_out_dir / "eval_run.json"
        eval_run_path.write_text(
            json.dumps(eval_run_meta, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # 写入 failure_signatures.json（per-case 内部用）
        if case_failure_sigs:
            sig_path = case_out_dir / "failure_signatures.json"
            sig_path.write_text(
                json.dumps(case_failure_sigs, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        # 同时在根目录生成汇总（index 用途）
        all_case_summaries.append({
            "case_id": case_id,
            "verdict": verdict,
            "round": round_num,
            "report_path": str(report_path.relative_to(out_dir))
        })

    # 生成根目录汇总（index）
    summary_report = {
        "evaluation_time": evaluation_time,
        "total_cases": total,
        "verdicts": verdict_counts,
        "pass_rate": f"{verdict_counts['PASS'] / total * 100:.1f}%" if total > 0 else "0%",
        "cases": all_case_summaries
    }

    index_path = out_dir / "index.json"
    index_path.write_text(
        json.dumps(summary_report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"[eval_run] 汇总已写入 {index_path}")

    # 写入 failure_signatures.json
    if all_failure_signatures:
        sig_path = out_dir / "failure_signatures.json"
        sig_path.write_text(
            json.dumps(all_failure_signatures, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"[eval_run] 失败签名已写入 {sig_path}")

    # 写入 feedback_to_generate.json
    if all_feedback:
        fb_path = out_dir / "feedback_to_generate.json"
        fb_path.write_text(
            json.dumps(all_feedback, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"[eval_run] 反馈已写入 {fb_path}")

    return summary_report


def finalize(temp_dir: Path, out_dir: Path) -> None:
    """将 temp 目录内容拷贝到 output 目录

    Args:
        temp_dir: 临时目录
        out_dir: 最终输出目录
    """
    if not temp_dir.exists():
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    for f in temp_dir.iterdir():
        dest = out_dir / f.name
        dest.write_bytes(f.read_bytes())

    print(f"[eval_run] temp 内容已拷贝到: {out_dir}")


def compute_round_and_feedback_iteration(
    prev_evaluation: Optional[Path],
    case_id: str
) -> Tuple[int, int]:
    """计算当前 case 的 round 和 feedback_iteration

    从上一次 evaluation 目录中查找该 case 的记录，推算轮次

    Args:
        prev_evaluation: 上一次 evaluation 目录路径
        case_id: 用例ID

    Returns:
        (round, feedback_iteration): 轮次从 1 开始
    """
    if not prev_evaluation or not prev_evaluation.exists():
        return (1, 0)  # v9: 无上轮时 round=1, feedback_iteration=0

    # 查找上一次该 case 的评估结果
    # 格式: <case_id>-v<round>-<timestamp>/evaluation_report.json
    prev_reports = sorted(
        [
            d for d in prev_evaluation.iterdir()
            if d.is_dir() and d.name.startswith(f"{case_id}-v")
            and (d / "evaluation_report.json").exists()
        ],
        key=lambda d: d.name,
        reverse=True  # 最新的在前面
    )

    if not prev_reports:
        return (1, 0)  # 无上轮记录

    # 读取上一次评估报告，获取 feedback_iteration 和 verdict
    latest_report_path = prev_reports[0] / "evaluation_report.json"
    prev_report = load_json(latest_report_path)

    # 从目录名解析 round: <case_id>-v<round>-<timestamp>
    latest = prev_reports[0].name
    parts = latest.split("-v")
    if len(parts) < 2:
        return (1, 0)

    try:
        prev_round = int(parts[1].split("-")[0])
    except ValueError:
        return (1, 0)

    # v9: feedback_iteration 继承机制
    # - 若上轮 verdict 为 PASS/PASS_WITH_GAPS（无 feedback），feedback_iteration 保持不变（或清零）
    # - 若上轮有 feedback（verdict 为 PARTIAL_PASS/FAIL），feedback_iteration + 1
    prev_verdict = prev_report.get("verdict", "")
    prev_feedback_iteration = prev_report.get("meta", {}).get("feedback_iteration", 0)

    # feedback_iteration 只在上轮有 feedback 时才 +1
    if prev_verdict in ("PARTIAL_PASS", "FAIL"):
        new_feedback_iteration = prev_feedback_iteration + 1
    else:
        new_feedback_iteration = 0  # PASS/PASS_WITH_GAPS 时清零

    return (prev_round + 1, new_feedback_iteration)


def build_default_out_dir(result_dir: Path, scenario: str) -> Path:
    """根据 result-dir 构建默认输出目录

    v9 规则: D:\\.claude\\output\\<scenario>-eval/

    Args:
        result_dir: 执行结果目录
        scenario: 场景名（从 result-dir 名称推断）

    Returns:
        默认输出目录路径
    """
    # 尝试从 result-dir 名称推断 scenario
    # result-dir 可能是: .../ldp-lsp-result/ 或 .../ldp-lsp/
    result_name = result_dir.name
    # 去掉可能的 -result 后缀
    if result_name.endswith("-result"):
        scenario = result_name[:-8]  # 去掉 "-result"
    elif result_name == "result":
        # result-dir 本身叫 "result"，尝试用父目录名
        parent_name = result_dir.parent.name
        if parent_name not in (".", ""):
            scenario = parent_name
    else:
        scenario = result_name if scenario else "unknown"

    return Path(DEFAULT_OUT_DIR_PREFIX) / f"{scenario}-eval"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="cs-scot-reflect 评估主入口 (v9)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 单用例模式
    python eval_run.py --case ldp-lsp-001 --result-dir output/ldp-lsp-result \\
        --skill-dir scot技能/ldp-lsp

    # 批量模式（自动推断 scenario）
    python eval_run.py --case-dir cases --result-dir output/ldp-lsp-result \\
        --skill-dir scot技能/ldp-lsp --out D:\\.claude\\output\\ldp-lsp-eval

    # 带上一轮评估记录（用于 round 追踪）
    python eval_run.py --case ldp-lsp-001 --result-dir output/ldp-lsp-result \\
        --skill-dir scot技能/ldp-lsp --prev-evaluation D:\\.claude\\output\\ldp-lsp-eval
"""
    )
    parser.add_argument("--case", help="用例ID（如 ldp-lsp-001）")
    parser.add_argument("--case-dir", help="用例根目录（批量模式）")
    parser.add_argument("--result-dir", required=True,
                        help="执行结果目录（支持 single-result 模式或多 result-* 子目录）")
    parser.add_argument("--skill-dir", required=True, help="技能包目录")
    parser.add_argument("--cases-dir", default=DEFAULT_CASES_DIR,
                        help="用例目录（默认: cases）")
    parser.add_argument("--out",
                        help="评估输出目录（默认: D:\\.claude\\output\\<scenario>-eval/）")
    parser.add_argument("--prev-evaluation",
                        help="上一次评估结果目录（用于 round 追踪）")
    parser.add_argument("--scenario", default="",
                        help="场景名（用于构建默认输出目录，不指定时从 result-dir 推断）")
    args = parser.parse_args()

    # 参数校验
    if not args.case and not args.case_dir:
        parser.error("--case 或 --case-dir 必须指定一个")
    if args.case and args.case_dir:
        parser.error("--case 和 --case-dir 不能同时指定")

    result_dir = Path(args.result_dir)
    skill_dir = Path(args.skill_dir)
    cases_dir = Path(args.cases_dir)
    prev_evaluation = Path(args.prev_evaluation) if args.prev_evaluation else None

    # v9: 输出目录默认规则
    scenario = args.scenario.strip() if args.scenario else result_dir.name
    out_dir = Path(args.out) if args.out else build_default_out_dir(result_dir, scenario)

    # v9: 检测 result-dir 模式
    mode, _ = detect_result_mode(result_dir)
    print(f"[eval_run] result_dir={result_dir} (mode={mode})")
    print(f"[eval_run] skill_dir={skill_dir}")
    print(f"[eval_run] out_dir={out_dir}")
    if prev_evaluation:
        print(f"[eval_run] prev_evaluation={prev_evaluation}")

    # 创建临时目录（用于中途落盘）
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    temp_base = Path("temp")
    temp_base.mkdir(exist_ok=True)
    temp_dir = temp_base / f"cs-scot-reflect-{timestamp}"
    temp_dir.mkdir(exist_ok=True)
    progress_file = temp_dir / "progress.jsonl"
    progress_file.write_text("", encoding="utf-8")
    print(f"[eval_run] 临时目录: {temp_dir}")

    # 单用例模式
    if args.case:
        case_ids = [args.case]
    else:
        # 批量模式：扫描 case-dir 下所有子目录
        if not cases_dir.exists():
            print(f"[error] case-dir 不存在: {cases_dir}", file=sys.stderr)
            sys.exit(1)
        case_ids = [d.name for d in cases_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]

    print(f"[eval_run] 待评估用例数: {len(case_ids)}")

    # 查找 tree_index（所有用例共用同一个 skill-dir）
    tree_indexes = find_tree_indexes(skill_dir)
    if not tree_indexes:
        print(f"[warn] 未找到 tree_index.json，绑定检查和树检查将跳过")
    else:
        print(f"[eval_run] 找到 {len(tree_indexes)} 个 tree_index.json")

    # ===== 优化1: 静态检查提到循环外，每个case复用 =====
    # check_binding.py 和 check_tree.py 是静态检查，基于 tree_index，与具体 case 无关
    shared_binding_check = {}
    shared_tree_check = {}

    if tree_indexes:
        tree_index = tree_indexes[0]

        # 执行一次 check_binding.py
        binding_out = temp_dir / "shared_binding_check.json"
        binding_args = [
            "--tree-index", str(tree_index),
            "--scripts-dir", str(skill_dir / "scripts"),
            "--out", str(binding_out)
        ]
        print(f"[eval_run] 执行共享绑定检查（所有case共用）...")
        update_progress(progress_file, "shared:binding_check", "started")
        binding_result = run_sub_script(SCRIPTS_DIR / "check_binding.py", binding_args)
        if binding_result["ok"]:
            print(f"[eval_run] 共享绑定检查成功")
            shared_binding_check = load_json(binding_out)
            update_progress(progress_file, "shared:binding_check", "completed")
        else:
            print(f"[eval_run] 共享绑定检查失败: {binding_result['stderr']}")
            update_progress(progress_file, "shared:binding_check", "completed",
                            f"returncode={binding_result['returncode']}")

    # ===== 熔断历史加载 =====
    fuse_history_file = temp_dir / "fuse_history.jsonl"
    fuse_history = []
    if fuse_history_file.exists():
        content = fuse_history_file.read_text(encoding="utf-8")
        for line in content.strip().split("\n"):
            if line:
                fuse_history.append(json.loads(line))
    print(f"[eval_run] 熔断历史记录数: {len(fuse_history)}")

    # 批量评估每个 case
    all_results = []
    for case_id in case_ids:
        print(f"\n{'='*60}")
        print(f"[eval_run] 处理用例: {case_id}")
        print(f"{'='*60}")

        result = process_case(
            case_id=case_id,
            cases_dir=cases_dir,
            result_dir=result_dir,
            skill_dir=skill_dir,
            tree_indexes=tree_indexes,
            temp_dir=temp_dir,
            progress_file=progress_file,
            shared_binding_check=shared_binding_check,
            shared_tree_check=shared_tree_check,
            fuse_history=fuse_history
        )
        all_results.append(result)

        # 更新熔断历史
        _update_fuse_history(fuse_history_file, case_id, result, fuse_history)

    # 保存熔断历史
    fuse_history_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in fuse_history) + "\n",
        encoding="utf-8"
    )

    # 生成最终报告
    evaluation_time = datetime.now(timezone.utc).isoformat()
    summary = generate_report(all_results, out_dir, evaluation_time, prev_evaluation, skill_dir, temp_dir)

    # 将临时目录内容归档到输出目录（eval_run.json 等元数据）
    finalize(temp_dir, out_dir)

    # v9: 输出 verdicts 统计
    verdicts = summary.get("verdicts", {})
    print(f"\n{'='*60}")
    print(f"[eval_run] 评估完成")
    print(f"[eval_run] 总用例: {summary['total_cases']}, 通过率: {summary['pass_rate']}")
    print(f"[eval_run] verdicts: {verdicts}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
