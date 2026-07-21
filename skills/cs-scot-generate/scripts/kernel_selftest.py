#!/usr/bin/env python3
"""内核回归自测(A/C/G/D/J/P):每个内核给若干「合成小表 + 绑定 → 期望事实」用例,断言关键字段。

为什么要它:condition_kernels.py 是整条流水线的地基(execute 只跑、不改统计),
但此前它**没有任何回归测试**——谁改坏一行(改了谓词、漏了分量字段、动了解析)都不会有东西报警。
这份用例覆盖每类的 成立/不成立/数据不足/边界(空表、缺列、%值、transform 链、require、三态组合、
regex 匹配、extract 取值、P 成对比较、封存类别拒收),是确定性的(无 ground truth、不调模型、
可反复跑出同一结果)。v1 的 B/E/F/H/I 内核已封存 archive/kernels-v1,对应用例随内核一起封存。

用法:
    kernel_selftest.py            # 跑全量,打印逐条结果,全过 exit 0、有失败 exit 1
    (也可被其它脚本 import:from kernel_selftest import run_battery)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from condition_kernels import evaluate_binding  # 被测对象


def _t(header, *rows):
    return (list(header), [dict(zip(header, r)) for r in rows])


# 每条用例:name / table(None=无源;多源用例改给 sources={源名: table})/ binding
#          / expect(字段=值,需相等) / present(必须出现的字段) / reason_has(reason 里必须含的子串)
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

    # ---- C/G 负存在(absent:故障=「没配置X」,缺行即证据)----
    {"name": "G absent 值确实不存在→成立(带 absent 标记)",
     "table": _t(["level2"], ["vrrp vrid 99 virtual-ip 10.1.1.111"],
                 ["vrrp vrid 99 preempt-mode timer delay 120"], ["shutdown"]),
     "binding": {"class": "G", "column": "level2", "contains": ["recover-delay"], "absent": True},
     "expect": {"status": "ok", "matched": 0, "predicate_satisfied": True, "absent": True}},
    {"name": "G absent 值存在→不成立(配了就不是故障)",
     "table": _t(["level2"], ["vrrp vrid 99 preempt-mode timer delay 120"], ["vrrp recover-delay 10"]),
     "binding": {"class": "G", "column": "level2", "contains": ["recover-delay"], "absent": True},
     "expect": {"status": "ok", "matched": 1, "predicate_satisfied": False}},
    {"name": "G absent 列全空→数据不足(没导出≠没配置,不判成立)",
     "table": _t(["level2"], ["NIL"], ["-"]),
     "binding": {"class": "G", "column": "level2", "contains": ["recover-delay"], "absent": True},
     "expect": {"status": "insufficient_data"}},
    {"name": "C absent+require 互斥→数据不足(语义矛盾拒收)",
     "table": _t(["c"], ["X"]),
     "binding": {"class": "C", "column": "c", "equals": ["X"], "require": 2, "absent": True},
     "expect": {"status": "insufficient_data"}},
    {"name": "D 负存在组合:配了A但没配B→成立(n77 VRRP回切延时实测场景)",
     "table": _t(["level2"], ["vrrp vrid 99 virtual-ip 10.1.1.111"],
                 ["vrrp vrid 99 preempt-mode timer delay 120"], ["shutdown"]),
     "binding": {"class": "D", "combine": "AND", "sub": [
         {"class": "G", "column": "level2", "contains": ["preempt-mode timer delay"]},
         {"class": "G", "column": "level2", "contains": ["recover-delay"], "absent": True}]},
     "expect": {"status": "ok", "predicate_satisfied": True}},

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

    # ---- D 分组组合(group_by:组内 AND,任一组全真才成立)----
    {"name": "D group_by 同组同现→成立且报命中组",
     "table": _t(["level1", "level2"], ["Eth-Trunk0", "cmd A x"], ["Eth-Trunk0", "cmd B y"],
                 ["GE1/0/1", "cmd C z"]),
     "binding": {"class": "D", "combine": "AND", "group_by": "level1", "sub": [
         {"class": "G", "column": "level2", "contains": ["cmd A"]},
         {"class": "G", "column": "level2", "contains": ["cmd B"]}]},
     "expect": {"status": "ok", "predicate_satisfied": True, "matched_groups": ["Eth-Trunk0"]}},
    {"name": "D group_by 跨组各现→不成立(整列绑定的假确证被治住)",
     "table": _t(["level1", "level2"], ["Eth-Trunk0", "cmd A x"], ["GE1/0/1", "cmd B y"]),
     "binding": {"class": "D", "combine": "AND", "group_by": "level1", "sub": [
         {"class": "G", "column": "level2", "contains": ["cmd A"]},
         {"class": "G", "column": "level2", "contains": ["cmd B"]}]},
     "expect": {"status": "ok", "predicate_satisfied": False}},
    {"name": "D group_by 组内负存在:本接口配A没配B→成立(他接口有B不掩盖)",
     "table": _t(["level1", "level2"], ["Eth-Trunk0", "vrrp preempt-mode timer delay 20"],
                 ["GE1/0/1", "vrrp recover-delay 10"]),
     "binding": {"class": "D", "combine": "AND", "group_by": "level1", "sub": [
         {"class": "G", "column": "level2", "contains": ["preempt-mode timer delay"]},
         {"class": "G", "column": "level2", "contains": ["recover-delay"], "absent": True}]},
     "expect": {"status": "ok", "predicate_satisfied": True, "matched_groups": ["Eth-Trunk0"]}},
    {"name": "D group_by 组数据不足传播(sub 缺列)",
     "table": _t(["level1", "level2"], ["Eth-Trunk0", "x"]),
     "binding": {"class": "D", "combine": "AND", "group_by": "level1", "sub": [
         {"class": "G", "column": "没有的列", "contains": ["A"]}]},
     "expect": {"status": "insufficient_data"}},
    {"name": "D group_by 分组列全空→数据不足",
     "table": _t(["level1", "level2"], ["NIL", "x"], ["-", "y"]),
     "binding": {"class": "D", "combine": "AND", "group_by": "level1", "sub": [
         {"class": "G", "column": "level2", "contains": ["x"]}]},
     "expect": {"status": "insufficient_data"}},
    {"name": "D group_by 跨源 sub→拒(跨源分组无定义)",
     "table": _t(["level1", "level2"], ["a", "x"]),
     "binding": {"class": "D", "combine": "AND", "group_by": "level1", "sub": [
         {"class": "G", "column": "level2", "contains": ["x"], "source": "别的源"}]},
     "expect": {"status": "insufficient_data"}},

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
    # C/G 缺 contains/equals/regex(op/target 等野字段)→ 响亮数据不足,绝不静默 matched=0 变假排除
    {"name": "C/G 空 targets→数据不足(op/target 野字段不认,防假排除)",
     "table": _t(["Summary"], ["hwLinkHeartbeatDropAlarm_active"]),
     "binding": {"class": "C", "column": "Summary", "op": "contains", "target": "hwLinkHeartbeatDropAlarm"},
     "expect": {"status": "insufficient_data", "predicate_satisfied": None}},

    # ---- C/G 正则匹配(regex:判据自带正则的家;不 lower、按 re.search)----
    {"name": "G regex 命中→成立(\\d+ 匹配变化次数)",
     "table": _t(["log"], ["Interface Eth-Trunk0 turned into DOWN state 5 times"], ["normal"]),
     "binding": {"class": "G", "column": "log", "regex": ["DOWN state \\d+ times"]},
     "expect": {"status": "ok", "match": "regex", "matched": 1, "predicate_satisfied": True}},
    {"name": "G regex 不命中→不成立",
     "table": _t(["log"], ["all links healthy"]),
     "binding": {"class": "G", "column": "log", "regex": ["DOWN state \\d+ times"]},
     "expect": {"status": "ok", "matched": 0, "predicate_satisfied": False}},
    {"name": "G regex 大小写敏感(contains 会 lower,regex 不会)",
     "table": _t(["log"], ["down detected"]),
     "binding": {"class": "G", "column": "log", "regex": ["DOWN"]},
     "expect": {"status": "ok", "matched": 0, "predicate_satisfied": False}},
    {"name": "G 坏正则→数据不足(判据原文的 [ 未转义,编译不过响亮拒收)",
     "table": _t(["log"], ["[ALM-123] port down"]),
     "binding": {"class": "G", "column": "log", "regex": ["[ALM-123"]},
     "expect": {"status": "insufficient_data", "predicate_satisfied": None},
     "reason_has": "编译失败"},
    {"name": "C match 乱写→数据不足(只认 exact/contains/regex)", "table": _t(["c"], ["X"]),
     "binding": {"class": "C", "column": "c", "equals": ["X"], "match": "fuzzy"},
     "expect": {"status": "insufficient_data"}},

    # ---- 取值层 extract(格内杂糅文本先抽值再判,贯穿 A/C/G/P)----
    {"name": "G extract kv 抽出值再精确匹配(状态:Down)",
     "table": _t(["info"], ["状态:Down,速率:1G"], ["状态:Up,速率:1G"]),
     "binding": {"class": "G", "column": "info", "extract": {"kv": "状态"}, "equals": ["Down"]},
     "expect": {"status": "ok", "matched": 1, "total": 2, "predicate_satisfied": True},
     "present": ["extract"]},
    {"name": "G extract 抽不到的行计 nulls(没这项信息≠不命中)",
     "table": _t(["info"], ["状态:Up"], ["garbage no key"]),
     "binding": {"class": "G", "column": "info", "extract": {"kv": "状态"}, "equals": ["Up"]},
     "expect": {"status": "ok", "matched": 1, "total": 1, "nulls": 1, "predicate_satisfied": True}},
    {"name": "A extract kv 抽数比阈值(利用率:83% > 70)",
     "table": _t(["perf"], ["利用率:83%,速率:1G"], ["利用率:20%,速率:1G"]),
     "binding": {"class": "A", "column": "perf", "extract": {"kv": "利用率"},
                 "op": ">", "threshold": 70, "scope": "count_rows", "require": 1},
     "expect": {"status": "ok", "count_satisfied": 1, "predicate_satisfied": True}},
    {"name": "A extract regex 捕获组抽数(rx power dBm)",
     "table": _t(["opt"], ["rx power -3.5 dBm ok"]),
     "binding": {"class": "A", "column": "opt", "extract": {"regex": "(-?\\d+\\.?\\d*) dBm"},
                 "op": "<", "threshold": 0, "scope": "aggregate", "agg": "mean"},
     "expect": {"status": "ok", "value": -3.5, "predicate_satisfied": True}},
    {"name": "A extract kv 千分位计数(CRC: 1,234 → 1234,不截在逗号前)",
     "table": _t(["echo"], ["CRC: 1,234 packets"], ["CRC: 0 packets"]),
     "binding": {"class": "A", "column": "echo", "extract": {"kv": "CRC"},
                 "op": ">", "threshold": 0, "scope": "count_rows", "require": 1},
     "expect": {"status": "ok", "count_satisfied": 1, "predicate_satisfied": True}},
    {"name": "A extract regex 千分位归一(12,345 → 12345)",
     "table": _t(["echo"], ["Symbol: 12,345 packets"]),
     "binding": {"class": "A", "column": "echo", "extract": {"regex": "Symbol:\\s*([\\d,]+)"},
                 "op": ">", "threshold": 12000, "scope": "aggregate", "agg": "max"},
     "expect": {"status": "ok", "value": 12345.0, "predicate_satisfied": True}},
    {"name": "A extract 一条也抽不到→数据不足(带该列样例)",
     "table": _t(["perf"], ["no such key here"]),
     "binding": {"class": "A", "column": "perf", "extract": {"kv": "利用率"}, "op": ">", "threshold": 1},
     "expect": {"status": "insufficient_data"}, "reason_has": "一条也没抽到"},
    {"name": "extract 规格错(kv+regex 同给)→数据不足",
     "table": _t(["c"], ["x:1"]),
     "binding": {"class": "G", "column": "c", "extract": {"kv": "x", "regex": "y"}, "contains": ["1"]},
     "expect": {"status": "insufficient_data"}},
    {"name": "extract.regex 编译失败→数据不足(printf 占位 %u 不是正则的错法之一)",
     "table": _t(["c"], ["x[1]"]),
     "binding": {"class": "A", "column": "c", "extract": {"regex": "x[\\d"}, "op": ">", "threshold": 0},
     "expect": {"status": "insufficient_data"}, "reason_has": "编译失败"},

    # ---- P 成对比较(本端/对端同一信息是否一致)----
    {"name": "P 两侧不一致→成立(fault_when 默认 not_equal)",
     "table": _t(["Object", "ip"], ["MLAG*", "10.1.1.1"], ["MLAG", "10.1.1.2"]),
     "binding": {"class": "P", "side_column": "Object", "value_column": "ip"},
     "expect": {"status": "ok", "local": "10.1.1.1", "peer": "10.1.1.2", "equal": False,
                "predicate_satisfied": True}},
    {"name": "P 两侧一致→不成立",
     "table": _t(["Object", "ip"], ["MLAG*", "10.1.1.1"], ["MLAG", "10.1.1.1"]),
     "binding": {"class": "P", "side_column": "Object", "value_column": "ip"},
     "expect": {"status": "ok", "equal": True, "predicate_satisfied": False}},
    {"name": "P 对端缺→数据不足(查不到对端是判不了,不是不成立)",
     "table": _t(["Object", "ip"], ["MLAG*", "10.1.1.1"]),
     "binding": {"class": "P", "side_column": "Object", "value_column": "ip"},
     "expect": {"status": "insufficient_data", "predicate_satisfied": None},
     "reason_has": "对端"},
    {"name": "P 一侧多个不同值→数据不足(歧义,先 filter 缩行)",
     "table": _t(["Object", "ip"], ["MLAG*", "10.1.1.1"], ["MLAG*", "10.9.9.9"], ["MLAG", "10.1.1.2"]),
     "binding": {"class": "P", "side_column": "Object", "value_column": "ip"},
     "expect": {"status": "insufficient_data"}, "reason_has": "歧义"},
    {"name": "P fault_when 乱写→数据不足",
     "table": _t(["Object", "ip"], ["MLAG*", "1"], ["MLAG", "2"]),
     "binding": {"class": "P", "side_column": "Object", "value_column": "ip", "fault_when": "differ"},
     "expect": {"status": "insufficient_data"}},
    {"name": "P + extract kv:从杂糅格抽 ip 再比;fault_when:equal 反向",
     "table": _t(["Object", "detail"], ["MLAG*", "ip:10.1.1.1;mask:24"], ["MLAG", "ip:10.1.1.1;mask:25"]),
     "binding": {"class": "P", "side_column": "Object", "value_column": "detail",
                 "extract": {"kv": "ip"}, "fault_when": "equal"},
     "expect": {"status": "ok", "local": "10.1.1.1", "peer": "10.1.1.1", "equal": True,
                "predicate_satisfied": True}},
    {"name": "P + filter:先选中检查项的行再分侧比较",
     "table": _t(["item", "Object", "val"], ["ip", "MLAG*", "1.1.1.1"], ["ip", "MLAG", "2.2.2.2"],
                 ["ver", "MLAG*", "V1"], ["ver", "MLAG", "V1"]),
     "binding": {"class": "P", "filter": {"column": "item", "equals": "ip"},
                 "side_column": "Object", "value_column": "val"},
     "expect": {"status": "ok", "local": "1.1.1.1", "peer": "2.2.2.2", "filter_rows": 2,
                "predicate_satisfied": True}},

    # ---- filter excludes(不含才保留:挑「Object 不带 *」的对端行)----
    {"name": "filter excludes 选出不含标记的行",
     "table": _t(["Object", "val"], ["MLAG*", "1"], ["MLAG", "2"]),
     "binding": {"class": "C", "filter": {"column": "Object", "excludes": "*"},
                 "column": "val", "equals": ["2"], "match": "exact"},
     "expect": {"status": "ok", "matched": 1, "total": 1, "predicate_satisfied": True}},
    {"name": "filter 子句 equals+excludes 同给→数据不足(歧义拒收)",
     "table": _t(["Object", "val"], ["A", "1"]),
     "binding": {"class": "C", "filter": {"column": "Object", "equals": "A", "excludes": "*"},
                 "column": "val", "contains": ["1"]},
     "expect": {"status": "insufficient_data"}},

    # ---- param_from 参数化查询(X:先抽参数、再代入)----
    {"name": "param_from 单跳:回显抽接口名→查该接口配置(peer-link 链)",
     "table": None, "sources": {
        "cmd": _t(["CmdName", "CmdResult"], ["display dfs-group 1 peer-link", "Port Name: Eth-Trunk0"]),
        "config": _t(["level1", "level2"], ["Eth-Trunk0", "mode lacp-static"], ["GE1/0/1", "x"])},
     "binding": {"class": "G", "source": "config", "column": "level2", "contains": ["mode lacp-static"],
                 "filter": {"column": "level1", "equals": "@plink"},
                 "param_from": {"plink": {"source": "cmd", "column": "CmdResult",
                                          "filter": {"column": "CmdName", "contains": "peer-link"},
                                          "extract": {"kv": "Port Name"}}}},
     "expect": {"status": "ok", "matched": 1, "predicate_satisfied": True,
                "params": {"plink": "Eth-Trunk0"}}},
    {"name": "param_from 多跳:Vbdif→bridge-domain 号→查 BD 配置",
     "table": _t(["level1", "level2"],
                 ["Vbdif100", "bridge-domain: 20"], ["bd 20", "vxlan vni 5000"]),
     "binding": {"class": "G", "column": "level2", "contains": ["vxlan vni"],
                 "filter": {"column": "level1", "equals": "@bdblk"},
                 "param_from": {
                     "vbdif": {"column": "level1", "extract": {"regex": "(Vbdif\\d+)"}},
                     "bdnum": {"column": "level2", "filter": {"column": "level1", "equals": "@vbdif"},
                               "extract": {"kv": "bridge-domain"}},
                     "bdblk": {"column": "level1", "extract": {"regex": "(bd \\d+)"},
                               "filter": {"column": "level1", "contains": "@bdnum"}}}},
     "expect": {"status": "ok", "matched": 1, "predicate_satisfied": True}},
    {"name": "param_from 参数抽不到→数据不足(不是不成立)",
     "table": _t(["a", "b"], ["x", "y"]),
     "binding": {"class": "G", "column": "b", "contains": ["y"],
                 "filter": {"column": "a", "equals": "@p"},
                 "param_from": {"p": {"column": "a", "extract": {"kv": "没有的键"}}}},
     "expect": {"status": "insufficient_data", "predicate_satisfied": None}, "reason_has": "抽取失败"},
    {"name": "param_from 参数多值无 expand→数据不足(歧义)",
     "table": _t(["a", "b"], ["v1", "x"], ["v2", "x"]),
     "binding": {"class": "G", "column": "b", "contains": ["@p"],
                 "param_from": {"p": {"column": "a"}}},
     "expect": {"status": "insufficient_data"}, "reason_has": "歧义"},
    {"name": "@引用未声明参数→数据不足(防拿字面 @ 查出假排除)",
     "table": _t(["a"], ["x"]),
     "binding": {"class": "G", "column": "a", "contains": ["@nobody"],
                 "param_from": {"p": {"column": "a"}}},
     "expect": {"status": "insufficient_data"}, "reason_has": "没有声明"},
    {"name": "写了 @xxx 却没 param_from→数据不足(响亮拦)",
     "table": _t(["a"], ["x"]),
     "binding": {"class": "G", "column": "a", "contains": ["@p"]},
     "expect": {"status": "insufficient_data"}, "reason_has": "没有 param_from"},
    {"name": "参数引用排在后面的参数→数据不足(只能向前引用)",
     "table": _t(["a"], ["x"]),
     "binding": {"class": "G", "column": "a", "contains": ["x"],
                 "param_from": {"p1": {"column": "a", "filter": {"column": "a", "equals": "@p2"}},
                                "p2": {"column": "a"}}},
     "expect": {"status": "insufficient_data"}, "reason_has": "排在前面"},
    {"name": "expand all:每个接口都要有配置,一个缺→不成立并点名",
     "table": _t(["level1", "level2"], ["Vbdif100", "bridge-domain 10"], ["Vbdif200", "shutdown"]),
     "binding": {"class": "G", "column": "level2", "contains": ["bridge-domain"],
                 "filter": {"column": "level1", "equals": "@ifc"},
                 "param_from": {"ifc": {"column": "level1", "extract": {"regex": "(Vbdif\\d+)"},
                                        "expand": "all"}}},
     "expect": {"status": "ok", "predicate_satisfied": False, "fail_values": ["Vbdif200"]}},
    {"name": "expand any:任一值命中即成立",
     "table": _t(["level1", "level2"], ["Vbdif100", "x"], ["Vbdif200", "vxlan anycast-gateway enable"]),
     "binding": {"class": "G", "column": "level2", "contains": ["anycast-gateway"],
                 "filter": {"column": "level1", "equals": "@ifc"},
                 "param_from": {"ifc": {"column": "level1", "expand": "any"}}},
     "expect": {"status": "ok", "predicate_satisfied": True, "hit_values": ["Vbdif200"]}},
    {"name": "expand 乱写→数据不足", "table": _t(["a"], ["x"]),
     "binding": {"class": "G", "column": "a", "contains": ["x"],
                 "param_from": {"p": {"column": "a", "expand": "each"}}},
     "expect": {"status": "insufficient_data"}},
    {"name": "两个参数都带 expand→数据不足(组合爆炸拒收)",
     "table": _t(["a", "b"], ["x", "y"]),
     "binding": {"class": "G", "column": "b", "contains": ["@q"],
                 "param_from": {"p": {"column": "a", "expand": "all"},
                                "q": {"column": "b", "expand": "any"}}},
     "expect": {"status": "insufficient_data"}},

    # ---- 评审整改回归(tuple/set 逃逸、规格白名单、excludes 空行、sub 作用域、字面@值、pair_from 顶层 filter)----
    {"name": "tuple 目标里的 @ 也被守卫拦(圆括号写法不逃逸)",
     "table": _t(["a"], ["x"]),
     "binding": {"class": "G", "column": "a", "contains": ("@p",)},
     "expect": {"status": "insufficient_data"}, "reason_has": "没有 param_from"},
    {"name": "set 目标里的 @ 也被代入(花括号写法不逃逸)",
     "table": _t(["a"], ["Eth-Trunk0"]),
     "binding": {"class": "G", "column": "a", "contains": {"@p"},
                 "param_from": {"p": {"column": "a", "extract": {"regex": "(Eth-Trunk\\d+)"}}}},
     "expect": {"status": "ok", "matched": 1, "predicate_satisfied": True}},
    {"name": "取值规格键拼错(extrct)→数据不足(白名单拒收,不静默降级)",
     "table": _t(["a", "b"], ["k: v", "x"]),
     "binding": {"class": "G", "column": "b", "contains": ["@p"],
                 "param_from": {"p": {"column": "a", "extrct": {"kv": "k"}}}},
     "expect": {"status": "insufficient_data"}, "reason_has": "不认识的键"},
    {"name": "excludes 不保留过滤列为空的行(脏行不混进对端)",
     "table": _t(["Object", "val"], ["MLAG*", "1"], ["", "脏行"], ["MLAG", "2"]),
     "binding": {"class": "C", "filter": {"column": "Object", "excludes": "*"},
                 "column": "val", "equals": ["2"], "match": "exact"},
     "expect": {"status": "ok", "matched": 1, "total": 1, "filter_rows": 1,
                "predicate_satisfied": True}},
    {"name": "D 的 sub 自带 param_from 可用(自成作用域,不被顶层守卫误杀)",
     "table": None, "sources": {
        "cmd": _t(["CmdName", "CmdResult"], ["display dfs-group 1 peer-link", "Port Name: Eth-Trunk0"]),
        "config": _t(["level1", "level2"], ["Eth-Trunk0", "mode lacp-static"])},
     "binding": {"class": "D", "combine": "AND", "source": "config", "sub": [
         {"class": "G", "source": "config", "column": "level2", "contains": ["mode lacp-static"],
          "filter": {"column": "level1", "equals": "@plink"},
          "param_from": {"plink": {"source": "cmd", "column": "CmdResult",
                                   "extract": {"kv": "Port Name"}}}}]},
     "expect": {"status": "ok", "predicate_satisfied": True}},
    {"name": "参数值本身长得像 @xxx→按字面参与匹配(不被二次解释误杀)",
     "table": _t(["alias", "log"], ["@peer1", "seen @peer1 down"]),
     "binding": {"class": "G", "column": "log", "contains": ["@p"],
                 "param_from": {"p": {"column": "alias"}}},
     "expect": {"status": "ok", "matched": 1, "predicate_satisfied": True}},
    {"name": "P pair_from 顶层 filter→数据不足(不静默忽略)",
     "table": None, "sources": {"a": _t(["c"], ["x: 1"]), "b": _t(["c"], ["x: 1"])},
     "binding": {"class": "P", "source": "a", "filter": {"column": "c", "contains": "x"},
                 "pair_from": {"local": {"source": "a", "column": "c", "extract": {"kv": "x"}},
                               "peer": {"source": "b", "column": "c", "extract": {"kv": "x"}}}},
     "expect": {"status": "insufficient_data"}, "reason_has": "两侧规格"},
    {"name": "source 误写 cross→数据不足并点破(补绑格式错误,不是缺数据)",
     "table": None, "sources": {"config": _t(["a"], ["x"])},
     "binding": {"class": "G", "source": "cross", "column": "a", "contains": ["x"]},
     "expect": {"status": "insufficient_data"}, "reason_has": "真实源名"},

    # ---- P pair_from(跨源成对比较:两侧各自选行取值)----
    {"name": "P pair_from 两文件比 Nve source 不一致→成立",
     "table": None, "sources": {
        "cfg_a": _t(["level1", "level2"], ["Nve1", "source: 1.1.1.1"]),
        "cfg_b": _t(["level1", "level2"], ["Nve1", "source: 2.2.2.2"])},
     "binding": {"class": "P", "source": "cfg_a", "fault_when": "not_equal", "pair_from": {
         "local": {"source": "cfg_a", "column": "level2",
                   "filter": {"column": "level1", "equals": "Nve1"}, "extract": {"kv": "source"}},
         "peer": {"source": "cfg_b", "column": "level2",
                  "filter": {"column": "level1", "equals": "Nve1"}, "extract": {"kv": "source"}}}},
     "expect": {"status": "ok", "local": "1.1.1.1", "peer": "2.2.2.2", "equal": False,
                "predicate_satisfied": True}},
    {"name": "P pair_from 对端源缺→数据不足",
     "table": None, "sources": {"cfg_a": _t(["level1", "level2"], ["Nve1", "source: 1.1.1.1"])},
     "binding": {"class": "P", "source": "cfg_a", "pair_from": {
         "local": {"source": "cfg_a", "column": "level2", "extract": {"kv": "source"}},
         "peer": {"source": "cfg_b", "column": "level2", "extract": {"kv": "source"}}}},
     "expect": {"status": "insufficient_data"}, "reason_has": "对端"},
    {"name": "cross_source 标记漏到运行时→数据不足并指路",
     "table": _t(["a"], ["x"]),
     "binding": {"class": "G", "decision": "cross_source", "column": "a", "contains": ["x"]},
     "expect": {"status": "insufficient_data"}, "reason_has": "补绑"},

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

    # ---- 封存/未知类别:响亮拒收,绝不静默 ----
    {"name": "封存类别 F→数据不足并指路(v1 内核在 archive)", "table": _t(["p"], ["ON"]),
     "binding": {"class": "F", "param": "p", "equals": "ON"},
     "expect": {"status": "insufficient_data", "predicate_satisfied": None},
     "reason_has": "封存"},
    {"name": "封存类别 H→数据不足并指路", "table": _t(["CallID", "MessageType"], ["1", "Setup"]),
     "binding": {"class": "H", "key": "CallID", "msg_column": "MessageType", "has": ["Setup"]},
     "expect": {"status": "insufficient_data"}, "reason_has": "封存"},
    {"name": "未知类别 Z→数据不足(只认 A/C/D/G/J/P)", "table": _t(["x"], ["1"]),
     "binding": {"class": "Z", "column": "x"},
     "expect": {"status": "insufficient_data"}, "reason_has": "只认"},

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
    sources = case.get("sources") or ({"_t": table} if table else {})
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
    if case.get("reason_has") and case["reason_has"] not in str(fact.get("reason", "")):
        errs.append(f"reason 缺子串「{case['reason_has']}」(实际:{str(fact.get('reason'))[:60]!r})")
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
    print(f"内核回归自测({total} 例,A/C/G/D/J/P):")
    print("\n".join(lines))
    ok = passed == total
    print(f"\n结论: {'✓ 全部通过 %d/%d' % (passed, total) if ok else '✗ %d/%d 通过,有回归' % (passed, total)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
