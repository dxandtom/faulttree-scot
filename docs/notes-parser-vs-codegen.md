# 设计笔记:解析脚本——通用解析器 vs 每场景生成代码(待定)

> 状态:**决策待定**。本笔记只记录分析与建议,代码暂不改(用户:先不动代码)。
> 触发:用真实数据跑了一版「SA场景接入失败」(chr 117 列真实 CHR + kpiTree.csv),
> 观察生成的 `chr_codegen_prompt.md / kpi_codegen_prompt.md / chr_analyzer.py / kpi_analyzer.py`。

## 两个待决问题
1. `referenced`(把"故障树点名的字段"单拎出来)这层,是帮 RCA 减负、还是 LLM 多此一举、反而读错/读少?
2. 有没有必要每场景都生成"指标专属 prompt → 专属代码"?能不能一步到位,用**一个**通用脚本?

## 本次真实运行结果(SA场景接入失败,20,496 条 CHR)
- **最终 `rca.json` 是诚实的**:`rca_result: []` + `undetermined`,理由是树的判据大量依赖话统计数器(N.xxx),而 kpiTree 无实测值 → 无法验证;并且**正确拒绝了近似值**(树要 `DCInnerCause=0x810D176C`,CHR 实际是相近的 `0x810D176B`,判"不成立")。这正是要的真实性。
- **但控制台叙述过度**:同一次还输出了"发现 5 条根因、AMF 为主、无悬置"的表格,把"数据不足"的节点当成了根因——这是我们明令禁止的"硬找根因"。即:落盘 `[]` 是对的,聊天总结过度了。
- RCA 输出还用了非契约 schema(`rca_result`/`undetermined`/`hit=null`,`hit` 是旧措辞)——疑似装的是改造前的旧版 RCA 技能,或模型自行发挥;建议重装匹配版本。

## 现状观察(均有据可查于生成产物)
- **统计内核 100% 通用**,无场景特异逻辑:
  - 表格型(chr):遍历表头 → 每列 `Counter` top-N + 样例 / 或 min·max·mean;
  - 目录型(kpi):逐行 `json.loads` → 遍历每行所有键统计 + 建 `parentId→id` 树。
  - "算法"仅 `collections.Counter` 频次 + min/max/mean,无其它。
- **唯一随场景变的**,是"这棵树引用了哪些 token"——而那是**树的投影(数据)**,不是代码逻辑。

## 现实现的三处害处(`referenced` 由 LLM 拍脑袋生成导致)
1. **LLM curate 还 padding(有铁证)**:`chr_analyzer.py` 的 `referenced_names` 比它自己的 codegen prompt 列的**多出十几个**(`PLMN / gNodeBID / NRCellID / Msg5AckCnt / Msg5NackCnt / UL BLER / DL AVG MCS / DL AVG RANK …`),prompt 未点名,是写代码的 LLM 自加。能多塞→能漏;三级链各级**对不上(drift)**。
2. **kpi 匹配了错误字段 + 模糊**:脚本拿树的 `N.xxx` 去匹 `kpiIndex`/`name`(那是 KPI 标签,如 `CSSR`/`RRC Setup Success Rate`),但**原始计数器其实写在 `formula` 里**(如 `"L.RRC.ConnReq.Att"-"L.RRC.ConnReq.Succ"`)。字段就找错了,又用子串模糊匹配 → 大量错配/漏配。正解(同事 prompt 也是这思路):**从 `formula` 正则抽 `N./L.` 计数器**,再对树计数器算覆盖、收集用到它的 KPI 及其 `formula`/阈值。
3. **无意义字段 / 死代码**:chr `referenced[...]."value" = nums[0] if nums else None`——对类别列取"第一个能转数字的值",无意义且与 `distribution` 重复;kpi 里 `find_kpi_by_name_or_index()` 定义了但从未调用(生成残留)。
4. 另:`is_numeric_col` 用**列名关键词**(rsrp/cnt/time…)猜列类型,会误判(带 `time/cnt` 的列被强行当数值)。应改为**按值**判定(抽样解析成功率),不看列名。

