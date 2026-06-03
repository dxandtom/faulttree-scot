#!/usr/bin/env python3
"""cs-scot-execute —— 轻量 SCoT 计划执行器。

执行一份符合 SCoT plan schema 的 plan.json:从 entry 出发,按 transition 遍历 nodes,
用工作记忆(working memory)+ 引用表达式($input / $memory / $step.<id> / $output)
在节点之间传数据。

支持的 node kind(SCoT schema 的子集):
- script:      跑一个自包含解析脚本(确定性);约定脚本 CLI 为
               `python scripts/<name>.py <数据文件> -o <输出.json>`,产出 JSON 存入 outputs。
- agent_skill: prompt-only 技能(模型判断,如 fault-tree-rca);runner 把解析好的 inputs
               备到 result/<id>_input.json,由所在 agent 执行该技能产出结果。

保持轻量:暂不实现 budget / reflection / parallel_after / conditions 等重型特性。

用法:
    runner.py --plan plan.json --data kpi=<file> --data chr=<file> [--base <dir>]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

_TOK = re.compile(r"[^.\[\]]+|\[\d+\]")


class _Pause(Exception):
    """到达一个还没产出的 agent 步骤:备好输入、打印待办、暂停整条计划(产出后重跑可续)。"""
    def __init__(self, node_id: str, todo: str):
        super().__init__(node_id)
        self.node_id = node_id
        self.todo = todo


def _get_path(obj: Any, path: str) -> Any:
    """按 a.b[0].c 在嵌套结构里取值;取不到返回 None。"""
    cur = obj
    for tok in _TOK.findall(path):
        if tok.startswith("["):
            i = int(tok[1:-1])
            cur = cur[i] if isinstance(cur, list) and -len(cur) <= i < len(cur) else None
        else:
            cur = cur.get(tok) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


def _set_path(obj: dict, path: str, value: Any) -> None:
    """按 a.b.c 写入嵌套 dict。"""
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def resolve_ref(ref: Any, ctx: Dict[str, Any]) -> Any:
    """解析引用表达式;非 $ 引用按字面量原样返回。

    支持 $input.x / $memory.x.y / $step.<id>.field / $output.x。
    """
    if not isinstance(ref, str):
        return ref
    for ns in ("input", "memory", "output"):
        pre = f"${ns}."
        if ref.startswith(pre):
            return _get_path(ctx[ns], ref[len(pre):])
    if ref.startswith("$step."):
        nid, _, p = ref[len("$step."):].partition(".")
        node_out = ctx["step"].get(nid, {})
        return _get_path(node_out, p) if p else node_out
    return ref  # 字面量


def resolve_inputs(inputs: dict, ctx: Dict[str, Any]) -> dict:
    """把节点 inputs 里的每个引用都解析成实际值。"""
    return {k: resolve_ref(v, ctx) for k, v in (inputs or {}).items()}


def store_outputs(outputs: dict, value: Any, ctx: Dict[str, Any]) -> None:
    """把节点产出(一个 JSON 对象)按 outputs 映射写入 $memory / $output。

    约定:script / agent_skill 的产出整体是一个对象;outputs 一般只有一项
    形如 {"result": "$memory.kpi_result"},表示把整个产出存到该位置。
    """
    for field, target in (outputs or {}).items():
        v = value.get(field, value) if isinstance(value, dict) and field in value else value
        if isinstance(target, str) and target.startswith("$memory."):
            _set_path(ctx["memory"], target[len("$memory."):], v)
        elif isinstance(target, str) and target.startswith("$output."):
            _set_path(ctx["output"], target[len("$output."):], v)


def run_script_node(node: dict, ctx: Dict[str, Any], base: Path) -> Any:
    """执行 kind=script 节点:跑 scripts/<name>.py <数据> -o <tmp>,返回其 JSON 产出。"""
    name = node.get("name") or node["id"]
    script = base / "scripts" / f"{name}.py"
    if not script.exists():
        raise FileNotFoundError(f"节点 {node['id']} 的脚本不存在: {script}")
    ins = resolve_inputs(node.get("inputs"), ctx)
    if "data" not in ins or not ins["data"]:
        raise ValueError(f"节点 {node['id']} 缺少已绑定的 inputs.data(用 --data <指标>=<文件> 提供)")
    data_path = Path(ins["data"])
    if not data_path.is_absolute():
        data_path = base / data_path
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.json"
        cmd = [sys.executable, str(script), str(data_path), "-o", str(out)]
        print(f"[script] {node['id']}: {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"节点 {node['id']} 失败 (exit {proc.returncode}):\n{proc.stderr[-2000:]}")
        result = json.loads(out.read_text(encoding="utf-8"))
    store_outputs(node.get("outputs"), result, ctx)
    return result


def run_agent_skill_node(node: dict, ctx: Dict[str, Any], base: Path) -> Any:
    """执行 kind=agent_skill 节点(模型步骤,**可重入**)。

    - 若 node.produces 指定的产物已存在 → 载入它、继续(重跑时自动续上);
    - 否则 → 把解析好的输入备到 result/<id>_input.json,打印待办,抛 _Pause 暂停整条计划。
    约定:fault_tree_md 输入若是 .md 路径会被读成正文;其余输入归到 indicator_results。
    """
    name = node.get("name") or node["id"]
    produces = node.get("produces")
    prod = (base / produces) if produces else None
    if prod is not None and prod.exists():
        try:
            result = json.loads(prod.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            result = {"_artifact": str(prod)}
        store_outputs(node.get("outputs"), result, ctx)
        print(f"[agent_skill] {node['id']}: 已载入既有产物 {prod} → 继续")
        return result

    ins = resolve_inputs(node.get("inputs"), ctx)
    ft = ins.pop("fault_tree_md", None)
    if isinstance(ft, str) and ft.lower().endswith(".md"):
        ftp = Path(ft)
        ftp = ftp if ftp.is_absolute() else (base / ftp)
        if ftp.exists():
            ft = ftp.read_text(encoding="utf-8")
    payload = {"fault_tree_md": ft, "indicator_results": ins}
    in_path = base / "result" / f"{node['id']}_input.json"
    in_path.parent.mkdir(parents=True, exist_ok=True)
    in_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"[agent_skill] {node['id']}: 模型步骤,用「{name}」技能。",
             f"  输入已备好 → {in_path}"]
    if node.get("guide"):
        lines.append(f"  参照契约 → {node['guide']}")
    if produces:
        lines.append(f"  产出请写到 → {prod}")
    lines.append("  (indicator_results 含各源概览/上游事实;按技能契约产出后写到上面的产出路径)")
    raise _Pause(node["id"], "\n".join(lines))


def run_condition_eval_node(node: dict, ctx: Dict[str, Any], base: Path) -> Any:
    """执行 kind=condition_eval 节点:跑同目录的 eval_conditions.py(+ validate_conditions.py),
    把逐条件事实存入 $memory。inputs.conditions=绑定文件(相对 base);inputs.data={源名: $input.<源>}。"""
    raw = node.get("inputs") or {}
    conditions = resolve_ref(raw.get("conditions"), ctx)
    if not conditions:
        raise ValueError(f"节点 {node['id']} 缺少 inputs.conditions")
    cpath = Path(conditions)
    cpath = cpath if cpath.is_absolute() else (base / cpath)
    if not cpath.exists():
        raise FileNotFoundError(
            f"节点 {node['id']}: 绑定文件不存在 {cpath}(应由上一步 bind_conditions 产出后再重跑)")
    sdir = Path(__file__).resolve().parent
    out = base / "result" / "condition_facts.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(sdir / "eval_conditions.py"), "--conditions", str(cpath)]
    for k, v in (raw.get("data") or {}).items():
        p = resolve_ref(v, ctx)
        if not p:
            continue
        pp = Path(p)
        pp = pp if pp.is_absolute() else (base / pp)
        cmd += ["--data", f"{k}={pp}"]
    cmd += ["-o", str(out)]
    print(f"[condition_eval] {node['id']}: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                          encoding="utf-8", errors="replace")
    if proc.stdout:
        print(proc.stdout.strip())
    if proc.returncode != 0:
        raise RuntimeError(f"节点 {node['id']} eval 失败 (exit {proc.returncode}):\n{proc.stderr[-2000:]}")
    vproc = subprocess.run([sys.executable, str(sdir / "validate_conditions.py"), str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("[validate_conditions]", (vproc.stdout or vproc.stderr or "").strip())
    result = json.loads(out.read_text(encoding="utf-8"))
    store_outputs(node.get("outputs"), result, ctx)
    return result


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="轻量 SCoT 计划执行器")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--data", action="append", default=[],
                    help="$input 绑定,如 --data kpi=path.csv(可多次)")
    ap.add_argument("--base", help="路径解析基准目录(默认取 plan.json 所在目录)")
    args = ap.parse_args()

    plan_path = Path(args.plan).resolve()
    base = Path(args.base).resolve() if args.base else plan_path.parent
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    data_map: Dict[str, str] = {}
    for item in args.data:
        if "=" not in item:
            print(f"--data 格式应为 key=path: {item}", file=sys.stderr)
            sys.exit(2)
        k, v = item.split("=", 1)
        data_map[k.strip()] = v.strip()

    nodes = {n["id"]: n for n in plan.get("nodes", [])}
    if not nodes:
        print("plan 里没有 nodes —— 这看起来不是 SCoT 计划", file=sys.stderr)
        sys.exit(2)

    # 工作记忆初始化(memory_init 里的引用也解析一遍)
    ctx: Dict[str, Any] = {"input": data_map, "memory": {}, "step": {}, "output": {}}
    for k, v in (plan.get("memory_init") or {}).items():
        ctx["memory"][k] = resolve_ref(v, ctx)

    print(f"执行 SCoT: {plan.get('plan_id') or plan.get('meta', {}).get('goal', '')} "
          f"(version={plan.get('version')}, nodes={len(nodes)})")

    cur = plan.get("entry")
    visited = 0
    try:
        while cur:
            if cur not in nodes:
                raise KeyError(f"transition 指向不存在的节点: {cur}")
            node = nodes[cur]
            kind = node.get("kind")
            if kind == "script":
                result = run_script_node(node, ctx, base)
            elif kind == "condition_eval":
                result = run_condition_eval_node(node, ctx, base)
            elif kind == "agent_skill":
                result = run_agent_skill_node(node, ctx, base)
            else:
                raise ValueError(f"节点 {cur} 的 kind={kind} 暂不支持(script / condition_eval / agent_skill)")
            ctx["step"][cur] = result
            visited += 1
            if visited > 1000:
                raise RuntimeError("节点访问超过 1000,疑似 transition 成环")
            tr = node.get("transition") or {}
            cur = None if tr.get("end") else tr.get("next_on_success")
    except _Pause as pause:
        print("\n" + pause.todo)
        print(f"\n[暂停] 计划停在「{pause.node_id}」等你产出;完成后**重跑同一条命令**即可从此处继续。")
        return

    print("SCoT 执行流程完成。")


if __name__ == "__main__":
    main()
