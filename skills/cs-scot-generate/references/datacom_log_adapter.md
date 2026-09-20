# 数通「诊断文件包」场景的接入说明——7 类 CSV 文件怎么读、怎么绑定

数通（路由器、交换机，如 ME60 BRAS）场景的输入是**一包 CSV 文件**，由平台采集工具直接导出。如果手头只有原始采集文本（`.txt`/`.log` 的命令回显），先用 zip2csv 转换成表，查找次序与转换命令见 SKILL.md 的「输入」节。配套的故障树是混合格式：层级列按新格式写法（同一层常有 `L1` 与 `L1(Chinese)` 两列），判断条件却是老格式的 `Judgment Condition(Chinese)` 单列，条件文本里含 HTML。这种树**统一用 `excel_to_tree_v2.py` 建树**，见下文事实 2。

本文是这套输入的接入说明：每类文件是什么样子、用哪类内核绑定、哪里容易出错。「内核」指 `condition_kernels.py` 里各判断类别（A 阈值比较、C 字段匹配、G 日志匹配、D 多条件组合、P 成对比较等）的模板实现；模板不够用时才为个别条件写 custom 函数。

## 一、三条已验证的事实

1. **多行回显是规范的 CSV**：cmd 与 diag 文件里，命令输出被引号正确包裹，一条记录可以跨几百个物理行。内核的读表函数 `load_table` 已内置调大 csv 模块的单字段大小上限（默认 128KB，超过就直接报错），**直接读取即可，不需要任何重组预处理**。判断方法：`csv 记录数 ≪ 物理行数`，说明是引号包裹的多行记录；两者接近（`≈`），说明是普通的规整表。
2. **故障树是混合格式，统一用 `excel_to_tree_v2.py`**：数通树的层级列是新格式写法（同一层常有 `L1` 与 `L1(Chinese)` 两列），判据列却是老格式的 `Judgment Condition(Chinese)` 单列。`excel_to_tree_v2.py` 已原生支持这种表：同一层有两列时只取中文列（否则中英文名会被拆成两层，树深翻倍）；通用判据列按「[通用]」标签挂到节点上（这类判据没有预先分派数据源，绑定时由模型读判据原文决定归哪个源）；HTML 清洗（`&nbsp;`、`<strong>`、实体转义，保留 `<>` 这类比较运算符）复用老格式脚本 `excel_to_tree.py` 的处理。要从 L1 开始截取子树，加 `--start-level 1`；注意换了起始层级后 `node_id` 会整体重排，`tree_index` 必须重新生成，绑定里的 `node_id` 也要跟着重填。**建树后必须核对输出里的「带判据节点 N」：N 为 0 就停下**，说明格式没被识别（步骤 2 的组装脚本也会拦下），不要继续往下绑定。
3. **空表不等于排除**：有的文件可能只有表头、0 行数据（如只有表头的 BasInfo）。内核对空表（所有读表类的内核）、以及 `filter` 命中 0 行的情况（A/C/G/P 类），自动输出「数据不足」。不要手工绕过这个语义。

## 二、7 类文件与内核的对应表

| 文件（示例名） | 形态 | 判据主要看什么 | 怎么绑定 |
|---|---|---|---|
| `*.txt.csv`（cmd，命令与完整回显） | 引号包裹的多行 CSV，一条记录对应一条命令，`CmdResult` 列是整段回显 | 回显里的告警、事件、状态 | 存在类判据用 G；计数器类判据用 **A + `filter` + `extract`**（用正则或键值方式抽出数值，见第三节）；语义复杂的才写 custom |
| `*_CmdResult.csv`（diag，命令明细） | 引号包裹的多行 CSV，**EAV 窄表**（每行只放一个指标名和它的值）：`DataItem`（约 282 种指标名）、`DataValue`、`Unit` | 各类指标值的判断 | **A/C + `filter`**（见第四节） |
| `*_diagfile.csv`（config，运行配置） | 规整表，层次列 `Module→level1→L2Module→level2…` | 配置项是否存在、取值、不存在（负向判断） | C 类绑 level 列（存在用普通匹配，不存在用 `absent`；同一接口下的关联条件用 D 类加 `group_by`）；单元格内取值加 `extract`；深层路径写 custom（可借用 `mo_config` 的原语函数） |
| `diaglog_*.log.csv`（dblg，诊断日志） | 规整表，`Module/Level/Summary` 码值列加 `Content/Log` 文本列 | 日志码、告警名 | **C/G 类绑 `Summary`**（首选，枚举干净）；只有需要正文细节时才用 G 类绑 `Content` |
| `log.log.csv`（log，运行日志） | 与 dblg 结构相同 | 同上 | 同上 |
| `log.log.opr.csv`（opr，操作记录） | 规整的小表 | 操作审计 | C 类（诊断价值有限） |
| `*_BasInfo.csv`（bas，用户会话） | 规整表，可能**只有表头没有数据** | `FaultType/FaultReason` | C 类直接绑；空表自动判为数据不足 |

