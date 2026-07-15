#!/usr/bin/env python3
"""A–J 内核回归自测:每个内核给若干「合成小表 + 绑定 → 期望事实」用例,断言关键字段。

为什么要它:condition_kernels.py 是整条流水线的地基(execute 只跑、不改统计),
但此前它**没有任何回归测试**——谁改坏一行(改了谓词、漏了分量字段、动了解析)都不会有东西报警。
这份用例覆盖每类的 成立/不成立/数据不足/边界(空表、缺列、%值、transform 链、require、三态组合),
是确定性的(无 ground truth、不调模型、可反复跑出同一结果),被 cs-scot-benchmark 当第 6 项接入。

用法:
    kernel_selftest.py            # 跑全量,打印逐条结果,全过 exit 0、有失败 exit 1
    (也可被 benchmark import:from kernel_selftest import run_battery)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from condition_kernels import evaluate_binding  # 被测对象


def _t(header, *rows):
    return (list(header), [dict(zip(header, r)) for r in rows])


# 每条用例:name / table(None=无源) / binding / expect(字段=值,需相等)/ present(必须出现的字段)
CASES = [
    # ---- A 阈值 ----
    {"name": "A aggregate 成立(率<100)", "table": _t(["rate"], ["95"], ["97"]),
     "binding": {"class": "A", "column": "rate", "op": "<", "threshold": 100, "scope": "aggregate", "agg": "mean"},
     "expect": {"status": "ok", "value": 96.0, "predicate_satisfied": True}, "present": ["valid", "n_rows"]},
    {"name": "A aggregate 不成立", "table": _t(["rate"], ["100"], ["100"]),
     "binding": {"class": "A", "column": "rate", "op": "<", "threshold": 100, "scope": "aggregate", "agg": "mean"},
     "expect": {"status": "ok", "predicate_satisfied": False}},
    {"name": "A %值自动剥百分号", "table": _t(["rate"], ["95%"], ["97%"]),
     "binding": {"class": "A", "column": "rate", "op": "<", "threshold": 100, "scope": "aggregate", "agg": "mean"},
     "expect": {"status": "ok", "value": 96.0}},
    {"name": "A count_rows 认 require(2满足/需3→不成立)", "table": _t(["x"], ["1"], ["1"], ["9"]),
     "binding": {"class": "A", "column": "x", "op": "<", "threshold": 5, "scope": "count_rows", "require": 3},
     "expect": {"status": "ok", "count_satisfied": 2, "predicate_satisfied": False}},
    {"name": "A count_rows 认 require(需2→成立)", "table": _t(["x"], ["1"], ["1"], ["9"]),
     "binding": {"class": "A", "column": "x", "op": "<", "threshold": 5, "scope": "count_rows", "require": 2},
     "expect": {"status": "ok", "count_satisfied": 2, "predicate_satisfied": True}},
    {"name": "A transform log10", "table": _t(["x"], ["100"], ["1000"]),
     "binding": {"class": "A", "column": "x", "op": ">", "threshold": 2, "scope": "aggregate", "agg": "mean", "transforms": ["log10"]},
     "expect": {"status": "ok", "value": 2.5, "predicate_satisfied": True}},
    {"name": "A 缺列→数据不足", "table": _t(["rate"], ["95"]),
     "binding": {"class": "A", "column": "不存在的列", "op": "<", "threshold": 100},
     "expect": {"status": "insufficient_data", "resolved_column": None}},
    {"name": "A 全空列→数据不足", "table": _t(["rate"], ["NIL"], ["-"]),
     "binding": {"class": "A", "column": "rate", "op": "<", "threshold": 100, "scope": "aggregate"},
     "expect": {"status": "insufficient_data"}},

    # ---- B 差值(逐操作数 transform)----
    {"name": "B 差值成立(Att-Succ>0)", "table": _t(["att", "succ"], ["10", "7"], ["8", "8"]),
     "binding": {"class": "B", "minuend": ["att"], "subtrahend": ["succ"], "op": ">", "threshold": 0, "scope": "aggregate", "agg": "sum"},
     "expect": {"status": "ok", "value": 3.0, "predicate_satisfied": True}},
    {"name": "B 主分集 10log10(a)-10log10(b)>6dB", "table": _t(["a", "b"], ["100", "1"]),
     "binding": {"class": "B", "minuend": ["a"], "subtrahend": ["b"], "transforms": ["log10", "scale:10"],
                 "op": ">", "threshold": 6, "scope": "aggregate", "agg": "mean"},
     "expect": {"status": "ok", "value": 20.0, "predicate_satisfied": True}},
    {"name": "B 缺原始计数列→数据不足", "table": _t(["att"], ["10"]),
     "binding": {"class": "B", "minuend": ["att"], "subtrahend": ["succ"], "op": ">", "threshold": 0},
     "expect": {"status": "insufficient_data"}},

    # ---- C 字段匹配(报分量 total/ratio/top)----
    {"name": "C 命中成立 + 报分量", "table": _t(["DCErrType"], ["RNL_Unspecified"], ["OK"], ["OK"]),
     "binding": {"class": "C", "column": "DCErrType", "equals": ["RNL_Unspecified"], "require": 1},
     "expect": {"status": "ok", "matched": 1, "total": 3, "predicate_satisfied": True}, "present": ["ratio", "top"]},
    {"name": "C require 不足→不成立", "table": _t(["DCErrType"], ["RNL_Unspecified"], ["OK"]),
     "binding": {"class": "C", "column": "DCErrType", "equals": ["RNL_Unspecified"], "require": 2},
     "expect": {"status": "ok", "matched": 1, "predicate_satisfied": False}},
    {"name": "C 缺列→数据不足", "table": _t(["other"], ["x"]),
     "binding": {"class": "C", "column": "DCErrType", "equals": ["RNL"]},
     "expect": {"status": "insufficient_data", "resolved_column": None}},

    # ---- D 组合(三态)----
    {"name": "D AND 两真→真", "table": _t(["err", "rsrp"], ["RNL", "-130"]),
     "binding": {"class": "D", "combine": "AND", "sub": [
         {"class": "C", "column": "err", "equals": ["RNL"], "require": 1},
         {"class": "A", "column": "rsrp", "op": "<", "threshold": -120, "scope": "count_rows", "require": 1}]},
     "expect": {"status": "ok", "predicate_satisfied": True}},
    {"name": "D AND 缺列→数据不足传播", "table": _t(["err"], ["RNL"]),
     "binding": {"class": "D", "combine": "AND", "sub": [
         {"class": "C", "column": "err", "equals": ["RNL"], "require": 1},
         {"class": "A", "column": "缺列", "op": "<", "threshold": 0}]},
     "expect": {"status": "insufficient_data"}},
    {"name": "D OR 一真一假→真", "table": _t(["err", "rsrp"], ["OK", "-130"]),
     "binding": {"class": "D", "combine": "OR", "sub": [
         {"class": "C", "column": "err", "equals": ["RNL"], "require": 1},
         {"class": "A", "column": "rsrp", "op": "<", "threshold": -120, "scope": "count_rows", "require": 1}]},
     "expect": {"status": "ok", "predicate_satisfied": True}},

    # ---- E 占比/TopN(报分量 valid/top1_count)----
    {"name": "E top1 占比>0.7 成立 + 报分量", "table": _t(["term"], ["A"], ["A"], ["A"], ["A"], ["B"]),
     "binding": {"class": "E", "group_by": "term", "op": ">", "threshold": 0.7},
     "expect": {"status": "ok", "top1": "A", "top1_count": 4, "valid": 5, "predicate_satisfied": True}},

    # ---- F 配置(报分量 n)----
    {"name": "F 配置全等成立 + 报 n", "table": _t(["param"], ["ON"], ["ON"]),
     "binding": {"class": "F", "param": "param", "equals": "ON"},
     "expect": {"status": "ok", "n": 2, "predicate_satisfied": True}},
    {"name": "F 缺参数列→数据不足", "table": _t(["other"], ["x"]),
     "binding": {"class": "F", "param": "param", "equals": "ON"},
     "expect": {"status": "insufficient_data", "resolved_column": None}},
    # not_equals / negate:故障="参数≠期望值",predicate_satisfied 始终=「故障成立」(省去 RCA 反推)
    {"name": "F not_equals 实测全是好值→故障不成立(SsbPeriod≠20,但都=MS20)",
     "table": _t(["SsbPeriod"], ["MS20"], ["MS20"]),
     "binding": {"class": "F", "param": "SsbPeriod", "not_equals": "MS20"},
     "expect": {"status": "ok", "compare": "!=", "matched": 0, "predicate_satisfied": False}},
    {"name": "F not_equals 存在坏值→故障成立",
     "table": _t(["SsbPeriod"], ["MS20"], ["MS10"]),
     "binding": {"class": "F", "param": "SsbPeriod", "not_equals": "MS20"},
     "expect": {"status": "ok", "compare": "!=", "matched": 1, "predicate_satisfied": True}},
    {"name": "F negate:true + equals 等价 not_equals(TxRxMode 非 64T64R→故障在)",
     "table": _t(["m"], ["1T1R"]),
     "binding": {"class": "F", "param": "m", "equals": "64T64R", "negate": True},
     "expect": {"status": "ok", "compare": "!=", "matched": 1, "predicate_satisfied": True}},

    # ---- G 日志匹配(= C 的 contains)----
    {"name": "G 日志含告警码成立", "table": _t(["log"], ["...ALM-21582 Path拥塞..."], ["normal"]),
     "binding": {"class": "G", "column": "log", "contains": ["ALM-21582"], "require": 1},
     "expect": {"status": "ok", "matched": 1, "predicate_satisfied": True}},
    # 多 target 时输出逐 target 命中分解(审计层字段;一行命中两个 target → matched 记 1、分解各记 1)
    {"name": "G 多 target 报 matched_by_target 分解", "table": _t(
        ["log"], ["ALM-111 occurred"], ["ALM-222 twice"], ["both ALM-111 ALM-222"], ["normal"]),
     "binding": {"class": "G", "column": "log", "contains": ["ALM-111", "ALM-222"]},
     "expect": {"status": "ok", "matched": 3,
                "matched_by_target": {"ALM-111": 2, "ALM-222": 2}}},
    # 单 target 分解=matched 纯冗余 → 不输出(fact.get 返回 None 即字段不存在)
    {"name": "G 单 target 不输出 matched_by_target", "table": _t(["log"], ["ALM-111 occurred"]),
     "binding": {"class": "G", "column": "log", "contains": ["ALM-111"]},
     "expect": {"status": "ok", "matched": 1, "matched_by_target": None}},
    # C/G 缺 contains/equals(op/target 等野字段)→ 响亮数据不足,绝不静默 matched=0 变假排除
    {"name": "C/G 空 targets→数据不足(op/target 野字段不认,防假排除)",
     "table": _t(["Summary"], ["hwLinkHeartbeatDropAlarm_active"]),
     "binding": {"class": "C", "column": "Summary", "op": "contains", "target": "hwLinkHeartbeatDropAlarm"},
     "expect": {"status": "insufficient_data", "predicate_satisfied": None}},

    # ---- H 信令(会话级 has)----
    {"name": "H 会话含某消息→匹配", "table": _t(["CallID", "MessageType"], ["1", "RRCSetupRequest"], ["1", "RRCSetupFailure"]),
     "binding": {"class": "H", "key": "CallID", "msg_column": "MessageType", "has": ["RRCSetupFailure"]},
     "expect": {"status": "ok"}, "present": ["matched_sessions"]},
    {"name": "H 无消息列→数据不足", "table": _t(["CallID"], ["1"]),
     "binding": {"class": "H", "key": "CallID", "msg_column": "MessageType", "has": ["X"]},
     "expect": {"status": "insufficient_data"}},

    # ---- I 特征 ----
    {"name": "I row_threshold 认 require(1满足/需2→不成立)", "table": _t(["ta"], ["200"], ["10"]),
     "binding": {"class": "I", "feature": "ta", "metric": "row_threshold", "op": ">", "threshold": 100, "require": 2},
     "expect": {"status": "ok", "count_satisfied": 1, "predicate_satisfied": False}},
    {"name": "I row_threshold 认 require(需1→成立)", "table": _t(["ta"], ["200"], ["10"]),
     "binding": {"class": "I", "feature": "ta", "metric": "row_threshold", "op": ">", "threshold": 100, "require": 1},
     "expect": {"status": "ok", "count_satisfied": 1, "predicate_satisfied": True}},
    {"name": "I all_null 列全空→成立", "table": _t(["c"], ["NIL"], ["-"], [""]),
     "binding": {"class": "I", "feature": "c", "metric": "all_null"},
     "expect": {"status": "ok", "predicate_satisfied": True}},
    {"name": "I all_same 列同值→成立", "table": _t(["c"], ["X"], ["X"]),
     "binding": {"class": "I", "feature": "c", "metric": "all_same"},
     "expect": {"status": "ok", "predicate_satisfied": True}},

    # ---- J 人工 / decision 逃逸 ----
    {"name": "J 人工→manual,不给判断", "table": None,
     "binding": {"class": "J", "note": "需现场确认"},
     "expect": {"status": "manual", "predicate_satisfied": None}},
    {"name": "decision:insufficient_data 逃逸", "table": None,
     "binding": {"class": "A", "decision": "insufficient_data", "mapping_reason": "本源无此计数器"},
     "expect": {"status": "insufficient_data", "predicate_satisfied": None}},
    {"name": "无数据源→数据不足", "table": None,
     "binding": {"class": "A", "column": "x", "op": "<", "threshold": 1},
     "expect": {"status": "insufficient_data"}},

    # ---- 空表守卫 + filter(EAV 窄表行级预过滤)----
    {"name": "空表(仅表头0行)→数据不足,不当排除", "table": _t(["FaultType"]),
     "binding": {"class": "C", "column": "FaultType", "contains": ["auth"]},
     "expect": {"status": "insufficient_data", "predicate_satisfied": None}},
    {"name": "A+filter 只算被选中的行", "table": _t(["DataItem", "DataValue"],
                                                    ["CPU", "85"], ["CPU", "20"], ["MEM", "90"]),
     "binding": {"class": "A", "filter": {"column": "DataItem", "equals": "CPU"},
                 "column": "DataValue", "op": ">", "threshold": 80, "scope": "count_rows"},
     "expect": {"status": "ok", "count_satisfied": 1, "filter_rows": 2, "predicate_satisfied": True}},
    {"name": "filter 命中0行→数据不足(指标未采集)", "table": _t(["DataItem", "DataValue"], ["CPU", "85"]),
     "binding": {"class": "A", "filter": {"column": "DataItem", "equals": "GPU"},
                 "column": "DataValue", "op": ">", "threshold": 1},
     "expect": {"status": "insufficient_data", "predicate_satisfied": None}},
    {"name": "filter 子句缺 equals/contains→数据不足(不许退化乱配)", "table": _t(["DataItem", "DataValue"], ["CPU", "85"]),
     "binding": {"class": "A", "filter": {"column": "DataItem", "equal": "CPU"},
                 "column": "DataValue", "op": ">", "threshold": 1},
     "expect": {"status": "insufficient_data"}},
    {"name": "filter 子句非对象→数据不足(不崩)", "table": _t(["DataItem", "DataValue"], ["CPU", "85"]),
     "binding": {"class": "A", "filter": "DataItem=CPU", "column": "DataValue", "op": ">", "threshold": 1},
     "expect": {"status": "insufficient_data"}},
    {"name": "D 顶层 filter→数据不足(不静默丢弃)", "table": _t(["DataItem", "DataValue"], ["CPU", "85"]),
     "binding": {"class": "D", "filter": {"column": "DataItem", "equals": "CPU"},
                 "sub": [{"class": "A", "column": "DataValue", "op": ">", "threshold": 80}]},
     "expect": {"status": "insufficient_data"}},
    {"name": "E+list filter→数据不足(内建只吃单子句,不崩)", "table": _t(["DataItem", "DataValue"], ["CPU", "85"]),
     "binding": {"class": "E", "filter": [{"column": "DataItem", "equals": "CPU"}],
                 "group_by": "DataItem", "op": ">", "threshold": 0.1},
     "expect": {"status": "insufficient_data"}},
    {"name": "H 顶层 filter→数据不足(用内建 where)", "table": _t(["msg", "key"], ["Setup", "c1"]),
     "binding": {"class": "H", "filter": {"column": "key", "equals": "c1"},
                 "msg_column": "msg", "key": "key", "has": ["Setup"]},
     "expect": {"status": "insufficient_data"}},

    # ---- 参数形状守卫(P0):绑定写错绝不静默出方向性结论 ----
    {"name": "D 空 sub→数据不足(空 AND 恒成立=假阳)", "table": _t(["x"], ["1"]),
     "binding": {"class": "D", "combine": "AND", "subs": [{"class": "A"}]},
     "expect": {"status": "insufficient_data", "predicate_satisfied": None}},
    {"name": "A 全角 op ≥ 归一后正常算", "table": _t(["x"], ["5"], ["7"]),
     "binding": {"class": "A", "column": "x", "op": "≥", "threshold": 5, "scope": "count_rows", "require": 2},
     "expect": {"status": "ok", "count_satisfied": 2, "predicate_satisfied": True}},
    {"name": "A op 乱写→数据不足", "table": _t(["x"], ["5"]),
     "binding": {"class": "A", "column": "x", "op": "gte", "threshold": 5},
     "expect": {"status": "insufficient_data"}},
    {"name": "A 缺 op→数据不足(旧内核静默无判定)", "table": _t(["x"], ["5"]),
     "binding": {"class": "A", "column": "x", "threshold": 5},
     "expect": {"status": "insufficient_data"}},
    {"name": "A threshold 字符串→数据不足(旧内核当场崩整源)", "table": _t(["x"], ["5"]),
     "binding": {"class": "A", "column": "x", "op": ">", "threshold": "down"},
     "expect": {"status": "insufficient_data"}},
    {"name": "A agg 拼错→数据不足(旧内核静默回退 mean)", "table": _t(["x"], ["5"]),
     "binding": {"class": "A", "column": "x", "op": ">", "threshold": 1, "agg": "avg"},
     "expect": {"status": "insufficient_data"}},
    {"name": "A scope 拼错→数据不足(旧内核静默换统计口径)", "table": _t(["x"], ["5"]),
     "binding": {"class": "A", "column": "x", "op": ">", "threshold": 1, "scope": "rows_count"},
     "expect": {"status": "insufficient_data"}},
    {"name": "A require:0→数据不足(≥0 恒真=假阳)", "table": _t(["x"], ["9"]),
     "binding": {"class": "A", "column": "x", "op": "<", "threshold": 5, "scope": "count_rows", "require": 0},
     "expect": {"status": "insufficient_data"}},
    {"name": "C require:0→数据不足", "table": _t(["c"], ["X"]),
     "binding": {"class": "C", "column": "c", "equals": ["Y"], "require": 0},
     "expect": {"status": "insufficient_data"}},
    {"name": "C match 乱写→数据不足(旧内核静默按 exact)", "table": _t(["c"], ["X"]),
     "binding": {"class": "C", "column": "c", "equals": ["X"], "match": "regex"},
     "expect": {"status": "insufficient_data"}},
    {"name": "F equals 列表→数据不足(旧内核 str(list) 恒不等=假阴)", "table": _t(["p"], ["ON"]),
     "binding": {"class": "F", "param": "p", "equals": ["ON"]},
     "expect": {"status": "insufficient_data"}},
    {"name": "F 缺 equals/not_equals→数据不足(旧内核拿 'None' 比恒排除)", "table": _t(["p"], ["ON"]),
     "binding": {"class": "F", "param": "p"},
     "expect": {"status": "insufficient_data"}},
    {"name": "B 单侧为空→数据不足(旧内核 0-0=0 恒不成立)", "table": _t(["a"], ["3"]),
     "binding": {"class": "B", "minuend": ["a"], "subtrahend": []},
     "expect": {"status": "insufficient_data"}},
    {"name": "H key 列名解析不到→数据不足(旧内核全表并成一个会话)", "table": _t(["MessageType"], ["Setup"]),
     "binding": {"class": "H", "key": "CallId", "msg_column": "MessageType", "has": ["Setup"]},
     "expect": {"status": "insufficient_data"}},
    {"name": "H where 子句缺取值→数据不足(旧内核恒不满足=假排除)", "table": _t(["MessageType", "cause"], ["Fail", "X"]),
     "binding": {"class": "H", "msg_column": "MessageType", "has": ["Fail"],
                 "where": {"column": "cause", "equal": ["X"]}},
     "expect": {"status": "insufficient_data"}},
    {"name": "'nan' 字符串当缺测:混合列只算有效数", "table": _t(["x"], ["nan"], ["5"]),
     "binding": {"class": "A", "column": "x", "op": ">", "threshold": 4, "scope": "aggregate", "agg": "mean"},
     "expect": {"status": "ok", "value": 5.0, "predicate_satisfied": True}},
    {"name": "全 'nan'/'inf' 列→数据不足(旧内核 NaN 毒化 mean 恒排除)", "table": _t(["x"], ["nan"], ["inf"]),
     "binding": {"class": "A", "column": "x", "op": "<", "threshold": 100},
     "expect": {"status": "insufficient_data"}},
]


def _check(case):
    b = case["binding"]
    table = case.get("table")
    sources = {"_t": table} if table else {}
    try:
        fact = evaluate_binding(b, sources, b.get("source", "_t"))
    except Exception as e:  # 内核抛异常 = 直接失败(本不该崩)
        return [f"内核抛异常: {e}"], {}
    errs = []
    for k, v in case.get("expect", {}).items():
        got = fact.get(k)
        if isinstance(v, float) and isinstance(got, (int, float)):
            if abs(got - v) > 1e-9:
                errs.append(f"{k}={got!r}≠{v!r}")
        elif got != v:
            errs.append(f"{k}={got!r}≠{v!r}")
    for k in case.get("present", []):
        if k not in fact:
            errs.append(f"缺分量字段「{k}」")
    return errs, fact


def run_battery():
    """跑全量,返回 (passed, total, 文本行列表)。"""
    lines, passed = [], 0
    for case in CASES:
        errs, _ = _check(case)
        if errs:
            lines.append(f"  ✗ {case['name']}: {'; '.join(errs)}")
        else:
            passed += 1
            lines.append(f"  ✓ {case['name']}")
    return passed, len(CASES), lines


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    passed, total, lines = run_battery()
    print(f"A–J 内核回归自测({total} 例):")
    print("\n".join(lines))
    ok = passed == total
    print(f"\n结论: {'✓ 全部通过 %d/%d' % (passed, total) if ok else '✗ %d/%d 通过,有回归' % (passed, total)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
