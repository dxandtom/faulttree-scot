# 根因分析规则(fault-tree-rca)

## 输入
- **fault_tree_md**:故障子树正文(`#/##/###` 层级,标题下是判断条件;人工编写,可能不规整)。
- **indicator_results**:各指标 parser 的输出,键为指标名;每条 `condition` 含
  `expr / values / actual / judgment / hit / basis / samples /(可选)status`。
  **`hit` 是三态**:`true`(成立)/ `false`(不成立)/ `null`(数据不足,**未判定**)。

## 铁律:只组装、不创作、绝不编造
1. 你**只能**用 indicator_results 里**真实存在**的 `condition` 来判断;**禁止**引入任何 parser 没算过的计数器、数字或条件(例如不得凭空写 `N.UECntx.Est.Att - N.UECntx.Est.Succ > 0 → 成立`)。
2. **`hit: null` = 未判定**,**不得**当成立或不成立;**不得**拿 `summary` 里的分布数当条件命中。(该 condition 的 `context`——如 KPI 定义、异常阈值、计数器覆盖——是真实信息,可如实呈现,但**不据此判成立**。)
3. evidence 里的 `condition / actual / judgment` **逐字取自** parser 对应 `condition`,**不要自己重算或改写数字**。
4. **没有充分证据就如实说"没有"**:不硬凑根因。若大量条件 `hit:null`,说明数据可能传错/不全——**把这个如实回报**,比编一个根因有用得多。

## 一条路径成立的判据
- 某叶子节点算"根因"**当且仅当**:从它回溯到根,**路径上每个带判断条件的节点,其对应 parser `condition` 的 `hit` 都为 `true`**。
- 路径上只要有节点 `hit:false` → 该路径**不成立**;有节点 `hit:null` → 该路径**未判定**。两种都**不得**作为根因输出。
- 纯描述性、无判断条件的中间节点:直接通过(不要求 hit)。

## 为什么可能有多个根因(也可能一个都没有)
- **单次**故障:顺一条路径走到底,通常**一个**根因。
- 但本流程吃的是**一批聚合数据**(成千上万条记录),一批里**可能同时存在多种失败模式** → **多个根因是合理的**,但**每个都必须按上面的判据被完整证实**。
- 输出多根因时:① 路径**去重 / 合并重叠**(别把同一条因重复列);② 按**支持度**(命中记录数,取自各节点 `condition` 的 `samples`/计数)从高到低排,让人一眼看出主因;③ 没把握的**不要堆**。
- 若**没有任何**被完整证实的路径 → 输出 `[]`,并如实说明卡在哪(见下),**绝不**编一个。

## 输出契约
```json
[
  {"Root_cause":"根因节点名称",
   "Fault_tree_path":["父节点1","父节点2","根因节点"],
   "support":"主要支持:<命中记录数/样例,来自 parser>",
   "evidence_path":[
     {"node":"父节点1","condition":"<逐字,取自 parser>","actual":"<取自 parser>","judgment":"<取自 parser>"},
     {"node":"根因节点","condition":"...","actual":"...","judgment":"..."}
   ]}
]
```
- 没有完全证实的路径时,输出 `[]`;并另给一段 `undetermined`:列出关键但 `hit:null` 的条件、缺什么数据、以及 parser 为其提取到的 `context`(定义/异常阈值/覆盖),让用户知道**哪些没查成、为什么、已知什么**。

## evidence_path 字段标准
- 与 `Fault_tree_path` 一一对应。
- `condition` 逐字取自故障树/ parser;`actual`、`judgment` 直接取自 parser 对应 condition,**不掺 parser 没产出的数字、不自行重算**。
- 无判断条件的中间节点:`condition`/`actual` 置空串,`judgment` 注明"路径中间节点,无独立判断条件"。

## 分工再强调
- 数值、命中、`actual`/`judgment` 都由 parser(代码)算好;**你不重算、不补算、不臆造**。
- 你只做:把 parser 的 `hit=true` 条件按故障树**串成成立的路径**、回溯到根、去重排序、逐节点取证;以及**诚实地报告"未判定/未找到根因"**。
