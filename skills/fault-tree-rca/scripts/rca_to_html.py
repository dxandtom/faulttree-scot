#!/usr/bin/env python3
"""把 fault-tree-rca 产出的 rca.json 渲染成一份给人看的 HTML 报告(自包含、纯标准库、指标/场景无关)。

读 rca.json(契约见 fault-tree-rca 技能:rca_result / probable_causes / undetermined / tree_issues),
按"确证根因 → 疑似 → 未确定 → 故障树未覆盖"分区呈现;每条根因摆出路径面包屑 + 逐节点 evidence_path
(条件/实际值/判定,判定上色)。结构忠实于 rca.json,不另做判断、不编内容。

用法:
    python rca_to_html.py <rca.json> [-o out.html] [--input rca_input.json] [--scenario 名称] [--title 标题]
- `--input`:可选,从 rca_input.json 取场景名;
- `--scenario`/`--title`:可选,覆盖标题。
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import sys
from pathlib import Path


# ---------- 读取与小工具 ----------
def _load(path: str):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(raw.decode("utf-8", "ignore"))


def esc(x) -> str:
    """任何值 → 转义后的字符串(None/数字也安全);列表转成顿号分隔。"""
    if x is None:
        return ""
    if isinstance(x, (list, tuple)):
        return "、".join(esc(i) for i in x)
    if isinstance(x, dict):
        return esc(json.dumps(x, ensure_ascii=False))
    return html.escape(str(x))


def _confirmed(doc):
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for k in ("rca_result", "root_causes", "results"):
            if isinstance(doc.get(k), list):
                return doc[k]
    return []


def _str(d: dict, *keys):
    """取第一个非空字段(容忍 Root_cause/root_cause 等大小写差异)。"""
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return ""


# ---------- 判定 / 置信上色 ----------
def _judge_class(j: str) -> str:
    j = str(j or "")                                      # judgment 可能是数字等非字符串,强转免 'in' 崩
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
    items = "<span class='arrow'>→</span>".join(f"<span class='crumb'>{esc(p)}</span>" for p in path)
    return f"<div class='path'>{items}</div>"


def _evidence_table(ev) -> str:
    if not isinstance(ev, list) or not ev:
        return ""
    rows = []
    for n in ev:
        if not isinstance(n, dict):                       # off-contract(字符串等)也如实带出,别静默丢证据
            rows.append(f"<tr><td colspan='4' class='ev-raw'>{esc(n)}</td></tr>")
            continue
        node = esc(_str(n, "node", "node_id"))
        nid = esc(n.get("node_id"))
        nid_html = f" <span class='nid'>{nid}</span>" if nid else ""
        cond = esc(_str(n, "condition", "condition_text"))
        mp = n.get("mapping")                             # 契约审计字段:树的量 X → 命中数据列(别只渲染 mapping_reason)
        mp_html = f"<div class='mr'>映射:{esc(mp)}</div>" if mp else ""
        mr = n.get("mapping_reason")
        mr_html = f"<div class='mr'>映射依据:{esc(mr)}</div>" if mr else ""
        actual = esc(n.get("actual"))
        judg = _str(n, "judgment")
        rows.append(
            f"<tr><td class='ev-node'>{node}{nid_html}</td>"
            f"<td class='ev-cond'>{cond}{mp_html}{mr_html}</td>"
            f"<td class='ev-act'>{actual}</td>"
            f"<td class='ev-judg'><span class='judg {_judge_class(judg)}'>{esc(judg)}</span></td></tr>")
    if not rows:
        return ""
    return ("<table class='ev'><thead><tr><th>节点</th><th>判断条件</th>"
            "<th>实际值</th><th>判定</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def _kv(label: str, value, cls="") -> str:
    if not value:
        return ""
    return f"<div class='kv {cls}'><span class='k'>{esc(label)}</span><span class='v'>{esc(value)}</span></div>"


def _confirmed_card(rc: dict) -> str:
    name = esc(_str(rc, "Root_cause", "root_cause") or "(未命名根因)")
    return (f"<div class='card confirmed'><div class='card-h'>"
            f"<span class='tag t-confirmed'>确证根因</span><h3>{name}</h3></div>"
            + _breadcrumb(rc.get("Fault_tree_path"))
            + _kv("支撑", _str(rc, "support"))
            + _evidence_table(_str(rc, "evidence_path", "evidence")) + "</div>")


def _probable_card(rc: dict) -> str:
    name = esc(_str(rc, "Root_cause", "root_cause") or "(未命名疑似)")
    conf = str(rc.get("confidence", "")).lower()
    label, ccls = _CONF.get(conf, (conf or "—", "c-na"))
    badge = f"<span class='conf {ccls}'>置信 {esc(label)}</span>"
    return (f"<div class='card probable {ccls}'><div class='card-h'>"
            f"<span class='tag t-probable'>疑似</span><h3>{name}</h3>{badge}</div>"
            + _breadcrumb(rc.get("Fault_tree_path"))
            + _kv("卡在", _str(rc, "blocked_at"), "kv-block")
            + _kv("存疑", _str(rc, "caveats"), "kv-caveat")
            + _kv("支撑", _str(rc, "support"))
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
        lis = "".join(f"<li>{esc(u)}</li>" for u in unc)
        body += (f"<details{' open' if len(unc) <= 12 else ''}><summary>未覆盖判断节点"
                 f"（{len(unc)}）——有条件却无数据/未绑定</summary><ul class='issue'>{lis}</ul></details>")
    if orphan:
        body += _kv("孤儿节点（绑定填了不存在的 node_id）", orphan)
    return f"<section><h2>故障树未覆盖 / 待补</h2>{body}</section>"


CSS = """
*{box-sizing:border-box}
body{font-family:'Microsoft YaHei','PingFang SC',Arial,sans-serif;margin:0;background:#f4f5f7;color:#222;line-height:1.6}
.wrap{max-width:1180px;margin:0 auto;padding:24px}
header h1{margin:0 0 4px;font-size:24px}
header .meta{color:#8a8f99;font-size:13px;margin-bottom:18px}
.summary{display:flex;gap:14px;flex-wrap:wrap;align-items:center;background:#fff;border-radius:10px;
  padding:16px 20px;box-shadow:0 1px 4px rgba(0,0,0,.06);margin-bottom:8px}
.stat{font-size:15px}.stat b{font-size:22px;margin-right:4px}
.s-confirmed b{color:#2e9e5b}.s-probable b{color:#e0922f}.s-undet b{color:#8a8f99}
.verdict{margin-left:auto;font-size:14px;color:#555;max-width:560px}
h2{margin:26px 0 10px;font-size:18px;padding-left:10px;border-left:4px solid #4caf7d}
section.probable h2,h2.h-prob{border-left-color:#e0922f}
.card{background:#fff;border-radius:10px;padding:16px 18px;margin:12px 0;box-shadow:0 1px 4px rgba(0,0,0,.06);
  border-left:5px solid #ccc}
.card.confirmed{border-left-color:#2e9e5b}
.card.probable.c-high{border-left-color:#e2603b}
.card.probable.c-med{border-left-color:#e0922f}
.card.probable.c-low{border-left-color:#d9b441}
.card.undet{border-left-color:#9aa0aa;background:#fbfbfc}
.card-h{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.card-h h3{margin:0;font-size:16px;flex:1 1 auto}
.tag{font-size:12px;padding:2px 9px;border-radius:10px;color:#fff;white-space:nowrap}
.t-confirmed{background:#2e9e5b}.t-probable{background:#e0922f}.t-undet{background:#9aa0aa}
.conf{font-size:12px;padding:2px 9px;border-radius:10px;color:#fff;white-space:nowrap}
.c-high{background:#e2603b}.c-med{background:#e0922f}.c-low{background:#d9b441}.c-na{background:#9aa0aa}
.path{margin:10px 0;font-size:13px;color:#444}
.crumb{display:inline-block;background:#eef1f5;border-radius:4px;padding:1px 8px;margin:2px 0}
.arrow{color:#aab;margin:0 4px}
.kv{margin:6px 0;font-size:13.5px}.kv .k{color:#7a7f88;margin-right:8px;font-weight:bold}
.kv-block .k{color:#c0612a}.kv-caveat .k{color:#9a7b27}
.kv .v{color:#333}
table.ev{width:100%;border-collapse:collapse;margin:12px 0 2px;font-size:13px}
table.ev th{background:#3a3f48;color:#fff;text-align:left;padding:7px 9px;font-weight:600}
table.ev td{padding:7px 9px;border-bottom:1px solid #eee;vertical-align:top}
table.ev tr:hover{background:#f6f9ff}
.ev-node{width:20%;font-weight:600}.nid{color:#8a8f99;font-weight:normal;font-size:12px}
.ev-cond{width:42%}.mr{color:#8a8f99;font-size:12px;margin-top:3px}
.ev-act{width:24%;color:#333}.ev-judg{width:14%}
.judg{display:inline-block;padding:1px 8px;border-radius:9px;font-size:12px;white-space:nowrap}
.j-hold{background:#d7f0e1;color:#1d7a44}.j-fail{background:#fbe0db;color:#b53b22}
.j-insuf{background:#ececec;color:#666}.j-other{background:#eef1f5;color:#555}
.ev-raw{color:#555;font-style:italic;background:#fafbfc}
details{margin:8px 0}summary{cursor:pointer;font-weight:600;color:#555}
ul.issue{margin:6px 0;font-size:13px;color:#555}ul.issue li{margin:2px 0}
.empty{color:#8a8f99;font-size:14px;padding:8px 0}
footer{color:#aab;font-size:12px;margin-top:28px;text-align:center}
"""


def render(doc, scenario: str, title: str = "") -> str:
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

    parts = [f"<header><h1>{h1}</h1><div class='meta'>生成时间 {now}　·　"
             f"本报告由 rca.json 渲染，内容忠实于诊断结果</div></header>"]
    parts.append(
        "<div class='summary'>"
        f"<span class='stat s-confirmed'><b>{len(confirmed)}</b>确证根因</span>"
        f"<span class='stat s-probable'><b>{len(probable)}</b>疑似</span>"
        f"<span class='stat s-undet'><b>{len(undet)}</b>未确定</span>"
        f"<span class='verdict'>{verdict}</span></div>")

    if confirmed:
        parts.append("<section><h2>确证根因</h2>" + "".join(_confirmed_card(c) for c in confirmed) + "</section>")
    if probable:
        parts.append("<section class='probable'><h2 class='h-prob'>疑似根因（按证据强弱排序，带 caveat）</h2>"
                     + "".join(_probable_card(c) for c in probable) + "</section>")
    if undet:
        parts.append("<section><h2>未确定（缺数据，仅观察）</h2>"
                     + "".join(_undet_card(c) for c in undet) + "</section>")
    parts.append(_tree_issues(tree_issues))
    if not (confirmed or probable or undet):
        parts.append("<div class='empty'>rca.json 三档（确证/疑似/未确定）均为空。</div>")

    parts.append("<footer>fault-tree-rca · rca_to_html</footer>")
    return (f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{h1}</title><style>{CSS}</style></head><body>"
            f"<div class='wrap'>{''.join(p for p in parts if p)}</div></body></html>")


def _scenario_from_input(input_path: str) -> str:
    try:
        d = _load(input_path)
        return (d.get("fault_tree") or {}).get("scenario") or d.get("scenario") or ""
    except Exception:
        return ""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="rca.json → HTML 诊断报告")
    ap.add_argument("rca")
    ap.add_argument("-o", "--output")
    ap.add_argument("--input", help="rca_input.json(可选,取场景名)")
    ap.add_argument("--scenario", default="")
    ap.add_argument("--title", default="")
    args = ap.parse_args()

    doc = _load(args.rca)
    scenario = args.scenario or (_scenario_from_input(args.input) if args.input else "")
    if not scenario and isinstance(doc, dict):
        for tier in ("rca_result", "probable_causes", "undetermined"):
            for rc in (doc.get(tier) or []):
                p = rc.get("Fault_tree_path") if isinstance(rc, dict) else None
                if isinstance(p, list) and p:             # 容忍 path 非 list(LLM 产出不可信),只接受非空列表
                    scenario = str(p[0])
                    break
            if scenario:
                break

    out = Path(args.output) if args.output else Path(args.rca).with_suffix(".html")
    out.write_text(render(doc, scenario, args.title), encoding="utf-8")
    print(f"[rca_to_html] -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
