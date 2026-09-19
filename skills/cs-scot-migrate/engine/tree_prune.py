#!/usr/bin/env python3
"""把 tree_index + 各源逐条件事实 → 压缩成"相关局部子树"(给 RCA),并产覆盖报告。

只做两件确定性的事,**不下任何判断**(成立/不成立/疑似仍由 fault-tree-rca 判):
1. **剪枝**:保留"有事实(被绑定)的节点 + 它们到根的祖先",其余分支砍掉;每个保留节点把它的事实挂上。
   —— RCA 因此既有**结构(路径/父子/AND-OR)**又有**事实**,而不必读整棵 69KB 子树。
2. **覆盖报告(= tree_issues 的一部分)**:
   - `uncovered_condition_nodes`:有判断条件、却没有任何绑定的节点(模型漏绑 / 无可用数据源);
   - `orphan_node_ids`:事实里引用了 index 中**不存在**的 node_id(typo 或模型造假信号)。

用法(也可被 runner import):
    tree_prune.py --index <tree_index.json> --results kpi=<kpi.json> --results chr=<chr.json> -o pruned.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def _facts_by_node(results: dict, alias: dict) -> dict:
    """{规范node_id: [该节点的所有事实条目]};node_id 用 alias 解析(认 nN key 或 code 两种);
    没填 node_id 的归 __no_node_id__,解析不到真实节点的留原值(后续判为孤儿)。"""
    m = defaultdict(list)
    for ind_name, res in (results or {}).items():
        for c in (res or {}).get("conditions", []):
            entry = {"indicator": ind_name, "id": c.get("id"), "class": c.get("class"),
                     "condition_text": c.get("condition_text"),
                     "mapping_reason": c.get("mapping_reason"), "fact": c.get("fact")}
            raw = c.get("node_id")
            nid = alias.get(raw, raw) if raw else "__no_node_id__"
            m[nid].append(entry)
    return m


_FACT_KEEP = ("class", "status", "gap_kind", "predicate_satisfied", "resolved_column", "value",
              "matched", "total", "ratio", "top", "count_satisfied",
              "valid", "n_rows", "filter_rows",
              "nulls", "op", "threshold", "absent",
              "group_by", "groups", "matched_groups", "sample_group",
              "match", "extract", "local", "peer", "equal", "fault_when", "n_local", "n_peer",
              "params", "expand", "fail_values", "hit_values", "sample_value",
              "local_source", "peer_source", "hits", "checked_nes", "script")


_AGG_ZH = {"mean": "平均", "min": "最小", "max": "最大", "sum": "合计", "last": "最新", "first": "最早"}
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def _filter_zh(flt) -> str:
    """filter 子句 →「列=值」短语(让 explain 说清"先选中了哪些行再算")。拼不出返回空串。"""
    try:
        parts = []
        for c in (flt if isinstance(flt, list) else [flt]):
            tgt = c.get("equals") if c.get("equals") is not None else c.get("contains")
            if isinstance(tgt, list):
                tgt = "/".join(str(t) for t in tgt)
            parts.append(f"{c.get('column')}={tgt}")
        return "、".join(parts)
    except Exception:  # noqa: BLE001
        return ""


def _extract_zh(spec) -> str:
    """extract 规格 → "抽哪段值"短语。"""
    if not isinstance(spec, dict):
        return ""
    return (f"「{spec['kv']}」后面的值" if spec.get("kv")
            else f"正则「{spec.get('regex')}」抽出的值")


def _sub_explains(subs) -> str:
    """把 D 组合的各子条件事实递归拼成 ①…;②… 一段(数字全部来自事实原字段)。"""
    parts = []
    for i, sf in enumerate((subs or [])[:4]):
        mark = _CIRCLED[i] if i < len(_CIRCLED) else f"{i + 1}."
        # 没有对应分支的子条件(如 S 脚本)同样用原 explain 作为默认;sub_facts 不进精简后的产物,不要引用它
        parts.append(mark + (_explain(sf) or str(sf.get("explain") or "")[:120]
                             or f"子条件{i + 1}({sf.get('class')} 类)"))
    if len(subs or []) > 4:
        parts.append(f"…等共 {len(subs)} 个子条件")
    return ";".join(parts)


def _explain_prefix(f: dict) -> str:
    """解释的前缀:参数化查询代入了什么参数、filter 先筛出了多少行(拼不出的部分留空)。"""
    pre = ""
    if f.get("params"):                                  # 参数化查询(X):先说清代入了什么参数
        pv = "、".join(f"{k}=「{v}」" for k, v in list(f.get("params").items())[:3])
        pre += f"先从数据里抽出参数({pv})代入,"
    if f.get("filter") and f.get("filter_rows") is not None:
        fd = _filter_zh(f.get("filter"))
        if fd:
            pre += f"先按「{fd}」筛出 {f.get('filter_rows')} 行,"
    return pre


def _explain_expand(f: dict, ex_info: dict, pre: str, verdict: str) -> str:
    """expand 逐值展开:按值说结果,点名违规/命中的值。"""
    need = "每个值都要满足" if ex_info.get("mode") == "all" else "任一值满足即可"
    head = (pre + f"参数「{ex_info.get('param')}」共 {ex_info.get('n')} 个值逐个代入查({need}):"
            f"{ex_info.get('n_true')} 个成立、{ex_info.get('n_false')} 个不成立")
    if ex_info.get("n_insuf"):
        head += f"、{ex_info.get('n_insuf')} 个查不了"
    if f.get("fail_values"):
        head += f";不满足的值:「{'」「'.join(f['fail_values'])}」"
    if f.get("hit_values"):
        head += f";命中的值:「{'」「'.join(f['hit_values'])}」"
    return head + f" → {verdict}"


def _explain_match(f: dict, col, pre: str, verdict: str) -> str:
    """C/G 字段匹配:在哪列多少行里找什么、命中几行;absent 反向判(命中 0 行=未配置=成立)。"""
    ex = f" 每格抽{_extract_zh(f['extract'])}后" if f.get("extract") else ""
    verb = "按正则匹配" if f.get("match") == "regex" else "找"
    tgt = "、".join(f"「{t}」" for t in (f.get("targets") or [])[:3])
    quote = f";命中原文:「{f['hits'][0]}」" if f.get("hits") else ""
    if f.get("absent"):
        hit = f.get("matched", 0)
        return (pre + f"在「{col}」列的 {f.get('total')} 行里{ex}{verb} {tgt} → 命中 {hit} 行"
                + ("(没有这项内容,即「未配置」)→ 成立" if hit == 0
                   else f"(该内容存在)→ 不成立{quote}"))
    return (pre + f"在「{col}」列的 {f.get('total')} 行里{ex}{verb} {tgt} → 命中 {f.get('matched')} 行"
            f" → {verdict}{quote}")


def _explain_threshold(f: dict, col, pre: str, verdict: str) -> str:
    """A 阈值:逐行计数(count_rows)或聚合值与阈值比较。"""
    ex = f"每格抽{_extract_zh(f['extract'])},再" if f.get("extract") else ""
    op, thr = f.get("op"), f.get("threshold")
    quote = f";命中原文:「{f['hits'][0]}」" if f.get("hits") else ""
    if f.get("scope") == "count_rows":
        return (pre + f"「{col}」{ex}逐行判「{op} {thr}」:{f.get('count_satisfied')}/{f.get('valid')} 行满足"
                f" → {verdict}{quote}")
    agg = _AGG_ZH.get(f.get("agg"), f.get("agg") or "")
    return (pre + f"「{col}」{ex}{f.get('valid')} 个有效值取{agg} = {f.get('value')},判「{op} {thr}」→ {verdict}{quote}")


def _explain_pair(f: dict, col, pre: str, verdict: str) -> str:
    """P 两侧对比:本端/对端各取什么值、一致与否、按哪条规则判。"""
    ex = f"(每格抽{_extract_zh(f['extract'])})" if f.get("extract") else ""
    rule = "不一致即故障" if f.get("fault_when") == "not_equal" else "一致即故障"
    side = "不一致" if not f.get("equal") else "一致"
    src = (f"(本端取自「{f.get('local_source')}」、对端取自「{f.get('peer_source')}」)"
           if f.get("local_source") or f.get("peer_source") else "")
    return (pre + f"对比两侧的「{col}」{ex}{src}:本端=「{f.get('local')}」、对端=「{f.get('peer')}」"
            f"→ 两侧{side},按「{rule}」→ {verdict}")


def _explain_grouped(f: dict, need: str, pred) -> str:
    """D 组合的 group_by 分组说明:分了几组、哪些组满足,并以样例组展开各子条件。"""
    mg = f.get("matched_groups") or []
    detail = _sub_explains(f.get("sub_facts"))
    sample = f.get("sample_group")
    if pred is True:
        head = (f"按「{f.get('group_by')}」分成 {f.get('groups')} 组,每组单独查全部子条件:"
                f"「{'」「'.join(mg)}」{'等 ' + str(f.get('matched_groups_total')) + ' 个' if f.get('matched_groups_total') else ''}组满足")
        if detail and sample:
            head += f"——以「{sample}」组为例:{detail} → 组内{need}满足"
        return head + " → 成立"
    head = (f"按「{f.get('group_by')}」分成 {f.get('groups')} 组,每组单独查全部子条件:"
            f"没有任何一组{need}满足")
    if detail and sample:
        head += f"(以「{sample}」组为例:{detail})"
    return head + " → 不成立"


def _explain_combo(f: dict, verdict: str, pred) -> str:
    """D 组合:含 group_by 时按组说明,否则列出各子条件(递归拼各子条件的解释)。"""
    need = "全部" if f.get("combine", "AND") == "AND" else "任一"
    if f.get("group_by"):
        return _explain_grouped(f, need, pred)
    subs = f.get("sub_facts") or []
    detail = _sub_explains(subs)
    return (f"{len(subs)} 个子条件须{need}满足:{detail} → {verdict}" if detail
            else f"{len(subs)} 个子条件须{need}满足(各子条件详见 sub_facts)→ {verdict}")


def _explain(f: dict) -> str:
    """把一条事实拼成"怎么判的"易懂说明(给不懂判据分类的人看,也给 RCA 报告直接引用)。
    机械生成、数字取自事实原字段——弱模型引用它就不会把数值/规则改写编错。拼不出的类返回空串。
    D 组合(含 group_by 分组)会**递归**拼装各子条件的解释:报告读者能看到"在哪个组里、
    每个子条件各命中多少"而非一句笼统的"组合成立"。"""
    st, pred = f.get("status"), f.get("predicate_satisfied")
    verdict = "成立" if pred is True else ("不成立" if pred is False else "无法判定")
    if st == "insufficient_data":
        return f"查不了:{str(f.get('reason', ''))[:120]}"
    if st == "manual":
        return "需人工现场确认,自动数据判不了"
    cls, col = f.get("class"), f.get("resolved_column")
    pre = _explain_prefix(f)
    try:
        ex_info = f.get("expand")
        if ex_info:                                      # expand 逐值展开:按值说结果,点名违规/命中的值
            return _explain_expand(f, ex_info, pre, verdict)
        if cls in ("C", "G"):
            return _explain_match(f, col, pre, verdict)
        if cls == "A":
            return _explain_threshold(f, col, pre, verdict)
        if cls == "P":
            return _explain_pair(f, col, pre, verdict)
        if cls == "D":
            return _explain_combo(f, verdict, pred)
    except Exception:  # noqa: BLE001
        return ""                                        # 解释拼不出绝不能影响剪枝主流程
    return ""