## 建议方向(倾向)
**收敛成一个固定的通用解析器**(测一次、稳定),不再每场景生成代码:
- ① **全字段统计**(全量事实,RCA 兜底);字段类型按"值"判定,不靠列名关键词。
- ② **机械精确投影 `referenced`**:脚本**读故障树**,`N./L.` 计数器用正则抽、chr 字段名用"与真实列名取交集"抽,**精确匹配**;匹不上就老实 `found:false`。**不让 LLM curate、不模糊匹配、不 padding。**
- ③ 自动判别 表格型 / 目录型 两种数据形态(目前真实数据就这两种)。
- `meta_prompt.md` 退化为**文档/契约说明**,不再每跑一次生成代码 → 不会反复长出 padding/模糊匹配/null value。
- 取舍:此前担心"通用 parser 不行";但两份真实代码证明 95% 是同一套通用遍历,真正变的只有"树点名的 token 清单(数据)"。通用脚本 + 树当数据更干净、更可控。

> 备选:保留每场景生成,但硬性禁止 padding、禁止模糊匹配(只精确)、删无意义 value。能止血,但每跑一次仍可能复发。

## 两个数据缺口(不论走哪条路都要处理)
1. **kpiTree = 计算方式/阈值字典,不是实测值(已确认,用户:只有 kpiTree)**:每行核心是 `formula`(KPI 由哪些原始计数器、怎么算)+ `absExceptionThreshold/high/lowCondition`(判异阈值)+ `definition`(含义)。例:`{"formula":"\"L.RRC.ConnReq.Att\"-\"L.RRC.ConnReq.Succ\"","name":"RRC Setup Failure Times"}`。所以 kpi 解析的**正确职责是抽取这套知识**(覆盖率 + formula + 阈值 + 含义),**不是取值**。
   - 真数据证实:本次 kpi `referenced[...].value` 全 `null`;`N.UECntx.FailEst.AMF > 0` 这类**光靠 kpiTree 永远算不了**。
   - **唯一实测数据是 CHR(逐记录)**。好在故障树很多节点**自带 CHR 判据**(如某叶子 `CHR DCInnerCause = 0x810D176C`),这些能直接用 CHR 评估;话统类计数器还可由 CHR **按树给出的语义映射计数**近似(显式、可审计),把"几乎全 undetermined"变成"有 CHR 证据的根因"。
2. **数值阈值条件无法判**:树里有 `RSRP < -120`、`SINR < 0`。脚本只给 min/max/mean,**答不了"有多少条 < -120"**。应让解析器(通用、由树驱动)算出"满足该原始谓词的**记录数/占比**"——这是事实不是判断,RCA 才有得判。

## 进展(已落地)
- **通用引擎**(`scripts/analyzer.py`):自动判别 tabular/catalog,只产事实/规则,删掉三级 codegen 链。已用真实数据跑通(20496 CHR / 5255 kpiTree 节点)。
- **kpi 实测值来源**:用户确认只有 kpiTree(无实测)→ 话统条件如实"数据不足",不搞 CHR 反推计数器。
- **RCA 改造**:① 改成**指标无关**(去掉 CHR/话统/kpiTree 耦合,只剩通用法则"要 X 没 X 就数据不足、绝不拿 Y 顶 X、含数据不足节点的路径不算根因");② 加机械自检 `scripts/validate_rca.py`,揪"无实测却判成立"的自相矛盾 + 假根因(对较弱的运行模型尤其必要——实测 MiniMax 跑出来仍会硬顶)。
- **输出过长**:引擎加**按故障树投影**(`--fault-tree`,或自动探测脚本旁 `../tree/*.md`),只留树提到的字段/计数器/规则;真实 kpiTree 几千条 → 压到个位/十几条。

