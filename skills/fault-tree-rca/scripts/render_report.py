#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rca.json → 诊断报告 md(机械渲染,零模型参与)。

病根:让模型把 rca.json 总结成表,证据被压成"S脚本matched=true"、内部字段名
(absent=True+matched=0)直接漏给业务同事——总结这活模型也做砸。本脚本把证据栏
从 evidence_path 逐字拼出(命中原文/网元/来源/实测值全保留),模型只在报告末尾
「补充说明」段写机械给不了的东西(同病根归组/处理优先级/复核提示),数字不许改。

rca.json 的格式约定只有一份,本渲染器只认它。
用法:python render_report.py <rca.json> [-o <rca_report.md>] [--tree <tree_index.json>] [--lang zh|en]
缺省写在输入同目录。--lang en:标题、表头、标签用英文,节点名用树里的 name_en(要带 --tree),
方案用 tree/solutions.json 的 en,判定词(成立/不成立/数据不足/冲突)译成英文;树里没有英文的
节点回退中文;证据原文、实测值、命令回显与 rca.json 里的自由文本(原因、注意、卡点)不译。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 注意匹配序:「不成立」含「成立」子串,必须先查(先长后短)
_SYM = (("不成立", "✗"), ("数据不足", "?"), ("冲突", "⚠"), ("无独立条件", "·"), ("成立", "✓"))
# 判定词英译(先长后短,避免「不成立」被「成立」先吃掉);未列出的原样保留
_JD_EN = (("不成立", "does not hold"), ("数据不足", "insufficient data"), ("无独立条件", "no independent condition"),
          ("中间节点", "intermediate node"), ("冲突", "conflict"), ("存疑", "doubtful"), ("成立", "holds"))
