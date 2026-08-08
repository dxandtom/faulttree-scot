# 故障树诊断流水线 · 三技能交接文档

面向接手开发的同事。覆盖 **cs-scot-generate**(生成)、**cs-scot-execute**(执行)、**fault-tree-rca**(根因分析)三个技能:每个技能干什么、目录里每个文件的职责、怎么用、以及它们之间靠哪些文件交接数据。

> 术语约定:**入口脚本** = 用户/agent 直接在命令行跑的;**内部库** = 只被别的脚本 `import`、不单独跑。

---

## 0. 一分钟全局图

一次完整诊断分三段,数据靠固定的 JSON/MD 文件在技能之间交接:

```
 故障树 Excel ─┐
              │  ① cs-scot-generate(一次生成、反复执行)
              ▼
     tree/<场景>.md + <场景>.tree_index.json   ← 故障树结构(权威)
     plan.json                                  ← SCoT 执行计划
     scripts/<指标>_analyzer.py                  ← 每个数据源一个"逐条件分析脚本"(填好绑定)
              │
              │  ② cs-scot-execute(每来一批数据跑一次)
     各源数据文件(csv/xlsx/log)
              ▼
     result/rca_input.json                       ← 逐节点"确定性事实"(成立/不成立/数据不足 + 实测)
              │
              │  ③ fault-tree-rca(模型判断,唯一的"慢"步骤)
              ▼
     result/rca.json                             ← 四档根因结论(确证/疑似/待定/树问题)
```

一句话分工:
- **generate**:把故障树 + 数据源表头,编译成"每条判据怎么用代码算"的脚本。**产出是资产,一个场景生成一次**。
- **execute**:拿一批真实数据,按计划跑那些脚本,算出每个节点"判据成立没有"的确定性事实。
- **fault-tree-rca**:读那些事实,做根因推理,输出结论。**这一步是模型判断,不是脚本**。

**关键设计**:确定性的统计(计数、比阈值、匹配)全在 generate 生成的脚本里、由 execute 跑;模型判断只在最后一步。这样同一个场景的脚本"生成一次、反复执行不同数据"。

---

## 1. cs-scot-generate —— 诊断产物生成器

**吃**:故障树 Excel + 各数据源的真实表头/样本。**吐**:一个场景的"诊断产物目录"(计划 + 每源一个分析脚本 + 故障树子树)。这是一次性的编译动作。

**SKILL.md 规定的流水线(4 步)**:
1. **步骤1**:Excel → 该场景子树 `md` + `tree_index.json`(并定出产物目录 `<OUT>`)。
2. **步骤2**:归集产物(拷模板库、为每个数据源写一份**待填的**分析脚本骨架、生成 `plan.json`)。
3. **步骤2.5(最关键、也是唯一靠模型的一步)**:模型逐条读判据,给每个 `<指标>_analyzer.py` 填 `CONDITIONS` 绑定(选 A–J 内核、对哪列、阈值)。**这一步禁止写脚本批量猜,必须逐条判断**。
4. **步骤3**:用真实数据跑每个脚本、过两道校验闸门(`validate_conditions` + `check_tree_coverage`)。

### scripts/(入口 vs 内部库)