## 待办 / 观察
- 数值阈值条件(`RSRP < -120`)目前引擎只给 min/max/mean,答不了"多少条 < -120"。若要支持,需让 RCA 端按谓词计数,或引擎在投影时对被点名的数值字段额外给分位/阈值计数(仍是事实,不是判断)。
- `docs/design.md` 仍停在旧的三级链描述,待同步到"通用引擎 + 投影 + 通用 RCA"。

## 更新(v1 改版):kpiTree 是错误数据;真 kpiData 是表;可变只锁在"映射"
> 用户确认:之前的 **kpiTree 是错的数据**(单列 JSON 的"公式字典"),真正要用的是 **kpiData——带表头的表**(58 列:`Time/Object/ObjectType/cellName` + 各 KPI/计数器列,有真实值)。
> 于是数据形态收敛为**只有"带表头的表"**(CHR、kpiData 皆然),catalog 那套作废。

**作废/删除**:`analyzer.py` 里整条 catalog 支路(`_parse_catalog`/`_analyze_catalog`/`_counters_in`/`counter_index`/`_COUNTER_RE`、`_load`/`_project` 的 catalog 分支)——都是给假 kpiTree 写的,已删。引擎纯表格化。

**新发现(决定本版设计)**:真数据里**树的量与数据列名对不上是常态**——树用英文/code 名(`SUSRLastAvgRsrp`、`SA_QosFlow_Setup_Success_Rate`),列是中文(`上行RSSI均值`、`SA_QoSFlow建立成功率(22B)`)。所以:
- 旧的"子串投影"对 kpiData **几乎全落空**(会把数据投空、害 RCA 瞎判)。
- 本地模型实测把映射做错且**自信地编**:`SINR < 0` 被映到"语音用户数"(其实根本没有 SINR 列);`RSRP` 映到 `RSSI`(语义/单位不符)。**映射错误是静默的**:数照样算得出、结论却全错且不报错。

**本版结论:可变的只该是"统计之前的映射/规整",不是统计本身。** 三段:① 装载(固定)② 映射/规整(随数据自适应、按语义、显式可审)③ 统计(固定)。统计模板可枚举、要稳;真正没法预知的是"这份表的列怎么对到树的量",那才交给模型——且**必须显式记录映射+理由,找不到就数据不足,绝不抓相关列硬顶**。

**已落地(v1)**:
- 引擎纯表格 + 数值列加**十分位 `deciles`(p0..p100)**(让 `<阈值` 条件可估)+ **`nulls`**(把 `NIL` 当缺测显式计数,绝不当类别/正常)。`NIL` 已纳入空值集。
- 投影改**跨命名兜底**:子串命中过稀(<3 列或 <10%)→ `mode:"kept_all_fallback"` 保留全部列,映射交给 RCA 语义完成。
- `rca_rules.md` 增"字段映射"硬规则(语义映射、找不到→数据不足、近似要标注降置信、成功率vs差值、NIL=数据不足、阈值出处、用 deciles 判数值阈值);RCA 输出 evidence 增 `mapping`/`mapping_reason`(落盘可审)。
- 新示例 `examples/kpi-tabular/`(kpiData 形态 + 英文 code 名树)跑通:`projection=kept_all_fallback`、成功率列 `nulls:1`、无 SINR 列 → 条件2 应判数据不足。

**待办(v2 候选)**:
- 数值阈值仍是"用 deciles 估比例",非精确计数。若要精确,引入"映射规格(列+op+阈值,带理由/置信)+ 固定执行器"算精确命中数/占比(仍是事实)。
- 旧示例 `examples/sa-access-fail/fixtures/kpi.csv` 是**废弃的假 kpiTree**,仅作历史;真形态以 `examples/kpi-tabular/` 为准。
- `validate_rca.py` 暂未校验"映射编造"(机械难判);靠 `rca_rules.md` 硬规则 + evidence 里 `mapping_reason` 人工/二次复核。