# 固定文案按语言取;zh 一字不改,en 与 cs-scot-execute/references/reply_format.md 的对照表一致
_T = {
    "zh": {
        "title": "# 诊断报告", "summary": "**确证根因 {c} 条 · 疑似 {p} 条 · 待确认 {u} 条**",
        "engine": " · 引擎:{e}", "tree": "## 树视图",
        "conf": "## 确证根因", "conf_hdr": "| # | 根因 | 关键证据(摘要,全文见下) | 推荐解决方案 |",
        "path": "- 路径:{p}(✓成立 ✗不成立 ?数据不足 ⚠冲突 ·无独立条件)", "cand": "- 候选证据:",
        "unv": "- ℹ 未验证上级(无绑定/数据不足,默认放行——此处没有做判断):",
        "caveat": "- 注意:{t}", "sol": "- 建议方案:{t}",
        "prob": "## 疑似根因(未满足确证条件,逐条给出卡点)", "blocked": "- 卡点:{t}", "unmarked": "(未标)",
        "ppath": "- 路径:{p}", "reason": "- 原因/注意:{t}", "contra": "- 矛盾上级「{n}」:",
        "und": "## 待确认{s}(数据不足的补齐后重跑可闭环;无法判断的属判据/脚本问题,补数据无用)",
        "und_split": "(其中 数据不足 {a} 条 · 数据在但无法判断 {b} 条", "und_mixed": " · 混合 {m} 条",
        "und_more": "- …另有 {n} 条,见 rca.json",
        "excl": "## 排除留痕", "issues": "## 树面问题",
        "no_fact": "- 本轮无事实节点 {n} 个(没人绑/缺数据)", "orphan": "- 孤儿节点 {n} 个",
        "notes": "## 补充说明(模型撰写)",
        "notes_ph": "_(待补:同病根归组、处理优先级建议、需人复核的疑点——只解读,数字以上文为准;"
                    "本节以上全部由 render_report.py 从 rca.json 机械生成,禁止改写)_",
        "ev_judg": "判定", "ev_cond": "判据", "ev_actual": "实测", "ev_how": "依据",
        "ev_conflict": "⚠反证", "ev_conflict_how": "依据", "ev_note": "注",
        "cell_more": "…(全文见 rca_report.md)",
        "r_head": "**诊断结论:确证根因 {c} 条 · 疑似 {p} 条 · 待确认 {u} 条**",
        "r_tree": "**树视图**(只展开确诊/疑似/缺数据所在路径)",
        "r_conf": "**确证根因**", "r_conf_hdr": "| # | 根因 | 根因路径 | 原文匹配(实测 / 依据) | 推荐方案 |",
        "r_prob": "**疑似根因**(未满足确证条件,给出卡点)", "r_prob_hdr": "| # | 根因 | 卡点 | 证据 | 注意 |",
        "r_und": "**待确认**(数据不足/无法判断)", "r_und_more": "- …另有 {n} 条,见 rca_report.md",
        "r_none": "本轮无确证/疑似/待确认节点。", "r_notes": "**补充说明**",
        "r_notes_ph": "_(此处由模型填写:同病根归组、处理优先级、需人复核的疑点;数字以上文为准)_",
    },
    "en": {
        "title": "# Diagnosis Report", "summary": "**Confirmed root causes {c} · Probable {p} · To be confirmed {u}**",
        "engine": " · engine: {e}", "tree": "## Tree view",
        "conf": "## Confirmed root causes", "conf_hdr": "| # | Root cause | Key evidence (summary; full text below) | Recommended solution |",
        "path": "- Path: {p} (✓holds ✗does not hold ?insufficient data ⚠conflict ·no independent condition)",
        "cand": "- Candidate evidence:",
        "unv": "- ℹ Unverified ancestors (no binding / insufficient data; passed by default, not judged here): ",
        "caveat": "- Caveat: {t}", "sol": "- Suggested solution: {t}",
        "prob": "## Probable root causes (did not meet the confirmation criteria; blocked-at is given)",
        "blocked": "- Blocked at: {t}", "unmarked": "(not marked)",
        "ppath": "- Path: {p}", "reason": "- Reason / caveat: {t}", "contra": "- Contradicting ancestor 「{n}」:",
        "und": "## To be confirmed{s} (insufficient data can be closed by rerunning after data is supplied; cannot-judge cases are criterion or script problems, more data will not help)",
        "und_split": " (insufficient data {a} · data present but cannot judge {b}", "und_mixed": " · mixed {m}",
        "und_more": "- … {n} more, see rca.json",
        "excl": "## Exclusion record", "issues": "## Tree issues",
        "no_fact": "- {n} nodes without facts this round (no binding / missing data)", "orphan": "- {n} orphan nodes",
        "notes": "## Notes (written by the model)",
        "notes_ph": "_(To be filled: grouping by common cause, handling priority, points needing human review; "
                    "interpretation only, numbers as stated above. Everything above this section is generated "
                    "mechanically by render_report.py from rca.json and must not be rewritten)_",
        "ev_judg": "Verdict", "ev_cond": "Criterion", "ev_actual": "Observed", "ev_how": "Basis",
        "ev_conflict": "⚠Counter-evidence", "ev_conflict_how": "basis", "ev_note": "Note",
        "cell_more": "…(full text in rca_report.md)",
        "r_head": "**Diagnosis: confirmed root causes {c} · probable {p} · to be confirmed {u}**",
        "r_tree": "**Tree view** (only paths with confirmed/probable/missing-data nodes are expanded)",
        "r_conf": "**Confirmed root causes**", "r_conf_hdr": "| # | Root cause | Path | Match (observed / basis) | Recommended solution |",
        "r_prob": "**Probable root causes** (did not meet the confirmation criteria; blocked-at is given)",
        "r_prob_hdr": "| # | Root cause | Blocked at | Evidence | Caveat |",
        "r_und": "**To be confirmed** (insufficient data / cannot judge)", "r_und_more": "- … {n} more, see rca_report.md",
        "r_none": "No confirmed, probable, or to-be-confirmed node this round.", "r_notes": "**Notes**",
        "r_notes_ph": "_(Filled in by the model: grouping by common cause, handling priority, points needing human review; numbers as stated above)_",
    },
}


def _sym(j: str) -> str:
    for k, v in _SYM:
        if k in (j or ""):
            return v
    return "~"                                           # 存疑/语义近似/评分制等


