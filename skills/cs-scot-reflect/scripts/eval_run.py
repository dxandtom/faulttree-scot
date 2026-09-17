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
    - 输出结构: <case_id>-v<round>-<时间戳>/evaluation_report.json（唯一对外出口）
    - 新增 --prev-evaluation 参数支持 loop 轮次追踪
    - verdict 只用 PASS/PASS_WITH_GAPS/PARTIAL_PASS/FAIL
    - success 字段表示评估过程正常完成（不依赖 verdict 结果）

v10 变更（对接执行链实测修正）:
    - verdict 按 SKILL.md 决策树统一：全命中→PASS / PASS_WITH_GAPS；有漏判/假阳→
      触发类0~5则 FAIL、否则 PARTIAL_PASS；评估过程自身失败→success=false + EVAL_ERROR
    - 批量模式 case↔result 按目录名匹配（执行时用 runner 的 --result-suffix 带上 case_id），
      匹配不到时响亮报错，绝不拿别的用例的结果顶数
    - check_tree 按用例接线（查该用例期望节点是否在树上）；checks 面板类型比较修复
    - 熔断历史固定存 --out 目录（跨轮生效），按 同case+同节点+同类 计数
    - --prev-evaluation 兼容传报告文件或评估目录；round/feedback_iteration 从 meta 读
    - 默认输出目录改为 <result-dir 父目录>/<scenario>-eval（去掉盘符硬编码）；
      临时文件走系统临时目录，不再整体回拷输出目录
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batch_summary import format_line, read_case_score, write_batch_score  # noqa: E402  批量汇总的唯一口径

# 脚本所在目录（相对于本文件位置推导）
SCRIPT_DIR = Path(__file__).parent.resolve()
SCRIPTS_DIR = SCRIPT_DIR

# 默认目录名
DEFAULT_CASES_DIR = "cases"


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


def find_skill_scripts_dir(skill_dir: Path, tree_index: Path) -> Path:
    """analyzer 脚本目录——跟着 tree_index 走：两种布局里 tree/ 与 scripts/ 都是兄弟目录
    （产物根/tree ↔ 产物根/scripts；<skill>/assets/tree ↔ <skill>/assets/scripts）。
    指错目录 = check_binding 一个绑定都看不见 → 全树被当"未绑定"，一切漏判错归 BINDING_GAP。
    """
    for d in (tree_index.parent.parent / "scripts",
              skill_dir / "assets" / "scripts", skill_dir / "scripts"):
        if d.is_dir():
            return d
    return skill_dir / "scripts"


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


def _norm_path(p: str) -> str:
    """路径归一化成可比对的字符串：统一斜杠、去尾分隔符、casefold（Windows 大小写不敏感）。"""
    return str(p or "").replace("\\", "/").rstrip("/").casefold()


def _run_inputs_match(result_subdir: Path, data_anchors: List[str]) -> bool:
    """查这轮运行的"出生证明"：run.json 的 inputs 里记着本轮吃的是哪些数据路径（runner
    每轮都写）。任一记录路径与用例的数据目录互为前缀（同一目录/上下级）即认定归属。"""
    run_p = result_subdir / "run.json"
    if not run_p.exists() or not data_anchors:
        return False
    inputs = load_json(run_p).get("inputs", {})
    anchors = [_norm_path(a) for a in data_anchors if a]
    for info in inputs.values():
        rec = _norm_path(info.get("path", "") if isinstance(info, dict) else "")
        if not rec:
            continue
        for a in anchors:
            if rec == a or rec.startswith(a + "/") or a.startswith(rec + "/"):
                return True
    return False


