#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rca.json + tree_index.json → 文本树视图(机械渲染,零模型参与)。

业务专家要的是"结果落在树的哪儿":整棵树几十个节点不能全铺开,只展开有事的路径。
- 展开:确诊 / 疑似 / 缺数据 节点所在路径(根→节点祖先链全开);确诊/疑似落在中间层时
  (叶子因待定)其子树展开到叶子;缺数据节点本身就是卡点,子树折叠计数;
- 兄弟:展开路径上**已知不成立**的兄弟节点各列一行(符号+名字,不带详情),其余兄弟折叠成
  一行"…(其余 N 个未涉及)";
- 详情:标记节点下带 判据(问题)/ 证据(实测+依据,逐字来自 evidence_path)/ 方案
  (recommend_solution)/ 卡点或缺什么。
符号:✅确诊 ⚠疑似 ?缺数据 ✓成立 ✗不成立 ·未判定/无独立条件

用法:python render_tree.py <rca.json> --tree <tree_index.json> [-o rca_tree.txt]
render_report.py 加 --tree 会把同一棵树塞进报告首节并写 rca_tree.txt。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SYM = (("不成立", "✗"), ("数据不足", "?"), ("冲突", "⚠"), ("无独立条件", "·"), ("成立", "✓"))
_NID_RE = re.compile(r"\((\w+)\)\s*$")


def _sym(j: str) -> str:
    for k, v in _SYM:
        if k in (j or ""):
            return v
    return "~"


def _one(s, n=160) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _load_tree(path: Path):
    idx = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes = idx.get("nodes") or {}
    root = idx.get("root")
    if root not in nodes:
        roots = [nid for nid, n in nodes.items() if not n.get("parent")]
        root = roots[0] if roots else None
    return nodes, root


def _kids(nodes, nid):
    ks = [k for k in (nodes[nid].get("children") or []) if k in nodes]
    if not ks:
        ks = sorted(k for k, n in nodes.items() if n.get("parent") == nid)
    return ks


def _ancestors(nodes, nid):
    out, cur, guard = [], nodes.get(nid, {}).get("parent"), 0
    while cur and cur in nodes and guard < 64:
        out.append(cur)
        cur = nodes[cur].get("parent")
        guard += 1
    return out


def _by_name(nodes, name):
    hits = [nid for nid, n in nodes.items() if str(n.get("name")) == str(name)]
    return hits[0] if len(hits) == 1 else None


def collect(doc: dict, nodes: dict):
    """rca.json → 标记节点 {nid: (tag, item)}、证据 {nid: evidence 条目}、已知不成立 {nid: 原因}。"""
    flags, ev, failed = {}, {}, {}
    for tag, key in (("confirmed", "rca_result"), ("probable", "probable_causes")):
        for it in doc.get(key) or []:
            nid = str(it.get("node_id") or "") or _by_name(nodes, (it.get("Fault_tree_path") or [""])[-1])
            if nid and nid in nodes:
                flags.setdefault(nid, (tag, it))
            for e in it.get("evidence_path") or []:
                eid = str(e.get("node_id") or "") or _by_name(nodes, e.get("node"))
                if eid and eid in nodes:
                    if eid not in ev or tag == "confirmed":
                        ev[eid] = e
                    if "不成立" in str(e.get("judgment") or ""):
                        failed.setdefault(eid, _one(e.get("actual") or e.get("judgment"), 120))
    for it in doc.get("undetermined") or []:
        m = _NID_RE.search(str(it.get("blocked_at") or ""))
        nid = m.group(1) if m else _by_name(nodes, (it.get("Fault_tree_path") or [""])[-1])
        if nid and nid in nodes and nid not in flags:
            flags[nid] = ("undetermined", it)
    # 排除留痕:「名」(nid):原因 → 已知不成立的候选(展开路径上的兄弟按此点名)
    for m in re.finditer(r"「([^」]+)」\((\w+)\):([^;]*)", str(doc.get("excluded_note") or "")):
        nid = m.group(2) if m.group(2) in nodes else _by_name(nodes, m.group(1))
        if nid:
            failed.setdefault(nid, _one(m.group(3), 120))
    return flags, ev, failed


_TAG = {"confirmed": ("✅", "确诊"), "probable": ("⚠", "疑似"), "undetermined": ("?", "缺数据")}


