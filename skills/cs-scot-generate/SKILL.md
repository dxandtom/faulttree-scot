---
name: cs-scot-generate
description: 把故障树(Excel)+ 若干指标数据编译成场景诊断产物:各指标解析脚本、SCoT 执行计划 plan.json、故障树 md。用于为网络保障场景(如某接入失败场景)生成可执行的诊断与根因定位。指标种类由用户数据决定,不固定。
---

# cs-scot-generate —— 故障诊断产物生成器

**本文件是给你照做的操作手册,已经加载进来了——不需要、也不要再次加载或调用它。**
**"手册已加载"≠"任务完成":只有当你用 Bash / 文件工具真正跑完下面的流水线、产出 `plan.json` 等文件,任务才算完成。**
**现在立刻开始:你的下一个动作就是用 Bash 运行【步骤 1】那条命令,然后逐步往下做,每步把输出汇报出来。**

你的任务:把一棵**故障树(Excel)+ 若干指标的数据**,编译成某场景的诊断产物——① 各指标解析脚本 `scripts/<指标>_analyzer.py`(确定性、自包含);② SCoT 执行计划 `plan.json`;③ 故障树 md `tree/<场景>.md`(外加 `codegen_prompts/` 留痕)。

> - 下文命令里的 `scripts/xxx.py` / `references/xxx.md` 指**本说明文件同目录**下的文件(通常在 `~/.claude/skills/cs-scot-generate/` 下),用绝对路径调用。
> - 输入路径可能带空格、括号、中文(如 `EnhancedExcelDataExport (7).xlsx`),命令里**整条路径务必加引号**。
> - 产物是数据/资产(交给执行器 cs-scot-execute 去跑),不是可执行程序。指标不固定,你给几种数据就为几种各生成一个解析脚本。

## 核心:三级提示词链(怎么生成解析脚本)
```
references/meta_prompt.md(元提示词,指标无关)
  │ 喂:故障子树 md + 某指标的真实数据
  ▼
该指标的「解析脚本生成提示词」(codegen_prompts/<指标>_codegen_prompt.md)
  │ 喂:该指标的数据
  ▼
该指标的解析脚本(scripts/<指标>_analyzer.py,自包含)
```

## 输入
- 故障树 `.xlsx`(绝对路径)。
- 场景名(作 `--start-node` 截子树、给产物命名)。
- 每种指标一份数据(真实或样例皆可;用于生成脚本 + 跑验证)。

## 产物目录(统一、固定,别到处存)
所有中间过程 + 最终产物都写进**同一个目录** `<OUT>`,默认就在**故障树 xlsx 旁边**:
```
<OUT> = <故障树 xlsx 所在目录>/scot产物/<场景名>/
```
- 步骤 1 的 `excel_to_tree.py` 会**自动算出并打印** `<OUT>`;**后续步骤 2-4 全部用同一个 `<OUT>`**,不要另起临时目录。
- 改位置:给步骤 1 / 4 传 `--out-base "<根目录>"`(产物 = `<根目录>/scot产物/<场景名>`),或给步骤 4 传 `--out "<目录>"` 直接指定。

## 流水线

### 步骤 1:Excel → 该场景子树 md(并定位 `<OUT>`)
```bash
python "<本目录>/scripts/excel_to_tree.py" "<故障树xlsx>" --start-node "<场景名>"
```
- **不带 `-o`**:子树写到 `<OUT>/tree/<场景名>.md`,并打印「本场景产物目录: `<OUT>`」——**记住这个 `<OUT>`,步骤 2-4 都用它**。
- 场景默认在 L2(`--start-level` 默认 2);`--exclude-pattern` 默认排除噪声节点 `萃取新增节点-.*`。出来应是几十 KB 的单场景子树。

### 步骤 2:逐指标走三级链生成解析脚本
对每个指标 `<ind>`:
1. 读 `references/meta_prompt.md`,注入(子树 md + 该指标的真实数据)→ 产出该指标的「解析脚本生成提示词」,写到 `<OUT>/codegen_prompts/<ind>_codegen_prompt.md`。
2. 据其写出**自包含**解析脚本,写到 `<OUT>/scripts/<ind>_analyzer.py`。
要点(详见 meta_prompt):**脚本只查数据、绝不下判断**——数据驱动遍历真实字段做统计 + 把故障树引用到的计数器/字段定向取值;精确映射真实列、不猜列名;**不发明阈值、不拿替身顶替、绝不输出 hit/judgment/"存在 XX 原因"**;数据里没有的如实标 `found:false`。是否成立、根因全部留给 `fault-tree-rca`。输出 JSON 只有事实:`{indicator, summary, field_stats, referenced}`。

### 步骤 3:验证每个解析脚本
```bash
python "<本目录>/scripts/validate_parser.py" "<OUT>/scripts/<ind>_analyzer.py" "<该指标数据>"
```
要求正常退出且产出合法 JSON;不过则按报错修脚本再跑。

### 步骤 4:归集产物(生成真 SCoT 计划)
```bash
python "<本目录>/scripts/assemble_scenario.py" \
  --scenario "<场景名>" --indicators <ind1>,<ind2> --out "<OUT>"
```
`--out` 用步骤 1 打印的 `<OUT>`(work 与 out 同一目录,脚本/树已在里面、不再复制)。产出 `plan.json`(真 SCoT v4.0:`entry`+`nodes`+`transition`+`$ref`)。格式见 `references/plan_schema.md`。

## 输出目录(就是 `<OUT>`)
```
<OUT>/                                       # = <故障树目录>/scot产物/<场景名>/
├── plan.json                              # SCoT 计划(产物①)
├── scripts/<ind>_analyzer.py              # 各指标解析脚本(产物②,自包含)
├── tree/<场景>.md                          # 故障树 md(产物③)
└── codegen_prompts/<ind>_codegen_prompt.md # 留痕
```
跑它:`python "<cs-scot-execute 目录>/scripts/runner.py" --plan "<OUT>/plan.json" --data <ind>=<数据文件> ...`

## 注意
- 提示词与脚本用**中文**;脚本 Python 3.11、自包含、精简忠实。
- 数据 xlsx/csv 都支持(xlsx 需 `pip install openpyxl`)。
- 路径有空格/括号/中文,命令里务必加引号。
