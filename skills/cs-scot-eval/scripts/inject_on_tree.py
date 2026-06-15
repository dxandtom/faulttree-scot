#!/usr/bin/env python3
"""多树故障注入 —— 在多棵不同大小的真实/合成拓扑上合成事实埋根因,测 RCA 大树推理准确率。

要点:
- **多棵树**(trees/ 下:真实树 + 其子树 + 合成树,~27~320 节点)→ 出"准确率 vs 规模"。
- **不带 predicate_satisfied**:事实只给实际值(成功率/占比/告警数/会话数…)+ 判据带阈值,
  **逼模型自己判 condition 成立/不成立**,而不是读字面布尔。(注:真实生产输入是带 predicate 的,
  这里刻意抽掉做更硬的考。)
- 每棵树**自动**生成 6 类场景(确证深/浅、疑似、抗假阳、诚实、排除),埋因+诱饵+噪声确定性选取。
- 答案(truth + 满分参考 + 埋下的逐节点状态 node_states)放 _answers/,做题时勿读。

用法: python inject_on_tree.py   (先跑 make_trees.py 备好 trees/)
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREES = HERE.parent / "trees"
DEFAULT_OUT = HERE.parent / "suite_real"
SOURCES = ["kpi", "chr", "sig", "alarm", "cfg"]

_CMP = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b, "==": lambda a, b: a == b}

# ── 业务"人设"池(真实 5G SA 接入失败域):name=因 / cond=判据(带阈值)/ 事实值。
# 每节点稳定挂一个;hold=值明显满足、fail=明显不满足、insuf=该源缺失。模型须拿值判 cond,再导航。
PERSONAS = [
    # —— 成功率/比率阈值类(num: 值 op 阈值)——
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
    {"name": "Msg2响应比例低", "cls": "A", "kind": "num", "col": "RAR_Resp_Rate(%)",
     "cond": "RAR_Resp_Rate(%) < 90", "op": "<", "thr": 90.0, "hold_v": 61.2, "fail_v": 97.4, "insuf": "无MAC层话统"},
    {"name": "PDCP上行丢包率高", "cls": "A", "kind": "num", "col": "PDCP_UL_LossRate(%)",
     "cond": "PDCP_UL_LossRate(%) > 1", "op": ">", "thr": 1.0, "hold_v": 6.7, "fail_v": 0.08, "insuf": "无PDCP丢包话统"},
    {"name": "RLC重传率高", "cls": "A", "kind": "num", "col": "RLC_Retx_Rate(%)",
     "cond": "RLC_Retx_Rate(%) > 10", "op": ">", "thr": 10.0, "hold_v": 23.5, "fail_v": 2.1, "insuf": "无RLC话统"},
    {"name": "弱覆盖采样占比高", "cls": "A", "kind": "num", "col": "弱覆盖采样占比 RSRP<-110(%)",
     "cond": "RSRP<-110 的采样占比 > 15%", "op": ">", "thr": 15.0, "hold_v": 38.4, "fail_v": 2.1,
     "insuf": "无MR/MDT测量,无法判覆盖"},
    {"name": "上行干扰偏高", "cls": "A", "kind": "num", "col": "PUSCH_RSSI(dBm)",
     "cond": "PUSCH_RSSI > -105 dBm(底噪抬升)", "op": ">", "thr": -105.0, "hold_v": -96.5, "fail_v": -117.8,
     "insuf": "无上行干扰话统"},
    {"name": "SSB SINR低", "cls": "A", "kind": "num", "col": "SSB_SINR(dB)",
     "cond": "SSB_SINR < 0 dB", "op": "<", "thr": 0.0, "hold_v": -4.3, "fail_v": 12.6, "insuf": "无SINR测量"},
    {"name": "切换成功率低", "cls": "A", "kind": "num", "col": "NR_HO_SuccRate(%)",
     "cond": "NR_HO_SuccRate(%) < 95", "op": "<", "thr": 95.0, "hold_v": 80.1, "fail_v": 99.2, "insuf": "无切换话统"},
    # —— 计数器差(B)——
    {"name": "RRC建立尝试与成功数差>0", "cls": "B", "kind": "num", "col": "N.RRC.ConnReq.Att−N.RRC.ConnReq.Succ",
     "cond": "N.RRC.ConnReq.Att − N.RRC.ConnReq.Succ > 0", "op": ">", "thr": 0, "hold_v": 1240, "fail_v": 0,
     "insuf": "无RRC原始计数器(只有成功率)"},
    {"name": "NGSig建立尝试与成功数差>0", "cls": "B", "kind": "num", "col": "N.NGSig.Est.Att−N.NGSig.Est.Succ",
     "cond": "N.NGSig.Est.Att − N.NGSig.Est.Succ > 0", "op": ">", "thr": 0, "hold_v": 588, "fail_v": 0,
     "insuf": "无NG原始计数器"},
    # —— 错误码/原因占比(propmatch: 占比≥阈值)——
    {"name": "NAS鉴权失败占比高", "cls": "C", "kind": "propmatch", "col": "ReleaseCause", "min_ratio": 0.2,
     "cond": "CHR ReleaseCause 含 NAS_Authentication_Failure 且占比 ≥ 20%",
     "hold": (286, 1000, [["NAS_Authentication_Failure", 286], ["normal_release", 671]]),
     "fail": (7, 1000, [["normal_release", 962], ["NAS_Authentication_Failure", 7]]), "insuf": "本次CHR未携带释放原因字段"},
    {"name": "无线链路失败RNL占比高", "cls": "C", "kind": "propmatch", "col": "DCErrType", "min_ratio": 0.2,
     "cond": "CHR DCErrType=RNL_RadioLinkFailure 占比 ≥ 20%",
     "hold": (343, 1000, [["RNL_RadioLinkFailure", 343], ["NORMAL", 540]]),
     "fail": (11, 1000, [["NORMAL", 980], ["RNL_RadioLinkFailure", 11]]), "insuf": "无CHR DCErrType字段"},
    {"name": "传输资源不可用占比高", "cls": "C", "kind": "propmatch", "col": "NgRanCause", "min_ratio": 0.2,
     "cond": "NgRanCause=transport-resource-unavailable 占比 ≥ 20%",
     "hold": (198, 600, [["transport-resource-unavailable", 198], ["normal", 380]]),
     "fail": (3, 600, [["normal", 590], ["transport-resource-unavailable", 3]]), "insuf": "无NGAP原因码记录"},
    {"name": "AMF过载拒绝占比高", "cls": "C", "kind": "propmatch", "col": "NgRanCause", "min_ratio": 0.2,
     "cond": "NgRanCause=amf-overload 占比 ≥ 20%",
     "hold": (140, 500, [["amf-overload", 140], ["normal", 330]]),
     "fail": (1, 500, [["normal", 495], ["amf-overload", 1]]), "insuf": "无AMF侧拒绝原因"},
    {"name": "RRC拒绝(拥塞)占比高", "cls": "C", "kind": "propmatch", "col": "RrcRejectCause", "min_ratio": 0.15,
     "cond": "RRCReject 原因=congestion 占比 ≥ 15%",
     "hold": (220, 800, [["congestion", 220], ["normal", 510]]),
     "fail": (5, 800, [["normal", 770], ["congestion", 5]]), "insuf": "无RRC拒绝原因统计"},
    {"name": "注册失败(PLMN不允许)占比高", "cls": "C", "kind": "propmatch", "col": "RegRejectCause", "min_ratio": 0.2,
     "cond": "Registration Reject=PLMN-not-allowed 占比 ≥ 20%",
     "hold": (132, 400, [["PLMN-not-allowed", 132], ["ok", 240]]),
     "fail": (2, 400, [["ok", 390], ["PLMN-not-allowed", 2]]), "insuf": "无注册结果记录"},
    # —— 告警存在(exist: 命中≥1)——
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
    {"name": "GPS失锁/时钟告警", "cls": "G", "kind": "exist", "col": "AlarmLog",
     "cond": "告警日志存在「ALM-26262 时钟参考源异常」",
     "hold": (2, 2, [["ALM-26262 时钟参考源异常", 2]]), "fail": (0, 1, []), "insuf": "本次无告警日志源"},
    {"name": "传输承载链路告警", "cls": "G", "kind": "exist", "col": "AlarmLog",
     "cond": "告警日志存在「ALM-25885 BBU CPRI接口异常」",
     "hold": (1, 1, [["ALM-25885 BBU CPRI接口异常", 1]]), "fail": (0, 1, []), "insuf": "本次无告警日志源"},
    {"name": "License受限告警", "cls": "G", "kind": "exist", "col": "AlarmLog",
     "cond": "告警日志存在「ALM-26818 License不足」",
     "hold": (1, 1, [["ALM-26818 License不足", 1]]), "fail": (0, 1, []), "insuf": "本次无告警日志源"},
    # —— 信令会话(sess)——
    {"name": "会话缺SecurityModeComplete", "cls": "H", "kind": "sess", "hold_s": 27,
     "cond": "信令:会话内出现 SecurityModeCommand 但缺 SecurityModeComplete", "insuf": "无信令trace(detailInfo页签)"},
    {"name": "有RRCSetupRequest无RRCSetupComplete", "cls": "H", "kind": "sess", "hold_s": 41,
     "cond": "信令:存在 RRCSetupRequest 但无 RRCSetupComplete", "insuf": "无信令trace(detailInfo页签)"},
    {"name": "RegistrationRequest无Accept", "cls": "H", "kind": "sess", "hold_s": 18,
     "cond": "信令:有 RegistrationRequest 但无 RegistrationAccept", "insuf": "无NAS信令trace"},
    {"name": "UECapabilityEnquiry无回复", "cls": "H", "kind": "sess", "hold_s": 12,
     "cond": "信令:UECapabilityEnquiry 后无 UECapabilityInformation", "insuf": "无信令trace"},
    # —— 占比/TopN(prop)——
    {"name": "TOP1终端型号集中", "cls": "E", "kind": "prop", "col": "TerminalType",
     "cond": "失败会话按 TerminalType 分组,TOP1 占比 > 50%",
     "top1": "BrandX-Model9", "op": ">", "thr": 0.5, "hold_r": 0.72, "fail_r": 0.16, "insuf": "无终端型号字段"},
    {"name": "单小区接入失败占比高", "cls": "E", "kind": "prop", "col": "NRCellID",
     "cond": "失败会话按 NRCellID 分组,TOP1 小区占比 > 60%",
     "top1": "CellID-0x1A3F", "op": ">", "thr": 0.6, "hold_r": 0.81, "fail_r": 0.22, "insuf": "无小区维度CHR"},
    {"name": "单频点失败集中", "cls": "E", "kind": "prop", "col": "ARFCN",
     "cond": "失败会话按 ARFCN 分组,TOP1 频点占比 > 50%",
     "top1": "627264", "op": ">", "thr": 0.5, "hold_r": 0.66, "fail_r": 0.19, "insuf": "无频点字段"},
    # —— 配置核查(F→当作 num/exist 类:这里用 exist 形式表示"配置不一致命中")——
    {"name": "TAC配置不一致", "cls": "F", "kind": "exist", "col": "ConfigDiff",
     "cond": "配置核查:小区 TAC 与核心网规划不一致(命中)",
     "hold": (1, 1, [["TAC_mismatch: 计划=10010 实际=10086", 1]]), "fail": (0, 1, []), "insuf": "无配置基线导出,无法核查"},
    {"name": "邻区漏配", "cls": "F", "kind": "exist", "col": "ConfigDiff",
     "cond": "配置核查:存在漏配的同频邻区关系(命中)",
     "hold": (5, 5, [["missing_nbr", 5]]), "fail": (0, 1, []), "insuf": "无邻区配置导出"},
]


def _persona_map(nodes: dict) -> dict:
    ids = sorted(nodes)
    return {nid: PERSONAS[i % len(PERSONAS)] for i, nid in enumerate(ids)}


def _fact(persona: dict, state: str) -> dict:
    """按人设+状态出事实——**不含 predicate_satisfied**(只给实际值,逼模型自己判)。"""
    cls = persona["cls"]
    if state == "insuf":
        return {"class": cls, "status": "insufficient_data", "resolved_column": None, "reason": persona["insuf"]}
    hold = state == "hold"
    k = persona["kind"]
    if k == "num":
        v = persona["hold_v"] if hold else persona["fail_v"]
        return {"class": cls, "status": "ok", "resolved_column": persona["col"],
                "op": persona["op"], "threshold": persona["thr"], "value": v}
    if k in ("propmatch", "exist"):
        m, tot, top = persona["hold"] if hold else persona["fail"]
        f = {"class": cls, "status": "ok", "resolved_column": persona["col"], "matched": m, "total": tot, "top": top}
        if k == "propmatch":
            f["ratio"] = round(m / tot, 3) if tot else 0.0
            f["min_ratio"] = persona["min_ratio"]
        return f
    if k == "sess":
        return {"class": cls, "status": "ok", "resolved_column": "MessageType",
                "matched_sessions": persona["hold_s"] if hold else 0}
    return {"class": cls, "status": "ok", "group_by": persona["col"], "top1": persona["top1"],
            "top1_ratio": persona["hold_r"] if hold else persona["fail_r"], "op": persona["op"], "threshold": persona["thr"]}


def _actual_str(persona: dict) -> str:
    f = _fact(persona, "hold")
    if "value" in f:
        return f"{persona['col']}={f['value']}"
    if "matched_sessions" in f:
        return f"命中会话 {f['matched_sessions']} 个"
    if "top1_ratio" in f:
        return f"TOP1={persona['top1']} 占比 {f['top1_ratio']}"
    return f"{persona['col']} 命中 {f['matched']}/{f['total']}" + (f"(占比 {f['ratio']})" if "ratio" in f else "")


def path_to_root(nodes, nid):
    p = [nid]
    while nodes[p[-1]].get("parent"):
        p.append(nodes[p[-1]]["parent"])
    return list(reversed(p))


def nearest_break(nodes, decoy, on_path):
    for anc in reversed(path_to_root(nodes, decoy)[:-1]):
        if anc not in on_path and not nodes[anc].get("pass_through"):
            return anc
    return None


def auto_specs(tree):
    """每棵树确定性地生成 6 类场景:埋因/诱饵/噪声都从真实叶子里挑,无随机。"""
    nodes, root = tree["nodes"], tree["root"]
    leaves = [n for n in sorted(nodes) if not nodes[n]["children"] and not nodes[n].get("pass_through")]
    if len(leaves) < 3:
        return []

    def l2(n):
        p = n
        while nodes[p].get("parent") and nodes[p]["parent"] != root:
            p = nodes[p]["parent"]
        return p

    deep = sorted(leaves, key=lambda n: (-nodes[n]["level"], n))
    shallow = sorted(leaves, key=lambda n: (nodes[n]["level"], n))

    def decoys(tgt, k):
        b = l2(tgt)
        return [l for l in deep if l2(l) != b and l != tgt][:k]

    def noise(excl, kf, ki):
        pool = [l for l in leaves if l not in excl]
        return pool[:kf], pool[kf:kf + ki]

    specs = []
    t = deep[0]
    dc = decoys(t, 2); nf, ni = noise(set([t]) | set(dc), 2, 2)
    specs.append({"kind": "confirmed_deep", "category": "confirmed", "target": t, "decoys": dc,
                  "noise_fail": nf, "noise_insuf": ni})
    t = shallow[0]
    if t != deep[0]:
        dc = decoys(t, 1); nf, ni = noise(set([t]) | set(dc), 1, 1)
        specs.append({"kind": "confirmed_shallow", "category": "confirmed", "target": t, "decoys": dc,
                      "noise_fail": nf, "noise_insuf": ni})
    t = deep[1] if len(deep) > 1 else deep[0]
    dc = decoys(t, 4); nf, ni = noise(set([t]) | set(dc), 1, 1)
    specs.append({"kind": "fp_heavy", "category": "false_positive_resist", "target": t, "decoys": dc,
                  "noise_fail": nf, "noise_insuf": ni})
    cand = [l for l in deep if nodes[l].get("parent") and not nodes[nodes[l]["parent"]].get("pass_through")]
    if cand:
        t = cand[0]; dc = decoys(t, 1); nf, ni = noise(set([t]) | set(dc), 1, 0)
        specs.append({"kind": "probable", "category": "probable", "target": t, "decoys": dc,
                      "noise_fail": nf, "noise_insuf": ni})
    specs.append({"kind": "honest", "category": "honest_insufficient", "target": None, "decoys": [],
                  "noise_fail": [], "noise_insuf": deep[:6]})
    specs.append({"kind": "excluded", "category": "excluded", "target": None, "decoys": [],
                  "noise_fail": deep[:6], "noise_insuf": []})
    return specs


def build(spec, tree):
    nodes = tree["nodes"]
    pmap = _persona_map(nodes)
    state = {}
    truth = {"root_cause_node_id": None, "root_cause_name": None, "tier": "undetermined", "must_not_report": []}

    def set_path_hold(leaf):
        for p in path_to_root(nodes, leaf):
            if not nodes[p].get("pass_through"):
                state[p] = "hold"

    cat = spec["category"]
    if cat in ("confirmed", "false_positive_resist"):
        tgt = spec["target"]
        set_path_hold(tgt); state[tgt] = "hold"
        truth.update(root_cause_node_id=tgt, root_cause_name=pmap[tgt]["name"], tier="confirmed")
    elif cat == "probable":
        leaf = spec["target"]; mid = nodes[leaf]["parent"]
        while mid and nodes[mid].get("pass_through"):
            mid = nodes[mid]["parent"]
        set_path_hold(mid); state[leaf] = "insuf"
        truth.update(root_cause_node_id=mid, root_cause_name=pmap[mid]["name"], tier="probable")
    else:
        truth.update(tier=("undetermined" if cat == "honest_insufficient" else "excluded"))

    on_path = {k for k, v in state.items() if v == "hold"}
    for d in spec.get("decoys", []):
        state[d] = "hold"
        brk = nearest_break(nodes, d, on_path)
        if brk:
            state[brk] = "fail"; truth["must_not_report"].append(brk)
        truth["must_not_report"].append(d)
    for d in spec.get("noise_fail", []):
        state.setdefault(d, "fail")
    for d in spec.get("noise_insuf", []):
        state.setdefault(d, "insuf")
    if cat in ("honest_insufficient", "excluded"):
        truth["must_not_report"] = [k for k, v in state.items() if v in ("hold", "insuf")]

    widx = copy.deepcopy(tree)
    results = {}
    for i, (nid, st) in enumerate(sorted(state.items())):
        p = pmap[nid]
        widx["nodes"][nid]["name"] = p["name"]
        widx["nodes"][nid]["condition"] = p["cond"]
        src = SOURCES[i % len(SOURCES)]
        results.setdefault(src, {"indicator": src, "source": src, "conditions": [], "summary": {}})
        results[src]["conditions"].append({"id": f"{nid}_b", "node_id": nid, "node": p["name"],
                                           "condition_text": p["cond"], "class": p["cls"],
                                           "mapping_reason": "合成注入(真实业务判据)", "fact": _fact(p, st)})
    for r in results.values():
        cs = r["conditions"]
        r["summary"] = {"total": len(cs), "ok": sum(c["fact"]["status"] == "ok" for c in cs),
                        "insufficient_data": sum(c["fact"]["status"] == "insufficient_data" for c in cs), "manual": 0}
    return widx, results, truth, state


def build_reference(truth, nodes):
    pmap = _persona_map(nodes)
    out = {"rca_result": [], "probable_causes": [], "undetermined": [], "tree_issues": {}}
    nid = truth["root_cause_node_id"]

    def ep_for(path):
        return [{"node": pmap[p]["name"], "node_id": p, "condition": pmap[p]["cond"],
                 "actual": _actual_str(pmap[p]), "judgment": "成立"} for p in path if not nodes[p].get("pass_through")]

    if truth["tier"] == "confirmed":
        path = path_to_root(nodes, nid)
        out["rca_result"] = [{"Root_cause": truth["root_cause_name"],
                              "Fault_tree_path": [pmap[p]["name"] for p in path if not nodes[p].get("pass_through")],
                              "evidence_path": ep_for(path)}]
    elif truth["tier"] == "probable":
        path = path_to_root(nodes, nid)
        out["probable_causes"] = [{"Root_cause": truth["root_cause_name"], "blocked_at": "叶子因(数据不足)",
                                   "caveats": "叶子因数据不足,已定位到该层、叶子待定", "support": "中间层判据成立",
                                   "evidence_path": ep_for(path)}]
    else:
        out["undetermined"] = [{"note": "无闭合成立路径,数据不足/条件不成立,未定位到根因"}]
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="多树故障注入(不带 predicate)")
    ap.add_argument("--trees", default=str(TREES))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    sys.path.insert(0, str(HERE.parent.parent / "cs-scot-execute" / "scripts"))
    from tree_prune import prune  # noqa: E402

    out = Path(args.out)
    ans = out / "_answers"
    ans.mkdir(parents=True, exist_ok=True)
    tree_files = sorted(Path(args.trees).glob("*.tree_index.json"))
    total = 0
    for tf in tree_files:
        tree = json.loads(tf.read_text(encoding="utf-8"))
        nodes = tree["nodes"]
        tag = tf.stem.replace(".tree_index", "")
        print(f"\n■ {tag} · {len(nodes)} 节点")
        for spec in auto_specs(tree):
            widx, results, truth, state = build(spec, tree)
            pr = prune(widx, results)
            payload = {"fault_tree": {"scenario": pr["scenario"], "root": pr["root"],
                                      "outline": pr["outline"], "pruned_nodes": pr["pruned_nodes"]},
                       "unmapped_facts": pr["unmapped"], "tree_issues": pr["coverage"]}
            name = f"{tag}__{spec['kind']}"
            d = out / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "rca_input.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            (ans / f"{name}.json").write_text(json.dumps(
                {"scenario": name, "tree": tag, "tree_nodes": len(nodes), "category": spec["category"],
                 "truth": truth, "node_states": state, "reference_rca": build_reference(truth, nodes)},
                ensure_ascii=False, indent=2), encoding="utf-8")
            total += 1
            tgt = truth["root_cause_node_id"] or "—"
            print(f"   {spec['kind']:<18} 埋因 {tgt:<5} 剪枝 {pr['coverage']['kept_nodes']}/{pr['coverage']['total_nodes']}"
                  f" 禁报 {len(truth['must_not_report'])}")
    (out / "README.md").write_text(
        "# 冻结的诊断准确率考题(多树故障注入,不带 predicate)\n\n"
        "可信侧预生成、已提交,**本地勿重新生成**。每个 `<场景>/rca_input.json` 是答题模型**唯一该读**的;\n"
        "事实**不含 predicate_satisfied**,模型须拿实际值自己判 condition 成立/不成立,再导航。\n"
        "`_answers/` 是答案区(truth+node_states+满分参考),**做题时勿读**,只给打分器。\n\n"
        "跑分:`python ../scripts/score_accuracy.py --suite . -o accuracy_report.txt`;"
        "自测:`... --use-reference`(应 100%)。\n", encoding="utf-8")
    print(f"\n→ {out}:共 {total} 个场景(跨 {len(tree_files)} 棵树,不同规模)")


if __name__ == "__main__":
    main()
