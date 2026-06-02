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