def _ev_str(x) -> str:
    """证据条目转成易懂文字:主路径(custom_lib 的通用适配函数)给的是拼好的字符串标签;file= 直调脚本可能
    透传 {type,id,data} 原始 dict——机械拼 type:id + k=v(剔 data 大块与 _ 元键),
    不许把 Python repr 漏给报告。"""
    if isinstance(x, dict):
        head = ":".join(str(x[k]) for k in ("type", "id") if x.get(k))
        rest = " ".join(f"{k}={v}" for k, v in x.items()
                        if k not in ("type", "id", "data") and not str(k).startswith("_"))
        return (head + (" " + rest if rest else "")).strip() or str(x)
    return str(x)


def _slim_fact(entry: dict) -> dict:
    """精简一条事实:去掉与节点重复的 condition_text、截断 mapping_reason,只留关键结果字段。"""
    f = entry.get("fact") or {}
    out = {"id": entry.get("id"), "indicator": entry.get("indicator")}
    if entry.get("mapping_reason"):
        out["mapping_reason"] = entry["mapping_reason"][:100]
    out.update({k: f[k] for k in _FACT_KEEP if k in f})
    # S 脚本的现场证据限量放行:evidence 标签(网元:实体 命中原文(来源:文件))和命中网元名单是
    # 报告证据栏与 harness 追问的原料——丢了它,报告只剩一句裸的"命中原文",哪个网元哪个文件全没了
    if isinstance(f.get("evidence"), list) and f["evidence"]:
        out["evidence"] = [_ev_str(x)[:300] for x in f["evidence"][:5]]
    if isinstance(f.get("matched_nes"), list) and f["matched_nes"]:
        out["matched_nes"] = [str(x) for x in f["matched_nes"][:8]]
        if len(f["matched_nes"]) > 8:                    # 截断后总数另存,下游算"几比几"不失真
            out["matched_nes_total"] = len(f["matched_nes"])
    # 非 ok 事实带上机器理由(截断):内核的"为什么数据不足"(含 extract 抽不到的样例、列解析失败详情)是 RCA 写
    # 供 undetermined 与复核错误绑定时使用的依据——绑定算错时,模型靠它才能提出异议。只挂在非 ok 的事实上、截到 160 字,对压缩比影响可忽略。
    if f.get("status") != "ok" and f.get("reason"):
        out["reason"] = str(f["reason"])[:160]
    # "怎么判的"说明:机械重拼自事实原字段;没有对应分支的(S 脚本)用事实原 explain 作为默认——
    # 它由通用适配函数/k_script 机械拼装("N/M 个网元命中"+脚本说明),数字同样不过模型的手
    ex = _explain(f) or str(f.get("explain") or "")[:300]
    if ex:
        out["explain"] = ex
    return out