def _txt(obj: dict, key: str, lang: str):
    """rca.json 里的说明字段:en 且有同名 _en 字段用英文,否则原文。"""
    if lang == "en" and (obj or {}).get(key + "_en"):
        return obj[key + "_en"]
    return (obj or {}).get(key)


def _jd_text(j, lang: str) -> str:
    """判定词:en 时把已知的中文判定词译成英文,其余原样。"""
    s = str(j or "")
    if lang != "en":
        return s
    for k, v in _JD_EN:
        s = s.replace(k, v)
    return s


class _Names:
    """节点名按语言取:优先 node_id 查树,其次按中文名查,树里没有英文回退中文。"""

    def __init__(self, nodes: dict | None, lang: str):
        self.nodes, self.lang = nodes or {}, lang
        self.by_name = {str(n.get("name")): n.get("name_en") for n in self.nodes.values() if n.get("name_en")}

    def __call__(self, name, nid=None):
        if self.lang == "en":
            n = self.nodes.get(str(nid or ""))
            if n and n.get("name_en"):
                return str(n["name_en"])
            if self.by_name.get(str(name)):
                return str(self.by_name[str(name)])
        return str(name if name is not None else "")


def _path_line(ev: list, lang: str = "zh", nm: "_Names | None" = None) -> str:
    nm = nm or _Names(None, lang)
    return " → ".join(f"{nm(e.get('node'), e.get('node_id'))}{_sym(str(e.get('judgment') or ''))}" for e in ev)


def _one(s, n=160) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _cell(s, n=160) -> str:
    """表格单元格:竖线转义——设备回显命中原文常含 |(接口表等),不转义会把 md 表格串列。"""
    return _one(s, n).replace("|", "\\|")


def _ev_block(e: dict, indent="  ", lang: str = "zh") -> list:
    """一条 evidence 条目 → 详情行(逐字来自 rca.json,只排版不改写;en 只译标签与判定词)。"""
    T = _T[lang]
    lines = [f"{indent}- {T['ev_judg']}:{_jd_text(e.get('judgment'), lang)}"]
    if e.get("condition") and e["condition"] != "(无独立判据)":
        lines.append(f"{indent}- {T['ev_cond']}:{_one(e['condition'], 300)}")
    if e.get("actual"):
        lines.append(f"{indent}- {T['ev_actual']}:{_one(_txt(e, 'actual', lang), 500)}")
    if e.get("how"):
        lines.append(f"{indent}- {T['ev_how']}:{_one(e['how'], 400)}")
    if e.get("conflict_actual"):
        lines.append(f"{indent}- {T['ev_conflict']}:{_one(_txt(e, 'conflict_actual', lang), 300)}"
                     + (f"({T['ev_conflict_how']}:{_one(e.get('conflict_how'), 200)})" if e.get("conflict_how") else ""))
    if e.get("other_source_note"):
        lines.append(f"{indent}- {T['ev_note']}:{_one(_txt(e, 'other_source_note', lang), 200)}")
    return lines


def _solution(c: dict, lang: str, solutions: dict | None) -> str:
    """根因的推荐方案:en 且 solutions.json 有该节点的 en 就用英文,否则 rca.json 里的原文(含 _en)。"""
    if lang == "en" and solutions:
        s = (solutions.get(str(c.get("node_id") or "")) or {}).get("en")
        if s:
            return str(s)
    return _txt(c, "recommend_solution", lang) or ""


def _confirmed_row(i: int, c: dict, nm: "_Names", lang: str, solutions: dict | None) -> str:
    """确证根因表一行:根因 | 关键证据摘要(候选实测,其次依据,再次 support) | 推荐方案。"""
    ev = c.get("evidence_path") or []
    cand = (ev or [{}])[-1]
    brief = _cell(_txt(cand, "actual", lang) or cand.get("how") or _txt(c, "support", lang), 120)
    return f"| {i} | {_cell(nm(c.get('Root_cause'), c.get('node_id')), 80)} | {brief} | {_cell(_solution(c, lang, solutions), 90) or '—'} |"