## 三、cmd 回显的两类判据（规则，不要混用）

> 通用规则见绑定格式约定文档 condition_binding_schema.md 里关于 C/G 类 `contains` 的三条规则：每个 target 必须单独就是判据成立的充分信号；禁止用字段标签、命令名、协议名这类泛词；否定语义禁止用 G 类；趋势语义要看有没有能检测到的告警码。

- **「存在某告警或事件名」**（如回显里出现 `hwBaseSocAttackTrap`）：G 类 `contains` **可以用**，因为告警名只在告警发生时才出现在回显里，子串命中就是事实。多个数据源都能回答同一判据时，**都要绑，不要互相推让**：log/dblg 的 `Summary` 码值列和 diag 的 `Alarm*` 结构化字段（按 EAV 窄表用 `filter` 绑）最干净，**必须绑**；在 cmd 回显里做子串匹配最粗糙，**但同样要绑**（告警可能只出现在其中一处；各源各出事实，主次由根因分析（RCA）合并）。各数据源的子代理互相看不见，「这个让日志侧去绑」的推让，结果是两边都没绑。
- **「某计数器异常」**（如 CRC 错包大于 0）：**禁止用 G 类**，因为 `CRC:` 这个标签在回显里**始终存在**，值为 0 的健康回显也会被子串命中，造成误报。**首选模板：A 类 + `filter`（选中该命令的行）+ `extract`（抽出计数值）**；千分位逗号自动归一化，抽不到值时判为数据不足并附样例：
```python
# 判据：display interface 回显有 CRC 错包（>0）
{"id":"L4-crc","node":"接口CRC错包","node_id":"nX","condition_text":"…","class":"A","source":"cmd",
 "filter":{"column":"CmdName","contains":"display interface"},
 "column":"CmdResult","extract":{"regex":"CRC:\\s*([\\d,]+)"},
 "op":">","threshold":0,"scope":"count_rows",
 "mapping_reason":"标签恒在禁 G 子串;filter 选中该命令的回显行,extract 抽 CRC 计数逐行比阈"}
```
  **边界（选错会漏报）**：`extract` 在每个单元格里只抽**第一个**命中，适用于「一行对应一条命令、一个对象」的回显（如逐接口执行的 `display interface Eth-Trunk0`）。如果**一个单元格的回显里同一计数器出现多次**（如不带参数的全接口 dump，`CRC:` 出现 N 次），第一个值健康不代表全部健康，必须写 custom 并用 `extract_metric`（抽取**全部**命中）：
```python
from mo_config import extract_metric, fact_ok, fact_insufficient

def _crc_error(table):                       # 判据：display interface 回显有 CRC 错包
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
# 绑定（class 与 mapping_reason 必须写，缺了通不过 validate_conditions 的检查；
#      fact 也必须带 resolved_column 之类的溯源字段，否则会被校验器判为「无中生有」）：
# {"id":"L4-crc","node":"接口CRC错包","node_id":"nX","condition_text":"…","class":"A",
#  "mode":"custom","source":"cmd",
#  "mapping_reason":"树要 display interface 回显的 CRC 错包计数;标签恒在,禁 G 子串,custom 抽值比较"}
```
`Symbol/Pause/Overrun` 等计数器同理（换正则即可）；`extract_metric` 自动处理千分位逗号，大小写不敏感。

## 四、diag（EAV 窄表）的标准绑法

先说清「diag」在本项目里的两个含义，避免把两种结构完全不同的表配错指标名：

- 本文的 diag 指平台采集工具导出的 `*_CmdResult.csv`：EAV 窄表，列是 `DataItem`、`DataValue`、`Unit`，绑定用 `filter` 选指标。
- zip2csv 输出目录里的 `csv/<网元>/DIAG/*.csv` 是命令回显表（列 `NEName`、`CmdName`、`ObjContent`），`DIAG` 只是 `CMD` 目录的旧名字。内核 `condition_kernels.py` 和执行器 runner 把 `diag` 与 `cmd` 当作同一类数据的两代源名互认：只提供了其中一个名字时，把同一张表也登记到另一个名字下；两个名字都提供了就各归各。这条兼容规则是为 DIAG 改名 CMD 准备的，不是为 EAV 表准备的。

由此得出的配对规则：同一个场景里 `diag` 指标名只对应一种表结构，由生成时的绑定决定（分析脚本里 `filter.column` 为 `DataItem` 的就是 EAV 表）。按 EAV 绑定的 `diag_analyzer.py` 收到 zip2csv 的 DIAG 或 CMD 表会整批报「找不到列」，反过来也一样。本文这类同时用到 `cmd` 与 `diag` 两个指标的场景，执行时两份数据都要传，不能只传一份靠别名补上，否则别名会把 cmd 表当 diag 表用。执行侧的说明见 `cs-scot-execute/references/data_entry.md`。

