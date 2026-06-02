---
name: cs-scot-generate
description: 把一棵故障树(Excel)+ 若干指标的数据,编译成一个场景的诊断产物:各指标的解析脚本、一份 SCoT 执行计划 plan.json、以及该场景的故障树 md。当用户要为某网络保障场景生成诊断脚本与执行计划、或说"生成故障树诊断脚本 / 把这个故障树做成可执行的诊断 / 生成 SCoT"时,务必使用本技能。指标种类不固定,由用户提供的数据决定。
---

# cs-scot-generate —— 故障诊断产物生成器

把一棵**故障树(Excel)+ 若干指标的数据**,编译成某场景的**诊断产物**。

## 产物就是这三样(外加留痕)
1. **SCoT 执行计划** `plan.json`(由 cs-scot-execute 来跑)
2. **各指标的解析脚本** `scripts/<指标>_analyzer.py`(确定性、**自包含**:内联读取函数,只用标准库;读 xlsx 才需 openpyxl)
3. **故障树 md** `tree/<场景>.md`(由 Excel 截取该场景子树转成)

> 留痕:还会保存生成各脚本所用的 codegen 提示词到 `codegen_prompts/`。
> 这些产物是**数据/资产**,不是一个 skill——真正的 skill 只有 cs-scot-generate / cs-scot-execute / fault-tree-rca 三个。

## 指标不固定(重要)
本 skill **不预设任何具体指标**。你给几种指标的数据,就为几种各生成一个解析脚本;指标名由你定。新增指标**不改本 skill**,只是多走一遍下面的"三级链"。

## 核心机关:三级提示词链
```
references/meta_prompt.md(元提示词,指标无关,只此一份)
   │  喂:故障子树 md + 某指标的数据(真实或样例皆可)
   ▼
该指标的「解析脚本生成提示词」(生成物 → codegen_prompts/<指标>_codegen_prompt.md)
   │  喂:该指标的数据
   ▼
该指标的解析脚本(生成物 → scripts/<指标>_analyzer.py,自包含)
```
泛化能力全在**元提示词**:它对任意故障子树、任意指标都适用。

## 输入
- `fault_tree_excel`(必填):故障树 `.xlsx`。
- `scenario_name`(必填):场景名。**它是"子树选择器"**——从大故障树里截出该场景子树(作 `--start-node`),并给产物命名。不传它,`excel_to_tree` 会把整棵大树全导出(可能上百 MB)。
- 每种指标一份**数据**(真实数据或小样例均可;用于生成脚本 + 跑验证)。
- (可选)`start_level`:场景节点所在层级(大树里场景常在 L2,则传 `--start-level 2`);`exclude_pattern` 等。

## 流水线(把脚本当命令行跑,**不要 import**,用绝对路径)
约定工作目录 `<work>`。

### 步骤 1:Excel → 该场景子树 md
```bash
python "<本skill>/scripts/excel_to_tree.py" "<fault_tree_excel>" \
  -o "<work>/tree/<scenario_name>.md" \
  --start-node "<scenario_name>" [--start-level 2] [--exclude-pattern "<正则>"]
```
> **务必带 `--start-node "<scenario_name>"`**,否则导出整棵大树。

### 步骤 2:逐指标走三级链生成解析脚本
对每个指标 `<ind>`:
1. 读 `references/meta_prompt.md`,注入(子树 md + 该指标数据)→ 产出「解析脚本生成提示词」,保存 `<work>/codegen_prompts/<ind>_codegen_prompt.md`。
2. 读上一步的 codegen 提示词,产出**自包含**解析脚本,保存 `<work>/scripts/<ind>_analyzer.py`。
   - 自包含 = 脚本自己内联读取函数(支持 csv/xlsx + 编码检测),**不 import 任何本仓自定义模块**;确定性计算(算术/计数/占比/比较)都在脚本里完成。

### 步骤 3:验证每个解析脚本
```bash
python "<本skill>/scripts/validate_parser.py" "<work>/scripts/<ind>_analyzer.py" "<该指标数据>"
```
要求正常退出且产出**合法 JSON**;不过则按报错修脚本,重跑直到通过。

### 步骤 4:归集产物
```bash
python "<本skill>/scripts/assemble_scenario.py" \
  --scenario "<scenario_name>" --work "<work>" --indicators <ind1>,<ind2> \
  --out "<输出目录>"
```
它把子树 md、各解析脚本、codegen 提示词归到 `<输出目录>`,并生成 `plan.json`(SCoT)。

## 输出目录长这样
```
<场景产物>/
├── plan.json                                 # SCoT 执行计划(产物①)
├── scripts/<ind>_analyzer.py                 # 各指标解析脚本(产物②,自包含)
├── tree/<scenario>.md                        # 故障树 md(产物③)
└── codegen_prompts/<ind>_codegen_prompt.md   # 留痕
```
跑它:`python "<cs-scot-execute>/scripts/runner.py" --plan "<场景产物>/plan.json" --data <ind>=<数据文件> ...`

## 注意
- 所有提示词与脚本用**中文**;脚本 Python 3.11、函数中文注释、完善异常处理、**自包含**。
- 数据 xlsx/csv 都支持(xlsx 需 `pip install openpyxl`)。
- 确定性计算放进解析脚本,模糊判断留给 fault-tree-rca。
