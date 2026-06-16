#!/usr/bin/env python3
"""v2 评测用例(**手工精心编排,非脚本随机生成**)—— 一棵连贯的 SA 接入失败故障树 + 真实数据 + 答案。

设计取舍:质量优先、量适中。一棵【因果连贯】的树(类别→具体因→根因,条件语义自洽),
每个节点固定一条大白话判据 + 系统列名(语义鸿沟)+ 真实 hold/fail 取值;再配几组【有意义的】
场景(弱覆盖/AMF过载/鉴权失败/疑似/排除/数据不足/抗诱饵/多根因),每组数据都用**真实内核**校验过
能算出预期成立/不成立/数据不足。供 v2 端到端:真 excel_to_tree→generate→execute→rca,再对答案打分。

产出 suite_v2/:fault_tree.csv(真 Excel 格式)+ gold_tree.json(code→金标准绑定)
            + scenarios/<场景>/data/*.csv + scenarios/<场景>/truth.json(code 索引)。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from binding_personas import (gold_binding, evaluate_binding, _table,  # noqa: E402
                              persona_cols, own_series, shared_series, sessions)

OUT = HERE.parent / "suite_v2"
N = 40

# ── 故障树(code, 名字, 父 code, 层级, pass_through)。因果连贯:类别→具体因→根因。 ──
TREE = [
    ("A00", "SA接入失败", None, 2, True),
    ("B01", "RRC连接建立失败", "A00", 3, False),
    ("C01", "随机接入失败", "B01", 4, False),
    ("D01", "上行干扰致接入失败", "C01", 5, False),
    ("D02", "弱覆盖致接入失败", "C01", 5, False),
    ("C02", "RRC拒绝-拥塞", "B01", 4, False),
    ("B02", "UE上下文建立失败", "A00", 3, False),
    ("C03", "安全模式失败", "B02", 4, False),
    ("C04", "鉴权失败", "B02", 4, False),
    ("B03", "NGAP/AMF异常", "A00", 3, False),
    ("C05", "AMF过载", "B03", 4, False),
    ("C06", "传输资源不可用", "B03", 4, False),
    ("B04", "QoS流建立失败", "A00", 3, False),
    ("C07", "无线链路失败", "B04", 4, False),
    ("B05", "网元告警", "A00", 3, True),
    ("C08", "射频驻波比异常告警", "B05", 4, False),
    ("C09", "小区不可用告警", "B05", 4, False),
]

# ── 每个带条件节点:大白话判据(tree_cond,不含系统列名)+ 系统列(col)+ 真实取值 + 金标准绑定参数。 ──
SPEC = {
    "B01": {"name": "RRC连接建立成功率低", "tree_cond": "SA RRC连接建立成功率 低于 99%", "source": "kpi",
            "cls": "A", "kind": "num", "col": "SA_RRC_ConnEst_SuccRate(%)", "op": "<", "thr": 99.0, "hold_v": 94.2, "fail_v": 99.8, "insuf": "无RRC成功率话统"},
    "C01": {"name": "随机接入成功率低", "tree_cond": "PRACH 随机接入成功率 低于 95%", "source": "kpi",
            "cls": "A", "kind": "num", "col": "SA_RACH_Acc_SuccRate(%)", "op": "<", "thr": 95.0, "hold_v": 81.5, "fail_v": 98.6, "insuf": "无RACH话统"},
    "D01": {"name": "上行干扰偏高", "tree_cond": "上行底噪 RSSI 高于 -105dBm(存在上行干扰)", "source": "kpi",
            "cls": "A", "kind": "num", "col": "PUSCH_UL_RSSI(dBm)", "op": ">", "thr": -105.0, "hold_v": -97.2, "fail_v": -116.0, "insuf": "无上行干扰话统"},
    "D02": {"name": "弱覆盖采样占比高", "tree_cond": "弱覆盖采样(RSRP 低于 -110)占比 超过 15%", "source": "kpi",
            "cls": "A", "kind": "num", "col": "WeakCov_RSRP_Ratio(%)", "op": ">", "thr": 15.0, "hold_v": 34.0, "fail_v": 3.2, "insuf": "无MR/MDT测量"},
    "C02": {"name": "RRC拒绝-拥塞占比高", "tree_cond": "RRC 拒绝原因中 拥塞(congestion) 占比 不低于 15%", "source": "chr",
            "cls": "C", "kind": "match", "col": "RrcRejectCause", "value": "congestion", "total": N, "hold_m": 13, "fail_m": 0, "min_ratio": 0.15, "insuf": "无RRC拒绝原因"},
    "B02": {"name": "UE上下文建立成功率低", "tree_cond": "SA UE上下文建立成功率 低于 99%", "source": "kpi",
            "cls": "A", "kind": "num", "col": "SA_UECtx_Est_SuccRate(%)", "op": "<", "thr": 99.0, "hold_v": 95.1, "fail_v": 99.9, "insuf": "无UEContext成功率列"},
    "C03": {"name": "安全模式失败", "tree_cond": "信令会话里 有 SecurityModeCommand 但 缺 SecurityModeComplete", "source": "sig",
            "cls": "H", "kind": "sess", "col": "MessageType", "key": "CallID", "msg": "SecurityModeComplete", "have": "SecurityModeCommand", "hold_s": 7, "insuf": "无信令trace"},
    "C04": {"name": "鉴权失败占比高", "tree_cond": "释放原因中 鉴权失败(NAS_Authentication_Failure) 占比 不低于 20%", "source": "chr",
            "cls": "C", "kind": "match", "col": "ReleaseCause", "value": "NAS_Authentication_Failure", "total": N, "hold_m": 12, "fail_m": 0, "min_ratio": 0.2, "insuf": "无CHR释放原因"},
    "B03": {"name": "NG信令建立成功率低", "tree_cond": "SA NG信令建立成功率 低于 99.5%", "source": "kpi",
            "cls": "A", "kind": "num", "col": "SA_NGSig_Est_SuccRate(%)", "op": "<", "thr": 99.5, "hold_v": 96.0, "fail_v": 99.99, "insuf": "无NG信令话统"},
    "C05": {"name": "AMF过载占比高", "tree_cond": "NGAP 原因中 AMF过载(amf-overload) 占比 不低于 20%", "source": "chr",
            "cls": "C", "kind": "match", "col": "NgapCause", "value": "amf-overload", "total": N, "hold_m": 11, "fail_m": 0, "min_ratio": 0.2, "insuf": "无NGAP原因码"},
    "C06": {"name": "传输资源不可用占比高", "tree_cond": "NGAP 原因中 传输资源不可用(transport-resource-unavailable) 占比 不低于 20%", "source": "chr",
            "cls": "C", "kind": "match", "col": "NgapCause", "value": "transport-resource-unavailable", "total": N, "hold_m": 13, "fail_m": 0, "min_ratio": 0.2, "insuf": "无NGAP原因码"},
    "B04": {"name": "QoS流建立成功率低", "tree_cond": "SA QoS Flow建立成功率 低于 99%", "source": "kpi",
            "cls": "A", "kind": "num", "col": "SA_QoSFlow_Est_SuccRate(%)", "op": "<", "thr": 99.0, "hold_v": 90.4, "fail_v": 99.7, "insuf": "无QoSFlow话统"},
    "C07": {"name": "无线链路失败占比高", "tree_cond": "CHR 错误码中 无线链路失败(RNL_RadioLinkFailure) 占比 不低于 20%", "source": "chr",
            "cls": "C", "kind": "match", "col": "DCErrType", "value": "RNL_RadioLinkFailure", "total": N, "hold_m": 14, "fail_m": 1, "min_ratio": 0.2, "insuf": "无CHR错误类型"},
    "C08": {"name": "射频驻波比异常告警", "tree_cond": "存在 射频单元驻波比异常 告警(ALM-26529)", "source": "alarm",
            "cls": "G", "kind": "exist", "col": "AlarmCode", "value": "ALM-26529", "hold_m": 3, "insuf": "无告警日志源"},
    "C09": {"name": "小区不可用告警", "tree_cond": "存在 小区不可用 告警(ALM-25888)", "source": "alarm",
            "cls": "G", "kind": "exist", "col": "AlarmCode", "value": "ALM-25888", "hold_m": 2, "insuf": "无告警日志源"},
}

# ── 有意义的场景:hold=成立的节点(埋的路径/诱饵);insuf=数据不足;其余带条件节点=不成立(正常)。 ──
SCENARIOS = [
    {"name": "S1_弱覆盖致接入失败", "category": "confirmed", "tier": "confirmed",
     "hold": ["B01", "C01", "D02"], "roots": ["D02"], "must_not_report": []},
    {"name": "S2_AMF过载", "category": "confirmed", "tier": "confirmed",
     "hold": ["B03", "C05"], "roots": ["C05"], "must_not_report": []},
    {"name": "S3_无线链路失败", "category": "confirmed", "tier": "confirmed",
     "hold": ["B04", "C07"], "roots": ["C07"], "must_not_report": []},
    {"name": "S4_鉴权失败", "category": "confirmed", "tier": "confirmed",
     "hold": ["B02", "C04"], "roots": ["C04"], "must_not_report": []},
    {"name": "S5_上下文层疑似", "category": "probable", "tier": "probable",
     "hold": ["B02"], "insuf": ["C03"], "roots": ["B02"], "must_not_report": []},
    {"name": "S6_网络正常排除", "category": "excluded", "tier": "excluded",
     "hold": [], "roots": [], "must_not_report": []},
    {"name": "S7_数据不足", "category": "honest_insufficient", "tier": "undetermined",
     "hold": [], "insuf": list(SPEC), "roots": [], "must_not_report": []},
    {"name": "S8_抗诱饵(弱覆盖断路)", "category": "false_positive_resist", "tier": "confirmed",
     "hold": ["B03", "C05", "D02"], "roots": ["C05"], "must_not_report": ["D02"]},
    {"name": "S9_多根因(弱覆盖+无线链路)", "category": "confirmed", "tier": "confirmed",
     "hold": ["B01", "C01", "D02", "B04", "C07"], "roots": ["D02", "C07"], "must_not_report": []},
]

NAME = {c: n for c, n, *_ in TREE}
PARENT = {c: p for c, _, p, *_ in TREE}
LEVEL = {c: lv for c, _, _, lv, _ in TREE}
PASS = {c: pt for c, _, _, _, pt in TREE}
CHILDREN = {}
for c, _, p, *_ in TREE:
    CHILDREN.setdefault(p, []).append(c)


def _states(scn):
    st = {}
    for c in scn.get("hold", []):
        st[c] = "hold"
    for c in scn.get("insuf", []):
        st[c] = "insuf"
    for c in SPEC:                       # 其余带条件节点 = 不成立(正常网络)
        st.setdefault(c, "fail")
    return st


def build_data(states):
    by_src = {}
    for code, stt in states.items():
        p = SPEC[code]
        by_src.setdefault(p["source"], []).append((code, p, stt))
    data = {}
    for src, items in by_src.items():
        if any(p["kind"] == "sess" for _, p, _ in items):
            rows = []
            for j, (code, p, stt) in enumerate(items):
                if stt != "insuf":
                    rows += sessions(p, stt, (j + 1) * 1000)
            data[src] = rows
        else:
            colmap = {}
            for code, p, stt in items:
                if stt == "insuf":
                    continue
                for c in persona_cols(p):
                    colmap.setdefault(c, []).append((p, stt))
            cs = {c: (own_series(o[0][0], o[0][1], c, N) if len(o) == 1 else shared_series(o, N)) for c, o in colmap.items()}
            data[src] = [{c: cs[c][i] for c in cs} for i in range(N)] if cs else []
    return data


def verify(scn):
    """用真实内核核对:每个条件节点的判定 == 期望(hold→成立/fail→不成立/insuf→数据不足)。"""
    states = _states(scn)
    data = {s: _table(rows) for s, rows in build_data(states).items()}
    bad = []
    for code, stt in states.items():
        p = SPEC[code]; src = p["source"]
        if stt == "insuf" or src not in data:
            f = {"status": "insufficient_data"}
        else:
            f = evaluate_binding(gold_binding(p, code, p["name"], src), {src: data[src]}, src)
        got = ("hold" if f.get("predicate_satisfied") is True and f.get("status") == "ok"
               else "insuf" if f.get("status") == "insufficient_data"
               else "fail" if f.get("predicate_satisfied") is False else "?")
        if got != stt:
            bad.append(f"{code}({NAME[code]}) 期望{stt} 实得{got}")
    return bad


def _path(code):
    p = [code]
    while PARENT.get(p[-1]):
        p.append(PARENT[p[-1]])
    return list(reversed(p))


def emit_excel(out: Path, codes_fmt):
    rows = []

    def mkrow(path, judg):
        r = {}
        for c in path:
            r[f"L{LEVEL[c]}(Chinese)"] = NAME[c]
            r[f"L{LEVEL[c]} Code"] = codes_fmt[c]
        if judg in SPEC:
            r["Judgment Condition(Chinese)"] = SPEC[judg]["tree_cond"]
        return r
    for c in NAME:
        if not CHILDREN.get(c):                          # 叶子行:铺全拓扑
            rows.append(mkrow(_path(c), c if c in SPEC else None))
    for c in SPEC:                                       # 中间带条件节点:单独一行挂条件
        if CHILDREN.get(c):
            rows.append(mkrow(_path(c), c))
    fields = sorted({k for r in rows for k in r})
    with (out / "fault_tree.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval=""); w.writeheader(); w.writerows(rows)


def _write_csv(path, rows):
    fields = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval=""); w.writeheader(); w.writerows(rows)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    OUT.mkdir(parents=True, exist_ok=True)
    codes_fmt = {c: f"FT-{i:03d}" for i, c in enumerate(NAME)}
    emit_excel(OUT, codes_fmt)
    # 金标准绑定(按 code 索引;真 excel_to_tree 重新分配 id 时用 code 映射回来)
    gold = {"root": NAME["A00"], "by_code": {}}
    for code, p in SPEC.items():
        b = gold_binding(p, "<map-by-code>", p["name"], p["source"]); b.pop("node_id", None)
        gold["by_code"][codes_fmt[code]] = {"name": p["name"], "source": p["source"], "binding": b}
    (OUT / "gold_tree.json").write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"故障树 {len(TREE)} 节点({len(SPEC)} 条件节点)→ fault_tree.csv + gold_tree.json")
    allbad = 0
    for scn in SCENARIOS:
        bad = verify(scn)
        allbad += len(bad)
        d = OUT / "scenarios" / scn["name"]
        (d / "data").mkdir(parents=True, exist_ok=True)
        for src, rows in build_data(_states(scn)).items():
            _write_csv(d / "data" / f"{src}.csv", rows)
        # truth 一律用 codes_fmt(= Excel/gold 的 code,也是映射到重解析 id 的 join key),与内部 code 解耦
        (d / "truth.json").write_text(json.dumps(
            {"scenario": scn["name"], "category": scn["category"], "tier": scn["tier"],
             "root_cause_codes": [codes_fmt[c] for c in scn["roots"]],
             "must_not_report_codes": [codes_fmt[c] for c in scn["must_not_report"]],
             "node_states": {codes_fmt[c]: s for c, s in _states(scn).items()}},
            ensure_ascii=False, indent=2), encoding="utf-8")
        flag = "✓" if not bad else "✗ " + "; ".join(bad[:3])
        print(f"  {scn['name']:<26}[{scn['category']:<22}] 内核校验 {flag}")
    print(f"\n→ {OUT}  ({'全部场景内核校验通过 ✓' if allbad == 0 else '✗ 有 %d 处不符' % allbad})")


if __name__ == "__main__":
    main()
