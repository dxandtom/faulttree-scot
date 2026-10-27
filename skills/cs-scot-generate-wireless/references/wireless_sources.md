# 无线场景的数据源与绑定写法

本文说明无线接入类场景（NSA/SA）常见的四种数据源各是什么样的表、判据怎么对应到内核类别，并给出每类的例子。类别与参数的完整定义见 `condition_binding_schema.md`，本文只讲"无线判据怎么选类、怎么写"。

## 1. 话统（KPI）

按小区或统计周期的计数器宽表，一列一个计数器，列名常带组前缀（如 `Common:N.QosFlow.Est.Att`），内核按首个冒号剥前缀，绑定写裸名即可。

- 阈值判据（"成功率低于 95%""RTWP 大于 -85"）用 A 类：

```json
{"node_id": "n12", "class": "A", "column": "RRC Setup Success Rate", "op": "<", "threshold": 95,
 "scope": "count_rows", "require": 1, "mapping_reason": "判据=成功率低于 95%,逐周期判,出现一次即成立"}
```

- 原始计数相减（"N.QosFlow.Est.Att − N.QosFlow.Est.Succ > 0"）用 B 类，两侧都是原始计数列；`transforms` 可写 `log10`、`scale:10` 做主分集功率差这类判据：

```json
{"node_id": "n2", "class": "B", "minuend": ["N.QosFlow.Est.Att"], "subtrahend": ["N.QosFlow.Est.Succ"],
 "op": ">", "threshold": 0, "scope": "aggregate", "agg": "sum", "mapping_reason": "判据=尝试数减成功数大于 0,按周期合计后相减"}
```

数据里只有成功率、没有原始计数时不要硬绑：写 `decision: insufficient_data` 并说明缺哪两列。

## 2. CHR（逐记录测量）

一行一条呼叫或会话记录，有错误类型（`DCErrType`）、`RelRsrp`、`RelSinr` 这类列。

- 取值匹配用 C 类，阈值计数用 A 类，两者联合用 D 类：

```json
{"node_id": "n3", "class": "D", "combine": "AND", "sub": [
  {"class": "C", "column": "DCErrType", "equals": ["RNL_Unspecified"]},
  {"class": "A", "column": "RelSinr", "op": "<", "threshold": 0, "scope": "count_rows"}],
 "mapping_reason": "判据=同时满足错误类型为 RNL_Unspecified 且 SINR 小于 0"}
```

- 占比判据（"某类终端占比超过 30%""RrcReject 占失败原因的比例大于 30%"）用 E 类，`threshold` 是 0 到 1 的比例，可先用 `filter` 选行：

```json
{"node_id": "n9", "class": "E", "filter": {"column": "DCErrType", "contains": "Rrc"},
 "group_by": "DCErrType", "op": ">", "threshold": 0.3, "mapping_reason": "判据=RrcReject 占比大于 30%,先选 Rrc 类错误再按类型分组看 top1 占比"}
```

E 类报 `top1`、`top1_count`、`top1_ratio`，判定看 top1 组的占比；判据指定的是某一个具体取值的占比时，用 `filter` 把行限定到该取值所在的范围，再核对 `top1` 是不是它。

## 3. 信令 trace

一行一条消息，带会话键列（如 `CallID`）和消息类型列（如 `MessageType`），可能还有原因值列。

- "会话缺某消息""出现某失败消息且携带 cause=X"用 H 类，`has` 是须出现的消息，`lacks` 是须不出现的，`where` 要求同一行同时满足若干列值：

```json
{"node_id": "n21", "class": "H", "key": "CallID", "msg_column": "MessageType",
 "has": ["RRCSetupFailure"], "lacks": ["RRCSetupComplete"],
 "where": {"column": "cause", "contains": ["congestion"]},
 "mapping_reason": "判据=建立失败且原因为拥塞,按会话看消息集合"}
```

`has` 里的消息名在整列从未出现时内核判数据不足并附词汇样例，多半是消息名写法与数据不符，按样例改绑定。顶层不收 `filter`，会话内的行级约束写在 `where`。

## 4. 配置（MO 文本）

`GNBCUS1:MOName=GNBCUS1_1,CellId=CELL_1;` 这种 MO 配置文本不是表，`scripts/mo_config.py` 会把它读成 `(header, rows)`，列是 MO 类型和各参数名。

- "参数等于期望值"用 C 类 `equals`（整格）或加 `extract` 取格内字段；跨 MO 追溯、IP 子网判断在 `custom` 函数里用 `mo_config` 的原语（`parse_mo_grouped`、`mo_get`、`ipv4_in_subnet` 等）。

## 5. 常见误绑

- 把成功率当原始计数去相减：B 类两侧必须是计数列。
- 把 E 类的 `threshold` 写成百分数：它是 0 到 1 的比例，30% 写 0.3。
- H 类写了顶层 `filter`：内核拒收，改写 `where`。
- 判据是"存在一次越线"却用 A 类 `aggregate` 取平均：用 `count_rows` 加 `require`。