def _confirmed_detail(i: int, c: dict, T: dict, nm: "_Names", lang: str, solutions: dict | None) -> list:
    """一条确证根因的详情段:路径、候选证据、未验证上级提示、注意、建议方案。"""
    ev = c.get("evidence_path") or []
    L = [f"### {i}. {nm(c.get('Root_cause'), c.get('node_id'))} ✅", "",
         T["path"].format(p=_path_line(ev, lang, nm))]
    if ev:
        L.append(T["cand"])
        L += _ev_block(ev[-1], "    ", lang)
    # 未验证的上级(无绑定/数据不足)提示出来:默认放行≠验证过,此处没有做判断
    unv = [e for e in ev[:-1] if _sym(str(e.get("judgment") or "")) == "?"]
    if unv:
        L.append(T["unv"]
                 + "、".join(f"「{nm(u.get('node'), u.get('node_id'))}」" for u in unv[:5])
                 + ("…" if len(unv) > 5 else ""))
    if c.get("caveats"):
        L.append(T["caveat"].format(t=_one(_txt(c, "caveats", lang), 500)))
    if _solution(c, lang, solutions):
        L.append(T["sol"].format(t=_one(_solution(c, lang, solutions), 400)))
    L.append("")
    return L


def _render_confirmed(conf: list, T: dict, nm: "_Names", lang: str, solutions: dict | None) -> list:
    """确证根因节:先摘要表,再逐条详情。"""
    L = [T["conf"], "", T["conf_hdr"], "|---|---|---|---|"]
    for i, c in enumerate(conf, 1):
        L.append(_confirmed_row(i, c, nm, lang, solutions))
    L.append("")
    for i, c in enumerate(conf, 1):
        L += _confirmed_detail(i, c, T, nm, lang, solutions)
    return L


def _probable_detail(i: int, c: dict, T: dict, nm: "_Names", lang: str) -> list:
    """一条疑似根因的详情段:卡点、路径、原因/注意、候选证据、矛盾上级(不成立/冲突)。"""
    ev = c.get("evidence_path") or []
    L = [f"### {i}. {nm(c.get('Root_cause'), c.get('node_id'))} ⚠(confidence={c.get('confidence', '?')})", "",
         T["blocked"].format(t=_one(_txt(c, "blocked_at", lang), 300) or T["unmarked"]),
         T["ppath"].format(p=_path_line(ev, lang, nm))]
    if c.get("caveats"):
        L.append(T["reason"].format(t=_one(_txt(c, "caveats", lang), 600)))
    if ev:
        L.append(T["cand"])
        L += _ev_block(ev[-1], "    ", lang)
        bad = [e for e in ev[:-1] if _sym(str(e.get("judgment") or "")) in "✗⚠"]
        for b in bad[:3]:
            L.append(T["contra"].format(n=nm(b.get('node'), b.get('node_id'))))
            L += _ev_block(b, "    ", lang)
    L.append("")
    return L


def _gap_kind(c: dict) -> str:
    """四态分型:优先按结构化 gap_kind 计数,老产物没有该字段时按 reason 文本推断。"""
    gk = c.get("gap_kind")
    if gk in ("no_data", "cannot_judge", "mixed"):
        return gk
    r = str(c.get("reason") or "")
    if r.startswith("部分缺"):
        return "mixed"
    return "cannot_judge" if r.startswith("数据在,无法判断") else "no_data"


def _render_undetermined(und: list, T: dict, nm: "_Names", lang: str) -> list:
    """待确认节:标题带四态分型计数,最多列 30 条,其余指向 rca.json。"""
    ks = [_gap_kind(c) for c in und]
    cj, mx = ks.count("cannot_judge"), ks.count("mixed")
    split = (T["und_split"].format(a=len(und) - cj - mx, b=cj)
             + (T["und_mixed"].format(m=mx) if mx else "") + ")") if (cj or mx) else ""
    L = [T["und"].format(s=split), ""]
    for c in und[:30]:
        L.append(f"- {nm((c.get('Fault_tree_path') or ['?'])[-1], c.get('node_id'))}:{_one(_txt(c, 'reason', lang), 200)}")
    if len(und) > 30:
        L.append(T["und_more"].format(n=len(und) - 30))
    L.append("")
    return L


