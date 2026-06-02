#!/usr/bin/env python3
"""[由 scot-generate 为「SA场景接入失败」生成] kpi 指标解析脚本。

读取 kpiTree 格式数据(每行一段 JSON,含 name 与 value),清洗计数器名,
评估本场景故障子树涉及的 kpi 判断条件。每条 condition 都把
expr / values / actual / judgment / hit 算清楚,RCA 直接取用、不再重算。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_loader import load_json_column  # noqa: E402


def _clean(name: str) -> str:
    """清洗计数器名:截断 HTML 污染(如末尾 <span、实体 &gt; 等)。"""
    return re.split(r"[<&]", name)[0].strip()


def _to_num(v) -> float:
    """把值转成 float,非法值按 0 处理。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fmt(x: float) -> str:
    """数字格式化:整数不带小数点,便于阅读。"""
    return str(int(x)) if float(x).is_integer() else str(x)


def analyze(data_path: str) -> dict:
    """解析 kpi 数据并评估本场景子树涉及的 kpi 判断条件。"""
    rows = load_json_column(data_path)
    counters = {}
    for r in rows:
        name = _clean(str(r.get("name", "")))
        if name:
            counters[name] = _to_num(r.get("value"))

    def g(n: str) -> float:
        return counters.get(n, 0.0)

    conditions = []

    # 条件1:RRC 建立尝试与成功之差 > 0(说明存在建立失败)
    att, succ = g("N.RRC.SetupAtt.Sum"), g("N.RRC.SetupSucc.Sum")
    diff = att - succ
    hit1 = diff > 0
    conditions.append({
        "expr": "N.RRC.SetupAtt.Sum - N.RRC.SetupSucc.Sum > 0",
        "values": {"N.RRC.SetupAtt.Sum": att, "N.RRC.SetupSucc.Sum": succ},
        "actual": f"N.RRC.SetupAtt.Sum = {_fmt(att)}, N.RRC.SetupSucc.Sum = {_fmt(succ)}",
        "judgment": f"{_fmt(att)} - {_fmt(succ)} = {_fmt(diff)} > 0 → {'成立' if hit1 else '不成立'}",
        "hit": hit1,
    })

    # 条件2:无线资源不足导致的建立失败计数 > 0
    nores = g("N.RRC.SetupFail.NoRes")
    hit2 = nores > 0
    conditions.append({
        "expr": "N.RRC.SetupFail.NoRes > 0",
        "values": {"N.RRC.SetupFail.NoRes": nores},
        "actual": f"N.RRC.SetupFail.NoRes = {_fmt(nores)}",
        "judgment": f"{_fmt(nores)} > 0 → {'成立' if hit2 else '不成立'}",
        "hit": hit2,
    })

    # 条件3:NG 接口 AMF 无响应计数 > 0
    norsp = g("N.NG.Setup.Fail.NoRsp")
    hit3 = norsp > 0
    conditions.append({
        "expr": "N.NG.Setup.Fail.NoRsp > 0",
        "values": {"N.NG.Setup.Fail.NoRsp": norsp},
        "actual": f"N.NG.Setup.Fail.NoRsp = {_fmt(norsp)}",
        "judgment": f"{_fmt(norsp)} > 0 → {'成立' if hit3 else '不成立'}",
        "hit": hit3,
    })

    return {"summary": {"counter_count": len(counters)},
            "counters": counters,
            "conditions": conditions}


def main() -> None:
    ap = argparse.ArgumentParser(description="kpi 指标解析(SA场景接入失败)")
    ap.add_argument("data", help="kpi 数据文件(xlsx/csv)")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    result = analyze(args.data)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已输出: {out}")


if __name__ == "__main__":
    main()