| 文件 | 入口/库 | 职责 | 命令行用法 |
|---|---|---|---|
| `excel_to_tree_v2.py` | **入口** | 步骤1(新格式/混血格式树):多页签 Excel → 场景子树 `md` + `tree_index.json`;判据列按数据源分列。**目前主用这个**。 | `excel_to_tree_v2.py <xlsx> --list`(列场景)/ `--start-node "<场景名>" [--start-level N]` |
| `excel_to_tree.py` | **入口** | 步骤1(老格式树):单页签、层级列 `Ln(Chinese)`、单判据列 `Judgment Condition(Chinese)`。v2 复用它的 HTML 清洗等通用件。**选脚本判据:先跑 v2 `--list`,能列出场景=新格式/混血用 v2;认不出再用本脚本**。 | `excel_to_tree.py <xlsx> --start-node "<场景>"` |
| `assemble_scenario.py` | **入口** | 步骤2:把中间产物归集成产物目录——拷内核库 + 为每个 `--indicators` 写待填脚本骨架 + 生成 `plan.json` + `manifest.json`。**带硬闸门:tree_index 缺失或 0 判据直接 exit 1**。 | `assemble_scenario.py --scenario "<场景>" --indicators cmd,chr,... --xlsx <xlsx>`(或 `--out <目录>`) |
| `analyzer.py` | **入口(辅助)** | 数据源概览引擎:读一个数据文件,吐每列的类型/取值分布/数值摘要,**只查数据不下判断**。步骤2.5 填绑定前用它看列;也会被拷进产物 `scripts/`。 | `analyzer.py <数据文件> --lean -o <概览.json>`(`--sheet <页签>` 看多页签某页) |
| `condition_kernels.py` | **内部库** | A–J 十类"统计内核"模板库(阈值/差值/匹配/占比/配置/日志/信令/特征/组合/人工)+ 数据加载 `load_table` + 列解析 + 行级 `filter`。**生成的 `<ind>_analyzer.py` import 它**,execute 间接跑它。 | (不单独跑;`kernel_selftest.py` 测它) |
| `mo_config.py` | **内部库** | MO 配置文本(`MOName:key=val;`)→ 表 的适配器 + IP/网段/追溯等 custom 原语 + `extract_metric`(从命令回显正则抽数值)。写 custom 绑定时 import。 | (不单独跑) |
| `validate_conditions.py` | **入口(闸门)** | 步骤3 闸门①:扫分析脚本产出的事实,揪"静默编造"(列没解析到却给了判断、类别缺失等)+ 代理/覆盖度软告警。不合格 exit 1。 | `validate_conditions.py <condition_facts.json>` |
| `check_tree_coverage.py` | **入口(闸门)** | 步骤3 闸门②:树驱动核对覆盖度——列出"本源能答、却没被任何绑定认领"的叶子(欠绑定)。漏绑 exit 1。 | `check_tree_coverage.py <tree_index.json> --analyzer <ind>_analyzer.py --columns "列1,列2,..."` |
| `kernel_selftest.py` | **入口(测试)** | A–J 内核的回归自测(合成小表 → 断言事实)。改内核后必跑。 | `kernel_selftest.py`(全过 exit 0) |

### references/

| 文件 | 是什么 |
|---|---|
| `condition_binding_schema.md` | **绑定契约(最重要的参考)**:A–J 每类的参数、`filter`/EAV 窄表/空表语义、"一个节点多条判据用 D 类"、代理禁忌等。步骤2.5 的规则书。 |
| `datacom_log_adapter.md` | 数通"诊断文件包"场景(路由器 cmd/diag/config/dblg/log 等一包 CSV + 混血格式树)的接入手册:7 类文件对号表、EAV 绑法、回显计数器红线。 |
| `plan_schema.md` | SCoT `plan.json` 的格式契约(与 execute 侧同一份)。 |

> 口径:references/ 只放**模型执行时要读的契约文档**;"给人看的"补充手册不进技能
> (机器契约懂技术的人同样读得懂,不设第二份,免双份维护漂移)。

---

## 2. cs-scot-execute —— 轻量 SCoT 计划执行器

**吃**:`plan.json` + 各源真实数据文件(`--data <指标>=<文件>`)。**吐**:`result/rca_input.json`(交给 RCA)。**每来一批新数据跑一次**。

**SKILL.md 规定**:一条命令 `runner.py --plan <plan.json> --data kpi=... --data chr=...`,顺序遍历计划节点。`script` 节点跑对应分析脚本算事实;末尾 `agent_skill` 节点把汇总事实备到 `rca_input.json`,交下一个技能。**版本管理是默认行为**:`result/` 永远是当前一轮,重跑前上一轮自动归档到 `runs/<run-id>/`。

