#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rca.json → 诊断报告 md(机械渲染,零模型参与)。

病根:让模型把 rca.json 总结成表,证据被压成"S脚本matched=true"、内部字段名
(absent=True+matched=0)直接漏给业务同事——总结这活模型也做砸。本脚本把证据栏
从 evidence_path 逐字拼出(命中原文/网元/来源/实测值全保留),模型只在报告末尾
「补充说明」段写机械给不了的东西(同病根归组/处理优先级/复核提示),数字不许改。

手工 RCA 与 debate 的 rca.json 同契约,本渲染器通吃。
用法:python render_report.py <rca.json> [-o <rca_report.md>]   # 缺省写在输入同目录
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 注意匹配序:「不成立」含「成立」子串,必须先查(先长后短)
_SYM = (("不成立", "✗"), ("数据不足", "?"), ("冲突", "⚠"), ("无独立条件", "·"), ("成立", "✓"))


def _sym(j: str) -> str:
    for k, v in _SYM:
        if k in (j or ""):
            return v
    return "~"                                           # 存疑/语义近似/评分制等


def _path_line(ev: list) -> str:
    return " → ".join(f"{e.get('node')}{_sym(str(e.get('judgment') or ''))}" for e in ev)


def _one(s, n=160) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _ev_block(e: dict, indent="  ") -> list:
    """一条 evidence 条目 → 详情行(逐字来自 rca.json,只排版不改写)。"""
    lines = [f"{indent}- 判定:{e.get('judgment')}"]
    if e.get("condition") and e["condition"] != "(无独立判据)":
        lines.append(f"{indent}- 判据:{_one(e['condition'], 300)}")
    if e.get("actual"):
        lines.append(f"{indent}- 实测:{_one(e['actual'], 500)}")
    if e.get("how"):
        lines.append(f"{indent}- 依据:{_one(e['how'], 400)}")
    if e.get("conflict_actual"):
        lines.append(f"{indent}- ⚠反证:{_one(e['conflict_actual'], 300)}"
                     + (f"(依据:{_one(e.get('conflict_how'), 200)})" if e.get("conflict_how") else ""))
    if e.get("other_source_note"):
        lines.append(f"{indent}- 注:{_one(e['other_source_note'], 200)}")
    return lines


def render(doc: dict) -> str:
    conf = doc.get("rca_result") or []
    prob = doc.get("probable_causes") or []
    und = doc.get("undetermined") or []
    ti = doc.get("tree_issues") or {}
    eng = next((c.get("_engine", {}).get("name") for c in conf + prob if isinstance(c, dict) and c.get("_engine")), None)

    L = ["# 诊断报告", "",
         f"**确证根因 {len(conf)} 条 · 疑似 {len(prob)} 条 · 待确认 {len(und)} 条**"
         + (f" · 引擎:{eng}" if eng else "") + "。", ""]

    if conf:
        L += ["## 确证根因", "", "| # | 根因 | 关键证据(摘要,全文见下) | 推荐解决方案 |",
              "|---|---|---|---|"]
        for i, c in enumerate(conf, 1):
            cand = (c.get("evidence_path") or [{}])[-1]
            brief = _one(cand.get("actual") or cand.get("how") or c.get("support"), 120)
            L.append(f"| {i} | {c.get('Root_cause')} | {brief} | {_one(c.get('recommend_solution'), 90) or '—'} |")
        L.append("")
        for i, c in enumerate(conf, 1):
            ev = c.get("evidence_path") or []
            L += [f"### {i}. {c.get('Root_cause')} ✅", "",
                  f"- 路径:{_path_line(ev)}(✓成立 ✗不成立 ?数据不足 ⚠冲突 ·无独立条件)"]
            if ev:
                L.append("- 候选证据:")
                L += _ev_block(ev[-1], "    ")
            if c.get("caveats"):
                L.append(f"- 注意:{_one(c['caveats'], 500)}")
            if c.get("recommend_solution"):
                L.append(f"- 建议方案:{_one(c['recommend_solution'], 400)}")
            L.append("")

    if prob:
        L += ["## 疑似根因(未过确证闸,逐条给出卡点)", ""]
        for i, c in enumerate(prob, 1):
            ev = c.get("evidence_path") or []
            L += [f"### {i}. {c.get('Root_cause')} ⚠(confidence={c.get('confidence', '?')})", "",
                  f"- 卡点:{_one(c.get('blocked_at'), 300) or '(未标)'}",
                  f"- 路径:{_path_line(ev)}"]
            if c.get("caveats"):
                L.append(f"- 原因/注意:{_one(c['caveats'], 600)}")
            if ev:
                L.append("- 候选证据:")
                L += _ev_block(ev[-1], "    ")
                bad = [e for e in ev[:-1] if _sym(str(e.get("judgment") or "")) in "✗⚠"]
                for b in bad[:3]:
                    L.append(f"- 矛盾上级「{b.get('node')}」:")
                    L += _ev_block(b, "    ")
            L.append("")

    if und:
        L += ["## 待确认(数据不足,补齐后重跑可闭环)", ""]
        for c in und[:30]:
            L.append(f"- {(c.get('Fault_tree_path') or ['?'])[-1]}:{_one(c.get('reason'), 200)}")
        if len(und) > 30:
            L.append(f"- …另有 {len(und) - 30} 条,见 rca.json")
        L.append("")

    if doc.get("excluded_note"):
        L += ["## 排除留痕", "", _one(doc["excluded_note"], 1500), ""]

    unc = ti.get("uncovered_condition_nodes") or []
    orp = ti.get("orphan_node_ids") or []
    if unc or orp:
        L += ["## 树面问题", "",
              f"- 本轮无事实节点 {len(unc)} 个(没人绑/缺数据)" if unc else "",
              f"- 孤儿节点 {len(orp)} 个" if orp else "", ""]

    L += ["## 补充说明(模型撰写)", "",
          "_(待补:同病根归组、处理优先级建议、需人复核的疑点——只解读,数字以上文为准;"
          "本节以上全部由 render_report.py 从 rca.json 机械生成,禁止改写)_", ""]
    return "\n".join(x for x in L if x is not None)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="rca.json → 诊断报告 md(机械渲染)")
    ap.add_argument("rca")
    ap.add_argument("-o", "--out", help="输出 md 路径(缺省:rca.json 同目录 rca_report.md)")
    a = ap.parse_args()
    src = Path(a.rca)
    doc = json.loads(src.read_text(encoding="utf-8"))
    md = render(doc)
    out = Path(a.out) if a.out else src.parent / "rca_report.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n[已写] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