# outline 图例:随 payload 一起下发(数据自解释)。历史教训:曾用英文缩词 hold/pass 且全链无解释,
# 弱模型把 hold 读成"搁置待查"(on hold)、把成立的节点丢进待确认写"具体证据待查"——词是给模型看的,
# 必须不可误读、且定义随数据走,不指望模型去翻文档。
OUTLINE_LEGEND = ("粗状态图例(仅导航,判档必须读该节点 facts):成立=有查证成功且命中的事实(有正向证据,"
                  "不许丢待确认);不成立=查了未命中;数据不足=查不了;未覆盖=无任何事实(没人绑/缺数据源);"
                  "免检=高层分类节点,不参与判断")


def _coarse(entry_list, pass_through) -> str:
    """给 outline 用的粗状态(仅导航,RCA 仍重判):成立/不成立/数据不足/未覆盖/免检(见 OUTLINE_LEGEND)。"""
    if pass_through:
        return "免检"
    if not entry_list:
        return "未覆盖"
    pairs = [((e.get("fact") or {}).get("status"), (e.get("fact") or {}).get("predicate_satisfied"))
             for e in entry_list]
    if any(s == "ok" and p is True for s, p in pairs):
        return "成立"
    if any(s == "ok" and p is False for s, p in pairs):
        return "不成立"
    if any(s in ("insufficient_data", "manual") for s, _ in pairs):
        return "数据不足"
    return "?"