### scripts/

| 文件 | 入口/库 | 职责 | 命令行用法 |
|---|---|---|---|
| `runner.py` | **入口** | 计划执行器:读 `plan.json`,从 `entry` 出发按 `transition` 遍历节点,用工作记忆 + 引用表达式(`$input/$memory/$step/$output`)在节点间传数据;`script` 节点跑子进程算事实,`agent_skill` 节点备 `rca_input.json`。含缺数据源跳过、运行版本归档、格式契约铭牌比对。 | `runner.py --plan <plan.json> --data <指标>=<文件> [...]` |
| `tree_prune.py` | **库 + 入口** | 把 `tree_index` + 各源事实剪成"相关局部子树"(只留有事实的节点 + 到根的祖先),压缩给 RCA 的上下文,并产覆盖报告。**runner import 它**(`compose_rca_payload`)组装 `rca_input.json`;也可单独跑测压缩率。 | `tree_prune.py --index <tree_index.json> --results kpi=<kpi.json> -o pruned.json [--md 子树md]` |

### references/

| 文件 | 是什么 |
|---|---|
| `plan_schema.md` | `plan.json` 执行侧视图(与 generate 侧同一契约):节点 kind、transition、引用表达式怎么解析。 |

---

## 3. fault-tree-rca —— 根因分析(模型判断)

**吃**:`result/rca_input.json`。**吐**:`result/rca.json`(四档结论)。**这是 prompt-only 技能——由 agent/模型按 SKILL.md 的规则逐节点判断,不是跑脚本**。脚本只是辅助导航和校验。

**SKILL.md 规定**:模型遍历子树,对每条判据看"要哪个量 → 到事实里找 → 代入算 成立/不成立/数据不足",回溯到根输出根因路径。产出后过 `validate_rca` 闸门。

`rca.json` 是**四档 dict**(顶层四键恒在):
- `rca_result`:确证根因(路径全成立、落叶子);
- `probable_causes`:疑似(有强证据但没闭环);
- `undetermined`:待定(卡在哪个节点、缺哪个量);
- `tree_issues`:树本身的问题(未覆盖节点、孤儿 node_id)。

### scripts/

| 文件 | 入口/库 | 职责 | 命令行用法 |
|---|---|---|---|
| `rca_query.py` | **入口(辅助)** | RCA 导航助手:**不把整个 rca_input 读进上下文**(大场景会超长),按分支抽取节点的"条件+事实"。 | `rca_query.py <rca_input.json> --outline`(全树粗状态)/ `--holding`(有成立事实的候选+路径)/ `--node nX`(某分支整条路径) |
| `validate_rca.py` | **入口(闸门)** | RCA 输出诚实性自检:揪"给无事实节点判成立"(伪造)、确证档路径含数据不足节点、空诊断、跨分支拼路径等。不合格 exit 1。 | `validate_rca.py <rca.json> [rca_input.json]`(带上 rca_input 才能核对"成立必须有事实") |

### references/

| 文件 | 是什么 |
|---|---|
| `rca_rules.md` | RCA 判断法则细则:怎么判三态、语义等价(RSRP≠RSSI 要降置信)、主次/置信怎么定。SKILL.md 要求先读它。 |

---

## 4. 数据交接文件一览(谁产、谁读)