def render_tree(doc: dict, nodes: dict, root: str) -> str:
    flags, ev, failed = collect(doc, nodes)
    if root is None or root not in nodes:
        return "(tree_index 无根节点,树视图略)"
    expand = set()                                        # 要往下走的节点(标记节点的祖先链 + 自身)
    for nid in flags:
        expand.add(nid)
        expand.update(_ancestors(nodes, nid))
    deep = {nid for nid, (tag, _it) in flags.items() if tag != "undetermined"}   # 确诊/疑似:子树到叶子

    def symbol(nid):
        if nid in flags:
            return _TAG[flags[nid][0]][0]
        if nid in failed:
            return "✗"
        if nid in ev:
            return _sym(str(ev[nid].get("judgment") or ""))
        return "·"

    def label(nid):
        n = nodes[nid]
        s = f"{symbol(nid)} {n.get('name')}"
        if nid in flags:
            s += f"  [{_TAG[flags[nid][0]][1]}]"
        return s

    def details(nid):
        tag, it = flags[nid]
        e = ev.get(nid) or {}
        out = []
        cond = e.get("condition") or nodes[nid].get("condition")
        if cond and cond != "(无独立条件)":
            out.append(f"判据:{_one(cond, 200)}")
        if tag == "undetermined":
            out.append(f"缺:{_one(it.get('reason') or '数据不足', 200)}")
        else:
            if e.get("actual"):
                out.append(f"证据:{_one(e['actual'], 300)}")
            elif it.get("support"):
                out.append(f"证据:{_one(it['support'], 300)}")
            if e.get("how"):
                out.append(f"依据:{_one(e['how'], 200)}")
            if tag == "probable" and it.get("blocked_at"):
                out.append(f"卡点:{_one(it['blocked_at'], 160)}")
            if it.get("caveats"):
                out.append(f"注意:{_one(it['caveats'], 200)}")
            if it.get("recommend_solution"):
                out.append(f"方案:{_one(it['recommend_solution'], 300)}")
        return out

    lines = []

    def walk(nid, prefix, is_last, is_root, in_deep):
        conn = "" if is_root else ("└─ " if is_last else "├─ ")
        lines.append(prefix + conn + label(nid))
        child_prefix = prefix if is_root else prefix + ("   " if is_last else "│  ")
        if nid in flags:
            for d in details(nid):
                lines.append(child_prefix + "      " + d)
        kids = _kids(nodes, nid)
        if not kids:
            return
        deep_here = in_deep or nid in deep
        if deep_here:                                     # 确诊/疑似子树:全展开到叶子
            for i, k in enumerate(kids):
                walk(k, child_prefix, i == len(kids) - 1, False, True)
            return
        if nid not in expand:
            return
        if nid in flags and flags[nid][0] == "undetermined":   # 缺数据:卡点在此,子树折叠
            lines.append(child_prefix + f"└─ …(子树 {_count(nid)} 个节点未判定,数据补齐后重跑)")
            return
        on = [k for k in kids if k in expand]
        shown_off = [k for k in kids if k not in expand and k in failed]        # 已知不成立的兄弟
        other = [k for k in kids if k not in expand and k not in failed]
        items = [(k, "on") for k in kids if k in on or k in shown_off]
        if other:
            items.append((None, "other"))
        for i, (k, kind) in enumerate(items):
            last = i == len(items) - 1
            if kind == "other":
                lines.append(child_prefix + ("└─ " if last else "├─ ")
                             + f"…(其余 {len(other)} 个分支未涉及)")
            elif k in expand:
                walk(k, child_prefix, last, False, False)
            else:
                lines.append(child_prefix + ("└─ " if last else "├─ ") + label(k))

    def _count(nid):
        n, stack = 0, list(_kids(nodes, nid))
        while stack:
            x = stack.pop()
            n += 1
            stack.extend(_kids(nodes, x))
        return n

    n_c = sum(1 for t, _ in flags.values() if t == "confirmed")
    n_p = sum(1 for t, _ in flags.values() if t == "probable")
    n_u = sum(1 for t, _ in flags.values() if t == "undetermined")
    head = [f"树视图:确诊 {n_c} · 疑似 {n_p} · 缺数据 {n_u}(只展开这些节点所在路径;"
            "✅确诊 ⚠疑似 ?缺数据 ✓成立 ✗不成立 ·未判定)", ""]
    if not flags:
        return "\n".join(head + [f"{nodes[root].get('name')}", "└─ …(本轮无确诊/疑似/缺数据节点,树未展开)"])
    walk(root, "", True, True, False)
    return "\n".join(head + lines)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="rca.json + tree_index → 文本树视图(机械渲染)")
    ap.add_argument("rca")
    ap.add_argument("--tree", required=True, help="tree/<场景>.tree_index.json")
    ap.add_argument("-o", "--out", help="输出 txt(缺省:rca.json 同目录 rca_tree.txt)")
    a = ap.parse_args()
    src = Path(a.rca)
    doc = json.loads(src.read_text(encoding="utf-8"))
    nodes, root = _load_tree(Path(a.tree))
    txt = render_tree(doc, nodes, root)
    out = Path(a.out) if a.out else src.parent / "rca_tree.txt"
    out.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    print(f"\n[已写] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
