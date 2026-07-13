# 数通「诊断文件包」场景适配 —— 7 类 CSV 模板怎么接、怎么绑

数通(路由器/交换机,如 ME60 BRAS)场景的输入是**一包 CSV 模板**(由采集工具从原始日志/命令回显
转换而来),配「混血」格式故障树(v2 层级列 + v1 的 `Judgment Condition(Chinese)` 单判据列,条件文本含 HTML;**统一走 excel_to_tree_v2.py**,见硬事实 2)。
本文是这套输入的接入手册:每类文件长什么样、用哪类内核、哪里有坑。

## 一、先记三条硬事实(实测钉死的)
1. **多行回显是规范 CSV**:cmd/diag 的命令输出被引号正确包裹(一条记录跨几百物理行),
   `load_table` 已内置调大 csv 单字段上限(默认 128KB 会当场炸),**直读即可,不需要任何重组预处理**。
   判据:`csv 记录数 ≪ 物理行数` = 引号多行;`≈` = 普通规整表。
2. **故障树是「混血」格式,统一走 v2**:数通树的层级列是 v2 风格(同层常有 `L1` 与 `L1(Chinese)` 双列),
   判据列却是 v1 风格的 `Judgment Condition(Chinese)` 单列。`excel_to_tree_v2.py` 已原生支持:
   同层双列只取中文列(否则中英文名会被串成两层、树深翻倍),通用判据列按「[通用]」标签挂上
   (路由靠模型读判据原文),HTML 清洗(`&nbsp;`/`<strong>`/实体转义,保留 `<>` 运算符)复用 v1。
   从 L1 起根用 `--start-level 1`(换起始层级后 node_id 整体重排,tree_index 必须重生成、绑定 node_id 跟着重填)。
   **建完必须核对输出里的「带判据节点 N」:N=0 → 停**(格式没被识别,assemble 也会硬拦),别往下绑定。
3. **空表 ≠ 排除**:有的模板可能只有表头、0 行数据(如 BasInfo 空壳)。内核对空表(所有读表类)、
   `filter` 命中 0 行(A/B/C/F/G/I)自动出「数据不足」——别手工绕过这个语义。
   (例外:E 类内建 filter 语义独立——单子句、大小写敏感、命中 0 行按"排除"算,见 binding schema。)

## 二、7 类文件 → 内核对号表

| 文件(示例名) | 形态 | 主要判据落点 | 用什么 |
|---|---|---|---|
| `*.txt.csv`(cmd,命令+完整回显) | 引号多行 CSV,一条记录=一条命令,`CmdResult`=整段回显 | 回显里的告警/事件/状态 | 存在类→G;计数器类→custom+`extract_metric`(见三) |
| `*_CmdResult.csv`(diag,命令明细) | 引号多行 CSV,**EAV 窄表**:`DataItem`(≈282 种指标名)/`DataValue`/`Unit` | 各类指标值判断 | **A/C + `filter`**(见四) |
| `*_diagfile.csv`(config,运行配置) | 规整表,层次列 `Module→level1→L2Module→level2…` | 配置项存在/取值 | C/F 绑 level 列;深层路径 custom(可借 `mo_config` 原语) |
| `diaglog_*.log.csv`(dblg,诊断日志) | 规整表,`Module/Level/Summary` 码值列 + `Content/Log` 文本 | 日志码/告警名 | **C/G 绑 `Summary`**(首选,枚举干净);正文细节才用 G 绑 `Content` |
| `log.log.csv`(log,运行日志) | 同 dblg 结构 | 同上 | 同上 |
| `log.log.opr.csv`(opr,操作记录) | 规整小表 | 操作审计 | C(诊断价值有限) |
| `*_BasInfo.csv`(bas,用户会话) | 规整表,可能**空壳** | `FaultType/FaultReason` | C 直绑;空壳自动数据不足 |

## 三、cmd 回显的两类判据(红线,别混)
> 通用规矩见 binding schema「C/G 的 contains 三条铁规」:每个 target 必须独立是充分信号,
> 禁标签/命令名/协议名泛词;否定语义禁 G;趋势语义看有没有检测告警码。
- **「存在某告警/事件名」**(如回显出现 `hwBaseSocAttackTrap`):G 类 `contains` **可用**——
  告警名只在发生时才出现在回显里,子串命中即事实。**优先级:这类判据先绑 log/dblg 的 `Summary`
  码值列和 diag 的 `Alarm*` 结构化字段(EAV filter),cmd 回显只当兜底**(回显里捞子串最糙)。
- **「某计数器异常」**(如 CRC 错包>0):**禁用 G**——`CRC:` 标签在回显里**恒在**,值为 0 的健康
  回显也会子串命中(假阳)。必须 custom 抽值再比:
