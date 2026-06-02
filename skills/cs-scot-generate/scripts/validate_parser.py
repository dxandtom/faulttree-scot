#!/usr/bin/env python3
"""验证一个生成的解析脚本:用样例数据跑一遍,检查能否产出合法 JSON。

解析脚本只查数据、不下判断,产出应为事实:indicator / summary / field_stats / referenced;
**不得**出现 conditions / hit / judgment 这类判断字段(那是 fault-tree-rca 的活)。

用法:
    validate_parser.py <analyzer.py> <sample_data> [--expect-keys indicator,summary,field_stats,referenced]
退出码 0 = 通过;非 0 = 失败(并打印原因,供据此修脚本)。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple


def validate(script: str, data: str, expect_keys: List[str]) -> Tuple[bool, str]:
    """跑一遍脚本并校验输出。返回 (是否通过, 说明)。"""
    script_p, data_p = Path(script), Path(data)
    if not script_p.exists():
        return False, f"脚本不存在: {script_p}"
    if not data_p.exists():
        return False, f"样例数据不存在: {data_p}"

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.json"
        cmd = [sys.executable, str(script_p), str(data_p), "-o", str(out)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return False, "脚本运行超时(>120s)"
        if proc.returncode != 0:
            return False, f"脚本退出码 {proc.returncode}\nstderr:\n{proc.stderr[-2000:]}"
        if not out.exists():
            return False, "脚本未生成输出文件"
        try:
            obj = json.loads(out.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as e:
            return False, f"输出不是合法 JSON: {e}"

    if not isinstance(obj, dict):
        return False, f"输出顶层应为对象,实际为 {type(obj).__name__}"
    missing = [k for k in expect_keys if k not in obj]
    if missing:
        return False, f"输出缺少顶层字段 {missing}(实际字段: {list(obj)[:20]})"
    forbidden = [k for k in ("conditions", "hit", "judgment") if k in obj]
    if forbidden:
        return False, (f"解析脚本不得下判断,却出现了顶层字段 {forbidden}。"
                       f"脚本只查数据(summary/field_stats/referenced),是否成立交给 fault-tree-rca。")
    return True, "通过:脚本产出合法 JSON、含预期事实字段、且未下任何判断"


def main() -> None:
    ap = argparse.ArgumentParser(description="验证生成的解析脚本能否产出合法 JSON")
    ap.add_argument("script")
    ap.add_argument("data")
    ap.add_argument("--expect-keys", default="indicator,kind,summary,field_stats",
                    help="逗号分隔的预期顶层字段(默认 indicator,kind,summary,field_stats)")
    args = ap.parse_args()
    keys = [k.strip() for k in args.expect_keys.split(",") if k.strip()]
    ok, msg = validate(args.script, args.data, keys)
    print(msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
