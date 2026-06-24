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

# 产物格式契约:执行器"认识"的版本。与 cs-scot-generate/scripts/assemble_scenario.py 的
# PRODUCT_FORMAT_VERSION 成对维护——产物结构有破坏性变化时两边一起 +1。比对只告警不阻断(知情,不是门禁)。
EXPECTED_PRODUCT_FORMAT = 1


def check_product_format(base: Path) -> None:
    """读产物铭牌 manifest.json,与执行器期望的格式契约版本比对;缺失/不一致只打告警。"""
    mf = base / "manifest.json"
    if not mf.exists():
        print(f"[warn] 产物缺 manifest.json(由旧版工具链生成,早于格式契约 v{EXPECTED_PRODUCT_FORMAT})——"
              "字段/语义可能与当前执行器对不上;建议用最新 cs-scot-generate 重新生成产物。")
        return
    try:
        ver = json.loads(mf.read_text(encoding="utf-8")).get("product_format_version")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] manifest.json 读取失败({e}),按旧产物对待;建议重新 generate。")
        return
    if ver == EXPECTED_PRODUCT_FORMAT:
        return
    hint = ("产物较旧 → 用最新 cs-scot-generate 重新生成"
            if isinstance(ver, int) and ver < EXPECTED_PRODUCT_FORMAT
            else "产物比执行器新 → 升级 cs-scot-* skills 再执行")
    print(f"[warn] 产物格式契约 v{ver} ≠ 执行器期望 v{EXPECTED_PRODUCT_FORMAT}:{hint}。不阻断,但字段可能对不上。")


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
    """执行 kind=agent_skill 节点:备好输入,交给该 prompt-only 技能(模型)执行。

    runner 自身不调模型;它把解析好的 inputs 写到 result/<id>_input.json 并打印待办,
    由所在 agent harness 用对应技能产出结果。对 fault-tree-rca,inputs 里的
    fault_tree_md 若是路径会被读成正文。
    """
    name = node.get("name") or node["id"]
    ins = resolve_inputs(node.get("inputs"), ctx)
    # 约定:fault_tree_md 输入若是 .md 路径则读成正文;其余输入视为各指标解析结果
    ft = ins.pop("fault_tree_md", None)
    md_path = None
    if isinstance(ft, str) and ft.lower().endswith(".md"):
        md_path = Path(ft)
        md_path = md_path if md_path.is_absolute() else (base / md_path)

    # 有权威 index → 剪成"局部子树"(把事实挂到节点上)替代整棵 md,压缩 RCA 上下文;
    # node_id 覆盖不足或剪枝失败 → 回退完整 md(老行为)。
    payload, tree_note = None, ""
    idx_path = md_path.with_suffix(".tree_index.json") if md_path else None
    if idx_path and idx_path.exists():
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from tree_prune import compose_rca_payload
            md_text = md_path.read_text(encoding="utf-8") if (md_path and md_path.exists()) else ""
            comp = compose_rca_payload(json.loads(idx_path.read_text(encoding="utf-8")), ins, md_text)
            payload, tree_note = comp["payload"], "  " + comp["note"]   # 剪枝/floor/覆盖回退已在 compose 内统一处理
        except Exception as e:  # noqa: BLE001
            payload, tree_note = None, f"  [warn] 剪枝失败,回退完整子树: {e}"
    if payload is None:  # 无 index / 剪枝异常 → 完整 md + 全量 indicator_results
        ft_text = md_path.read_text(encoding="utf-8") if (md_path and md_path.exists()) else ft
        payload = {"fault_tree_md": ft_text, "indicator_results": ins}

    in_path = base / "result" / f"{node['id']}_input.json"
    in_path.parent.mkdir(parents=True, exist_ok=True)
    in_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # 复用产物目录重跑时:把上一轮的最终结果 <id>.json 挪开。RCA 结果由**模型**写(runner 只备输入),
    # 旧文件留着会让模型看到「result/rca.json 已存在」就直接读了**过期结论**(与本轮绑定/数据不符,实测踩过)。
    # 挪成 <id>.prev.json:不丢、可对比;本轮必须基于当前 rca_input.json 重新产出。
    prev = in_path.parent / f"{node['id']}.json"
    if prev.exists():
        try:
            prev.replace(in_path.parent / f"{node['id']}.prev.json")
            print(f"  [stale] 上轮 {prev.name} 已挪为 {node['id']}.prev.json —— 本轮请基于新输入重产,别读旧的")
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] 清理旧 {prev.name} 失败({e});请确认本轮是新写而非沿用旧结果", file=sys.stderr)
    print(f"[agent_skill] {node['id']}: 已备好输入 → {in_path}")
    if tree_note:
        comp = in_path.parent / "_compression.txt"
        comp.write_text(tree_note.strip() + "\n", encoding="utf-8")
        print(tree_note)
        print(f"  ↑ 压缩统计已落盘 → {comp}(终端把它折叠了也能去这文件看)")
    print(f"[agent_skill] 这是模型判断步骤。请用「{name}」技能,依据该输入产出结果,"
          f"写到 {in_path.parent / 'rca.json'}(契约见 fault-tree-rca 技能)。")
    print("  注意:result/ 目录已建好,**直接写文件、无需 mkdir**;Windows 路径用正斜杠 /,"
          "且别让带引号的路径以反斜杠结尾(`...\\\"` 会让 shell 一直等闭合引号而卡住)。")
    print("  ⚠ 输入可能很大:**别一次性把整个 rca_input.json 读进上下文**(会超长报错)。"
          "先读 `fault_tree.outline`(id→名字→粗状态,很小)把握全局,再**按分支用 python 只提取相关 `pruned_nodes[id]`**。")
    # 占位:实际结果由 agent 执行技能后填入;这里存一个待办标记,供下游引用不致 KeyError
    placeholder = {"_pending_skill": name, "_input": str(in_path)}
    store_outputs(node.get("outputs"), placeholder, ctx)
    return placeholder


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
    check_product_format(base)
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
    while cur:
        if cur not in nodes:
            raise KeyError(f"transition 指向不存在的节点: {cur}")
        node = nodes[cur]
        kind = node.get("kind")
        if kind == "script":
            result = run_script_node(node, ctx, base)
        elif kind == "agent_skill":
            result = run_agent_skill_node(node, ctx, base)
        else:
            raise ValueError(f"节点 {cur} 的 kind={kind} 暂不支持(当前实现 script / agent_skill)")
        ctx["step"][cur] = result
        visited += 1
        if visited > 1000:
            raise RuntimeError("节点访问超过 1000,疑似 transition 成环")
        tr = node.get("transition") or {}
        cur = None if tr.get("end") else tr.get("next_on_success")

    print("SCoT 执行流程完成(script 步骤已跑;agent_skill/RCA 待对应技能完成,见上方提示)。")


if __name__ == "__main__":
    main()
