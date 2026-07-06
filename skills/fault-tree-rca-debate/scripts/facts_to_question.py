#!/usr/bin/env python3
"""rca_input.json(证据引擎的确定性事实)→ 辩论引擎的 question 文本。

这是"证据引擎 × 辩论引擎"融合的桥:辩论引擎吃一段自然语言 question、拿条件文本去里面做匹配;
我们把逐节点事实渲染成这段文本,让它的匹配建立在**算出来的事实**上,而不是用户的一句主诉。

**回声策略(本文件最重要的设计,别破坏)**:
- 仅「成立」(status=ok 且 predicate_satisfied=True)的条件,**原文复述判据 + 附实测数字**
  —— 让引擎的文本匹配对真成立的条件命中(full match)。
- 「不成立/数据不足」只报**节点名**,**绝不复述判据原文** —— 否则引擎的 condition_matcher
  可能把"已排除"一节里出现的判据文本误配成 full match,给被排除的分支打满分(假阳性)。
- 「未认领取值 uncovered_values」原样列出(码值本身不是判据文本,无回声风险,且是防漏诊线索)。

兼容 rca_input 两种形态:压缩(fault_tree.pruned_nodes 挂 facts)/ 完整(indicator_results)。

用法(预览):facts_to_question.py <rca_input.json> [--tree-index <tree_index.json>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _fmt_fact(f: dict) -> str:
    """一条 fact → 简短实测描述(只挑有意义的量)。
    D 类组合等瘦身后无数值字段的事实 → 回退成"判定为真(class X)",别渲染出空的「实测:」。"""
    bits = []
    for k in ("resolved_column", "value", "matched", "total", "ratio",
              "count_satisfied", "matched_sessions", "op", "threshold"):
        if f.get(k) is not None:
            bits.append(f"{k}={f[k]}")
    if not bits:
        return f"判定为真(class {f.get('class', '?')},组合/无数值型判据)"
    return ", ".join(bits)


def _fmt_fail(entries: list) -> str:
    """不成立节点的**安全**实测摘要:只报列名+命中计数/比例,绝不带判据的目标值/op/阈值——
    那些是判据文本的组成部分,进 question 会被引擎的文本匹配误配成"成立"(回声策略红线)。"""
    for e in entries or []:
        f = e.get("fact") or e
        if f.get("status") == "ok" and f.get("predicate_satisfied") is False:
            col = f.get("resolved_column")
            if f.get("matched") is not None and f.get("total") is not None:
                m = f"命中 {f['matched']}/{f['total']}"
            elif f.get("count_satisfied") is not None:
                m = f"count={f['count_satisfied']}"
            elif f.get("ratio") is not None:
                m = f"ratio={f['ratio']}"
            else:
                continue
            return f"{col} {m}" if col else m
    return ""


def _insuf_reason(entries: list) -> str:
    """数据不足节点的缺数原因(mapping_reason)——评委据此能建议补什么数据,不给就只剩个名字。"""
    for e in entries or []:
        f = e.get("fact") or e
        if f.get("status") in ("insufficient_data", "manual"):
            r = str(e.get("mapping_reason") or f.get("mapping_reason") or "").strip()
            if r:
                return r[:80]
    return ""


def _classify(entries: list) -> tuple:
    """节点的事实列表 → (held?, failed?, insufficient?, 实测串列表)。"""
    held = failed = insuf = False
    obs = []
    for e in entries or []:
        f = e.get("fact") or e                      # 压缩形态 facts 已扁平(无 fact 包裹)也兼容
        st, ps = f.get("status"), f.get("predicate_satisfied")
        if st == "ok" and ps is True:
            held = True
            obs.append(_fmt_fact(f))
        elif st == "ok" and ps is False:
            failed = True
        elif st in ("insufficient_data", "manual"):
            insuf = True
    return held, failed, insuf, obs


def build_question(rca_input: dict, tree_index: dict | None = None) -> str:
    names = {}
    if tree_index:
        names = {nid: (n.get("name") or nid) for nid, n in (tree_index.get("nodes") or {}).items()}

    held_lines, excluded, insufficient = [], [], []

    ft = rca_input.get("fault_tree")
    if isinstance(ft, dict) and ft.get("pruned_nodes"):                      # 压缩形态
        scenario = ft.get("scenario") or ""
        for nid, node in ft["pruned_nodes"].items():
            facts = node.get("facts")
            if not facts:
                continue
            name = node.get("name") or names.get(nid, nid)
            held, failed, insuf, obs = _classify(facts)
            cond = str(node.get("condition") or "").strip()
            if held:
                cond_part = f"(判据:{cond})" if cond else ""
                held_lines.append(f"- 「{name}」条件成立{cond_part};实测:{'; '.join(obs)}")
            elif failed:                                                     # 只报名+安全计数,不回声判据
                d = _fmt_fail(facts)
                excluded.append(f"{name}({d})" if d else name)
            elif insuf:
                r = _insuf_reason(facts)
                insufficient.append(f"{name}(缺:{r})" if r else name)
    else:                                                                     # 完整形态
        scenario = ""
        for ind, res in (rca_input.get("indicator_results") or {}).items():
            for c in (res or {}).get("conditions", []):
                name = c.get("node") or names.get(c.get("node_id"), c.get("node_id") or "?")
                held, failed, insuf, obs = _classify([c])
                cond = str(c.get("condition_text") or "").strip()
                if held:
                    held_lines.append(f"- 「{name}」条件成立(判据:{cond});实测({ind}):{'; '.join(obs)}")
                elif failed:
                    d = _fmt_fail([c])
                    excluded.append(f"{name}({d})" if d else name)
                elif insuf:
                    r = _insuf_reason([c])
                    insufficient.append(f"{name}(缺:{r})" if r else name)

    parts = [f"故障场景:{scenario or (tree_index or {}).get('scenario', '')}。",
             "以下为各数据源**已由代码算好的确定性事实**(非人工主诉),请据此定位最可能的根因路径。"]
    if held_lines:
        parts.append("\n【成立的判据(附实测)】\n" + "\n".join(held_lines))
    if excluded:
        parts.append("\n【已用数据排除的节点(其判据实测不成立,此处不复述判据)】\n- " + "、".join(sorted(set(excluded))))
    if insufficient:
        parts.append("\n【数据不足、无法判定的节点】\n- " + "、".join(sorted(set(insufficient))))
    # 从未被检查的分支(有判据但无任何绑定):防漏诊信号,此前在辩论通道上归零——只报名字,零回声风险
    unc_nodes = ((rca_input.get("tree_issues") or {}).get("uncovered_condition_nodes")) or []
    unc_names = [(u.get("name") or u.get("id")) if isinstance(u, dict) else names.get(u, str(u))
                 for u in unc_nodes]
    unc_names = [n for n in unc_names if n]
    if unc_names:
        more = f" …等共 {len(unc_names)} 个" if len(unc_names) > 40 else ""
        parts.append("\n【从未被检查的分支(有判据但无任何数据绑定,可能藏漏诊)】\n- "
                     + "、".join(unc_names[:40]) + more)
    unc = rca_input.get("uncovered_values") or {}
    if unc:
        lines = [f"- {col}: " + ", ".join(f"{m['value']}(×{m['count']})" for m in miss)
                 for col, miss in unc.items()]
        parts.append("\n【数据里出现、但未被任何判据认领的取值(可能指向漏诊分支)】\n" + "\n".join(lines))
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="rca_input → 辩论引擎 question 文本(预览)")
    ap.add_argument("rca_input")
    ap.add_argument("--tree-index")
    a = ap.parse_args()
    ri = json.loads(Path(a.rca_input).read_text(encoding="utf-8"))
    ti = json.loads(Path(a.tree_index).read_text(encoding="utf-8")) if a.tree_index else None
    print(build_question(ri, ti))
    return 0


if __name__ == "__main__":
    sys.exit(main())