def _render_tail(doc: dict, T: dict, lang: str) -> list:
    """报告尾部:排除留痕、树面问题(无事实节点/孤儿节点)、补充说明占位。"""
    L = []
    if doc.get("excluded_note"):
        L += [T["excl"], "", _one(_txt(doc, "excluded_note", lang), 1500), ""]

    ti = doc.get("tree_issues") or {}
    unc = ti.get("uncovered_condition_nodes") or []
    orp = ti.get("orphan_node_ids") or []
    if unc or orp:
        L += [T["issues"], "",
              T["no_fact"].format(n=len(unc)) if unc else "",
              T["orphan"].format(n=len(orp)) if orp else "", ""]

    L += [T["notes"], "", T["notes_ph"], ""]
    return L


def render(doc: dict, tree_text: str | None = None, lang: str = "zh",
           nodes: dict | None = None, solutions: dict | None = None) -> str:
    T = _T.get(lang) or _T["zh"]
    nm = _Names(nodes, lang)
    conf = doc.get("rca_result") or []
    prob = doc.get("probable_causes") or []
    und = doc.get("undetermined") or []
    eng = next((c.get("_engine", {}).get("name") for c in conf + prob if isinstance(c, dict) and c.get("_engine")), None)

    L = [T["title"], "",
         T["summary"].format(c=len(conf), p=len(prob), u=len(und))
         + (T["engine"].format(e=eng) if eng else "") + ("。" if lang == "zh" else "."), ""]
    if tree_text:                                        # 树视图(render_tree.py):只展开有事的路径
        L += [T["tree"], "", "```text", tree_text, "```", ""]

    if conf:
        L += _render_confirmed(conf, T, nm, lang, solutions)

    if prob:
        L += [T["prob"], ""]
        for i, c in enumerate(prob, 1):
            L += _probable_detail(i, c, T, nm, lang)

    if und:
        L += _render_undetermined(und, T, nm, lang)

    L += _render_tail(doc, T, lang)
    return "\n".join(x for x in L if x is not None)


def _cell_full(s, n=400, lang: str = "zh") -> str:
    """回复稿表格单元格:去换行、转义竖线;超长截到 n 并指向报告全文。"""
    t = " ".join(str(s or "").split()).replace("|", "\\|")
    return t if len(t) <= n else t[: n - 1] + _T[lang]["cell_more"]


def _reply_confirmed(conf: list, T: dict, nm: "_Names", lang: str, solutions: dict | None) -> list:
    """回复稿确证根因表:根因 | 根因路径 | 原文匹配(实测 / 依据) | 推荐方案。"""
    L = [T["r_conf"], "", T["r_conf_hdr"], "|---|---|---|---|---|"]
    for i, c in enumerate(conf, 1):
        ev = c.get("evidence_path") or []
        cand = (ev or [{}])[-1]
        match = " / ".join(x for x in (_txt(cand, "actual", lang), cand.get("how")) if x) or _txt(c, "support", lang) or "—"
        path_names = [nm(p, None) for p in (c.get("Fault_tree_path") or [])]
        L.append(f"| {i} | {_cell_full(nm(c.get('Root_cause'), c.get('node_id')), 80, lang)} | {_cell_full(' → '.join(path_names), 160, lang)} "
                 f"| {_cell_full(match, lang=lang)} | {_cell_full(_solution(c, lang, solutions), 200, lang) or '—'} |")
    L.append("")
    return L


def _reply_probable(prob: list, T: dict, nm: "_Names", lang: str) -> list:
    """回复稿疑似表:根因 | 卡点 | 证据(候选实测,其次 support) | 注意。"""
    L = [T["r_prob"], "", T["r_prob_hdr"], "|---|---|---|---|---|"]
    for i, c in enumerate(prob, 1):
        ev = c.get("evidence_path") or []
        cand = (ev or [{}])[-1]
        L.append(f"| {i} | {_cell_full(nm(c.get('Root_cause'), c.get('node_id')), 80, lang)} | {_cell_full(_txt(c, 'blocked_at', lang), 160, lang) or '—'} "
                 f"| {_cell_full(_txt(cand, 'actual', lang) or _txt(c, 'support', lang), lang=lang)} | {_cell_full(_txt(c, 'caveats', lang), 300, lang) or '—'} |")
    L.append("")
    return L