| 文件 | 产出方 | 读取方 | 内容 |
|---|---|---|---|
| `tree/<场景>.tree_index.json` | generate 步骤1 | generate(绑定核对)、execute(剪枝挂树) | 故障树权威结构:节点 `nN` → 名字/父/层级/判据/子节点 |
| `tree/<场景>.md` | generate 步骤1 | RCA(读故障树正文) | 故障树可读版 |
| `<指标>_analyzer.py` | generate 步骤2 骨架 + 模型填绑定 | execute(当 script 节点跑) | 每个数据源一份"逐条件分析脚本" |
| `plan.json` | generate 步骤2 | execute | SCoT 执行计划(节点 + transition) |
| `manifest.json` | generate 步骤2 | execute(格式契约比对) | 产物铭牌(格式版本号,当前 v2,generate/execute 成对维护) |
| `<指标>` 事实 JSON | `<指标>_analyzer.py` 跑出 | 步骤3 两道闸门、runner(存入工作记忆) | 该源逐条件的确定性事实(中间产物) |
| `result/rca_input.json` | execute(runner 的 agent_skill 节点) | fault-tree-rca | 逐节点确定性事实(压缩形态:剪枝子树 + facts) |
| `result/rca.json` | fault-tree-rca(模型) | 下游/用户 | 四档根因结论 |

**node_id 是贯穿三段的主键**:generate 的 `tree_index` 发 `nN` 编号 → 绑定里 `node_id` 填 `nN` → execute 靠它把事实挂回树 → RCA 靠它拼路径。**换了故障树起始层级(`--start-level`)会导致 `nN` 整体重排,tree_index 必须重生成、绑定的 node_id 跟着重填**。

---

## 5. 从零跑一个场景(最短路径)

```bash
# —— cs-scot-generate(一次性) ——
# 步1:看有哪些场景,建子树
python excel_to_tree_v2.py 故障树.xlsx --list
python excel_to_tree_v2.py 故障树.xlsx --start-node "SA接入失败"          # → 定出 <OUT>,产 tree_index
# 步2:归集(每个数据源一个 indicator)
python assemble_scenario.py --scenario "SA接入失败" --indicators kpi,chr --xlsx 故障树.xlsx
# 步2.5:看列 → 模型逐条填 <OUT>/scripts/<ind>_analyzer.py 的 CONDITIONS
python <OUT>/scripts/analyzer.py <kpi数据> --lean -o /tmp/kpi_overview.json
# 步3:验证
python <OUT>/scripts/kpi_analyzer.py <kpi数据> -o /tmp/kpi.json
python validate_conditions.py /tmp/kpi.json
python check_tree_coverage.py <OUT>/tree/*.tree_index.json --analyzer <OUT>/scripts/kpi_analyzer.py --columns "列1,列2"

# —— cs-scot-execute(每批数据) ——
python runner.py --plan <OUT>/plan.json --data kpi=<kpi数据> --data chr=<chr数据>
# → 产出 <OUT>/result/rca_input.json

# —— fault-tree-rca(模型判断) ——
# agent 读 SKILL.md + rca_rules.md,用 rca_query.py 导航,逐节点判断,写出 <OUT>/result/rca.json
python rca_query.py <OUT>/result/rca_input.json --outline
python validate_rca.py <OUT>/result/rca.json <OUT>/result/rca_input.json
```

---

## 6. 接手要点(容易踩的坑)

1. **generate 是"编译"、execute 是"运行"**:统计逻辑一次写进分析脚本,反复执行不同数据。别在 execute 里加工数据。
2. **绑定必须逐条模型判断**,禁止写关键词分类脚本批量生成 `CONDITIONS`(会绕过语义映射、产出假绑定)。
3. **建树后先看"带判据节点 N"**:N=0 说明判据列没被识别(格式问题),停下查表头,别往下走(assemble 也会硬拦)。
4. **三态语义**:数据不足 ≠ 不成立;空表/缺列 → 数据不足;有数据但没命中 → 确定排除。这条贯穿内核和 RCA。
5. **改内核库**(`condition_kernels.py`)后必跑 `kernel_selftest.py`;**改产物结构**记得同步 generate/execute 两侧的格式契约版本号。
6. **node_id 一致性**:tree_index 重生成后,绑定和历史产物的 `nN` 可能失效。
