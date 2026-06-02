#!/usr/bin/env python3
"""[由 cs-scot-generate 为「SA场景接入失败」生成] kpi 指标解析脚本(自包含)。

读取 kpiTree 格式数据(每行一段 JSON,含 name 与 value),清洗计数器名,
评估本场景故障子树涉及的 kpi 判断条件。每条 condition 都把
expr / values / actual / judgment / hit 算清楚,RCA 直接取用、不再重算。

自包含:内联读取函数,不依赖任何本仓自定义模块。读 xlsx 需 openpyxl;csv 用标准库。
用法:python kpi_analyzer.py "<kpi数据.csv 或 .xlsx>" -o "<输出.json>"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")


def _read_json_lines(path: str, column: str = "KpiTreeJson") -> list:
    """读"每行一段 JSON"的 kpiTree(csv/xlsx)→ dict 列表;非法行跳过。自包含。"""
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
        out = []
        for r in it:
            cells = [("" if v is None else str(v)) for v in r]
            raw = ""
            if column in header:
                raw = cells[header.index(column)] if header.index(column) < len(cells) else ""
            if not raw:
                raw = next((c for c in cells if c.strip()), "")
            raw = raw.strip()
            if raw:
                try:
                    out.append(json.loads(raw))
                except (json.JSONDecodeError, ValueError):
                    pass
        wb.close()
        return out
    raw_bytes = p.read_bytes()
    text = None
    for enc in _ENCODINGS:
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw_bytes.decode("utf-8", "ignore")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    start = 1 if lines and (lines[0].startswith(column) or not lines[0].lstrip().startswith("{")) else 0
    out = []
    for ln in lines[start:]:
        s = ln.strip()
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            s = s[1:-1].replace('""', '"')
        try:
            out.append(json.loads(s))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


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
    rows = _read_json_lines(data_path)
    counters = {}
    for r in rows:
        name = _clean(str(r.get("name", "")))
        if name:
            counters[name] = _to_num(r.get("value"))

    def g(n: str) -> float:
        return counters.get(n, 0.0)

    conditions = []

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

    nores = g("N.RRC.SetupFail.NoRes")
    hit2 = nores > 0
    conditions.append({
        "expr": "N.RRC.SetupFail.NoRes > 0",
        "values": {"N.RRC.SetupFail.NoRes": nores},
        "actual": f"N.RRC.SetupFail.NoRes = {_fmt(nores)}",
        "judgment": f"{_fmt(nores)} > 0 → {'成立' if hit2 else '不成立'}",
        "hit": hit2,
    })

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
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
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