def _alias_table(nodes: dict) -> dict:
    """别名表:节点 key(nN)和 code 都指向同一节点 → 绑定填哪个 node_id 都能挂上。"""
    alias = {}
    for nid, n in nodes.items():
        alias[nid] = nid
        if n.get("code"):
            alias[n["code"]] = nid
    return alias


def _kept_node_ids(index: dict, nodes: dict, facts: dict) -> set:
    """要保留的节点集合:有事实的真实节点 + 它们到根的祖先 + 根 + 保留节点下的"其他"类子节点。"""
    keep = set()
    for nid in [k for k in facts if k in nodes]:        # 有事实的真实节点
        cur = nid
        while cur and cur in nodes and cur not in keep:  # 连同祖先一路留到根
            keep.add(cur)
            cur = nodes[cur].get("parent")
    if index.get("root"):
        keep.add(index["root"])
    # "其他"类节点也留:任一保留节点的"非典型/其他/unspecified"子节点 → 留住,给未被具体兄弟认领的记录一个归处
    # (绑定漏绑时,RCA 仍能据原始事实把那些记录归到这个真实节点上、可展示,不致随剪枝静默消失)。
    _CATCHALL = ("非典型", "其他", "其它", "unspecified", "unknown", "other", "misc")

    def _is_catchall(n):
        tl = ((n.get("name") or "") + " " + (n.get("condition") or "")).lower()
        return any(k in tl for k in _CATCHALL)

    for nid in list(keep):
        for c in (nodes[nid].get("children") or []):
            if c in nodes and _is_catchall(nodes[c]):
                keep.add(c)
    return keep


