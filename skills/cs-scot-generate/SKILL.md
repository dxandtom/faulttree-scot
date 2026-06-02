---
name: cs-scot-generate
description: 把"故障子树 Excel + 各指标数据样例"编译成一个自包含的故障诊断场景 skill。当用户要为某网络保障场景(如 SA场景接入失败)生成诊断脚本与执行计划 SCoT、或说"生成故障树诊断 skill / 生成解析脚本 / 把这个故障树做成场景"时,务必使用本技能。产物两大件:各指标的解析脚本,以及生成并保存的 SCoT 执行计划 plan.json,最终打包成可上传平台、在独立容器里端到端跑出根因的场景 skill。
---

# cs-scot-generate —— 故障诊断场景 skill 生成器

## 这个 skill 干什么
把一个**故障子树(Excel)+ 各指标数据样例**,编译成一个**自包含的场景 skill**(可上传平台、在独立容器里端到端跑出根因)。

产物两大件:
1. 各指标的**解析脚本**(`scripts/<指标>_analyzer.py`);
2. **SCoT 执行计划**(`plan.json`)—— 必须生成并保存,见步骤 4。

## 核心思想:三级生成链(千万不要写死指标)
```
元提示词(references/meta_prompt.md,指标无关)
   │  喂:故障子树 + 某指标的数据格式/样例
   ▼
该指标的「解析脚本生成提示词」(生成物,每指标一份 → codegen_prompts/<指标>_codegen_prompt.md)
   │  喂:该指标数据
   ▼
该指标的解析脚本(生成物 → scripts/<指标>_analyzer.py)
```
当前指标是 `kpi` 和 `chr`;将来新增指标时**不改本 skill**,只是多走一遍同样的三级链。元提示词是唯一需要泛化好的资产——它对任意故障子树、任意指标都适用。

## 输入
- `fault_tree_excel`(必填):故障树 `.xlsx`。
- `scenario_name`(必填):场景名,如 `SA场景接入失败`。
- 每个指标一份**数据样例 + 格式说明**:当前为 `kpi`、`chr`(xlsx 或 csv 均可)。
- (可选)Excel 截取参数 `start_level` / `start_node` / `exclude_pattern`,透传给 `excel_to_tree.py`。

> 注:开发时不需要真实保密数据,用结构真实、内容虚构的小样例即可生成并验证脚本。

## 流水线(按序执行)
约定一个工作目录 `<work>`(下面各步骤的中间产物都放这里)。

### 步骤 1:故障树 Excel → 子树 Markdown(确定性)
```bash
python scripts/excel_to_tree.py <fault_tree_excel> \
  -o <work>/tree/<scenario_name>.md \
  [--start-level N --start-node <名> --exclude-pattern <正则>]
```

### 步骤 2:逐指标走三级链生成解析脚本
对每个指标 `<ind>`(kpi、chr、…):
1. 读 `references/meta_prompt.md`,注入(子树 md + 该指标的数据格式说明/样例)→ 产出该指标的「解析脚本生成提示词」,**保存**到 `<work>/codegen_prompts/<ind>_codegen_prompt.md`。
2. 读上一步生成的 codegen prompt,据其要求 → 产出解析脚本,**保存**到 `<work>/scripts/<ind>_analyzer.py`。

> 这两步是"模型生成"步骤(由你/harness 的模型按提示词来写)。确定性的算术计算要写进生成的脚本里,不要留给后面的 RCA 去"目测"。

### 步骤 3:验证每个解析脚本(闭环)
```bash
python scripts/validate_parser.py <work>/scripts/<ind>_analyzer.py <该指标样例数据>
```
要求脚本正常退出且产出**合法 JSON**;不过则按报错修脚本,重跑直到通过。

### 步骤 4:生成并保存 SCoT(plan.json)—— 一等产物
依据已生成的指标脚本,生成一条**直线计划**:每个指标一个 `kind=script` 步骤,最后一个 `kind=rca` 步骤。格式见 `references/plan_schema.md`。由 `assemble_scenario.py` 自动生成并写入场景目录(见步骤 5),也可单独保存到 `<work>/plan.json`。

### 步骤 5:打包自包含场景 skill
```bash
python scripts/assemble_scenario.py \
  --scenario <scenario_name> --work <work> \
  --indicators kpi,chr \
  --out examples/<scenario_name>
```
它会把:子树 md、各指标解析脚本、`data_loader.py`、cs-scot-execute 的 `runner.py`、fault-tree-rca 的 `rca_rules.md`、生成的 `plan.json`、以及一份场景 `SKILL.md`(由 `references/scenario_template/SKILL.md.tmpl` 渲染)全部拷/写进 `examples/<scenario_name>/`,形成**可上传的自包含 skill**。

## 输出物(位于 examples/<scenario_name>/)
- `scripts/<ind>_analyzer.py` —— 生成的解析脚本(**产物①**)
- `plan.json` —— 生成的 SCoT 执行计划(**产物②**)
- `tree/<scenario>.md` —— 故障子树
- `codegen_prompts/<ind>_codegen_prompt.md` —— 生成的指标提示词(可追溯/可调)
- `scripts/data_loader.py`、`scripts/runner.py`、`references/rca_rules.md`、`SKILL.md` —— 运行所需(拷入,保证自包含)

## 注意
- 所有生成的提示词与脚本用**中文**;脚本 Python 3.11、函数中文注释、完善异常处理。
- 数据 xlsx/csv 都要支持(靠 `data_loader`)。
- 故障子树是人工编写、可能不规整的;**确定性计算放进解析脚本,模糊判断留给 RCA**。
