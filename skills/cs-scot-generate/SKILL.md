---
name: cs-scot-generate
description: 把一棵故障树(Excel)+ 若干指标的数据,编译成一个场景的诊断产物:各指标的解析脚本、一份真正的 SCoT 执行计划 plan.json、以及该场景的故障树 md。当用户要为某网络保障场景生成诊断脚本与执行计划、或说"生成故障树诊断脚本 / 把这个故障树做成可执行的诊断 / 生成 SCoT"时,务必使用本技能。指标种类不固定,由用户提供的数据决定。
---

# cs-scot-generate —— 故障诊断产物生成器

把一棵**故障树(Excel)+ 若干指标的数据**,编译成某场景的**诊断产物**。

## 产物就是这三样(外加留痕)
1. **SCoT 执行计划** `plan.json` —— 真正的 SCoT 计划(v4.0 子集:`entry`+`nodes`+`transition`+`$ref`),由 cs-scot-execute 跑。
2. **各指标的解析脚本** `scripts/<指标>_analyzer.py`(确定性、**自包含**:内联读取,只用标准库;读 xlsx 才需 openpyxl)。
3. **故障树 md** `tree/<场景>.md`(由 Excel 截取该场景子树转成)。

> 留痕:还会保存生成各脚本所用的 codegen 提示词到 `codegen_prompts/`。
> 这些产物是**数据/资产**,不是 skill——真正的 skill 只有 cs-scot-generate / cs-scot-execute / fault-tree-rca 三个。

## 指标不固定(重要)
本 skill **不预设任何具体指标**。你给几种指标的数据,就为几种各生成一个解析脚本;指标名由你定。

## 核心机关:三级提示词链
```
references/meta_prompt.md(元提示词,指标无关,只此一份)
   │  喂:故障子树 md + 某指标的数据
   ▼
该指标的「解析脚本生成提示词」(生成物 → codegen_prompts/<指标>_codegen_prompt.md)
   │  喂:该指标的数据
   ▼
该指标的解析脚本(生成物 → scripts/<指标>_analyzer.py,自包含、精简)
```

## 输入
- `fault_tree_excel`(必填):故障树 `.xlsx`。
- `scenario_name`(必填):场景名,作 `--start-node` 截取该场景子树并给产物命名。
- 每种指标一份**数据**(真实或样例皆可;用于生成脚本 + 跑验证)。

## 流水线(把脚本当命令行跑,**不要 import**,用绝对路径)
约定工作目录 `<work>`。

### 步骤 1:Excel → 该场景子树 md
```bash
python "<本skill>/scripts/excel_to_tree.py" "<fault_tree_excel>" \
  -o "<work>/tree/<scenario_name>.md" \
  --start-node "<scenario_name>"
```
- 背景业务知识:场景节点通常在 **L2**,`--start-level` 已默认 2;`--start-node` 在该层选定场景、截取以它为根的子树(万一不在 L2 会自动兜底到别的层)。
- `--exclude-pattern` 默认排除噪声节点 `萃取新增节点-.*`;传 `--exclude-pattern ""` 保留全部。
- 这样出来的是**单场景的精简子树**(几十 KB 量级),不是整棵大树。**务必用本 CLI**,别自己 import 函数跑。

### 步骤 2:逐指标走三级链生成解析脚本
对每个指标 `<ind>`:
1. 读 `references/meta_prompt.md`,注入(子树 md + 该指标数据)→ 产出「解析脚本生成提示词」,保存 `<work>/codegen_prompts/<ind>_codegen_prompt.md`。
2. 据其产出**自包含**解析脚本,保存 `<work>/scripts/<ind>_analyzer.py`。
   - 自包含 = 内联读取函数,**不 import 任何本仓自定义模块**。
   - **精简忠实**:只抽取子树判断条件涉及的数据、只计算**子树里写明的**条件(`expr` 逐字取自子树);**不要发明阈值/条件**,不做分布统计、失败分类、记录转储等子树没要求的事。综合判断与根因是 fault-tree-rca 的活。

### 步骤 3:验证每个解析脚本
```bash
python "<本skill>/scripts/validate_parser.py" "<work>/scripts/<ind>_analyzer.py" "<该指标数据>"
```
要求正常退出且产出**合法 JSON**;不过则按报错修脚本重跑。

### 步骤 4:归集产物(生成真正的 SCoT 计划)
```bash
python "<本skill>/scripts/assemble_scenario.py" \
  --scenario "<scenario_name>" --work "<work>" --indicators <ind1>,<ind2> \
  --out "<输出目录>"
```
它把子树、各解析脚本、codegen 提示词归到 `<输出目录>`,并生成 **`plan.json`(真 SCoT v4.0)**:
每个指标一个 `kind=script` 节点 → 末尾一个 `kind=agent_skill`(fault-tree-rca)节点,用
`transition` 串成直线、`$input/$memory/$output` 传参。格式见 `references/plan_schema.md`。

## 输出目录长这样
```
<场景产物>/
├── plan.json                                 # SCoT 计划(产物①;version 4.0 / entry / nodes)
├── scripts/<ind>_analyzer.py                 # 各指标解析脚本(产物②,自包含、精简)
├── tree/<scenario>.md                        # 故障树 md(产物③)
└── codegen_prompts/<ind>_codegen_prompt.md   # 留痕
```
跑它:`python "<cs-scot-execute>/scripts/runner.py" --plan "<场景产物>/plan.json" --data <ind>=<数据文件> ...`

## 注意
- 提示词与脚本用**中文**;脚本 Python 3.11、中文注释、**自包含、精简**。
- 数据 xlsx/csv 都支持(xlsx 需 `pip install openpyxl`)。
- 确定性计算放进解析脚本(只算子树写明的条件),综合判断留给 fault-tree-rca。