def _drop_insufficient_if_established(ent: list) -> list:
    """多源合成剔噪:节点已被 ok+成立 事实确立时,剔掉同节点的 insufficient_data 事实。"""
    # 多源合成剔噪:节点已被真凭实据确立(存在 ok+成立 事实)时,剔掉同节点的 insufficient_data
    # 事实——"某源答不了/没查到"是**没来作证**,不是反证,留着会诱导 RCA 把已确立节点降档
    # (实测:dblg 声明"本源无法提供"+ config 实测确立,模型写 blocked_at="dblg insufficient"
    # 赖在疑似档不肯升确证)。ok+不成立(真冲突)与 manual 照留;未确立节点的 insufficient
    # 原样保留(那是 undetermined/疑似 的依据)。要件覆盖是否齐全仍由 RCA 按判据原文语义判。
    if any((e.get("fact") or {}).get("status") == "ok"
           and (e.get("fact") or {}).get("predicate_satisfied") is True for e in ent):
        ent = [e for e in ent if (e.get("fact") or {}).get("status") != "insufficient_data"]
    return ent


def _node_record(n: dict, ent: list, nodes: dict, facts: dict) -> dict:
    """一个保留节点在 pruned_nodes 里的记录:名称/父/层级、英文名、未覆盖子节点或叶子标记、条件、免检、精简后的事实。"""
    rec = {"name": n.get("name"), "parent": n.get("parent"), "level": n.get("level")}
    if n.get("name_en"):                                        # 英文名(树里有才带):候选清单与 rca.json 的英文说明用
        rec["name_en"] = n["name_en"]
    ch = n.get("children") or []
    if ch:                                                      # 非叶子:RCA 据此知道"这不是根因、下面还有更深的因"
        unc = [c for c in ch if c in nodes and not nodes[c].get("pass_through")
               and c not in facts and nodes[c].get("condition")]
        if unc:                                                 # 带条件却没人绑的子节点 = 还没查到的叶子因
            rec["uncovered_children"] = unc
    else:
        rec["is_leaf"] = True
    for k in ("condition", "combine"):
        if n.get(k):                                            # 稀疏:有才写
            rec[k] = n[k]
    if n.get("pass_through"):
        rec["pass_through"] = True
    if ent:
        rec["facts"] = [_slim_fact(e) for e in ent]
    return rec


def _unmapped_facts(facts: dict, nodes: dict) -> tuple:
    """挂不上节点的事实:返回 (孤儿 node_id 列表, 没填 node_id 与孤儿的事实精简后合并的列表)。"""
    orphans = sorted(k for k in facts if k != "__no_node_id__" and k not in nodes)
    unmapped = list(facts.get("__no_node_id__", []))
    for oid in orphans:
        unmapped.extend(facts[oid])
    unmapped = [_slim_fact(e) for e in unmapped]   # 孤儿事实也精简(同 pruned_nodes):去整条 condition_text、截 mapping_reason
    return orphans, unmapped


def _column_claims(facts: dict) -> tuple:
    """按分类列(C/G 的 ok 事实)汇总:各列被绑定认领的取值、取值分布 top、匹配模式、总行数。"""
    col_claims, col_top, col_mode, col_total = defaultdict(set), defaultdict(dict), {}, defaultdict(int)
    for ent_list in facts.values():
        for e in ent_list:
            f = e.get("fact") or {}
            if str(e.get("class") or f.get("class") or "").upper() not in ("C", "G"):
                continue
            if f.get("filter") is not None:              # 带行级 filter 的事实(EAV 窄表):同列多绑定各看不同行
                continue                                 # 子集,取值分布不可跨绑定合并——不参与按列的覆盖对账
            col = f.get("resolved_column")
            if not col or f.get("targets") is None or f.get("status") != "ok":
                continue
            col_claims[col].update(str(t).strip().lower() for t in (f.get("targets") or []))
            col_mode[col] = f.get("match", "contains")
            col_total[col] = max(col_total[col], f.get("total") or 0)
            for val, cnt in (f.get("top") or []):
                col_top[col][str(val)] = max(col_top[col].get(str(val), 0), cnt)
    return col_claims, col_top, col_mode, col_total


