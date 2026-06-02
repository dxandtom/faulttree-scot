#!/usr/bin/env python3
"""[由 cs-scot-generate 为「SA场景接入失败」生成] chr 指标解析脚本(自包含)。

读取 chr 记录(表格),统计 DCErrType 分布与占比,评估本场景子树涉及的 chr 判断条件。
每条 condition 都把 expr / values / actual / judgment / hit 算清楚,RCA 直接取用、不再重算。

自包含:内联读取函数,不依赖任何本仓自定义模块。读 xlsx 需 openpyxl;csv 用标准库。
用法:python chr_analyzer.py "<chr数据.csv 或 .xlsx>" -o "<输出.json>"
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter
from pathlib import Path

_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")


def _read_table(path: str):
    """读 xlsx/csv → (表头, 行dict列表);自动编码检测。自包含。"""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        try:
            from openpyxl import load_workbook
        except ImportError as e:
            raise RuntimeError("读取 xlsx 需要 openpyxl,请先 pip install openpyxl") from e
        wb = load_workbook(p, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = [("" if h is None else str(h)).strip() for h in next(it, [])]
        rows = []
        for r in it:
            rows.append({header[i]: ("" if i >= len(r) or r[i] is None else str(r[i]))
                         for i in range(len(header))})
        wb.close()
        return header, rows
    raw = p.read_bytes()
    text = None
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "ignore")
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    return headers, [{(k or ""): ("" if v is None else str(v)) for k, v in row.items()} for row in reader]


def analyze(data_path: str) -> dict:
    """解析 chr 数据,统计 DCErrType 分布并评估本场景子树涉及的 chr 判断条件。"""
    _, rows = _read_table(data_path)
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
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
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