def find_case_result_dir(result_dir: Path, case_id: str,
                         data_anchors: Optional[List[str]] = None) -> Optional[Path]:
    """为指定 case 查找对应的 result 子目录

    single 模式（用户直接指到结果目录）原样采用。
    multi 模式按阶梯对号（多个命中取最新;带 -abandoned/-superseded 后缀的目录不参与）:
    ① 目录名含完整 case_id（执行时用 --result-suffix 显式标注的，仍然优先认）；
    ①b 目录名后缀段与 case_id 按段精确比对（后缀只认字母数字-_,执行侧常只带 nX 短段;
       n5 只配 n5-*，配不上 n50-*）；
    ② run.json 记录的输入数据路径落在本用例数据目录内（runner 每轮都写"出生证明"；
       注意执行侧若喂的是合并副本目录，此通道会失手——①b 就是为这兜底）；
    ③ 只有一个候选 → 用它并警告；
    ④ 都对不上 → 拒绝猜测返回 None（拿别的用例的结果对账会把漏判/假阳全部张冠李戴）。

    Returns:
        result 子目录路径，或 None
    """
    mode, effective_dir = detect_result_mode(result_dir)
    if mode == "single":
        return effective_dir

    # multi 模式：查找 result-* 子目录（按修改时间倒序）
    subdirs = find_result_subdirs(result_dir)
    with_rca = [d for d in subdirs if (d / "rca.json").exists()]
    if not with_rca:
        return subdirs[0] if len(subdirs) == 1 else None

    named = [d for d in with_rca if case_id and case_id in d.name]
    if named:
        return named[0]

    by_sfx = [d for d in with_rca if _suffix_token_match(d, case_id)]
    if by_sfx:
        if len(by_sfx) > 1:
            print(f"[warn] {case_id}: 短后缀命中 {len(by_sfx)} 个目录,取最新 {by_sfx[0].name}"
                  f"(旧轮次确不该参与对号就改名加 -superseded 后缀移出)")
        return by_sfx[0]

    by_inputs = [d for d in with_rca if _run_inputs_match(d, data_anchors or [])]
    if by_inputs:
        print(f"[info] {case_id}: 按 run.json 记录的输入数据路径对号 → {by_inputs[0].name}")
        return by_inputs[0]

    if len(with_rca) == 1:
        print(f"[warn] {case_id}: 目录名/数据路径都对不上，但只有这一个候选，采用 "
              f"{with_rca[0].name}")
        return with_rca[0]

    print(f"[error] {case_id}: {len(with_rca)} 个 result 目录既不含该 case_id、run.json 记录的"
          f"数据路径也不在本用例数据目录内，无法确定归属，拒绝猜测。"
          f"最稳的修法：执行诊断时直接跑本用例自己的数据目录（run.json 会自动记下），"
          f"或用 runner --result-suffix {case_id} 显式标注。候选: "
          + ", ".join(d.name for d in with_rca[:5]))
    return None


_RETIRED_SUFFIXES = ("-abandoned", "-superseded")   # 人工标记"移出对号"的目录后缀(空壳/残次轮留档)


def find_result_subdirs(result_dir: Path) -> List[Path]:
    """在 result-dir 下查找所有 result-<timestamp> 格式的子目录

    带 -abandoned / -superseded 后缀的目录 = 人工标记的空壳/残次轮留档,不参与对号
    (实测:首次探路只喂了部分数据的残次轮若参与对号,可能顶掉正式轮)。

    Returns:
        按修改时间倒序排列的子目录列表
    """
    if not result_dir.exists():
        return []
    subdirs = sorted(
        [d for d in result_dir.iterdir() if d.is_dir() and d.name.startswith("result-")
         and not d.name.endswith(_RETIRED_SUFFIXES)],
        key=lambda d: d.stat().st_mtime,
        reverse=True
    )
    return subdirs


_SUBDIR_SFX_RE = re.compile(r"^result-(?:(?P<sfx>.+)-)?\d{8}T\d{6}Z-[0-9a-f]{6}$")