```python
from mo_config import extract_metric, fact_ok, fact_insufficient

def _crc_error(table):                       # 判据:display interface 回显有 CRC 错包
    header, rows = table
    vals = [v for r in rows if "display interface" in str(r.get("CmdName", ""))
            for v in extract_metric(r.get("CmdResult"), r"CRC:\s*([\d,]+)\s*packets")]
    if not vals:
        return fact_insufficient("回显里未抽到 CRC 计数(该命令未采集或格式变了)",
                                 resolved_column="CmdResult")
    bad = sum(1 for v in vals if v > 0)
    return fact_ok(satisfied=bad > 0, resolved_column="CmdResult",
                   count_satisfied=bad, total=len(vals), value=max(vals))

CUSTOM = {"L4-crc": _crc_error}
# 绑定(class 与 mapping_reason 必写,缺了过不了 validate_conditions 闸门;
#      fact 也必须带 resolved_column 之类的溯源痕迹,否则触发"无中生有"检查):
# {"id":"L4-crc","node":"接口CRC错包","node_id":"nX","condition_text":"…","class":"A",
#  "mode":"custom","source":"cmd",
#  "mapping_reason":"树要 display interface 回显的 CRC 错包计数;标签恒在,禁 G 子串,custom 抽值比较"}
```
`Symbol/Pause/Overrun` 等同理(换正则);`extract_metric` 自动吃逗号千分位、大小写不敏感。

## 四、diag(EAV 窄表)的标准绑法
表是「一行一个量」:`DataItem` 说这行是什么指标,`DataValue` 是值。`filter` 先选量、`column` 指值:
```python
# A 类:某指标值越限(如 CPU 占用率 > 80)
{"id":"L3-cpu","node":"CPU过载","node_id":"nY","condition_text":"…","class":"A","source":"diag",
 "filter":{"column":"DataItem","equals":"SYS_CpuUsage"},
 "column":"DataValue","op":">","threshold":80,"scope":"count_rows"}

# C 类:某指标取值命中(如单板状态字段含 Abnormal)
{"id":"L3-board","node":"单板异常","node_id":"nZ","condition_text":"…","class":"C","source":"diag",
 "filter":{"column":"DataItem","equals":"SYS_BoardStatus"},
 "column":"DataValue","contains":["Abnormal"]}
```
- `filter` 命中 0 行 = 该指标本次未采集 → 自动数据不足(不是"不成立")。
- 选 `DataItem` 名字时**以概览/实际枚举为准**(先跑 `analyzer.py` 看 distinct),别按树正文猜。
- 多个指标同判一个节点 → D 类组合(见 binding schema「一个节点多条判据怎么绑」)。
- **"diag 一条都不用绑"这种全空结论,必须先把 `DataItem` 枚举对着树判据逐个核过再下**
  (`Alarm*`/`BFD_*`/`INTF_*`/`FIB_*`/`SYS_*` 这些结构化字段常能替代回显捞子串),
  并在汇报里附上"核对了哪些、为什么都不适用"——不许因为 cmd 好绑就把 diag 一笔带过。

## 五、日志类(dblg/log)绑定要点
- 首选 **`Summary` 列**(告警/日志码的干净枚举,如 `RDS_ACCT_SERVER_PROB`、`hwArpsGatewayConflict`):
  C 类 `equals`/`contains`,树驱动逐叶子绑,值这次没出现也照绑(`matched=0`=确定排除)。
- 树条件常给 `FEI/2/hwLinkHeartbeatChangeAlarm_active` 这种全称:取**告警名段**绑 `Summary`
  (`contains:["hwLinkHeartbeatChangeAlarm"]` 连 active/clear 一起认),要区分 active/clear 再加档。
- `Level`(级别)、`Module`(模块)可做 A/C 绑定的 `filter` 维度(E 类另有内建单子句 filter,
  语义独立,别混用);正文细节才落 `Content/Log` 的 G 类。

## 六、典型坑速查
| 症状 | 根因 | 处置 |
|---|---|---|
| 读 cmd/diag 当场抛 csv 错 | 老版内核没调字段上限 | 升级本仓 `condition_kernels.py`(已内置) |
| 空壳模板全树"排除" | 空表被当 `matched=0` | 已内置空表→数据不足;老版需升级 |
| **filter 绑定跑出诡异"成立"** | **旧产物里的旧内核副本静默忽略 filter,在全表上算**(最危险:不报错、直接假阳) | 重跑步骤2 assemble 刷新产物内核副本;runner 铭牌告警"产物较旧"就别带病跑(格式契约 v2 起含 filter) |
| CRC/计数器类判据全成立 | 用了 G 子串(标签恒在) | 换 custom+`extract_metric`(见三) |
| diag 绑不上任何判据 | 拿宽表思路绑 EAV | 用 `filter` 选量(见四) |
| node_id 全对不上 | 换了 `--start-level` 没重生成 | 重跑步骤1,绑定 node_id 重填 |
| **建树输出「带判据节点 0」/ 节点数≈真实行数×2、中英文名混层** | 旧版 v2 不认混血表:判据列被静默丢弃、同层中英双列被串成两层 | 升级 `excel_to_tree_v2.py` 重跑步骤1;0 判据时 assemble 会硬拦,**别**自己读 Excel 写脚本量产绑定 |
