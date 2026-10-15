#!/usr/bin/env python3
"""evaluation_report.json → quality_score_batch.json(适配平台展示,纯机械转换,不经过模型)。

平台的评估展示 = 若干维度各一个分 + 一个综合分。本技能的评估是"六类错误按顺序检查、命中即停",
不做程度打分——对就是对、错就是错,所以:
- 六个维度 = 六类错误(顺序即评估顺序),value_type=BOOLEAN:
  通过 → value:true, score:100;命中 → value:false, score:0;
  前序维度未通过、本维度没评到 → value:null, score:null(展示层渲染成 N/A);
- total_score = 通过维度数 ÷ 6 × 100(取整)——含义是"六个维度通过了几个",越早未通过分越低
  (全部通过 100;第 1 个维度未通过 0;最后一个维度未通过 83);
- reason 三语(default/zh_CN/en_US)模板化:未通过维度的 zh_CN 附机械摘要(逐字取自报告的
  verdict_detail.reason),en_US 用固定英文模板——机械转换不做自由翻译,default=zh_CN。

判定来源:verdict_detail(verdict + error_class 整数 0~5)——与"命中即停"的检查顺序严格一致。
不读 checks 的 passed(旧版报告里,没评到的维度会被写成 passed:true,读它会把
"未评估"错误地展示成"通过";按 error_class 推导对新旧报告都成立)。

评估流程末尾由 eval_run.py 自动生成;也可对任意历史 evaluation_report.json 手动运行本脚本补出:
  python score_report.py <evaluation_report.json 或其所在目录> [-o <输出路径>]
维度的 key/三语名称/权重/排序的平台配置对照表见 ../references/score_dimensions.md。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# (dimension_key, 中文名, 英文名) —— 顺序 = 评估顺序 = 平台 sort_order
DIMS = [
    ("env_failure",   "执行环境",   "Execution Environment"),
    ("input_gap",     "数据完整性", "Input Completeness"),
    ("binding_gap",   "绑定正确性", "Binding Integrity"),
    ("knowledge_gap", "树节点认知", "Tree Knowledge"),
    ("criteria_gap",  "判据正确性", "Criteria Correctness"),
    ("verdict_gap",   "裁决正确性", "Verdict Correctness"),
]
CLASS_NAMES = ["ENV_FAILURE", "INPUT_GAP", "BINDING_GAP", "KNOWLEDGE_GAP", "CRITERIA_GAP", "VERDICT_GAP"]


def _tri_reason(state, idx, ec, summary_zh):
    """按三种状态(通过/未通过/未评估)生成 reason 模板(default=zh_CN;en_US 用固定模板,不做自由翻译)。"""
    key, zh, en = DIMS[idx]
    if state is True:
        z = f"通过:未发现{zh}({CLASS_NAMES[idx]})类问题。"
        e = f"Passed: no {en} ({CLASS_NAMES[idx]}) issue detected."
    elif state is False:
        z = f"未通过({CLASS_NAMES[idx]}):{summary_zh or '详见 evaluation_report.json 的 verdict_detail'}"
        e = f"Failed ({CLASS_NAMES[idx]}). See zh_CN for mechanical details."
    else:
        stop_zh, stop_en = DIMS[ec][1], DIMS[ec][2]
        z = f"未评估:按序评估在「{stop_zh}」维度终止,本维度未执行。"
        e = f"Not evaluated: sequential evaluation stopped at '{stop_en}'."
    return {"default": z, "zh_CN": z, "en_US": e}


def build_score(report: dict) -> dict:
    """报告 dict → 平台评分 dict。评估自身失败(EVAL_ERROR/success=false)时没有可信的六维度
    结果,抛 ValueError——宁可不出评分,也不拿假分数当作有效评分。"""
    meta = report.get("meta") or {}
    vd = report.get("verdict_detail") or {}
    verdict = vd.get("verdict") or meta.get("verdict") or ""
    if not report.get("success", False) or verdict == "EVAL_ERROR":
        raise ValueError("评估过程自身失败(EVAL_ERROR/success=false),无有效评分——"
                         "先修评估环境重跑,不产 quality_score")
    try:
        ec = int(vd.get("error_class"))
    except (TypeError, ValueError):
        ec = None
    if verdict.startswith("PASS"):
        # PASS 与 PASS_WITH_GAPS 同样处理:后者是全部命中并附数据缺口备注,缺口只记录在 verdict 层,
        # 六个维度(错误分类)全部判为通过——以前只精确匹配 "PASS" 字符串,曾把 PASS_WITH_GAPS 错评成全 N/A、0 分
        ec = None
    elif ec is None:
        # 非 PASS 却没定位到错误类:推导不出通过了几个维度,保守地全记 N/A、0 分,并明确提示
        print("[score_report] ⚠ verdict 非 PASS 但 error_class 缺失——六维全记未评估、总分 0,"
              "请查 evaluation_report.json 的 verdict_detail", file=sys.stderr)
        dims = {k: {"value": None, "score": None,
                    "reason": {"default": "未评估:错误类未定位,详见 evaluation_report.json。",
                               "zh_CN": "未评估:错误类未定位,详见 evaluation_report.json。",
                               "en_US": "Not evaluated: error class missing in the report."}}
                for k, _, _ in DIMS}
        return {"total_score": 0, "dimensions": dims, "meta": _meta_echo(report, None)}

    summary_zh = " ".join(str(vd.get("reason") or "").split())[:300]
    dims, passed = {}, 0
    for i, (key, _zh, _en) in enumerate(DIMS):
        state = True if ec is None or i < ec else (False if i == ec else None)
        if state is True:
            passed += 1
        dims[key] = {"value": state,
                     "score": 100 if state is True else (0 if state is False else None),
                     "reason": _tri_reason(state, i, ec, summary_zh)}
    return {"total_score": round(passed * 100 / len(DIMS)),
            "dimensions": dims, "meta": _meta_echo(report, ec)}


def _meta_echo(report: dict, ec) -> dict:
    meta = report.get("meta") or {}
    return {"case_id": meta.get("case_id"), "scenario": meta.get("scenario"),
            "round": meta.get("round"), "verdict": meta.get("verdict"),
            "evaluation_time": meta.get("evaluation_time"),
            "error_class": CLASS_NAMES[ec] if ec is not None else None}


def write_score(report_path: Path, out_path: Path | None = None) -> Path:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    score = build_score(report)
    out = Path(out_path) if out_path else Path(report_path).parent / "quality_score_batch.json"
    out.write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="evaluation_report.json → quality_score_batch.json(平台六维评分,机械转换)")
    ap.add_argument("report", help="evaluation_report.json 路径,或其所在目录")
    ap.add_argument("-o", "--out", default="", help="输出路径(默认与报告同目录 quality_score_batch.json)")
    args = ap.parse_args()
    rp = Path(args.report)
    if rp.is_dir():
        rp = rp / "evaluation_report.json"
    if not rp.exists():
        print(f"✗ 找不到 {rp}", file=sys.stderr)
        return 2
    try:
        out = write_score(rp, Path(args.out) if args.out else None)
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    score = json.loads(out.read_text(encoding="utf-8"))
    states = {True: "✓", False: "✗", None: "N/A"}
    line = " ".join(f"{k}={states[v['value']]}" for k, v in score["dimensions"].items())
    print(f"total_score={score['total_score']}  {line}")
    print(f"已写 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
