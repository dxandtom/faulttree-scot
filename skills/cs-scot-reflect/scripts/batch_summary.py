#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量评估汇总——生成评估输出根目录下的 quality_score.json(整批一份;单用例目录里的
quality_score_batch.json 不动):一键回归(regress.py)与批量评估(eval_run.py)都写这一份,计算方式只在这里定义。

计算方式:
- 通过 = verdict 为 PASS 或 PASS_WITH_GAPS(期望根因全部命中;缺部分数据不影响通过);
- pass_rate = 通过数 ÷ 用例总数,分母是全部用例——执行失败、评估异常、待裁决都算不通过;
- total_score = 各用例 total_score 的算术平均(没评出分的按 0),保留一位小数;
- dimensions 六个维度 = 该维度通过的用例数 ÷ 用例总数(单用例文件里是 true/false/null,批量里是比例),
  没评到的维度(前一维度已未通过)按不通过计——与单用例"通过维度数 ÷ 6"的算法一致,所以
  total_score 也等于六个维度分的平均。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PASS_VERDICTS = ("PASS", "PASS_WITH_GAPS")
VERDICT_KEYS = ("PASS", "PASS_WITH_GAPS", "PARTIAL_PASS", "FAIL", "EVAL_ERROR", "NOT_EVALUATED")


def _dims():
    """六个维度的 key 与中英文名,直接取单用例评分脚本(score_report.py)的定义,不再重复维护一份。"""
    spec = importlib.util.spec_from_file_location("score_report", Path(__file__).resolve().parent / "score_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DIMS


def _verdict_counts(cases: list) -> dict:
    counts = {k: 0 for k in VERDICT_KEYS}
    for c in cases:
        v = c.get("verdict") or "NOT_EVALUATED"
        counts[v if v in counts else "NOT_EVALUATED"] += 1
    return counts


def _dimension_ratios(cases: list) -> dict:
    """各维度 = 通过该维度(value 为 true)的用例数 ÷ 用例总数;没评到(null)按不通过。"""
    total = len(cases)
    out = {}
    for key, zh, en in _dims():
        ok = sum(1 for c in cases if (c.get("dimensions") or {}).get(key) is True)
        ratio = ok / total if total else 0.0
        out[key] = {"value": round(ratio, 4), "score": round(ratio * 100), "passed_cases": ok,
                    "dimension_name": {"default": zh, "zh_CN": zh, "en_US": en}}
    return out


def build_batch_score(cases: list, evaluation_time: str) -> dict:
    """cases 每条至少有 case_id;可带 verdict(缺省按 NOT_EVALUATED)、quality_score(单用例总分)、
    dimensions({维度: true/false/null})。"""
    total = len(cases)
    passed = sum(1 for c in cases if c.get("verdict") in PASS_VERDICTS)
    score_sum = sum(float(c.get("quality_score") or 0) for c in cases)
    return {
        "level": "batch",
        "total_score": round(score_sum / total, 1) if total else 0.0,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "total_cases": total,
        "passed": passed,
        "verdicts": _verdict_counts(cases),
        "dimensions": _dimension_ratios(cases),
        "meta": {"evaluation_time": evaluation_time, "value_type": "RATIO",
                 "note": "单用例 quality_score_batch.json 的六维是 true/false/null;本文件是整批:各维=通过该关的用例比例"},
        "cases": cases,
    }


def write_batch_score(out_dir: Path, cases: list, evaluation_time: str) -> dict:
    score = build_batch_score(cases, evaluation_time)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stale = Path(out_dir) / "quality_score_batch.json"
    if stale.exists():                               # 旧版整批文件名:根目录下没有单用例文件,这里只可能是旧版残留
        stale.unlink()
        print(f"[batch_summary] 已删除旧版整批文件 {stale}(整批汇总现在叫 quality_score.json)")
    (Path(out_dir) / "quality_score.json").write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
    return score


def read_case_score(path: Path):
    """读单用例 quality_score_batch.json → (total_score, {维度: value});文件不在或读不了 → (None, {})。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {}
    return data.get("total_score"), {k: v.get("value") for k, v in (data.get("dimensions") or {}).items()}


def format_line(score: dict) -> str:
    """终端输出一行:通过 k/N = xx.x%,总得分 s(附计算方式说明)。"""
    return (f"通过 {score['passed']}/{score['total_cases']} = {score['pass_rate']:.1%}"
            f"(通过=PASS+PASS_WITH_GAPS,分母含未判卷);总得分 {score['total_score']}(各用例平均)")