这张表是「一行一个量」：`DataItem` 列说明这一行是哪个指标，`DataValue` 列是它的值。绑定时先用 `filter` 选出指标，再用 `column` 指向值列：
```python
# A 类：某指标值越限（如 CPU 占用率 > 80）
{"id":"L3-cpu","node":"CPU过载","node_id":"nY","condition_text":"…","class":"A","source":"diag",
 "filter":{"column":"DataItem","equals":"SYS_CpuUsage"},
 "column":"DataValue","op":">","threshold":80,"scope":"count_rows"}

# C 类：某指标取值命中（如单板状态字段含 Abnormal）
{"id":"L3-board","node":"单板异常","node_id":"nZ","condition_text":"…","class":"C","source":"diag",
 "filter":{"column":"DataItem","equals":"SYS_BoardStatus"},
 "column":"DataValue","contains":["Abnormal"]}
```
- `filter` 命中 0 行，表示该指标本次没有采集，自动判为数据不足（不是「不成立」）。
- 选 `DataItem` 的名字时**以概览或实际枚举为准**（先跑 `analyzer.py` 看各列的不同取值），不要按树正文猜。**注意：`DataItem` 常有上万个不同取值，`--lean` 精简概览会把这种超大枚举列省略掉（`field_stats` 里没有它）**。这时不要自行猜测，直接抽取全量枚举写入产物目录，子代理照读：
  ```bash
  python -c "import csv,sys;csv.field_size_limit(10**9);print('\n'.join(sorted({r.get('DataItem','') for r in csv.DictReader(open(sys.argv[1],encoding='utf-8',errors='ignore'))})))" "<diag的csv>" > "<OUT>/overview/diag_dataitem枚举.txt"
  ```
  这条抽枚举命令是「不用 `python -c` 解析数据」这条规矩的唯一例外：它只读一列做去重，不解析任何产物 JSON；路径照旧加引号。
- 多个指标共同判断一个节点时，用 D 类组合（见 condition_binding_schema.md 的「一个节点多条判据怎么绑」一节）。
- **「diag 一条都不用绑」这种全空结论，必须先把 `DataItem` 枚举对着树上的判据逐个核对过才能下**（`Alarm*`、`BFD_*`、`INTF_*`、`FIB_*`、`SYS_*` 这些结构化字段常常可以替代回显的子串匹配），并在汇报里写明「核对了哪些、为什么都不适用」。不要因为 cmd 容易绑定就略过 diag。

## 五、日志类（dblg/log）的绑定要点

- 首选 **`Summary` 列**（告警或日志码的干净枚举，如 `RDS_ACCT_SERVER_PROB`、`hwArpsGatewayConflict`）：用 C 类 `equals` 或 `contains`，按树遍历逐个叶子绑定；这次数据里没出现的值也照绑（`matched=0` 表示确定排除）。
- 树条件常给出 `FEI/2/hwLinkHeartbeatChangeAlarm_active` 这种全称：取其中的**告警名段**绑 `Summary`（`contains:["hwLinkHeartbeatChangeAlarm"]` 会把 active 与 clear 一起匹配到），需要区分 active/clear 时再增加更细的绑定。
- `Level`（级别）、`Module`（模块）可以作为 A/C 类绑定的 `filter` 维度；只有需要正文细节时才用 G 类绑 `Content/Log`（正文本身带正则形态的判据用 `regex`，占位符先改写：`%u` 改为 `\d+`）。

## 六、常见问题速查

| 症状 | 原因 | 处理 |
|---|---|---|
| 读 cmd/diag 时直接抛 csv 错误 | 旧版内核没有调大字段上限 | 升级本仓库的 `condition_kernels.py`（已内置） |
| 只有表头的文件让全树「排除」 | 空表被当成 `matched=0` | 已内置「空表判为数据不足」；旧版需要升级 |
| **带 filter 的绑定跑出异常的「成立」** | **旧产物里的旧内核副本会不报错地忽略 filter，在全表上计算**（最危险：不报错，直接误报） | 重跑步骤 2 的组装脚本，刷新产物里的内核副本；执行脚本 runner 启动时比对产物标识 `manifest.json` 提示「产物较旧」，就不要在这种状态下继续运行（产物格式版本 2 起才包含 filter） |
| CRC 等计数器类判据全部成立 | 用了 G 类子串匹配（标签始终存在） | 改为 A 类 + `filter` + `extract` 抽值比较阈值（见第三节） |
| diag 绑不上任何判据 | 用宽表的思路绑 EAV 窄表 | 用 `filter` 选指标（见第四节） |
| node_id 全部对不上 | 换了 `--start-level` 没有重新生成 | 重跑步骤 1，绑定里的 node_id 重填 |
| **建树输出「带判据节点 0」，或节点数约等于真实行数的 2 倍、中英文名混在同一层** | 旧版 `excel_to_tree_v2.py` 不支持混合格式的表：判据列被不报错地丢弃，同层的中英两列被拆成两层 | 升级 `excel_to_tree_v2.py` 重跑步骤 1；带判据节点为 0 时组装脚本会拦下，**不要**自己读 Excel 写脚本批量生成绑定 |