def _reply_undetermined(und: list, T: dict, nm: "_Names", lang: str) -> list:
    """回复稿待确认列表:最多 20 条,其余指向报告全文。"""
    L = [T["r_und"], ""]
    for c in und[:20]:
        L.append(f"- {nm((c.get('Fault_tree_path') or ['?'])[-1], c.get('node_id'))}:{_cell_full(_txt(c, 'reason', lang), 200, lang)}")
    if len(und) > 20:
        L.append(T["r_und_more"].format(n=len(und) - 20))
    L.append("")
    return L


def render_reply(doc: dict, tree_text: str | None = None, lang: str = "zh",
                 nodes: dict | None = None, solutions: dict | None = None) -> str:
    """聊天框回复稿(机械生成,模型原样贴出):树视图 → 确证根因表 → 疑似表 → 待确认 → 补充说明位。
    平台上只看得见聊天框,文件没人打开——所以结论必须进回复正文,且每次一样,不靠模型发挥。"""
    T = _T.get(lang) or _T["zh"]
    nm = _Names(nodes, lang)
    conf = doc.get("rca_result") or []
    prob = doc.get("probable_causes") or []
    und = doc.get("undetermined") or []
    L = [T["r_head"].format(c=len(conf), p=len(prob), u=len(und)), ""]
    if tree_text:
        L += [T["r_tree"], "", "```text", tree_text, "```", ""]
    if conf:
        L += _reply_confirmed(conf, T, nm, lang, solutions)
    if prob:
        L += _reply_probable(prob, T, nm, lang)
    if und:
        L += _reply_undetermined(und, T, nm, lang)
    if not (conf or prob or und):
        L += [T["r_none"], ""]
    L += [T["r_notes"], "", T["r_notes_ph"], ""]
    return "\n".join(L)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="rca.json → 诊断报告 md(机械渲染)")
    ap.add_argument("rca")
    ap.add_argument("-o", "--out", help="输出 md 路径(缺省:rca.json 同目录 rca_report.md)")
    ap.add_argument("--tree", help="tree/<场景>.tree_index.json:带上则报告首节加树视图,并写 rca_tree.txt")
    ap.add_argument("--lang", choices=("zh", "en"), default="zh",
                    help="输出语言:en 时标题/表头/标签用英文,节点名与判据用树里的英文(要带 --tree),缺失回退中文")
    a = ap.parse_args()
    src = Path(a.rca)
    doc = json.loads(src.read_text(encoding="utf-8"))
    out = Path(a.out) if a.out else src.parent / "rca_report.md"
    tree_text, nodes, solutions = None, None, None
    if a.tree:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from render_tree import _load_tree, load_solutions, render_tree
        nodes, root = _load_tree(Path(a.tree))
        solutions = load_solutions(Path(a.tree))
        tree_text = render_tree(doc, nodes, root, lang=a.lang, solutions=solutions)
        (out.parent / "rca_tree.txt").write_text(tree_text + "\n", encoding="utf-8")
    md = render(doc, tree_text, lang=a.lang, nodes=nodes, solutions=solutions)
    out.write_text(md, encoding="utf-8")
    reply = render_reply(doc, tree_text, lang=a.lang, nodes=nodes, solutions=solutions)
    reply_p = out.parent / "rca_reply.md"
    reply_p.write_text(reply, encoding="utf-8")
    print(md)
    print(f"\n[已写] {out}" + (f" 与 {out.parent / 'rca_tree.txt'}" if tree_text else ""))
    print(f"[回复稿] {reply_p} ——最终回复把它全文原样贴出(树视图+结论表),只在末尾「补充说明」落笔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