def _uncovered_values(facts: dict) -> dict:
    """覆盖度:分类列里出现、却没被任何绑定 targets 认领的取值 + 记录数(每列最多 8 个)。"""
    # 覆盖度:分类列(C/G)里出现、却没被任何绑定 targets 认领的取值 + 记录数 → 显式提供给 RCA,
    # 这样即便 bind 时漏绑/忽略告警,RCA 也拿到"这 N 条没着落",可归到上面保留的"其他"类节点、不致漏诊。
    # 去噪:跳过占比≥50% 的取值(多半是该列"正常/健康"值,如 Fault/Normal 的 Normal),
    # 但**不**按"认领值个数"去噪——只绑了 1 个值却漏了一堆失败码,正是最该报的(别误伤)。
    col_claims, col_top, col_mode, col_total = _column_claims(facts)
    uncovered_values = {}
    for col, claims in col_claims.items():
        mode = col_mode.get(col, "contains")
        tot = col_total[col] or sum(col_top[col].values())
        miss = []
        for val, cnt in sorted(col_top[col].items(), key=lambda kv: -kv[1]):
            v = val.strip().lower()
            if tot and cnt >= 0.5 * tot:                 # 占比畸高 → 该列"正常/健康"值(如 Normal),跳过免噪
                continue
            covered = (any(t in v for t in claims) if mode == "contains" else v in claims)
            if not covered and v:
                miss.append({"value": val, "count": cnt})
        if miss:
            uncovered_values[col] = miss[:8]
    return uncovered_values


def prune(index: dict, results: dict) -> dict:
    """index + 各源事实 → 剪枝后的局部子树 + 覆盖报告。精简:outline(导航)+ 精简后的事实。"""
    nodes = index.get("nodes", {})
    alias = _alias_table(nodes)
    facts = _facts_by_node(results, alias)
    keep = _kept_node_ids(index, nodes, facts)

    pruned, outline = {}, {}
    for nid in keep:
        n = nodes[nid]
        ent = _drop_insufficient_if_established(facts.get(nid, []))
        pruned[nid] = _node_record(n, ent, nodes, facts)
        outline[nid] = _coarse(ent, n.get("pass_through"))          # outline 只存状态(name/parent 去 pruned_nodes 取)

    # 未覆盖清单只留 id+name(给人/RCA 当"没查到的分支"线索):条件文本无任何逻辑消费,
    # 越稀疏这张表越长,带 60 字条件会把 payload 顶大 → 去掉(要看细节有每个保留节点的 uncovered_children)。
    uncovered = [{"id": nid, "name": n.get("name")}
                 for nid, n in nodes.items() if not n.get("pass_through") and nid not in facts]
    orphans, unmapped = _unmapped_facts(facts, nodes)
    uncovered_values = _uncovered_values(facts)

    return {"scenario": index.get("scenario"), "root": index.get("root"),
            "outline": outline, "pruned_nodes": pruned, "unmapped": unmapped,
            "uncovered_values": uncovered_values,
            "coverage": {"total_nodes": len(nodes), "kept_nodes": len(pruned),
                         "uncovered_condition_nodes": uncovered,
                         "orphan_node_ids": orphans,
                         "facts_without_node_id": len(facts.get("__no_node_id__", []))}}


