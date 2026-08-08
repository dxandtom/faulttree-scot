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
import re
import sys
from pathlib import Path


def _fmt_fact(f: dict) -> str:
    """一条 fact → 简短实测描述(只挑有意义的量)。
    D 类组合等瘦身后无数值字段的事实 → 回退成"判定为真(class X)",别渲染出空的「实测:」。
    S 类脚本事实的金证据是 hits(命中的数据原文片段,代码抓的)——必须带上:评委和最终
    报告的证据栏都靠它;命中原文是数据现场不是判据文本,成立节点上渲染不破回声策略。"""
    bits = []
    for k in ("resolved_column", "value", "matched", "total", "ratio",
              "count_satisfied", "local", "peer", "op", "threshold"):
        if f.get(k) is not None:
            bits.append(f"{k}={f[k]}")
    hits = [str(h).strip() for h in (f.get("hits") or [])[:2] if str(h).strip()]
    if hits:
        bits.append("命中原文:" + " / ".join(f"『{h[:80]}』" for h in hits))
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
    # 整个数据源没提供(execute 部分数据运行,解析步骤被跳过):只报源名+原因,不含判据文本——
    # 名字级暴露与既有小节(排除/数据不足)同级;再给 ASCII 源名(kpi/chr/config…)插零宽空格断词,
    # 引擎的关键词兜底是**子串匹配**,防止「CHR 异常」这类短判据被源名 token 翻成假阳命中
    # (兜底只在 LLM 失败时启用,LLM 主路径读零宽空格无碍)。
    # 让评委知道"这些源的判据既未证实也未证伪",而不是把静默无事实误读成"查过没问题"。
    mi = rca_input.get("missing_indicators") or {}
    if mi:
        zw = lambda s: re.sub(r"[0-9A-Za-z_\-]{3,}", lambda m: "\u200b".join(m.group(0)), str(s))
        parts.append("\n【本轮未提供的数据源(其判据既未证实也未证伪,勿当已排除)】\n"
                     + "\n".join(f"- {zw(k)}: {zw(v)}" for k, v in mi.items()))
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


# ---------------- 辩论侧分层卷宗(信息全员对称,任务各自专属) ----------------
# 上面的 build_question 是给**机械文本匹配器**的回声安全版(不成立/缺数只报名,防误配)。
# 辩论角色(辩护/攻击/回应/一致性/评委)是推理者,不做文本匹配——给它们完整档案:
# **判据原文全员必读**(含被证伪/缺数节点的),附实测事实与反证:攻击者才能拿数据说话,
# 评委才能"实测优先于辩词"。分层:角色任务框架(唯一按角色不同的部分)+ 本组节点档案
# (组局部细节,控 token)+ 全局案情(共享)。信息不裁剪、不做信息不对称。
# engine_boost._wrap_agent_questions 在各 agent 门口把 question 换成这里出的卷宗。

_CAP = 40                                                # 全局清单每节最多带判据全文的节点数


