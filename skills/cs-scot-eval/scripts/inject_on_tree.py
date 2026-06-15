#!/usr/bin/env python3
"""真实大树故障注入 —— 在【真实拓扑】上合成事实埋根因,测 RCA 在大树上的导航/推理准确率。

为什么不用 toy 树:5 个节点人一眼看穿,证明不了"大树会不会乱"。这里直接用脱敏后的**真实 178 节点树**,
把根因埋在深处,周围撒"事实成立但路径断了"的诱饵 + 一堆不成立/数据不足的噪声 —— 模型必须在几十上百
节点里**追出唯一闭合的那条路径**,这才是 1231819×182736 那种"算不出就错"的考题。

做法(条件已脱敏为 null,所以这里测的是【导航/结构推理】,不是条件判数据):
  按 spec 给节点合成事实(路径节点成立 / 诱饵叶子成立但父断 / 噪声不成立或数据不足)→ prune 成局部子树
  → 写 suite_real/<场景>/result/rca_input.json + gold.json(truth) + reference_rca.json。
之后:模型逐场景做 RCA 写 rca.json → score_accuracy.py 打分。

用法: python inject_on_tree.py [--tree <real.tree_index.json>] [--out <suite_real>]
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TREE = HERE.parent / "real_trees" / "SA接入失败.tree_index.json"
DEFAULT_OUT = HERE.parent / "suite_real"
SOURCES = ["kpi", "chr", "sig"]

# 注入规格(引用真实 node_id;深度/诱饵数/类别各异)。target=埋的根因叶子;decoys=诱饵叶子(成立但路径会被断)。
SPECS = [
    {"name": "R01_confirmed_L6_deep", "category": "confirmed", "target": "n6",
     "decoys": ["n121"], "noise_fail": ["n12", "n51"], "noise_insuf": ["n43", "n164"]},
    {"name": "R02_confirmed_L6_other", "category": "confirmed", "target": "n121",
     "decoys": ["n6", "n72"], "noise_fail": ["n85", "n175"], "noise_insuf": ["n50"]},
    {"name": "R03_confirmed_L4_mid", "category": "confirmed", "target": "n164",
     "decoys": ["n50"], "noise_fail": ["n12"], "noise_insuf": ["n39", "n137"]},
    {"name": "R04_confirmed_via_passthrough", "category": "confirmed", "target": "n50",
     "decoys": ["n39"], "noise_fail": ["n51", "n66"], "noise_insuf": ["n6"]},
    {"name": "R05_probable_leaf_insuf", "category": "probable", "target": "n11",
     "decoys": ["n50"], "noise_fail": ["n12"], "noise_insuf": ["n121"]},
    {"name": "R06_false_positive_heavy", "category": "false_positive_resist", "target": "n39",
     "decoys": ["n6", "n121", "n50", "n72"], "noise_fail": ["n85"], "noise_insuf": ["n137"]},
    {"name": "R07_honest_all_insuf", "category": "honest_insufficient", "target": None,
     "decoys": [], "noise_fail": ["n12", "n51"], "noise_insuf": ["n6", "n39", "n121", "n164"]},
    {"name": "R08_confirmed_L5", "category": "confirmed", "target": "n72",
     "decoys": ["n135"], "noise_fail": ["n164"], "noise_insuf": ["n50"]},
    {"name": "R09_excluded_all_fail", "category": "excluded", "target": None,
     "decoys": [], "noise_fail": ["n6", "n39", "n50", "n72", "n164"], "noise_insuf": []},
    {"name": "R10_confirmed_L6_c", "category": "confirmed", "target": "n135",
     "decoys": ["n6", "n164"], "noise_fail": ["n12"], "noise_insuf": ["n39"]},
]


def path_to_root(nodes, nid):
    p = [nid]
    while nodes[p[-1]].get("parent"):
        p.append(nodes[p[-1]]["parent"])
    return list(reversed(p))                       # 根→叶


def nearest_break(nodes, decoy, on_path: set):
    """诱饵叶子到根路径上,**最近的、不在目标路径上、非 pass_through** 的祖先 → 在此断路。"""
    for anc in reversed(path_to_root(nodes, decoy)[:-1]):   # 叶→根,跳过自身
        if anc not in on_path and not nodes[anc].get("pass_through"):
            return anc
    return None


_CMP = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b, "==": lambda a, b: a == b}

# ── 业务"人设"池:真实 5G SA 接入失败域的判据 + 具体事实值。每个 = 一种可判的根因/中间因。
# 让每个节点稳定挂一个人设(按 node_id 定),RCA 要【拿实际值判 condition 成不成立】再导航,
# 不是读 predicate 标志位。hold=值明显满足条件、fail=明显不满足、insuf=该源缺失。
PERSONAS = [
    {"name": "RRC连接建立成功率低", "cls": "A", "kind": "num", "col": "SA_RRC_ConnSetup_SuccRate(%)",
     "cond": "SA_RRC_ConnSetup_SuccRate(%) < 98", "op": "<", "thr": 98.0, "hold_v": 91.4, "fail_v": 99.7,
     "insuf": "本次无小时粒度KPI该成功率计数器"},
    {"name": "UE上下文建立成功率低", "cls": "A", "kind": "num", "col": "SA_UECntx_Est_SuccRate(%)",
     "cond": "SA_UECntx_Est_SuccRate(%) < 99", "op": "<", "thr": 99.0, "hold_v": 93.8, "fail_v": 99.95,
     "insuf": "本次无UEContext建立成功率列"},
    {"name": "NG接口/AMF信令建立失败", "cls": "A", "kind": "num", "col": "SA_NGSig_Conn_SuccRate(%)",
     "cond": "SA_NGSig_Conn_SuccRate(%) < 99.5", "op": "<", "thr": 99.5, "hold_v": 95.1, "fail_v": 99.99,
     "insuf": "无NG接口信令统计"},
    {"name": "QoS Flow建立成功率低", "cls": "A", "kind": "num", "col": "SA_QoSFlow_Est_SuccRate(%)",
     "cond": "SA_QoSFlow_Est_SuccRate(%) < 99", "op": "<", "thr": 99.0, "hold_v": 90.2, "fail_v": 99.8,
     "insuf": "无QoSFlow建立计数器"},
    {"name": "PRACH随机接入成功率低", "cls": "A", "kind": "num", "col": "RACH_Setup_SuccRate(%)",
     "cond": "RACH_Setup_SuccRate(%) < 95", "op": "<", "thr": 95.0, "hold_v": 78.6, "fail_v": 98.9,
     "insuf": "无RACH话统"},
    {"name": "RRC建立尝试与成功数差>0", "cls": "B", "kind": "num", "col": "N.RRC.ConnReq.Att−N.RRC.ConnReq.Succ",
     "cond": "N.RRC.ConnReq.Att − N.RRC.ConnReq.Succ > 0", "op": ">", "thr": 0, "hold_v": 1240, "fail_v": 0,
     "insuf": "无RRC原始计数器(只有成功率)"},
    {"name": "NAS鉴权失败占比高", "cls": "C", "kind": "propmatch", "col": "ReleaseCause", "min_ratio": 0.2,
     "cond": "CHR ReleaseCause 含 NAS_Authentication_Failure 且占比 ≥ 20%",
     "hold": (286, 1000, [["NAS_Authentication_Failure", 286], ["normal_release", 671]]),
     "fail": (7, 1000, [["normal_release", 962], ["NAS_Authentication_Failure", 7]]),
     "insuf": "本次CHR未携带释放原因字段"},
    {"name": "无线链路失败RNL占比高", "cls": "C", "kind": "propmatch", "col": "DCErrType", "min_ratio": 0.2,
     "cond": "CHR DCErrType=RNL_RadioLinkFailure 占比 ≥ 20%",
     "hold": (343, 1000, [["RNL_RadioLinkFailure", 343], ["NORMAL", 540]]),
     "fail": (11, 1000, [["NORMAL", 980], ["RNL_RadioLinkFailure", 11]]),
     "insuf": "无CHR DCErrType字段"},
    {"name": "传输资源不可用", "cls": "C", "kind": "propmatch", "col": "NgRanCause", "min_ratio": 0.2,
     "cond": "NgRanCause=transport-resource-unavailable 占比 ≥ 20%",
     "hold": (198, 600, [["transport-resource-unavailable", 198], ["normal", 380]]),
     "fail": (3, 600, [["normal", 590], ["transport-resource-unavailable", 3]]),
     "insuf": "无NGAP原因码记录"},
    {"name": "AMF过载拒绝", "cls": "C", "kind": "propmatch", "col": "NgRanCause", "min_ratio": 0.2,
     "cond": "NgRanCause=amf-overload 占比 ≥ 20%",
     "hold": (140, 500, [["amf-overload", 140], ["normal", 330]]),
     "fail": (1, 500, [["normal", 495], ["amf-overload", 1]]), "insuf": "无AMF侧拒绝原因"},
    {"name": "射频单元驻波比异常告警", "cls": "G", "kind": "exist", "col": "AlarmLog",
     "cond": "告警日志存在「ALM-26529 射频单元驻波比异常」",
     "hold": (3, 3, [["ALM-26529 射频单元驻波比异常", 3]]), "fail": (0, 1, []), "insuf": "本次无告警日志源"},
    {"name": "小区不可用告警", "cls": "G", "kind": "exist", "col": "AlarmLog",
     "cond": "告警日志存在「ALM-25888 小区不可用」",
     "hold": (2, 2, [["ALM-25888 小区不可用", 2]]), "fail": (0, 1, []), "insuf": "本次无告警日志源"},
    {"name": "Xn链路故障告警", "cls": "G", "kind": "exist", "col": "AlarmLog",
     "cond": "告警日志存在「ALM-29240 Xn链路故障」",
     "hold": (1, 1, [["ALM-29240 Xn链路故障", 1]]), "fail": (0, 1, []), "insuf": "本次无告警日志源"},
    {"name": "GTP-U路径故障告警", "cls": "G", "kind": "exist", "col": "AlarmLog",
     "cond": "告警日志存在「ALM-25952 GTPU路径故障」",
     "hold": (4, 4, [["ALM-25952 GTPU路径故障", 4]]), "fail": (0, 1, []), "insuf": "本次无告警日志源"},
    {"name": "会话缺SecurityModeComplete", "cls": "H", "kind": "sess", "hold_s": 27,
     "cond": "信令:会话内出现 SecurityModeCommand 但缺 SecurityModeComplete", "insuf": "无信令trace(detailInfo页签)"},
    {"name": "有RRCSetupRequest无RRCSetupComplete", "cls": "H", "kind": "sess", "hold_s": 41,
     "cond": "信令:存在 RRCSetupRequest 但无 RRCSetupComplete", "insuf": "无信令trace(detailInfo页签)"},
    {"name": "TOP1终端型号集中", "cls": "E", "kind": "prop", "col": "TerminalType",
     "cond": "失败会话按 TerminalType 分组,TOP1 占比 > 50%",
     "top1": "BrandX-Model9", "op": ">", "thr": 0.5, "hold_r": 0.72, "fail_r": 0.16, "insuf": "无终端型号字段"},
    {"name": "单小区接入失败占比高", "cls": "E", "kind": "prop", "col": "NRCellID",
     "cond": "失败会话按 NRCellID 分组,TOP1 小区占比 > 60%",
     "top1": "CellID-0x1A3F", "op": ">", "thr": 0.6, "hold_r": 0.81, "fail_r": 0.22, "insuf": "无小区维度CHR"},
    {"name": "弱覆盖采样占比高", "cls": "A", "kind": "num", "col": "弱覆盖采样占比 RSRP<-110(%)",
     "cond": "RSRP<-110 的采样占比 > 15%", "op": ">", "thr": 15.0, "hold_v": 38.4, "fail_v": 2.1,
     "insuf": "无MR/MDT测量,无法判覆盖"},
    {"name": "上行干扰偏高", "cls": "A", "kind": "num", "col": "PUSCH_RSSI(dBm)",
     "cond": "PUSCH_RSSI > -105 dBm(底噪抬升)", "op": ">", "thr": -105.0, "hold_v": -96.5, "fail_v": -117.8,
     "insuf": "无上行干扰话统"},
]


def _persona_map(nodes: dict) -> dict:
    ids = sorted(nodes)
    return {nid: PERSONAS[i % len(PERSONAS)] for i, nid in enumerate(ids)}


def _fact(persona: dict, state: str) -> dict:
    """按人设+状态出"内核同形"的事实。predicate 与判据语义一致:阈值/占比/存在/信令各按各的判。"""
    cls = persona["cls"]
    if state == "insuf":
        return {"class": cls, "status": "insufficient_data", "resolved_column": None, "reason": persona["insuf"]}
    hold = state == "hold"
    k = persona["kind"]
    if k == "num":
        v = persona["hold_v"] if hold else persona["fail_v"]
        return {"class": cls, "status": "ok", "resolved_column": persona["col"], "op": persona["op"],
                "threshold": persona["thr"], "value": v, "predicate_satisfied": _CMP[persona["op"]](v, persona["thr"])}
    if k in ("propmatch", "exist"):
        m, tot, top = persona["hold"] if hold else persona["fail"]
        ratio = round(m / tot, 3) if tot else 0.0
        pred = (ratio >= persona["min_ratio"]) if k == "propmatch" else (m >= 1)
        return {"class": cls, "status": "ok", "resolved_column": persona["col"], "matched": m, "total": tot,
                "ratio": ratio, "top": top, "predicate_satisfied": pred}
    if k == "sess":
        s = persona["hold_s"] if hold else 0
        return {"class": cls, "status": "ok", "resolved_column": "MessageType", "matched_sessions": s,
                "predicate_satisfied": s >= 1}
    r = persona["hold_r"] if hold else persona["fail_r"]   # prop(E)
    return {"class": cls, "status": "ok", "group_by": persona["col"], "top1": persona["top1"],
            "top1_ratio": r, "op": persona["op"], "threshold": persona["thr"],
            "predicate_satisfied": _CMP[persona["op"]](r, persona["thr"])}


def _actual_str(persona: dict) -> str:
    """给 reference 的 evidence_path 写一个像样的"实际值"。"""
    f = _fact(persona, "hold")
    if "value" in f:
        return f"{persona['col']}={f['value']}"
    if "matched_sessions" in f:
        return f"命中会话 {f['matched_sessions']} 个"
    if "top1_ratio" in f:
        return f"TOP1={persona['top1']} 占比 {f['top1_ratio']}"
    return f"{persona['col']} 命中 {f['matched']}/{f['total']}(占比 {f['ratio']})"


def build(spec, tree):
    nodes = tree["nodes"]
    pmap = _persona_map(nodes)                     # 每个节点稳定挂一个真实业务人设
    state = {}                                     # nid -> hold/fail/insuf
    truth = {"root_cause_node_id": None, "root_cause_name": None,
             "tier": "undetermined", "must_not_report": []}

    def set_path_hold(leaf):
        for p in path_to_root(nodes, leaf):
            if not nodes[p].get("pass_through"):
                state[p] = "hold"

    cat = spec["category"]
    if cat in ("confirmed", "false_positive_resist"):
        tgt = spec["target"]
        set_path_hold(tgt)
        state[tgt] = "hold"
        truth.update(root_cause_node_id=tgt, root_cause_name=pmap[tgt]["name"], tier="confirmed")
    elif cat == "probable":
        leaf = spec["target"]
        mid = nodes[leaf]["parent"]
        while mid and nodes[mid].get("pass_through"):
            mid = nodes[mid]["parent"]
        set_path_hold(mid)                         # 中间层闭合
        state[leaf] = "insuf"                       # 叶子数据不足 → 只能疑似
        truth.update(root_cause_node_id=mid, root_cause_name=pmap[mid]["name"], tier="probable")
    else:                                          # honest_insufficient / excluded:无闭合路径
        truth.update(tier=("undetermined" if cat == "honest_insufficient" else "excluded"))

    on_path = {k for k, v in state.items() if v == "hold"}
    # 诱饵:叶子成立,但其路径在最近的非路径祖先处断开 → 不许被报成根因
    for d in spec.get("decoys", []):
        state[d] = "hold"
        brk = nearest_break(nodes, d, on_path)
        if brk:
            state[brk] = "fail"
            truth["must_not_report"].append(brk)
        truth["must_not_report"].append(d)
    for d in spec.get("noise_fail", []):
        state.setdefault(d, "fail")
    for d in spec.get("noise_insuf", []):
        state.setdefault(d, "insuf")
    if cat in ("honest_insufficient", "excluded"):
        truth["must_not_report"] = [k for k, v in state.items() if v in ("hold", "insuf")]

    # 工作副本:给有事实的节点叠上真实业务人设(名字+判据+事实)。脱敏后原 condition 为 null,这里补真判据。
    widx = copy.deepcopy(tree)
    results = {}
    for i, (nid, st) in enumerate(sorted(state.items())):
        p = pmap[nid]
        widx["nodes"][nid]["name"] = p["name"]
        widx["nodes"][nid]["condition"] = p["cond"]
        src = SOURCES[i % len(SOURCES)]
        results.setdefault(src, {"indicator": src, "source": src, "conditions": [], "summary": {}})
        results[src]["conditions"].append({
            "id": f"{nid}_b", "node_id": nid, "node": p["name"],
            "condition_text": p["cond"], "class": p["cls"],
            "mapping_reason": "合成注入(真实业务判据)", "fact": _fact(p, st)})
    for r in results.values():
        cs = r["conditions"]
        r["summary"] = {"total": len(cs), "ok": sum(c["fact"]["status"] == "ok" for c in cs),
                        "insufficient_data": sum(c["fact"]["status"] == "insufficient_data" for c in cs),
                        "manual": 0}
    return widx, results, truth


def build_reference(truth, nodes):
    pmap = _persona_map(nodes)
    out = {"rca_result": [], "probable_causes": [], "undetermined": [], "tree_issues": {}}
    nid = truth["root_cause_node_id"]

    def ep_for(path):
        return [{"node": pmap[p]["name"], "node_id": p, "condition": pmap[p]["cond"],
                 "actual": _actual_str(pmap[p]), "judgment": "成立"}
                for p in path if not nodes[p].get("pass_through")]

    if truth["tier"] == "confirmed":
        path = path_to_root(nodes, nid)
        out["rca_result"] = [{"Root_cause": truth["root_cause_name"],
                              "Fault_tree_path": [pmap[p]["name"] for p in path if not nodes[p].get("pass_through")],
                              "evidence_path": ep_for(path)}]
    elif truth["tier"] == "probable":
        path = path_to_root(nodes, nid)
        out["probable_causes"] = [{"Root_cause": truth["root_cause_name"], "blocked_at": "叶子因(数据不足)",
                                   "caveats": "叶子因数据不足,已定位到该层、叶子待定",
                                   "support": "中间层判据成立", "evidence_path": ep_for(path)}]
    else:
        out["undetermined"] = [{"note": "无闭合成立路径,数据不足/条件不成立,未定位到根因"}]
    return out


def main():
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="真实大树故障注入")
    ap.add_argument("--tree", default=str(DEFAULT_TREE))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    sys.path.insert(0, str(HERE.parent.parent / "cs-scot-execute" / "scripts"))
    from tree_prune import prune  # noqa: E402

    tree = json.loads(Path(args.tree).read_text(encoding="utf-8"))
    nodes = tree["nodes"]
    out = Path(args.out)
    ans = out / "_answers"
    ans.mkdir(parents=True, exist_ok=True)
    print(f"真实树:{tree['scenario']} · {len(nodes)} 节点 · 注入 {len(SPECS)} 个场景")
    for spec in SPECS:
        widx, results, truth = build(spec, tree)
        pr = prune(widx, results)
        payload = {"fault_tree": {"scenario": pr["scenario"], "root": pr["root"],
                                  "outline": pr["outline"], "pruned_nodes": pr["pruned_nodes"]},
                   "unmapped_facts": pr["unmapped"], "tree_issues": pr["coverage"]}
        d = out / spec["name"]
        d.mkdir(parents=True, exist_ok=True)
        # 模型只读这个(冻结的考题);答案另放 _answers/,做 RCA 时别读。
        (d / "rca_input.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (ans / f"{spec['name']}.json").write_text(json.dumps(
            {"scenario": spec["name"], "category": spec["category"], "truth": truth,
             "reference_rca": build_reference(truth, nodes)}, ensure_ascii=False, indent=2), encoding="utf-8")
        tgt = truth["root_cause_node_id"] or "—(无根因)"
        print(f"  {spec['name']:<32}[{spec['category']:<22}] 埋因 {tgt:<5} "
              f"剪枝保留 {pr['coverage']['kept_nodes']}/{pr['coverage']['total_nodes']} "
              f"诱饵/禁报 {len(truth['must_not_report'])}")
    (out / "README.md").write_text(
        "# 冻结的诊断准确率考题(真实大树注入)\n\n"
        "这些场景由可信侧预先生成并提交,**本地不要重新生成**(避免出题方=答题方、自找便宜路径)。\n\n"
        "- `<场景>/rca_input.json` —— **答题模型只读这个**(局部子树+事实)。\n"
        "- `<场景>/rca.json` —— 答题模型把 RCA 结果写这里(本地产物,不提交)。\n"
        "- `_answers/<场景>.json` —— **答案区(truth+满分参考),做 RCA 时别读**,只给打分器用。\n\n"
        "跑分:`python ../scripts/score_accuracy.py --suite . -o accuracy_report.txt`\n"
        "自测打分链路:`... score_accuracy.py --suite . --use-reference`(应 100%)。\n", encoding="utf-8")
    print(f"\n→ {out}(已冻结提交;答案在 _answers/)\n下一步:逐场景用 fault-tree-rca 读 <场景>/rca_input.json 写 <场景>/rca.json,再跑 score_accuracy.py --suite suite_real")


if __name__ == "__main__":
    main()
