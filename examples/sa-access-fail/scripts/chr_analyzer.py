#!/usr/bin/env python3
"""[由 cs-scot-generate 为「SA场景接入失败」生成] chr 指标解析脚本。

读取 chr 记录(表格),统计 DCErrType 分布与占比,评估本场景子树涉及的 chr 判断条件。
每条 condition 都把 expr / values / actual / judgment / hit 算清楚,RCA 直接取用、不再重算。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_loader import load_table  # noqa: E402


def analyze(data_path: str) -> dict:
    """解析 chr 数据,统计 DCErrType 分布并评估本场景子树涉及的 chr 判断条件。"""
    _, rows = load_table(data_path)
    total = len(rows)
    dist = Counter((r.get("DCErrType") or "").strip()
                   for r in rows if (r.get("DCErrType") or "").strip())

    def cond_ratio(errtype: str, threshold: float) -> dict:
        """构造一条「某 DCErrType 占比 > 阈值」的判断结果(含实际值与结论)。"""
        cnt = dist.get(errtype, 0)
        ratio = (cnt / total * 100.0) if total else 0.0
        hit = ratio > threshold
        return {
            "expr": f"DCErrType 中 {errtype} 占比 > {int(threshold)}%",
            "values": {f"{errtype}_count": cnt, "total": total, "ratio_percent": round(ratio, 2)},
            "actual": f"{errtype} = {cnt} / 总数 {total} = {ratio:.1f}%",
            "judgment": f"{ratio:.1f}% > {int(threshold)}% → {'成立' if hit else '不成立'}",
            "hit": hit,
        }

    conditions = [cond_ratio("RrcReject", 30), cond_ratio("TransLink", 20)]

    return {"summary": {"record_count": total},
            "dcerrtype_distribution": dict(dist),
            "conditions": conditions}


def main() -> None:
    ap = argparse.ArgumentParser(description="chr 指标解析(SA场景接入失败)")
    ap.add_argument("data", help="chr 数据文件(xlsx/csv)")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    result = analyze(args.data)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已输出: {out}")


if __name__ == "__main__":
    main()