def compose_rca_payload(index: dict, results: dict, md_text: str = "") -> dict:
    """剪枝 + 组装 RCA 输入 + 应用"零损失下界" —— **唯一出口**:任何调用方(runner/benchmark/
    单独测压缩的脚本)都从这里拿到一致结果,floor 想漏都漏不掉。

    返回 {payload, mode, note, before, after, eff_after, saved, floored, kept, total_nodes, coverage}:
    - mode="pruned"            用局部子树(确实比完整树小);
    - mode="fallback_floor"    局部子树 ≥ 完整树 → 回退完整树(小/密的树,省 0%、绝不倒贴);
    - mode="fallback_coverage" node_id 覆盖 <50% → 回退完整树(剪枝不可信)。
    `payload` 即最终该发给 RCA 的对象;`note` 是给终端打印的中文说明。
    """
    pr = prune(index, results)
    cov = pr["coverage"]
    cand = {"fault_tree": {"scenario": pr["scenario"], "root": pr["root"],
                           "outline_legend": OUTLINE_LEGEND,
                           "outline": pr["outline"], "pruned_nodes": pr["pruned_nodes"]},
            "unmapped_facts": pr["unmapped"], "tree_issues": cov,
            # 判别列里没被绑定认领的取值 —— prune() 已算好,必须随压缩形态一起送给 RCA
            # (fault-tree-rca 的规则据此把漏绑记录归到"非典型/其他"节点报出、防漏诊);
            # 键恒在(空则空 dict),别再在这里静默丢掉。
            "uncovered_values": pr["uncovered_values"]}
    md_payload = {"fault_tree_md": md_text, "indicator_results": results}
    before = len(json.dumps(md_payload, ensure_ascii=False))
    after = len(json.dumps(cand, ensure_ascii=False))
    total = sum(len((r or {}).get("conditions", [])) for r in (results or {}).values())
    mapped = total - cov.get("facts_without_node_id", 0)
    base = {"before": before, "after": after, "coverage": cov,
            "kept": cov["kept_nodes"], "total_nodes": cov["total_nodes"]}

    if total > 0 and mapped < 0.5 * total:                       # 覆盖太低 → 剪枝不可信,回退完整树
        return {**base, "payload": md_payload, "mode": "fallback_coverage", "eff_after": before,
                "saved": 0.0, "floored": True,
                "note": f"[info] node_id 覆盖不足(mapped {mapped}/{total}),回退完整子树。"}
    if md_text and before and after >= before:                  # 零损失下界:剪枝反而更大 → 回退完整树
        return {**base, "payload": md_payload, "mode": "fallback_floor", "eff_after": before,
                "saved": 0.0, "floored": True,
                "note": (f"[info] 局部子树(~{after})≥完整树(~{before}),零损失下界生效:"
                         f"回退完整子树、压缩 0%(此树小/密,本就无可剪)。")}
    saved = 100 * (1 - after / before) if before else 0.0       # 真省了 → 用局部子树
    return {**base, "payload": cand, "mode": "pruned", "eff_after": after,
            "saved": saved, "floored": False,
            "note": (f"已用局部子树:保留 {cov['kept_nodes']}/{cov['total_nodes']} 节点;"
                     f"上下文 压缩前(完整树+全量事实)~{before} → 压缩后 ~{after} 字符 (省 {saved:.0f}%);"
                     f"未覆盖 {len(cov['uncovered_condition_nodes'])}、孤儿 {len(cov['orphan_node_ids'])}。")}


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="tree_index + 事实 → 局部子树 + 覆盖报告")
    ap.add_argument("--index", required=True)
    ap.add_argument("--results", action="append", default=[], help="name=path(可多次)")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--md", help="该场景子树 md;给了就按 runner 同款'零损失下界'报压缩率(单独测压缩用,负数会被夹到0)")
    args = ap.parse_args()
    results = {}
    for item in args.results:
        if "=" not in item:
            print(f"--results 应为 name=path: {item}", file=sys.stderr)
            sys.exit(2)
        k, v = item.split("=", 1)
        results[k.strip()] = _load(v.strip())
    index = _load(args.index)
    out = prune(index, results)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    cov = out["coverage"]
    print(f"[tree_prune] 保留 {cov['kept_nodes']}/{cov['total_nodes']} 节点;"
          f"未覆盖条件节点 {len(cov['uncovered_condition_nodes'])};孤儿 node_id {len(cov['orphan_node_ids'])} → {args.output}")
    if args.md:  # 单独测压缩:走与 runner 完全相同的 compose+floor,得到"实际发送"的压缩率(永不为负)
        md_text = Path(args.md).read_text(encoding="utf-8") if Path(args.md).exists() else ""
        comp = compose_rca_payload(index, results, md_text)
        print(f"[compress] {comp['note']}")
        print(f"[compress] 实际节省 {comp['saved']:.0f}% (mode={comp['mode']}, "
              f"压缩前~{comp['before']} → 实际发送~{comp['eff_after']})")


if __name__ == "__main__":
    main()