class DossierKit:
    """rca_input + tree_index → 预组装分层卷宗(全局层惰性构建一次,组层按组缓存)。"""

    def __init__(self, rca_input: dict, tree_index: dict):
        nodes = (tree_index or {}).get("nodes") or {}
        self.names = {nid: (n.get("name") or nid) for nid, n in nodes.items()}
        self.conds = {nid: str(n.get("condition") or "").strip() for nid, n in nodes.items()}
        self.parents = {nid: n.get("parent") for nid, n in nodes.items()}
        self.facts: dict = {}
        rank = {"held": 2, "refuted": 1, "insufficient": 0}

        def _put(nid, entries):
            held, failed, insuf, obs = _classify(entries)
            st = "held" if held else ("refuted" if failed else ("insufficient" if insuf else None))
            if not nid or st is None:
                return
            prev = self.facts.get(nid)
            if prev is not None and rank[prev["st"]] >= rank[st]:
                return
            self.facts[nid] = {"st": st, "obs": "; ".join(obs),
                               "fail": _fmt_fail(entries), "insuf": _insuf_reason(entries)}

        ft = (rca_input or {}).get("fault_tree")
        if isinstance(ft, dict) and ft.get("pruned_nodes"):
            self.scenario = ft.get("scenario") or (tree_index or {}).get("scenario") or ""
            for nid, node in ft["pruned_nodes"].items():
                if (node or {}).get("facts"):
                    _put(nid, node["facts"])
        else:
            self.scenario = (tree_index or {}).get("scenario") or ""
            for res in ((rca_input or {}).get("indicator_results") or {}).values():
                for c in (res or {}).get("conditions", []) if isinstance(res, dict) else []:
                    _put(c.get("node_id"), [c])
        self.rca_input = rca_input or {}
        self._global_text = None
        self._groups: dict = {}

    # ----- 素材 -----
    def _nm(self, vnode: dict) -> str:
        nid = vnode.get("code")
        return self.names.get(nid) or vnode.get("chinese_name") or vnode.get("name") or str(nid)

    def _path(self, nid) -> str:
        chain, cur, seen = [], nid, set()
        while cur and cur not in seen and len(chain) < 12:
            seen.add(cur)
            chain.append(self.names.get(cur, str(cur)))
            cur = self.parents.get(cur)
        return " > ".join(reversed(chain))

    def _fact_line(self, nid) -> str:
        f = self.facts.get(nid)
        if f is None:
            return "未绑定:判据从未被机器检查(既未证实也未证伪)"
        if f["st"] == "held":
            return "实测成立" + (f";实测:{f['obs']}" if f["obs"] else "")
        if f["st"] == "refuted":
            return ("已被数据证伪" + (f"(反证:{f['fail']})" if f["fail"] else "")
                    + "——除非质疑数据本身,不可仅凭辩词翻案")
        return "数据不足" + (f"(缺:{f['insuf']})" if f["insuf"] else "") + ",既未证实也未证伪"

    def group_text(self, vnodes) -> str:
        key = tuple(sorted(str(n.get("code")) for n in vnodes))
        if key not in self._groups:
            lines = []
            for n in vnodes:
                nid = n.get("code")
                cond = self.conds.get(nid) or "\n    ".join(n.get("judgment_conditions") or []) \
                    or "(无独立判据)"
                lines.append(f"- 「{self._nm(n)}」(路径:{self._path(nid)})\n"
                             f"  判据:{cond}\n  事实:{self._fact_line(nid)}")
            self._groups[key] = "\n".join(lines)
        return self._groups[key]

    def global_text(self) -> str:
        if self._global_text is None:
            held, refuted, insuf = [], [], []
            for nid, f in sorted(self.facts.items()):
                line = f"- 「{self.names.get(nid, nid)}」判据:{self.conds.get(nid, '')}"
                if f["st"] == "held":
                    held.append(line + (f";实测:{f['obs']}" if f["obs"] else ""))
                elif f["st"] == "refuted":
                    refuted.append(line + (f";反证:{f['fail']}" if f["fail"] else ""))
                else:
                    insuf.append(line + (f";缺:{f['insuf']}" if f["insuf"] else ""))

            def cap(lst, label):
                if not lst:
                    return ""
                extra = f"\n  …等共 {len(lst)} 个(其余略)" if len(lst) > _CAP else ""
                return f"\n【{label}】\n" + "\n".join(lst[:_CAP]) + extra

            parts = [f"场景:{self.scenario}。以下事实均由代码在数据上算出(非人工主诉、非模型判断)。",
                     cap(held, "实测成立的节点(判据+实测)"),
                     cap(refuted, "已被数据证伪的节点(判据+反证;不可仅凭辩词翻案)"),
                     cap(insuf, "数据不足的节点(判据+缺口;既未证实也未证伪)")]
            mi = self.rca_input.get("missing_indicators") or {}
            if mi:
                parts.append("\n【本轮未提供的数据源(其判据既未证实也未证伪,勿当已排除)】\n"
                             + "\n".join(f"- {k}: {v}" for k, v in mi.items()))
            unc_nodes = ((self.rca_input.get("tree_issues") or {}).get("uncovered_condition_nodes")) or []
            unames = [(u.get("name") or u.get("id")) if isinstance(u, dict)
                      else self.names.get(u, str(u)) for u in unc_nodes]
            unames = [x for x in unames if x]
            if unames:
                parts.append("\n【从未被检查的分支(有判据但无绑定,可能藏漏诊)】\n- "
                             + "、".join(unames[:_CAP]))
            unc = self.rca_input.get("uncovered_values") or {}
            if unc:
                parts.append("\n【数据里出现、但未被任何判据认领的取值】\n" + "\n".join(
                    f"- {col}: " + ", ".join(f"{m['value']}(×{m['count']})" for m in miss)
                    for col, miss in unc.items()))
            self._global_text = "\n".join(p for p in parts if p)
        return self._global_text

    def _compose(self, header: str, vnodes) -> str:
        return (header + "\n\n【本组节点档案(判据原文+实测事实;信息全员一致)】\n"
                + self.group_text(vnodes) + "\n\n【全局案情】\n" + self.global_text())

    # ----- 各角色:信息同一份,只有任务框架不同 -----
    def for_defense(self, my_node, sibling_nodes) -> str:
        return self._compose(
            f"【你的任务】你为节点「{self._nm(my_node)}」辩护:基于下方档案论证它为什么可能是"
            "本次故障的根因,并正面回应对它不利的证据(数据证伪/数据不足)。",
            [my_node] + list(sibling_nodes or []))

    def for_respond(self, my_node) -> str:
        return self._compose(
            f"【你的任务】你为节点「{self._nm(my_node)}」回应质疑:针对攻击内容逐点回应,"
            "以档案中的数据实测为准,不得与实测矛盾。", [my_node])

    def for_attack(self, target_node) -> str:
        return self._compose(
            f"【你的任务】你质疑节点「{self._nm(target_node)}」作为根因的主张:优先引用档案中的"
            "数据实测反证与数据缺口,其次才是机制层面的质疑。", [target_node])

    def for_consistency(self) -> str:
        return ("【你的任务】检查辩护词与判据、与已算出的数据事实是否自洽:"
                "与实测矛盾的辩护主张要点名。\n\n【全局案情】\n" + self.global_text())

    def for_judge(self, vnodes) -> str:
        return self._compose(
            "【裁决准则】数据实测优先于任何辩词;判据已被数据证伪的节点不得高分;"
            "数据不足的节点按机制合理性排序;你的评分只用于排序,不构成确证。", vnodes)


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
