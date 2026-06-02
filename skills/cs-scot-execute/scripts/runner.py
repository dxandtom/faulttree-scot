#!/usr/bin/env python3
"""SCoT 执行器:按 plan.json 顺序执行各 step,支持 plan 定义的所有 kind。

- kind=script: 跑某指标解析脚本(确定性),产出该指标结果 JSON。
- kind=rca:    把子树正文 + 各指标结果 + 规则汇总成 result/rca_input.json,
               做综合根因分析。RCA 是模型判断步骤;当前环境无可调用模型时,
               runner 备好输入并打印待办,由所在 agent harness 依据
               rca_rules.md 完成 result/rca.json。

用法:
    runner.py --plan plan.json --data kpi=<file> --data chr=<file> [--base <dir>]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict


def _resolve(base: Path, rel: str) -> Path:
    """相对路径按 base(默认 plan 所在目录)解析。"""
    p = Path(rel)
    return p if p.is_absolute() else (base / p)


def _bind_data(data_ref: str, data_map: Dict[str, str]) -> str:
    """把 $data.<指标> 占位绑定到 --data 传入的实际文件;也允许直接写死路径。"""
    if data_ref.startswith("$data."):
        key = data_ref[len("$data."):]
        if key not in data_map:
            raise KeyError(f"步骤需要数据 '{key}',但未通过 --data {key}=<file> 提供")
        return data_map[key]
    return data_ref


def run_script_step(step: dict, base: Path, data_map: Dict[str, str]) -> None:
    """执行 kind=script 步骤:跑解析脚本并校验产出。"""
    run = _resolve(base, step["run"])
    out = _resolve(base, step["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    data_path = _resolve(base, _bind_data(step["data"], data_map))
    cmd = [sys.executable, str(run), str(data_path), "-o", str(out)]
    print(f"[script] {step['id']}: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"步骤 {step['id']} 失败 (exit {proc.returncode}):\n{proc.stderr[-2000:]}")
    if not out.exists():
        raise RuntimeError(f"步骤 {step['id']} 未产出 {out}")
    print(f"         → {out}")


def run_rca_step(step: dict, base: Path) -> Path:
    """执行 kind=rca 步骤:汇总输入,交给模型(或所在 agent)产出根因。"""
    tree = _resolve(base, step["tree"]).read_text(encoding="utf-8")
    inputs = {}
    for rel in step["inputs"]:
        p = _resolve(base, rel)
        inputs[Path(rel).stem] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    rules_p = _resolve(base, step["rules"])
    rules = rules_p.read_text(encoding="utf-8") if rules_p.exists() else ""

    rca_input = {"fault_tree_md": tree, "indicator_results": inputs, "rca_rules": rules}
    rca_input_path = _resolve(base, "result/rca_input.json")
    rca_input_path.parent.mkdir(parents=True, exist_ok=True)
    rca_input_path.write_text(json.dumps(rca_input, ensure_ascii=False, indent=2), encoding="utf-8")

    out = _resolve(base, step["out"])
    print(f"[rca] {step['id']}: 已汇总输入 → {rca_input_path}")
    print(f"[rca] 这是模型判断步骤。请依据 references/rca_rules.md,基于上述输入产出 {out}。")
    print('[rca] 输出契约: [{"Root_cause":...,"Fault_tree_path":[...],"evidence_path":[...]}]')
    return rca_input_path


def main() -> None:
    ap = argparse.ArgumentParser(description="SCoT 执行器")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--data", action="append", default=[],
                    help="指标数据绑定,如 --data kpi=path.csv(可多次)")
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

    print(f"执行计划: {plan.get('scenario')} (steps={len(plan.get('steps', []))})")
    for step in plan.get("steps", []):
        kind = step.get("kind")
        if kind == "script":
            run_script_step(step, base, data_map)
        elif kind == "rca":
            run_rca_step(step, base)
        else:
            raise ValueError(f"未知 step kind: {kind}(plan_schema v1 仅定义 script / rca)")
    print("执行流程完成(script 步骤已跑;rca 若待模型完成见上方提示)。")


if __name__ == "__main__":
    main()
