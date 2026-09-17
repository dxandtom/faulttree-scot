#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量评估的统一汇总——平台/业务读这一份:一键回归(regress.py)与批量判卷(eval_run.py)
都在评估输出目录写 index.json,字段与口径完全一样。

口径(唯一一套,别处不再另算):
- 通过 = verdict 为 PASS 或 PASS_WITH_GAPS(期望根因全命中;缺部分数据不扣通过);
- pass_rate = 通过数 ÷ 用例总数,分母是全部用例——执行失败、判卷异常、待裁决都算不通过;
- score_avg = 各用例 quality_score.json 的 total_score 算术平均,没评出分的按 0,保留一位小数。
"""
from __future__ import annotations

import json
from pathlib import Path

PASS_VERDICTS = ("PASS", "PASS_WITH_GAPS")
VERDICT_KEYS = ("PASS", "PASS_WITH_GAPS", "PARTIAL_PASS", "FAIL", "EVAL_ERROR", "NOT_EVALUATED")


def build_index(cases: list, evaluation_time: str) -> dict:
    """cases 每条至少有 case_id、verdict(可缺,缺=NOT_EVALUATED)、quality_score(总分,可缺)。"""
    total = len(cases)
    counts = {k: 0 for k in VERDICT_KEYS}
    for c in cases:
        v = c.get("verdict") or "NOT_EVALUATED"
        counts[v if v in counts else "NOT_EVALUATED"] += 1
    passed = sum(1 for c in cases if c.get("verdict") in PASS_VERDICTS)
    score_sum = sum(float(c.get("quality_score") or 0) for c in cases)
    return {
        "evaluation_time": evaluation_time,
        "total_cases": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "score_avg": round(score_sum / total, 1) if total else 0.0,
        "verdicts": counts,
        "cases": cases,
    }


def write_index(out_dir: Path, cases: list, evaluation_time: str) -> dict:
    index = build_index(cases, evaluation_time)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def format_line(index: dict) -> str:
    """终端一行:通过 k/N = xx.x%,平均得分 s(口径注明)。"""
    return (f"通过 {index['passed']}/{index['total_cases']} = {index['pass_rate']:.1%}"
            f"(通过=PASS+PASS_WITH_GAPS,分母含未判卷);平均得分 {index['score_avg']}")
