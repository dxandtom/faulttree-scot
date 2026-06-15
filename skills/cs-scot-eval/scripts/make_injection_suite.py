#!/usr/bin/env python3
"""合成故障注入套件生成器 —— 造"自带标准答案"的故障树+数据,用于测诊断准确率(ground truth)。

每个场景 = 一棵小故障树(埋了一个已知根因)+ 让该根因路径成立、兄弟支不成立的数据
         + 金标准绑定(gold.json,Mode A 用,跳过模型绑定、隔离测 RCA)+ truth.json(正确答案)
         + reference_rca.json(该场景的"满分参考答案",供 scorer 自测与人对照,不是评分输入)。

覆盖 5 类情形(确证/疑似/排除/诚实数据不足/抗假阳)× A/C/D/E/H 内核。
写到 ../suite/<场景名>/ 下,确定性、可反复重生成。

用法: python make_injection_suite.py [--out <目录,默认 ../suite>]
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent / "suite"


def N(name, parent, level, *, condition=None, combine=None, children=None, pass_through=False):
    rec = {"name": name, "parent": parent, "level": level, "children": children or []}
    if condition:
        rec["condition"] = condition
    if combine:
        rec["combine"] = combine
    if pass_through:
        rec["pass_through"] = True
    return rec


# ───────────────────────── 6 个场景规格(显式、可读、可核对) ─────────────────────────
SCENARIOS = [
    # 1) 确证 · A 阈值:RRC 支闭合到叶子,QoS 支不成立
    {
        "name": "01_confirmed_threshold", "category": "confirmed", "kernels": ["A"],
        "nodes": {
            "n1": N("接入失败", None, 2, pass_through=True, children=["n2", "n3"]),
            "n2": N("RRC建立失败", "n1", 3, condition="RRC建立成功率<100", children=["n4"]),
            "n3": N("QoS建立失败", "n1", 3, condition="QoS建立成功率<100", children=["n5"]),
            "n4": N("RRC可用资源不足", "n2", 4, condition="RRC可用资源率<100"),
            "n5": N("QoS鉴权失败", "n3", 4, condition="QoS鉴权成功率<100"),
        },
        "bindings": [
            {"id": "b2", "node_id": "n2", "source": "kpi", "class": "A", "column": "rrc_succ_rate",
             "op": "<", "threshold": 100, "scope": "aggregate", "agg": "mean",
             "condition_text": "RRC建立成功率<100", "mapping_reason": "同流程率重述"},
            {"id": "b3", "node_id": "n3", "source": "kpi", "class": "A", "column": "qos_succ_rate",
             "op": "<", "threshold": 100, "scope": "aggregate", "agg": "mean",
             "condition_text": "QoS建立成功率<100", "mapping_reason": "同流程率重述"},
            {"id": "b4", "node_id": "n4", "source": "kpi", "class": "A", "column": "rrc_res_avail",
             "op": "<", "threshold": 100, "scope": "aggregate", "agg": "mean",
             "condition_text": "RRC可用资源率<100", "mapping_reason": "资源率阈值"},
            {"id": "b5", "node_id": "n5", "source": "kpi", "class": "A", "column": "qos_auth_rate",
             "op": "<", "threshold": 100, "scope": "aggregate", "agg": "mean",
             "condition_text": "QoS鉴权成功率<100", "mapping_reason": "鉴权率阈值"},
        ],
        "data": {"kpi": {"header": ["rrc_succ_rate", "qos_succ_rate", "rrc_res_avail", "qos_auth_rate"],
                         "rows": [[95, 100, 80, 100], [96, 100, 82, 100]]}},
        "truth": {"root_cause_node_id": "n4", "root_cause_name": "RRC可用资源不足",
                  "tier": "confirmed", "must_not_report": ["n5", "n3"]},
    },
    # 2) 确证 · D 组合(C 且 A):弱覆盖叶子(错误码 且 RSRP 低)都成立
    {
        "name": "02_confirmed_combo", "category": "confirmed", "kernels": ["D", "C", "A"],
        "nodes": {
            "n1": N("接入失败", None, 2, pass_through=True, children=["n2", "n3"]),
            "n2": N("弱覆盖致接入失败", "n1", 3, condition="DCErrType=RNL_Unspecified 且 RSRP<-120", combine="AND"),
            "n3": N("其它原因", "n1", 3, condition="DCErrType=OTHER"),
        },
        "bindings": [
            {"id": "b2", "node_id": "n2", "source": "chr", "class": "D", "combine": "AND",
             "condition_text": "DCErrType=RNL_Unspecified 且 RSRP<-120", "mapping_reason": "错误码且弱场,组合",
             "sub": [{"class": "C", "source": "chr", "column": "DCErrType", "equals": ["RNL_Unspecified"], "require": 1},
                     {"class": "A", "source": "chr", "column": "RSRP", "op": "<", "threshold": -120,
                      "scope": "count_rows", "require": 1}]},
            {"id": "b3", "node_id": "n3", "source": "chr", "class": "C", "column": "DCErrType",
             "equals": ["OTHER"], "require": 1, "condition_text": "DCErrType=OTHER", "mapping_reason": "错误码匹配"},
        ],
        "data": {"chr": {"header": ["DCErrType", "RSRP"],
                         "rows": [["RNL_Unspecified", -130], ["RNL_Unspecified", -125], ["NORMAL", -90]]}},
        "truth": {"root_cause_node_id": "n2", "root_cause_name": "弱覆盖致接入失败",
                  "tier": "confirmed", "must_not_report": ["n3"]},
    },
    # 3) 疑似 · 中间层成立、叶子数据不足(不许升成确证)
    {
        "name": "03_probable_leaf_insufficient", "category": "probable", "kernels": ["A"],
        "nodes": {
            "n1": N("传输故障", None, 2, pass_through=True, children=["n2"]),
            "n2": N("承载网丢包", "n1", 3, condition="传输丢包率>1", children=["n4"]),
            "n4": N("光模块劣化", "n2", 4, condition="光功率<-20"),
        },
        "bindings": [
            {"id": "b2", "node_id": "n2", "source": "kpi", "class": "A", "column": "pkt_loss_rate",
             "op": ">", "threshold": 1, "scope": "count_rows", "require": 1,
             "condition_text": "传输丢包率>1", "mapping_reason": "丢包率阈值"},
            {"id": "b4", "node_id": "n4", "source": "kpi", "class": "A", "column": "optical_power",
             "op": "<", "threshold": -20, "scope": "count_rows", "require": 1,
             "condition_text": "光功率<-20", "mapping_reason": "本源无光功率列→数据不足"},
        ],
        "data": {"kpi": {"header": ["pkt_loss_rate"], "rows": [[5], [3]]}},   # 无 optical_power 列 → n4 数据不足
        "truth": {"root_cause_node_id": "n2", "root_cause_name": "承载网丢包",
                  "tier": "probable", "must_not_report": []},
    },
    # 4) 诚实 · 信令源缺失,只能数据不足(绝不伪造成立)
    {
        "name": "04_honest_insufficient", "category": "honest_insufficient", "kernels": ["H"],
        "nodes": {
            "n1": N("接入失败", None, 2, pass_through=True, children=["n2"]),
            "n2": N("安全模式失败", "n1", 3, condition="会话缺 SecurityModeComplete"),
        },
        "bindings": [
            {"id": "b2", "node_id": "n2", "source": "sig", "class": "H", "key": "CallID",
             "msg_column": "MessageType", "lacks": ["SecurityModeComplete"],
             "condition_text": "会话缺 SecurityModeComplete", "mapping_reason": "信令缺失判定"},
        ],
        "data": {"sig": {"header": ["CallID"], "rows": [[1], [2]]}},   # 无 MessageType 列 → H 数据不足
        "truth": {"root_cause_node_id": None, "root_cause_name": None,
                  "tier": "undetermined", "must_not_report": ["n2"]},
    },
    # 5) 排除 · E 占比条件不成立(终端分散)→ 无根因
    {
        "name": "05_excluded_proportion", "category": "excluded", "kernels": ["E"],
        "nodes": {
            "n1": N("质差", None, 2, pass_through=True, children=["n2"]),
            "n2": N("TOP终端集中", "n1", 3, condition="TOP终端占比>0.5"),
        },
        "bindings": [
            {"id": "b2", "node_id": "n2", "source": "chr", "class": "E", "group_by": "TerminalType",
             "op": ">", "threshold": 0.5, "condition_text": "TOP终端占比>0.5", "mapping_reason": "占比阈值"},
        ],
        "data": {"chr": {"header": ["TerminalType"],
                         "rows": [["A"], ["B"], ["C"], ["D"], ["E"]]}},   # top1 占比 0.2 < 0.5 → 不成立
        "truth": {"root_cause_node_id": None, "root_cause_name": None,
                  "tier": "excluded", "must_not_report": ["n2"]},
    },
    # 6) 抗假阳 · 真因覆盖支闭合;干扰支叶子事实成立但父节点断路 → 不许误报干扰叶子
    {
        "name": "06_false_positive_resist", "category": "false_positive_resist", "kernels": ["A"],
        "nodes": {
            "n1": N("接入质差", None, 2, pass_through=True, children=["n2", "n3"]),
            "n2": N("覆盖问题", "n1", 3, condition="RSRP<-110", children=["n4"]),
            "n3": N("干扰问题", "n1", 3, condition="干扰电平>-100", children=["n5"]),
            "n4": N("弱覆盖", "n2", 4, condition="RSRP<-120"),
            "n5": N("外部干扰", "n3", 4, condition="SINR<0"),
        },
        "bindings": [
            {"id": "b2", "node_id": "n2", "source": "kpi", "class": "A", "column": "rsrp",
             "op": "<", "threshold": -110, "scope": "count_rows", "require": 1,
             "condition_text": "RSRP<-110", "mapping_reason": "弱场阈值"},
            {"id": "b3", "node_id": "n3", "source": "kpi", "class": "A", "column": "interference",
             "op": ">", "threshold": -100, "scope": "count_rows", "require": 1,
             "condition_text": "干扰电平>-100", "mapping_reason": "干扰阈值"},
            {"id": "b4", "node_id": "n4", "source": "kpi", "class": "A", "column": "rsrp",
             "op": "<", "threshold": -120, "scope": "count_rows", "require": 1,
             "condition_text": "RSRP<-120", "mapping_reason": "强弱场阈值"},
            {"id": "b5", "node_id": "n5", "source": "kpi", "class": "A", "column": "sinr",
             "op": "<", "threshold": 0, "scope": "count_rows", "require": 1,
             "condition_text": "SINR<0", "mapping_reason": "干扰致 SINR 负"},
        ],
        # rsrp 极低(n2/n4 成立);interference=-120 不>-100(n3 断路);sinr=-5<0(n5 事实成立但路径断)
        "data": {"kpi": {"header": ["rsrp", "interference", "sinr"],
                         "rows": [[-130, -120, -5], [-128, -121, -3]]}},
        "truth": {"root_cause_node_id": "n4", "root_cause_name": "弱覆盖",
                  "tier": "confirmed", "must_not_report": ["n5", "n3"]},
    },
]


def build_index(scn):
    nodes = {}
    for nid, n in scn["nodes"].items():
        rec = {"name": n["name"], "code": n["name"], "parent": n["parent"],
               "children": n["children"], "level": n["level"]}
        for k in ("condition", "combine", "pass_through"):
            if k in n:
                rec[k] = n[k]
        nodes[nid] = rec
    return {"scenario": scn["name"], "root": "n1", "nodes": nodes}


def build_md(scn):
    lines = [f"# {scn['name']}", ""]
    def walk(nid, depth):
        n = scn["nodes"][nid]
        cond = f" —— {n['condition']}" if n.get("condition") else ("（无条件进入）" if n.get("pass_through") else "")
        lines.append("  " * depth + f"- {n['name']}{cond}")
        for c in n["children"]:
            walk(c, depth + 1)
    walk("n1", 0)
    return "\n".join(lines) + "\n"


def path_to_root(nodes, nid):
    p = [nid]
    while nodes[p[-1]].get("parent"):
        p.append(nodes[p[-1]]["parent"])
    return list(reversed(p))   # 根→叶


def build_reference_rca(scn):
    """该场景的"满分参考答案"——供 scorer 自测 + 给人对照(不是评分输入,评分输入是模型现跑的 rca.json)。"""
    nodes = scn["nodes"]
    t = scn["truth"]
    out = {"rca_result": [], "probable_causes": [], "undetermined": [], "tree_issues": {}}
    if t["tier"] == "confirmed":
        path = path_to_root(nodes, t["root_cause_node_id"])
        ep = [{"node": nodes[p]["name"], "node_id": p, "condition": nodes[p].get("condition", "无条件进入"),
               "actual": "满足", "judgment": "成立"} for p in path if not nodes[p].get("pass_through")]
        out["rca_result"] = [{"Root_cause": t["root_cause_name"],
                              "Fault_tree_path": [nodes[p]["name"] for p in path],
                              "evidence_path": ep}]
    elif t["tier"] == "probable":
        path = path_to_root(nodes, t["root_cause_node_id"])
        leaf = [c for c in nodes if nodes[c].get("parent") == t["root_cause_node_id"]]
        ep = [{"node": nodes[p]["name"], "node_id": p, "condition": nodes[p].get("condition", ""),
               "actual": "满足", "judgment": "成立"} for p in path if not nodes[p].get("pass_through")]
        out["probable_causes"] = [{"Root_cause": t["root_cause_name"],
                                   "blocked_at": (nodes[leaf[0]]["name"] if leaf else "叶子因"),
                                   "caveats": "叶子因数据不足,已定位到该层、叶子因待定",
                                   "support": "中间层条件成立", "evidence_path": ep}]
    else:  # excluded / honest_insufficient → 无确证根因(诚实空)
        out["undetermined"] = [{"note": "数据不足/条件不成立,未定位到根因"}]
    return out


def main():
    ap = argparse.ArgumentParser(description="生成合成故障注入套件")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    out = Path(args.out)
    for scn in SCENARIOS:
        d = out / scn["name"]
        (d / "tree").mkdir(parents=True, exist_ok=True)
        (d / "data").mkdir(parents=True, exist_ok=True)
        idx = build_index(scn)
        (d / "tree" / f"{scn['name']}.tree_index.json").write_text(
            json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "tree" / f"{scn['name']}.md").write_text(build_md(scn), encoding="utf-8")
        for src, tbl in scn["data"].items():
            with (d / "data" / f"{src}.csv").open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(tbl["header"])
                w.writerows(tbl["rows"])
        (d / "gold.json").write_text(json.dumps(
            {"scenario": scn["name"], "category": scn["category"], "kernels": scn["kernels"],
             "bindings": scn["bindings"], "truth": scn["truth"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "reference_rca.json").write_text(json.dumps(
            build_reference_rca(scn), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  生成 {scn['name']:<32} [{scn['category']}] 内核 {scn['kernels']}")
    print(f"\n共 {len(SCENARIOS)} 个场景 → {out}")


if __name__ == "__main__":
    main()
