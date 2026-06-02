#!/usr/bin/env python3
"""通用解析引擎(指标无关,自动判别数据形态)——只查数据/抽规则,绝不下判断。

一个脚本应对不同数据:**看数据形态,做对应的事**。
- **tabular(表格型,如 CHR)**:逐字段数据驱动统计(数值给 min/max/mean,类别给 top-N + 样例)。
- **catalog(目录型,如 kpiTree:单列、每行一段 JSON)**:把它当"规则字典"——
  抽每个 KPI 的 formula / 阈值(absExceptionThreshold·highCondition·lowCondition)/ 定义,
  并从所有 formula 里抽出 `N./L.` 原始计数器,建"计数器 → 用到它的 KPI"索引。

绝不输出 hit / judgment / conditions:是否成立、根因、以及"故障树引用了哪些字段/计数器"的匹配,
全部由 fault-tree-rca 拿着故障树去做。本引擎只产**事实 / 规则**。

自包含:不 import 任何本仓自定义模块。读 xlsx 需 openpyxl;csv 用标准库。
用法:python analyzer.py <数据文件> -o <输出.json> [--indicator <标签>]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")
_NULLISH = {"", "na", "n/a", "null", "none", "-"}
# 原始话统计数器:以 N./L./Q. 开头的点分名(段内可含 - _,如 L.E-RAB.SuccEst)。
# formula 里计数器都带引号,minus 运算符紧贴引号,故不会跨引号误并。
_COUNTER_RE = re.compile(r"[NLQ]\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")
_ID_CANDIDATES = ("CallID", "CALLID", "Call_Id", "id", "ID", "Index")


# ---------- 读取 ----------
def _decode(raw: bytes) -> str:
    """字节流按候选编码解码,全失败则忽略错误兜底。"""
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def _strip_json_cell(s: str) -> str:
    """CSV 里整段 JSON 常被双引号包裹、内部 "" 转义;剥掉外层引号、还原引号。"""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1].replace('""', '"')
    return s


def _load(path: str):
    """读数据 → ('catalog', nodes) 或 ('tabular', header, rows)。自动判别形态。"""
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
        raw_rows = [["" if c is None else str(c) for c in r] for r in it]
        wb.close()
        # 单列 + 单元格是 JSON → catalog
        if len([h for h in header if h]) <= 1:
            nodes = _parse_catalog([r[0] for r in raw_rows if r], header)
            if nodes:
                return ("catalog", nodes)
        rows = [{header[i]: (r[i] if i < len(r) else "") for i in range(len(header))} for r in raw_rows]
        return ("tabular", header, rows)

    text = _decode(p.read_bytes())
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ("tabular", [], [])
    # catalog 判别:表头是单个 token(无逗号),且下一行像 JSON(可能带引号)
    first, second = lines[0].strip(), (lines[1].strip() if len(lines) > 1 else "")
    header_is_single = ("," not in first) or first.lower().endswith("json")
    second_is_json = _strip_json_cell(second).startswith("{")
    if header_is_single and second_is_json:
        nodes = _parse_catalog(lines[1:], [first])
        if nodes:
            return ("catalog", nodes)
    reader = csv.DictReader(io.StringIO(text))
    header = list(reader.fieldnames or [])
    rows = [{(k or ""): ("" if v is None else str(v)) for k, v in row.items()} for row in reader]
    return ("tabular", header, rows)


def _parse_catalog(cells, header) -> list:
    """把"每格一段 JSON"解析成 dict 列表;非法跳过。"""
    nodes = []
    for c in cells:
        s = _strip_json_cell(str(c))
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                nodes.append(obj)
        except (json.JSONDecodeError, ValueError):
            continue
    return nodes


# ---------- 通用统计 ----------
def _to_num(v):
    """尽量转数值(去掉末尾 %);失败返回 None。"""
    s = str(v).strip().rstrip("%")
    if s.lower() in _NULLISH:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _nonempty(values):
    return [v for v in values if str(v).strip().lower() not in _NULLISH]


def _stat_values(values, samples_for=None):
    """对一列/一键的值做统计:数值(>=90% 可转数)给摘要,否则类别给 top-N。按值判别,不看名字。"""
    ne = _nonempty(values)
    if not ne:
        return {"kind": "empty", "count": 0}
    nums = [_to_num(v) for v in ne]
    ok = [n for n in nums if n is not None]
    if len(ok) >= max(1, int(0.9 * len(ne))):  # 绝大多数能转数 → 数值列
        return {"kind": "numeric", "count": len(ok),
                "min": round(min(ok), 4), "max": round(max(ok), 4),
                "mean": round(sum(ok) / len(ok), 4)}
    cnt = Counter(str(v).strip() for v in ne)
    top = dict(cnt.most_common(20))
    out = {"kind": "categorical", "count": len(ne), "distinct": len(cnt), "top": top}
    if samples_for is not None:  # 给每个 top 值附几个样例标识
        out["samples"] = {val: samples_for(val)[:3] for val in list(top)[:8]}
    return out


# ---------- tabular ----------
def _analyze_tabular(header, rows, indicator) -> dict:
    """表格型:逐列统计;不写死字段,不投影故障树(投影交给 RCA)。"""
    id_col = next((c for c in _ID_CANDIDATES if c in header), None)

    def make_sampler(col):
        def sampler(val):
            return [str(rows[i].get(id_col) if id_col else f"row#{i}")
                    for i, r in enumerate(rows) if str(r.get(col, "")).strip() == val]
        return sampler

    field_stats = {}
    for col in header:
        vals = [r.get(col, "") for r in rows]
        field_stats[col] = _stat_values(vals, samples_for=make_sampler(col))
    return {"indicator": indicator, "kind": "tabular",
            "summary": {"total_records": len(rows), "columns": header},
            "field_stats": field_stats}


# ---------- catalog ----------
def _clean_counter(tok: str) -> str:
    """清洗计数器名:去 HTML 污染(末尾 <span、&gt; 等)、去首尾点。"""
    return re.split(r"[<&]", tok)[0].strip().strip(".")


def _counters_in(formula: str) -> list:
    """从 formula 文本里抽出 N./L./Q. 原始计数器(去重保序、清洗)。"""
    seen, out = set(), []
    for m in _COUNTER_RE.findall(formula or ""):
        c = _clean_counter(m)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _analyze_catalog(nodes, indicator) -> dict:
    """目录型(kpiTree):当"规则字典"——抽 formula/阈值/定义 + 计数器索引。"""
    # 1) 全键统计(数据驱动,别写死)
    keys = []
    for n in nodes:
        for k in n:
            if k not in keys:
                keys.append(k)
    field_stats = {k: _stat_values([n.get(k, "") for n in nodes]) for k in keys}

    # 2) 规则:凡有 formula(或阈值)的节点,摆出它的计算方式 + 阈值 + 定义
    def pick(n, *names):
        for nm in names:
            v = n.get(nm)
            if v not in (None, ""):
                return v
        return None

    rules, counter_index = [], {}
    for n in nodes:
        formula = pick(n, "formula")
        thr = pick(n, "absExceptionThreshold")
        if not formula and thr is None:
            continue
        counters = _counters_in(str(formula)) if formula else []
        rule = {"id": n.get("id"), "name": n.get("name"),
                "kpiIndex": n.get("kpiIndex"), "formula": formula,
                "counters": counters,
                "absExceptionThreshold": thr,
                "highCondition": pick(n, "highCondition"),
                "lowCondition": pick(n, "lowCondition"),
                "unit": pick(n, "unit"), "definition": pick(n, "definition")}
        rules.append({k: v for k, v in rule.items() if v not in (None, "", [])})
        # 3) 计数器 → 用到它的 KPI 索引
        for c in counters:
            ent = counter_index.setdefault(c, {"in_kpis": [], "usage_count": 0})
            ent["usage_count"] += 1
            if len(ent["in_kpis"]) < 8:
                ent["in_kpis"].append({"name": n.get("name"), "kpiIndex": n.get("kpiIndex"),
                                       "formula": formula, "absExceptionThreshold": thr})

    levels = sorted({str(n.get("level")) for n in nodes if n.get("level") is not None})
    return {"indicator": indicator, "kind": "catalog",
            "summary": {"total_nodes": len(nodes), "levels": levels,
                        "distinct_keys": len(keys), "rule_count": len(rules),
                        "distinct_counters": len(counter_index)},
            "field_stats": field_stats,
            "rules": rules,
            "counter_index": counter_index}


# ---------- 入口 ----------
def _default_indicator(script_path, data_path, override):
    if override:
        return override
    stem = Path(script_path).stem
    if stem.endswith("_analyzer"):
        return stem[: -len("_analyzer")] or Path(data_path).stem
    if stem != "analyzer":
        return stem
    return Path(data_path).stem


def analyze(data_path: str, indicator: str) -> dict:
    loaded = _load(data_path)
    if loaded[0] == "catalog":
        return _analyze_catalog(loaded[1], indicator)
    _, header, rows = loaded
    return _analyze_tabular(header, rows, indicator)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="通用解析引擎(只查数据/抽规则,不下判断)")
    ap.add_argument("data", help="数据文件(csv/xlsx)")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--indicator", help="指标标签(默认从脚本名 <ind>_analyzer 或数据文件名推断)")
    args = ap.parse_args()
    indicator = _default_indicator(__file__, args.data, args.indicator)
    result = analyze(args.data, indicator)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[analyzer] indicator={indicator} kind={result.get('kind')} → {out}")


if __name__ == "__main__":
    main()
