#!/usr/bin/env python3
"""把 fault-tree-rca 产出的 rca.json 渲染成一份给人看的 HTML 报告(自包含、纯标准库、指标/场景无关)。

读 rca.json(契约见 fault-tree-rca 技能:rca_result / probable_causes / undetermined / tree_issues),
按"确证根因 → 疑似 → 未确定 → 故障树未覆盖"分区呈现;每条根因摆出路径面包屑 + 逐节点 evidence_path
(条件/映射/实际值/判定,判定上色)。结构忠实于 rca.json,不另做判断、不编内容。
**节点 id 自动解析为名字**:文里裸 nNN → "nNN(节点名)"(名字取自 evidence_path / 路径叶子 / rca_input)。

用法:
    python rca_to_html.py <rca.json> [-o out.html] [--input rca_input.json] [--scenario 名称] [--title 标题]
- `--input`:可选;不给时自动找同目录 rca_input.json(用于把全部 nNN 解析成名字、取场景名)。
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import sys
from pathlib import Path

_ID_NAMES: dict = {}                                       # nNN → 节点名(render 前填好)
_ID_RE = re.compile(r"(?<![0-9A-Za-z_])n\d+(?![0-9A-Za-z_])")


# ---------- 读取与小工具 ----------
def _load(path: str):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(raw.decode("utf-8", "ignore"))


def _annotate(s: str) -> str:
    """把文本里的裸节点 id(如 n36)注成 'n36(节点名)',名字未知则原样。"""
    if not _ID_NAMES or not s:
        return s
    return _ID_RE.sub(lambda m: f"{m.group(0)}（{_ID_NAMES[m.group(0)]}）"
                      if m.group(0) in _ID_NAMES else m.group(0), s)


def esc(x) -> str:
    """转义(None/数字安全;列表用顿号连)。"""
    if x is None:
        return ""
    if isinstance(x, (list, tuple)):
        return "、".join(esc(i) for i in x)
    if isinstance(x, dict):
        return html.escape(json.dumps(x, ensure_ascii=False))
    return html.escape(str(x))


def esca(x) -> str:
    """先把裸节点 id 注上名字,再转义(用于会提到 nNN 的正文字段)。"""
    if x is None:
        return ""
    if isinstance(x, (list, tuple)):
        return "、".join(esca(i) for i in x)
    if isinstance(x, dict):
        return esc(x)
    return html.escape(_annotate(str(x)))


def _confirmed(doc):
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for k in ("rca_result", "root_causes", "results"):
            if isinstance(doc.get(k), list):
                return doc[k]
    return []


def _str(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return ""


def _iter_findings(doc):
    out = list(_confirmed(doc))
    if isinstance(doc, dict):
        for k in ("probable_causes", "undetermined"):
            out += [x for x in (doc.get(k) or []) if isinstance(x, dict)]
    return [x for x in out if isinstance(x, dict)]


def _build_id_names(doc, pruned) -> dict:
    """{nNN: 名字}:① rca_input.pruned_nodes(最全)② evidence_path 的 node_id→node
    ③ blocked_at 恰为裸 id 时 → 该条路径叶子名(常见:卡在某个未触达的叶子)。"""
    names: dict = {}
    if isinstance(pruned, dict):
        for nid, n in pruned.items():
            nm = n.get("name") if isinstance(n, dict) else None
            if nm:
                names[str(nid)] = str(nm)
    for f in _iter_findings(doc):
        for e in (f.get("evidence_path") or f.get("evidence") or []):
            if isinstance(e, dict) and e.get("node_id") and e.get("node"):
                names.setdefault(str(e["node_id"]), str(e["node"]))
    for f in _iter_findings(doc):
        b = str(f.get("blocked_at") or "").strip()
        p = f.get("Fault_tree_path")
        if re.fullmatch(r"n\d+", b) and isinstance(p, list) and p:
            names.setdefault(b, str(p[-1]))
    return names


# ---------- 判定 / 置信上色 ----------
def _judge_class(j) -> str:
    j = str(j or "")
    if "成立" in j and not any(m in j for m in ("不成立", "数据不足", "未判定", "无法")):
        return "j-hold"
    if "不成立" in j:
        return "j-fail"
    if "数据不足" in j or "无法" in j or "缺" in j:
        return "j-insuf"
    return "j-other"


_CONF = {"high": ("高", "c-high"), "medium": ("中", "c-med"), "low": ("低", "c-low")}


# ---------- 片段渲染 ----------
def _breadcrumb(path) -> str:
    if not isinstance(path, list) or not path:
        return ""
    items = "<span class='arrow'>›</span>".join(f"<span class='crumb'>{esc(p)}</span>" for p in path)
    return f"<div class='path'>{items}</div>"


def _evidence_table(ev) -> str:
    if not isinstance(ev, list) or not ev:
        return ""
    rows = []
    for n in ev:
        if not isinstance(n, dict):                       # off-contract 也如实带出,别静默丢证据
            rows.append(f"<tr><td colspan='4' class='ev-raw'>{esca(n)}</td></tr>")
            continue
        nid = n.get("node_id")
        nid_html = f" <span class='nid'>{esc(nid)}</span>" if nid else ""
        cond = esca(_str(n, "condition", "condition_text"))
        mp = n.get("mapping")
        mp_html = f"<div class='mr'><b>映射</b> {esca(mp)}</div>" if mp else ""
        mr = n.get("mapping_reason")
        mr_html = f"<div class='mr'><b>映射依据</b> {esca(mr)}</div>" if mr else ""
        judg = _str(n, "judgment")
        rows.append(
            f"<tr><td class='ev-node'>{esc(_str(n, 'node', 'node_id'))}{nid_html}</td>"
            f"<td class='ev-cond'>{cond}{mp_html}{mr_html}</td>"
            f"<td class='ev-act'>{esca(n.get('actual'))}</td>"
            f"<td class='ev-judg'><span class='judg {_judge_class(judg)}'>{esc(judg)}</span></td></tr>")
    if not rows:
        return ""
    return ("<table class='ev'><thead><tr><th>节点</th><th>判断条件 / 映射</th>"
            "<th>实际值</th><th>判定</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _kv(label: str, value, cls="") -> str:
    if not value:
        return ""
    return f"<div class='kv {cls}'><span class='k'>{esc(label)}</span><span class='v'>{esca(value)}</span></div>"


def _confirmed_card(rc: dict) -> str:
    name = esca(_str(rc, "Root_cause", "root_cause") or "(未命名根因)")
    return (f"<div class='card confirmed'><div class='card-h'>"
            f"<span class='tag t-confirmed'>确证根因</span><h3>{name}</h3></div>"
            + _breadcrumb(rc.get("Fault_tree_path")) + _kv("支撑", _str(rc, "support"), "kv-sup")
            + _evidence_table(_str(rc, "evidence_path", "evidence")) + "</div>")


def _probable_card(rc: dict) -> str:
    name = esca(_str(rc, "Root_cause", "root_cause") or "(未命名疑似)")
    conf = str(rc.get("confidence", "")).lower()
    label, ccls = _CONF.get(conf, (conf or "—", "c-na"))
    return (f"<div class='card probable {ccls}'><div class='card-h'>"
            f"<span class='tag t-probable'>疑似</span><h3>{name}</h3>"
            f"<span class='conf {ccls}'>置信 {esc(label)}</span></div>"
            + _breadcrumb(rc.get("Fault_tree_path"))
            + _kv("卡在", _str(rc, "blocked_at"), "kv-block")
            + _kv("存疑", _str(rc, "caveats"), "kv-caveat")
            + _kv("支撑", _str(rc, "support"), "kv-sup")
            + _evidence_table(_str(rc, "evidence_path", "evidence")) + "</div>")


def _undet_card(rc: dict) -> str:
    return ("<div class='card undet'><div class='card-h'>"
            "<span class='tag t-undet'>未确定</span></div>"
            + _breadcrumb(rc.get("Fault_tree_path"))
            + _kv("卡在", _str(rc, "blocked_at"), "kv-block")
            + _kv("缺什么", _str(rc, "reason"))
            + _kv("观察", rc.get("observed")) + "</div>")


def _tree_issues(ti: dict) -> str:
    if not isinstance(ti, dict):
        return ""
    unc = ti.get("uncovered_condition_nodes") or []
    orphan = ti.get("orphan_node_ids") or []
    if not unc and not orphan:
        return ""
    body = ""
    if unc:
        lis = "".join(f"<li>{esca(u)}</li>" for u in unc)
        body += (f"<details{' open' if len(unc) <= 12 else ''}><summary>未覆盖判断节点"
                 f"（{len(unc)}）——有条件却无数据/未绑定</summary><ul class='issue'>{lis}</ul></details>")
    if orphan:
        body += _kv("孤儿节点（绑定填了不存在的 node_id）", orphan)
    return f"<section><h2 class='h-gray'>故障树未覆盖 / 待补</h2>{body}</section>"


CSS = """
*{box-sizing:border-box}
body{font-family:'PingFang SC','Microsoft YaHei','Segoe UI',Arial,sans-serif;margin:0;
  background:#eef0f3;color:#23272e;line-height:1.65;font-size:14px}
.wrap{max-width:1180px;margin:0 auto;padding:26px 20px 40px}
header h1{margin:0 0 4px;font-size:23px;color:#1b1f24}
header .meta{color:#6b7280;font-size:12.5px;margin-bottom:16px}
.summary{display:flex;gap:22px;flex-wrap:wrap;align-items:center;background:#fff;border-radius:12px;
  padding:16px 22px;box-shadow:0 1px 3px rgba(20,30,50,.08);margin-bottom:6px;border:1px solid #e7eaef}
.stat{font-size:14px;color:#3a3f48}.stat b{font-size:23px;margin-right:5px;vertical-align:-1px}
.s-confirmed b{color:#27924f}.s-probable b{color:#d98008}.s-undet b{color:#7b828c}
.verdict{margin-left:auto;font-size:13.5px;color:#3a3f48;max-width:580px}
h2{margin:28px 0 12px;font-size:17px;color:#23272e;padding-left:11px;border-left:4px solid #27924f}
h2.h-prob{border-left-color:#d98008}h2.h-gray{border-left-color:#9aa1ab}
.card{background:#fff;border:1px solid #e7eaef;border-radius:12px;padding:16px 18px;margin:12px 0;
  box-shadow:0 1px 3px rgba(20,30,50,.06);border-left-width:4px}
.card.confirmed{border-left-color:#27924f}
.card.probable.c-high{border-left-color:#cf4b34}
.card.probable.c-med{border-left-color:#d98008}
.card.probable.c-low{border-left-color:#b88a1e}
.card.probable.c-na{border-left-color:#9aa1ab}
.card.undet{border-left-color:#9aa1ab;background:#fbfcfd}
.card-h{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.card-h h3{margin:0;font-size:15.5px;color:#1b1f24;flex:1 1 auto;font-weight:600}
.tag{font-size:11.5px;padding:2px 9px;border-radius:6px;color:#fff;white-space:nowrap;font-weight:600}
.t-confirmed{background:#27924f}.t-probable{background:#6b7280}.t-undet{background:#9aa1ab}
.conf{font-size:11.5px;padding:2px 10px;border-radius:11px;color:#fff;white-space:nowrap;font-weight:600}
.c-high{background:#cf4b34}.c-med{background:#d98008}.c-low{background:#b88a1e}.c-na{background:#9aa1ab}
.path{margin:11px 0 4px;font-size:13px;color:#3a3f48}
.crumb{display:inline-block;background:#eef1f6;border:1px solid #e2e7ee;border-radius:5px;padding:1px 9px;margin:2px 0;color:#33383f}
.arrow{color:#aeb6c2;margin:0 5px;font-weight:700}
.kv{margin:7px 0;font-size:13.5px}
.kv .k{display:inline-block;font-weight:700;margin-right:9px;color:#fff;background:#8a929c;
  border-radius:5px;padding:0 8px;font-size:12px}
.kv-block .k{background:#cf4b34}.kv-caveat .k{background:#cf9216}.kv-sup .k{background:#3f8f5c}
.kv .v{color:#2b2f36}
table.ev{width:100%;border-collapse:collapse;margin:13px 0 2px;font-size:13px}
table.ev th{background:#363b44;color:#fff;text-align:left;padding:8px 10px;font-weight:600}
table.ev td{padding:8px 10px;border-bottom:1px solid #edf0f4;vertical-align:top;color:#2b2f36}
table.ev tr:hover{background:#f3f7fd}
.ev-node{width:20%;font-weight:600;color:#1b1f24}.nid{color:#8a929c;font-weight:normal;font-size:11.5px}
.ev-cond{width:42%}.mr{color:#5b626c;font-size:12px;margin-top:4px}.mr b{color:#7b828c;font-weight:600}
.ev-act{width:24%}.ev-judg{width:14%}
.judg{display:inline-block;padding:2px 9px;border-radius:11px;font-size:12px;white-space:nowrap;color:#fff;font-weight:600}
.j-hold{background:#27924f}.j-fail{background:#cf4b34}.j-insuf{background:#9aa1ab}.j-other{background:#7b828c}
.ev-raw{color:#5b626c;font-style:italic;background:#fafbfc}
details{margin:8px 0}summary{cursor:pointer;font-weight:600;color:#3a3f48}
ul.issue{margin:7px 0;font-size:13px;color:#4a5059}ul.issue li{margin:3px 0}
.empty{color:#7b828c;font-size:14px;padding:8px 0}
footer{color:#aeb6c2;font-size:12px;margin-top:30px;text-align:center}
"""


def render(doc, scenario: str, title: str, id_names: dict) -> str:
    global _ID_NAMES
    _ID_NAMES = id_names or {}
    confirmed = [c for c in _confirmed(doc) if isinstance(c, dict)]
    probable = [c for c in (doc.get("probable_causes") or []) if isinstance(c, dict)] if isinstance(doc, dict) else []
    undet = [c for c in (doc.get("undetermined") or []) if isinstance(c, dict)] if isinstance(doc, dict) else []
    tree_issues = doc.get("tree_issues") if isinstance(doc, dict) else None
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    h1 = esc(title or (f"{scenario} 根因诊断报告" if scenario else "根因诊断报告"))

    if confirmed:
        verdict = f"已定位 <b>{len(confirmed)}</b> 条确证根因。"
    elif probable:
        verdict = "未闭合到确证根因（多为上游缺数据），下列为按证据排序的疑似根因，供进一步核查。"
    else:
        verdict = "本次未得出根因，详见未确定与未覆盖清单。"

    parts = [f"<header><h1>{h1}</h1><div class='meta'>生成时间 {now}　·　由 rca.json 渲染，"
             f"内容忠实于诊断结果（节点 id 已尽量标注名称）</div></header>",
             "<div class='summary'>"
             f"<span class='stat s-confirmed'><b>{len(confirmed)}</b>确证根因</span>"
             f"<span class='stat s-probable'><b>{len(probable)}</b>疑似</span>"
             f"<span class='stat s-undet'><b>{len(undet)}</b>未确定</span>"
             f"<span class='verdict'>{verdict}</span></div>"]
    if confirmed:
        parts.append("<section><h2>确证根因</h2>" + "".join(_confirmed_card(c) for c in confirmed) + "</section>")
    if probable:
        parts.append("<section><h2 class='h-prob'>疑似根因（按证据强弱排序，带 caveat）</h2>"
                     + "".join(_probable_card(c) for c in probable) + "</section>")
    if undet:
        parts.append("<section><h2 class='h-gray'>未确定（缺数据，仅观察）</h2>"
                     + "".join(_undet_card(c) for c in undet) + "</section>")
    parts.append(_tree_issues(tree_issues))
    if not (confirmed or probable or undet):
        parts.append("<div class='empty'>rca.json 三档（确证/疑似/未确定）均为空。</div>")
    parts.append("<footer>fault-tree-rca · rca_to_html</footer>")
    return (f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{h1}</title><style>{CSS}</style></head><body>"
            f"<div class='wrap'>{''.join(p for p in parts if p)}</div></body></html>")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="rca.json → HTML 诊断报告")
    ap.add_argument("rca")
    ap.add_argument("-o", "--output")
    ap.add_argument("--input", help="rca_input.json(可选;不给则自动找同目录的)")
    ap.add_argument("--scenario", default="")
    ap.add_argument("--title", default="")
    args = ap.parse_args()

    doc = _load(args.rca)
    # rca_input:显式 --input,否则自动探测同目录 rca_input.json(用于解析 id 名字 + 场景名)
    in_path = args.input
    if not in_path:
        sib = Path(args.rca).with_name("rca_input.json")
        in_path = str(sib) if sib.exists() else None
    fault_tree = {}
    if in_path:
        try:
            fault_tree = (_load(in_path).get("fault_tree") or {})
        except Exception:
            fault_tree = {}
    id_names = _build_id_names(doc, fault_tree.get("pruned_nodes"))

    scenario = args.scenario or fault_tree.get("scenario") or ""
    if not scenario:
        for f in _iter_findings(doc):
            p = f.get("Fault_tree_path")
            if isinstance(p, list) and p:
                scenario = str(p[0])
                break

    out = Path(args.output) if args.output else Path(args.rca).with_suffix(".html")
    out.write_text(render(doc, scenario, args.title, id_names), encoding="utf-8")
    print(f"[rca_to_html] -> {out}（解析了 {len(id_names)} 个节点名）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