def _suffix_token_match(d: Path, case_id: str) -> bool:
    """①b 短后缀对号:runner 的 --result-suffix 只认字母数字-_,执行侧常按规约只用 case_id 的
    nX 短段当后缀(中文进不了后缀)——目录名装不下完整 case_id 时,取目录名的后缀段与 case_id
    **按段精确**比对:后缀 n5 只配 n5-*,配不上 n50-*(实测 9 个用例因短后缀配不上完整
    case_id、run.json 又指向合并副本目录,双通道全失手被误判 EVAL_ERROR)。"""
    m = _SUBDIR_SFX_RE.match(d.name)
    sfx = (m.group("sfx") or "") if m else ""
    return bool(sfx) and (case_id == sfx or case_id.startswith(sfx + "-"))




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
            "detail": f"裁决与期望不符。节点 {node_id} 可能被错误地包含/排除在 RCA 结论中。建议人工审查 RCA 规则和裁决逻辑。{fuse_warning}",
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
    case_id: str,
    result: Dict[str, Any],
    fuse_history: List[Dict[str, Any]]
) -> None:
    """更新熔断历史：同 case 同节点同类错误跨轮计数，达 2 次触发熔断。

    fuse_history 由调用方从 --out 目录加载/写回（跨轮稳定，temp 目录每轮都换、存那里
    历史永远为空）。熔断触发时把 classified details 打上 fused 标记，供 suggestion
    带出【熔断警告】。
    """
    classify_data = result.get("classify", {})
    if not classify_data.get("classified"):
        return  # PASS 不计入

    error_class = classify_data.get("error_class")
    error_category = classify_data.get("error_category")
    details = classify_data.get("details", {}) or {}
    node_id = details.get("node_id", "unknown")

    # 同 case 同节点同类的既往轮次数（不含熔断标记行）
    prior = [e for e in fuse_history
             if not e.get("fused")
             and e.get("case_id") == case_id
             and e.get("error_class") == error_class
             and e.get("node_id") == node_id]

    # 记录本轮
    fuse_history.append({
        "case_id": case_id,
        "error_class": error_class,
        "error_category": error_category,
        "node_id": node_id,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

    # 熔断判断：算上本轮 ≥ 3 轮（自动迭代 3 轮不过 → 转人工深查）
    if len(prior) >= 2:
        fuse_history.append({
            "fused": True,
            "case_id": case_id,
            "node_id": node_id,
            "error_class": error_class,
            "error_category": error_category,
            "fuse_reason": "同节点同类连改3轮不过",
            "rounds": len(prior) + 1
        })
        details["fused"] = True    # 供 _generate_suggestion 带【熔断警告】
        print(f"[fuse] 熔断触发: {case_id} {node_id} {error_category}（连续 {len(prior) + 1} 轮）")


def process_case(
    case_id: str,
    cases_dir: Path,
    result_dir: Path,
    skill_dir: Path,
    tree_indexes: List[Path],
    temp_dir: Path,
    progress_file: Path,
    scripts_dir: Path = SCRIPTS_DIR,
    shared_binding_check: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """处理单个 case 的评估

    流程：
    1. 定位 expected.json 和 result-dir
    2. 查找 tree_index.json
    3. 依次执行: reconcile → check_binding(共享) → check_tree(按用例) → classify
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

    # Step 2: 定位 result 目录（对号锚=本用例的数据目录：expected 记录的 data_source
    # 与 cases/<id>/data，run.json 的输入路径落在其中即认定归属）
    data_anchors = [expected_data.get("data_source", ""),
                    str(cases_dir / case_id / "data")]
    case_result_dir = find_case_result_dir(result_dir, case_id, data_anchors)
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

    # 加载 reconcile 结果（对账退出码=1 表示有漏判/假阳，属预期；文件写不出来才是评估失败）
    reconcile_data = load_json(reconcile_out) if reconcile_out.exists() else {}
    if rca_path.exists() and not reconcile_data:
        return {"case_id": case_id, "ok": False,
                "error": f"reconcile.py 未产出结果文件: {reconcile_result['stderr'][:300]}"}

    # 3b: 绑定检查（使用共享结果，避免重复计算）
    # 优化: check_binding.py 是静态检查，所有 case 共用同一结果
    binding_check_data = shared_binding_check if shared_binding_check else {}
    if shared_binding_check:
        print(f"[{case_id}] 使用共享绑定检查结果，unbound_count={shared_binding_check.get('unbound_count', 'N/A')}")
        # 写入临时文件供 classify.py 读取（--binding-check 读文件）
        binding_check_path = temp_dir / f"{case_id}_binding.json"
        binding_check_path.write_text(json.dumps(shared_binding_check, ensure_ascii=False), encoding="utf-8")

    # 3c: 树结构检查（按用例：查本用例期望根因是否在 tree_index 中——KNOWLEDGE_GAP 的输入）
    tree_check_data: Dict[str, Any] = {}
    expected_ids = [r.get("node_id") for r in expected_data.get("expected_root_causes", [])
                    if r.get("node_id")]
    if tree_indexes and expected_ids:
        tree_check_out = temp_dir / f"{case_id}_tree.json"
        tree_args = [
            "--tree-index", str(tree_indexes[0]),
            "--node-ids", ",".join(expected_ids),
            "--out", str(tree_check_out)
        ]
        print(f"[{case_id}] 执行 check_tree.py（期望节点: {','.join(expected_ids)}）...")
        update_progress(progress_file, f"{task_prefix}:check_tree", "started")
        run_sub_script(scripts_dir / "check_tree.py", tree_args)   # 有缺失节点时退出码=1，属预期
        tree_check_data = load_json(tree_check_out) if tree_check_out.exists() else {}
        update_progress(progress_file, f"{task_prefix}:check_tree", "completed")

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
        # 真正失败：输出文件不存在或无法解析——评估过程失败，不能冒充"未分类=PASS"
        print(f"[{case_id}] classify.py 失败: {classify_result['stderr']}")
        update_progress(progress_file, f"{task_prefix}:classify", "completed",
                        f"returncode={classify_result['returncode']}")
        return {"case_id": case_id, "ok": False,
                "error": f"classify.py 未产出结果文件: {classify_result['stderr'][:300]}",
                "expected": expected_data, "reconcile": reconcile_data,
                "result_dir": str(case_result_dir)}

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
    """将分类结果转换为 checks 结构(v11:六键三态,与「六类按序命中即停」严格一致)

    {
      "env_failure":  {"passed": true/false/null, "detail": any},
      "input_gap":    {...}, "binding_gap": {...}, "knowledge_gap": {...},
      "criteria_gap": {...}, "verdict_gap": {...}
    }
    passed 三态:true=评了且通过;false=命中(整场唯一,链条停在这);
    **null=没评到**(前序命中,按序评估已终止)。v10 之前把没评到的关写成
    passed:true,平台/评分一读就是"通过",语义错——三态就是为修它。
    passed 一律按 classify 的 error_class(0~5)链条推导(单一事实来源);
    detail 只在命中的那关附机械原料(执行失败节点/缺源/未绑列表/问题节点)。"""
    classified = classify_data.get("classified", False)
    try:
        ec = int(classify_data.get("error_class"))
    except (TypeError, ValueError):
        ec = None
    if not classified:
        ec = None                                        # PASS:六关全过

    exec_failures = [nid for nid, st in (run_data.get("execution_status") or {}).items()
                     if isinstance(st, dict) and not st.get("ok", True)]
    unbound_nodes = binding_check_data.get("unbound") or binding_check_data.get("unbound_nodes") or []
    missing_nodes = (tree_check_data or {}).get("missing") or []
    cd_node = (classify_data.get("details") or {}).get("node_id")   # details 可能是 None,别 .get 上炸
    details = {
        0: exec_failures or None,
        1: run_data.get("missing_inputs") or None,
        2: unbound_nodes or cd_node,
        3: missing_nodes or cd_node,
        4: cd_node,
        5: cd_node,
    }
    keys = ["env_failure", "input_gap", "binding_gap", "knowledge_gap", "criteria_gap", "verdict_gap"]
    out = {}
    for i, key in enumerate(keys):
        passed = True if ec is None or i < ec else (False if i == ec else None)
        # detail 双职:命中关=定级原料;通过关=信息栏(如 PASS 场也把未绑节点留档,不定级);
        # 没评到(null)不附——别让数据暗示"评过"。
        out[key] = {"passed": passed, "detail": details[i] if passed is not None else None}
    return out


def _compute_verdict(
    reconcile_data: Dict[str, Any],
    classify_data: Dict[str, Any],
    run_data: Dict[str, Any],
    expected_data: Dict[str, Any]
) -> str:
    """按 SKILL.md 决策树计算标准 verdict

    v10 规则（与决策树一字对齐）:
        全部期望根因命中（精确/祖先/降级都算命中，且无假阳）:
            有 missing_inputs → PASS_WITH_GAPS
            无 missing_inputs → PASS
        有 MISSED 或 FALSE_CONFIRM:
            触发类0~5 → FAIL
            未触发 → PARTIAL_PASS
    阴性用例真阴 = 命中。期望根因数为 0 的阳性用例视为用例本身有问题 → PARTIAL_PASS。
    """
    recon = (reconcile_data or {}).get("reconciliation", {})
    missed = recon.get("missed", 0)
    false_confirm = recon.get("false_confirm", 0)
    total = recon.get("total_expected", 0)
    negative = (expected_data or {}).get("negative", False)

    all_hit = missed == 0 and false_confirm == 0 and (negative or total > 0)
    if all_hit:
        return "PASS_WITH_GAPS" if (run_data or {}).get("missing_inputs") else "PASS"
    return "FAIL" if (classify_data or {}).get("classified") else "PARTIAL_PASS"


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
    missed = reconciliation.get("missed", 0)
    total_expected = reconciliation.get("total_expected", 0)

    # 判断影响：所有期望根因均已命中（精确/祖先/降级都算）→ 数据缺口不影响本次验证结论
    all_hit = (missed == 0 and total_expected > 0)

    if all_hit:
        impact = f"数据缺口（{missing_inputs}）不影响本次验证结论；期望根因均已命中"
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
    verdict_counts = {"PASS": 0, "PASS_WITH_GAPS": 0, "PARTIAL_PASS": 0, "FAIL": 0,
                      "EVAL_ERROR": 0}
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

        # v9: success = 评估过程正常完成（不依赖 verdict）
        success = result.get("ok", False)

        # v10: 标准 verdict 按 SKILL.md 决策树从 对账+分类+缺口 计算；
        # 评估过程自身失败（子脚本没产出）单列 EVAL_ERROR，绝不冒充 PASS
        classified = classify_data.get("classified", False)
        error_category = classify_data.get("error_category", "")
        error_class = classify_data.get("error_class", "")
        if success:
            verdict = _compute_verdict(reconcile_data, classify_data, run_data, expected_data)
        else:
            verdict = "EVAL_ERROR"
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        # 对账明细（供签名定位实际对账结果）
        recon_details = reconcile_data.get("details", [])
        missed_ids = [d.get("node_id") for d in recon_details if d.get("recon") == "MISSED"]

        # 收集 failure_signatures 和 feedback_to_generate（先收集再写文件）
        case_failure_sigs = []
        case_issues = []
        if classified:
            details = classify_data.get("details", {})
            if details:
                # v10: 签名锚定分类出的问题节点（没有则取第一个漏判节点），
                # recon_result 取该节点的实际对账结果
                anchor = details.get("node_id") or (missed_ids[0] if missed_ids else None)
                recon_of_anchor = next(
                    (d.get("recon") for d in recon_details if d.get("node_id") == anchor),
                    "MISSED"
                )
                sig = {
                    "expected_node": anchor,
                    "recon_result": recon_of_anchor,
                    "error_class": error_class,
                    "error_category": error_category,
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
                    "node_name": details.get("node_name", "") or details.get("name", ""),
                    "expected_result": "confirmed",
                    "actual_result": recon_of_anchor,
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
            # case_quality: 祖先命中产生的"期望粒度偏粗"提示（用例库维护线索，非错误）
            "case_quality": reconcile_data.get("case_quality", []),
            # data_gaps: 从 run_data.missing_inputs 构建，与 SKILL.md 格式对齐
            "data_gaps": _build_data_gaps(run_data, reconcile_data, expected_data),
            "failure_signatures": case_failure_sigs,
            "verdict_detail": {
                "verdict": verdict,
                "error_class": error_class if classified else None,
                "reason": (result.get("error", "评估过程失败") if not success else
                           (classify_data.get("details", {}).get("reason", "") if classified else ""))
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

        # 平台展示适配:同目录机械产出 quality_score.json(六维 BOOLEAN 计分,短路后未评维度
        # 记 null/N-A,总分=通过关数/6×100;口径与实现见 score_report.py)。EVAL_ERROR 无有效
        # 评分主动跳过;生成失败不拖垮评估——报告仍是权威,可手跑 score_report.py 补出。
        case_score, case_dims = None, {}
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                "score_report", Path(__file__).resolve().parent / "score_report.py")
            _sr = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_sr)
            _score_path = _sr.write_score(report_path)
            case_score, case_dims = read_case_score(_score_path)
            print(f"[eval_run] {case_id}: 平台评分已写入 {_score_path}")
        except ValueError as e:
            print(f"[eval_run] {case_id}: 不产 quality_score({e})")
        except Exception as e:  # noqa: BLE001
            print(f"[eval_run] ⚠ {case_id}: quality_score.json 生成失败({e})——评估报告不受影响,"
                  f"可手跑 score_report.py \"{report_path}\" 补出")

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
            "quality_score": case_score,
            "dimensions": case_dims,
            "round": round_num,
            "report_path": str(report_path.relative_to(out_dir))
        })

    # 根目录 quality_score.json(整批):与一键回归同一口径(batch_summary.py),平台读这一份
    summary_report = write_batch_score(out_dir, all_case_summaries, evaluation_time)
    print(f"[eval_run] 整批汇总已写入 {out_dir / 'quality_score.json'}")

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


def _round_from_dirname(name: str) -> int:
    """从 <case_id>-v<round>-<timestamp> 目录名解析 round，解析不出返回 0。"""
    m = re.search(r"-v(\d+)-", name)
    return int(m.group(1)) if m else 0


def compute_round_and_feedback_iteration(
    prev_evaluation: Optional[Path],
    case_id: str
) -> Tuple[int, int]:
    """计算当前 case 的 round 和 feedback_iteration

    --prev-evaluation 两种传法都认：
    - 直接传上一轮 evaluation_report.json 文件路径（SKILL.md 的推荐用法）
    - 传评估输出目录（在其下按 <case_id>-v<round>-* 找该 case 最新一轮）

    round 从上轮报告 meta.round 读（目录名只作兜底）；feedback_iteration：
    上轮 verdict 为 PARTIAL_PASS/FAIL（有 feedback）→ +1，否则清零。

    Returns:
        (round, feedback_iteration): 轮次从 1 开始
    """
    if not prev_evaluation or not prev_evaluation.exists():
        return (1, 0)  # v9: 无上轮时 round=1, feedback_iteration=0

    p = Path(prev_evaluation)
    prev_report: Dict[str, Any] = {}
    prev_round_fallback = 0

    if p.is_file():
        prev_report = load_json(p)
        rep_case = prev_report.get("meta", {}).get("case_id")
        if rep_case not in (None, "", case_id):
            # 传的是别的 case 的报告：退回目录模式（报告在 <out>/<case>-v*/ 下两层）
            prev_report = {}
            p = p.parent.parent

    if not prev_report:
        base = p if p.is_dir() else p.parent
        cands = sorted(
            [
                d for d in base.iterdir()
                if d.is_dir() and d.name.startswith(f"{case_id}-v")
                and (d / "evaluation_report.json").exists()
            ],
            key=lambda d: _round_from_dirname(d.name),
            reverse=True  # 轮次最大的在前面
        ) if base.exists() and base.is_dir() else []
        if not cands:
            return (1, 0)  # 无上轮记录
        prev_report = load_json(cands[0] / "evaluation_report.json")
        prev_round_fallback = _round_from_dirname(cands[0].name)

    meta = prev_report.get("meta", {})
    try:
        prev_round = int(meta.get("round") or prev_round_fallback or 0)
    except (TypeError, ValueError):
        prev_round = prev_round_fallback

    # verdict 在 meta.verdict（verdict_detail.verdict 兜底）——报告顶层没有 verdict 字段
    prev_verdict = meta.get("verdict") or prev_report.get("verdict_detail", {}).get("verdict", "")
    try:
        prev_feedback_iteration = int(meta.get("feedback_iteration", 0) or 0)
    except (TypeError, ValueError):
        prev_feedback_iteration = 0

    # feedback_iteration 只在上轮有 feedback 时才 +1；PASS/PASS_WITH_GAPS 清零
    if prev_verdict in ("PARTIAL_PASS", "FAIL"):
        new_feedback_iteration = prev_feedback_iteration + 1
    else:
        new_feedback_iteration = 0

    return (prev_round + 1 if prev_round else 1, new_feedback_iteration)


def build_default_out_dir(result_dir: Path, scenario: str) -> Path:
    """根据 result-dir 构建默认输出目录

    v10 规则: <result-dir 的父目录>/<scenario>-eval/（跨平台，不再硬编码盘符）

    Args:
        result_dir: 执行结果目录
        scenario: 场景名（从 result-dir 名称推断）

    Returns:
        默认输出目录路径
    """
    # 尝试从 result-dir 名称推断 scenario
    # result-dir 可能是: .../ldp-lsp-result/ 或 .../ldp-lsp/
    result_dir = result_dir.resolve()
    result_name = result_dir.name
    # 去掉可能的 -result 后缀
    if result_name.endswith("-result"):
        scenario = result_name[:-7]  # 去掉 "-result"
    elif result_name == "result":
        # result-dir 本身叫 "result"，尝试用父目录名
        parent_name = result_dir.parent.name
        if parent_name not in (".", ""):
            scenario = parent_name
    else:
        scenario = result_name if not scenario else scenario

    return result_dir.parent / f"{scenario}-eval"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="cs-scot-reflect 评估主入口 (v9)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 单用例模式
    python eval_run.py --case ldp-lsp-001 --result-dir output/ldp-lsp-result \\
        --skill-dir scot技能/ldp-lsp

    # 批量模式（自动推断 scenario；执行时用 runner --result-suffix 把 case_id 打进结果目录名）
    python eval_run.py --case-dir cases --result-dir output/ldp-lsp-result \\
        --skill-dir scot技能/ldp-lsp --out output/ldp-lsp-eval

    # 带上一轮评估记录（用于 round 追踪；传报告文件或评估目录都行）
    python eval_run.py --case ldp-lsp-001 --result-dir output/ldp-lsp-result \\
        --skill-dir scot技能/ldp-lsp \\
        --prev-evaluation output/ldp-lsp-eval/ldp-lsp-001-v1-.../evaluation_report.json
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
                        help="评估输出目录（默认: <result-dir 父目录>/<scenario>-eval/）")
    parser.add_argument("--prev-evaluation",
                        help="上一轮 evaluation_report.json 文件或评估输出目录（用于 round 追踪）")
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
    # 批量模式的用例根 = --case-dir 给的路径;它此前只被当"模式开关",扫描却用 --cases-dir 的
    # 缺省值 cases,--case-dir 传绝对路径直接报"case-dir 不存在: cases"(51 用例实跑逮到)。
    cases_dir = Path(args.case_dir) if args.case_dir else Path(args.cases_dir)
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

    # 创建临时目录（用于中途落盘）——走系统临时目录，不在当前目录/技能目录里拉 temp/
    temp_dir = Path(tempfile.mkdtemp(prefix="cs-scot-reflect-"))
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

    # ===== 优化1: 绑定静态检查提到循环外，每个case复用 =====
    # check_binding.py 基于 tree_index+scripts，与具体 case 无关；
    # check_tree.py 查的是各用例自己的期望节点，在 process_case 里按用例执行
    shared_binding_check = {}

    if tree_indexes:
        tree_index = tree_indexes[0]

        # 执行一次 check_binding.py
        skill_scripts_dir = find_skill_scripts_dir(skill_dir, tree_index)
        if not skill_scripts_dir.is_dir():
            print(f"[warn] 找不到 analyzer 脚本目录（按 {skill_scripts_dir} 兜底）——"
                  "绑定检查将把全树当未绑定，BINDING_GAP 归因不可信，先核对 --skill-dir")
        binding_out = temp_dir / "shared_binding_check.json"
        binding_args = [
            "--tree-index", str(tree_index),
            "--scripts-dir", str(skill_scripts_dir),
            "--out", str(binding_out)
        ]
        print(f"[eval_run] 执行共享绑定检查（所有case共用）...")
        update_progress(progress_file, "shared:binding_check", "started")
        binding_result = run_sub_script(SCRIPTS_DIR / "check_binding.py", binding_args)
        # check_binding.py 有未绑定节点时按设计退出码=1（警告）——只要结果文件在就加载，
        # 按退出码丢结果会把"真有未绑定节点"的信息恰好扔掉
        shared_binding_check = load_json(binding_out) if binding_out.exists() else {}
        if shared_binding_check:
            print(f"[eval_run] 共享绑定检查完成，unbound_count="
                  f"{shared_binding_check.get('unbound_count', 'N/A')}")
            update_progress(progress_file, "shared:binding_check", "completed")
        else:
            print(f"[eval_run] 共享绑定检查失败: {binding_result['stderr']}")
            update_progress(progress_file, "shared:binding_check", "completed",
                            f"returncode={binding_result['returncode']}")

    # ===== 熔断历史加载 =====
    # 固定存在 --out 目录（跨轮稳定）；temp 每轮都换，历史存那里永远是空的
    fuse_history_file = out_dir / "fuse_history.jsonl"
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
            shared_binding_check=shared_binding_check
        )
        all_results.append(result)

        # 更新熔断历史
        _update_fuse_history(case_id, result, fuse_history)

    # 保存熔断历史（--out 目录，供下一轮读取）
    out_dir.mkdir(parents=True, exist_ok=True)
    fuse_history_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in fuse_history) + "\n",
        encoding="utf-8"
    )

    # 生成最终报告
    evaluation_time = datetime.now(timezone.utc).isoformat()
    summary = generate_report(all_results, out_dir, evaluation_time, prev_evaluation, skill_dir, temp_dir)

    # v9: 输出 verdicts 统计
    verdicts = summary.get("verdicts", {})
    print(f"\n{'='*60}")
    print(f"[eval_run] 评估完成")
    print(f"[eval_run] {format_line(summary)}")
    print(f"[eval_run] verdicts: {verdicts}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
