#!/usr/bin/env python3
"""绑定/数据 人设池 —— 把每个"业务因"同时定义出三样东西,且三样自洽:
  ① 树判据(tree_cond,大白话,**不含系统列名** → 制造"语义对列"的难点);
  ② 系统数据列名(col)+ 能让内核算出 成立/不成立 的真实数据行(synth_rows);
  ③ 金标准绑定(gold_binding:类别/列/参数)。
facts 不再手写,而是 **synth_rows → 真实内核 evaluate_binding** 算出来(保证 data+binding→fact 自洽,
也顺带验证 execute 链)。这样同一套场景能测三段:绑定(模型 vs 金标准)、执行(数据→事实)、RCA(事实→根因)。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_KROOT = Path(__file__).resolve().parent.parent.parent / "cs-scot-generate" / "scripts"
sys.path.insert(0, str(_KROOT))
from condition_kernels import evaluate_binding  # noqa: E402

# 每个人设:name 因 / tree_cond 树判据(大白话)/ cls 类别 / kind 数据形态 / col 系统列名 + 类内参数。
# 关键:tree_cond 里**不出现 col**,逼模型把"大白话指标"映射到"系统列名"。
PERSONAS = [
    # —— A 阈值(成功率/比率)——
    {"name": "RRC连接建立成功率低", "tree_cond": "RRC连接建立成功率 低于 98%", "cls": "A", "kind": "num",
     "col": "SA_RRC_ConnSetup_SuccRate(%)", "op": "<", "thr": 98.0, "hold_v": 91.4, "fail_v": 99.7,
     "insuf": "本次无该成功率话统计数器"},
    {"name": "UE上下文建立成功率低", "tree_cond": "UE上下文建立成功率 低于 99%", "cls": "A", "kind": "num",
     "col": "SA_UECntx_Est_SuccRate(%)", "op": "<", "thr": 99.0, "hold_v": 93.8, "fail_v": 99.95,
     "insuf": "无UEContext建立成功率列"},
    {"name": "PRACH随机接入成功率低", "tree_cond": "PRACH 随机接入成功率 低于 95%", "cls": "A", "kind": "num",
     "col": "RACH_Setup_SuccRate(%)", "op": "<", "thr": 95.0, "hold_v": 78.6, "fail_v": 98.9, "insuf": "无RACH话统"},
    {"name": "上行干扰偏高", "tree_cond": "上行底噪(RSSI)抬升,高于 -105dBm", "cls": "A", "kind": "num",
     "col": "PUSCH_RSSI(dBm)", "op": ">", "thr": -105.0, "hold_v": -96.5, "fail_v": -117.8, "insuf": "无上行干扰话统"},
    {"name": "弱覆盖采样占比高", "tree_cond": "弱覆盖采样(RSRP低于-110)占比 超过 15%", "cls": "A", "kind": "num",
     "col": "WeakCov_Sample_Ratio(%)", "op": ">", "thr": 15.0, "hold_v": 38.4, "fail_v": 2.1, "insuf": "无MR/MDT测量"},
    # —— B 计数器差 ——
    {"name": "RRC建立尝试与成功数差大于0", "tree_cond": "RRC建立 尝试次数 减 成功次数 大于 0", "cls": "B", "kind": "diff",
     "minuend": "N.RRC.ConnReq.Att", "subtrahend": "N.RRC.ConnReq.Succ", "op": ">", "thr": 0,
     "hold": (8420, 7180), "fail": (5000, 5000), "insuf": "无RRC原始计数器(只有成功率)"},
    # —— C 错误码/原因占比 ——
    {"name": "无线链路失败占比高", "tree_cond": "建链错误码中 无线链路失败(RNL_RadioLinkFailure) 占比 不低于 20%",
     "cls": "C", "kind": "match", "col": "DCErrType", "value": "RNL_RadioLinkFailure",
     "total": 50, "hold_m": 18, "fail_m": 1, "min_ratio": 0.2, "insuf": "无CHR错误类型字段"},
    {"name": "NAS鉴权失败占比高", "tree_cond": "释放原因中 鉴权失败(NAS_Authentication_Failure) 占比 不低于 20%",
     "cls": "C", "kind": "match", "col": "ReleaseCause", "value": "NAS_Authentication_Failure",
     "total": 40, "hold_m": 14, "fail_m": 1, "min_ratio": 0.2, "insuf": "无CHR释放原因字段"},
    {"name": "传输资源不可用占比高", "tree_cond": "NGAP原因中 传输资源不可用(transport-resource-unavailable) 占比 不低于 20%",
     "cls": "C", "kind": "match", "col": "NgRanCause", "value": "transport-resource-unavailable",
     "total": 30, "hold_m": 12, "fail_m": 0, "min_ratio": 0.2, "insuf": "无NGAP原因码"},
    # —— G 告警存在 ——
    {"name": "射频单元驻波比告警", "tree_cond": "存在 射频单元驻波比异常 告警(ALM-26529)", "cls": "G", "kind": "exist",
     "col": "AlarmLog", "value": "ALM-26529", "hold_m": 3, "insuf": "无告警日志源"},
    {"name": "GTP-U路径故障告警", "tree_cond": "存在 GTP-U 路径故障 告警(ALM-25952)", "cls": "G", "kind": "exist",
     "col": "AlarmLog", "value": "ALM-25952", "hold_m": 4, "insuf": "无告警日志源"},
    {"name": "Xn链路故障告警", "tree_cond": "存在 Xn 链路故障 告警(ALM-29240)", "cls": "G", "kind": "exist",
     "col": "AlarmLog", "value": "ALM-29240", "hold_m": 1, "insuf": "无告警日志源"},
    # —— E 占比/TopN ——
    {"name": "TOP1终端型号集中", "tree_cond": "失败会话按 终端型号 分组,占比最高的型号 超过 50%", "cls": "E", "kind": "prop",
     "col": "TerminalModel", "top1": "BrandX-Model9", "op": ">", "thr": 0.5, "hold_r": 0.72, "fail_r": 0.16,
     "insuf": "无终端型号字段"},
    {"name": "单小区接入失败集中", "tree_cond": "失败会话按 小区 分组,占比最高的小区 超过 60%", "cls": "E", "kind": "prop",
     "col": "NRCellIdentity", "top1": "Cell-0x1A3F", "op": ">", "thr": 0.6, "hold_r": 0.81, "fail_r": 0.22,
     "insuf": "无小区维度CHR"},
    # —— H 信令缺失 ——
    {"name": "会话缺安全模式完成", "tree_cond": "信令会话里 有 SecurityModeCommand 却 缺 SecurityModeComplete", "cls": "H",
     "kind": "sess", "col": "MessageType", "key": "CallID", "msg": "SecurityModeComplete", "have": "SecurityModeCommand",
     "hold_s": 6, "insuf": "无信令trace"},
    {"name": "有建立请求无建立完成", "tree_cond": "信令会话里 有 RRCSetupRequest 却 缺 RRCSetupComplete", "cls": "H",
     "kind": "sess", "col": "MessageType", "key": "CallID", "msg": "RRCSetupComplete", "have": "RRCSetupRequest",
     "hold_s": 5, "insuf": "无信令trace"},
]


def gold_binding(p: dict, node_id: str, node_name: str, source: str) -> dict:
    """该人设的【金标准绑定】(就是 generate 阶段模型该写出来的 CONDITIONS 条目)。"""
    base = {"id": f"{node_id}_b", "node_id": node_id, "node": node_name,
            "condition_text": p["tree_cond"], "source": source, "class": p["cls"],
            "mapping_reason": "金标准:大白话判据→系统列"}
    k = p["kind"]
    if k == "num":
        base.update(column=p["col"], op=p["op"], threshold=p["thr"], scope="aggregate", agg="mean")
    elif k == "diff":
        base.update(minuend=[p["minuend"]], subtrahend=[p["subtrahend"]], op=p["op"], threshold=p["thr"],
                    scope="aggregate", agg="sum")
    elif k == "match":
        base.update(column=p["col"], contains=[p["value"]], require=math.ceil(p["min_ratio"] * p["total"]))
    elif k == "exist":
        base.update(column=p["col"], contains=[p["value"]], require=1)
    elif k == "prop":
        base.update(group_by=p["col"], op=p["op"], threshold=p["thr"])
    elif k == "sess":
        base.update(key=p["key"], msg_column=p["col"], lacks=[p["msg"]])
    return base


def synth_rows(p: dict, state: str, sid: int):
    """(旧:单列稀疏行)保留给单元自测;主流程改用 own_series/sessions 造密集表。"""
    if state == "insuf":
        return []
    cols = persona_cols(p)
    if p["kind"] == "sess":
        return sessions(p, state, (sid + 1) * 1000)
    series = {c: own_series(p, state, c, 12) for c in cols}
    return [{c: series[c][i] for c in series} for i in range(12)]


def persona_cols(p: dict):
    k = p["kind"]
    if k == "diff":
        return [p["minuend"], p["subtrahend"]]
    if k == "sess":
        return [p["key"], p["col"]]
    return [p["col"]]


def own_series(p: dict, state: str, col: str, N: int):
    """该列在 N 行里的取值(单一来源)。num 加点抖动更像真,但保持比较关系。"""
    hold = state == "hold"
    k = p["kind"]
    if k == "num":
        v = p["hold_v"] if hold else p["fail_v"]
        step = 0.0 if abs(v) < 1e-9 else abs(v) * 0.01
        return [round(v + (i % 5 - 2) * step, 4) for i in range(N)]   # 围绕 v 抖动,均值≈v
    if k == "diff":
        m, s = p["hold"] if hold else p["fail"]
        return [m] * N if col == p["minuend"] else [s] * N
    if k == "match":
        mm = max(0, round((p["hold_m"] if hold else p["fail_m"]) / p["total"] * N))
        return [p["value"]] * mm + ["OTHER"] * (N - mm)
    if k == "exist":
        mm = (p.get("hold_m", 1) if hold else 0)
        return [p["value"]] * mm + ["NONE"] * (N - mm)
    if k == "prop":
        n1 = round((p["hold_r"] if hold else p["fail_r"]) * N)
        return [p["top1"]] * n1 + [f"Other-{i % 5}" for i in range(N - n1)]
    return [""] * N


def shared_series(owners, N):
    """多个判据共用一列(如 AlarmLog 里多种告警码):各放各的命中行,补 NONE。"""
    vals = []
    for p, st in owners:
        vals += [p["value"]] * (p.get("hold_m", 1) if st == "hold" else 0)
    vals += ["NONE"] * max(0, N - len(vals))
    return vals[:N]


def sessions(p: dict, state: str, base: int):
    """H 信令:hold→若干会话(有 have、缺 msg);fail→补上 msg(于是不缺)。"""
    rows = []
    cnt = p["hold_s"] if state == "hold" else 3
    for i in range(cnt):
        cid = base + i
        rows.append({p["key"]: cid, p["col"]: p["have"]})
        rows.append({p["key"]: cid, p["col"]: "OtherMsg"})
        if state != "hold":
            rows.append({p["key"]: cid, p["col"]: p["msg"]})
    return rows


def _table(rows):
    header = []
    for r in rows:
        for kk in r:
            if kk not in header:
                header.append(kk)
    return (header, rows)
