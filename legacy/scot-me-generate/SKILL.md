---
name: scot-me-generate
description: >
  Generate a SCOT (Semi-automatic Computerized Operation Tree) plan text from a fault name.
  Use this skill when users want to generate an execution plan for fault diagnosis without running it.
  Triggers when users say "generate SCOT plan", "create execution plan for [故障名称]", or want to plan
  a diagnostic workflow before execution. The output is plain text scot plan saved to ref/ folder.
version: 1.1.0
inputs:
  - name: fault_name
    type: string
    required: true
    description: 故障名称
  - name: excel_path
    type: string
    required: false
    default: "data/EnhancedExcelDataExport (7).xlsx"
    description: Excel文件路径，默认使用 data/EnhancedExcelDataExport (7).xlsx
output:
  type: text
  description: SCOT执行计划文本，保存到generate/ref/[故障名称].txt
---

# SCOT Plan 生成器

## 技能说明

本技能根据输入的故障名称和Excel文件路径，依次完成：
1. 调用 excel_to_tree.py 生成故障树
2. 基于数据文件和prompt生成分析脚本
3. 生成SCOT执行计划文本

## 执行流程

### 步骤1：生成故障树

**检查文件是否存在**：如果 `generate/tree/[故障名称].md` 已存在，则跳过此步骤。

调用 excel_to_tree.py 将Excel数据转换为故障树Markdown：

```bash
python scripts/excel_to_tree.py "data/EnhancedExcelDataExport (7).xlsx" \
  -o "generate/tree/[故障名称].md" \
  --start-level 2 \
  --start-node "[故障名称]" \
  --exclude-pattern "萃取新增节点-.*"
```

### 步骤2：生成分析脚本

**检查文件是否存在**：如果目标脚本已存在（`generate/scripts/[故障名称]/kpi/kpi_tree_analyzer.py` 和 `generate/scripts/[故障名称]/chr/sa_chr_analyzer.py`），则跳过此步骤。

基于 `generate/tree/[故障名称].md` 故障树文件，根据对应的prompt生成脚本：

| prompt文件 | 脚本输出目录 |
|-----------|------------|
| kpi_analysis_prompt.md | generate/scripts/[故障名称]/kpi/ |
| chr_analysis_prompt.md | generate/scripts/[故障名称]/chr/ |


脚本生成逻辑：
- 读取对应的prompt模板（kpi_analysis_prompt.md 或 chr_analysis_prompt.md）
- 结合故障树内容（generate/tree/[故障名称].md）
- 生成Python分析脚本

### 步骤3：运行验证脚本

**条件执行**：仅当 `data/[故障名称]/` 目录下存在对应数据文件时执行。若没有对应的数据文件则跳过此步骤。

数据文件路径：
- KPI数据：`data/[故障名称]/kpiTree.csv`
- CHR数据：`data/[故障名称]/chrData.csv`

**验证命令**：
```bash
# 验证KPI脚本（若kpiTree.csv存在）
python generate/scripts/[故障名称]/kpi/kpi_tree_analyzer.py \
  generate/tree/[故障名称].md \
  data/[故障名称]/kpiTree.csv \
  -o result/[故障名称]/kpi_analysis.json

# 验证CHR脚本（若chrData.csv存在）
python generate/scripts/[故障名称]/chr/sa_chr_analyzer.py \
  -i data/[故障名称]/chrData.csv \
  -o result/[故障名称]/chr_analysis.json \
  -t generate/tree/[故障名称].md
```

**验证通过条件**：
1. 脚本正常退出（exit code = 0）
2. 输出JSON文件成功生成
3. 无编码错误或解析错误

**注意**：如果验证失败，需要修复脚本后重新验证，直到通过。不需要生成额外的测试文件。

### 步骤4：生成SCOT执行计划

**检查文件是否存在**：如果 `generate/ref/[故障名称].txt` 已存在，则跳过此步骤。

根据生成的脚本，按顺序生成SCOT执行计划。假设生成了 kpi 和 chr 两个分析脚本：


**SCOT格式说明**：
- **Step-1 ~ Step-N-1**: 基于生成的脚本顺序，每个脚本对应一个Step
  - kind: tool
  - 工具: `python generate/scripts/[故障名称]/[类型]/[脚本名].py`
  - 输入: 对应的数据文件
  - 输出: 结果JSON文件

- **Step-N**: 最后调用 fault-tree-analyzer 技能
  - kind: skill
  - 技能名称: fault-tree-analyzer
  - 输入: 故障名称 + 前N-1步的结果文件
  - 输出: 根因路径和判断依据

**SCOT输出示例**：
```text
Step-1: [kpiTree.csv]#tool
执行[python generate/scripts/[故障名称]/kpi/kpi_tree_analyzer.py data/[故障名称]/kpiTree.csv -o result/[故障名称]/kpi_analysis.json]

Step-2: [chrData.csv]#tool
执行[python generate/scripts/[故障名称]/chr/sa_chr_analyzer.py -i data/[故障名称]/chrData.csv -o result/[故障名称]/chr_analysis.json]

Step-3: [fault_name, result/[故障名称]/kpi_analysis.json, result/[故障名称]/chr_analysis.json]#skill
执行[调用技能「fault-tree-analyzer」完成故障树分析]
```

## 目录结构

```
scot-me-generate/
├── SKILL.md
├── scripts/
│   └── excel_to_tree.py
├── data/
│   └── EnhancedExcelDataExport (7).xlsx
├── prompt/
│   ├── chr_analysis_prompt.md
│   └── kpi_analysis_prompt.md
└── generate/                 (所有生成的文件和脚本)
    ├── tree/
    │   └── [故障名称].md     (生成的故障树)
    ├── scripts/
    │   └── [故障名称]/
    │       ├── chr/          (生成的CHR分析脚本)
    │       └── kpi/          (生成的KPI分析脚本)
    └── ref/
        └── [故障名称].txt    (生成的SCOT计划)
```

## 使用示例

**输入**：
```
故障名称：SA场景接入失败
excel文件：data/EnhancedExcelDataExport (7).xlsx
```

**输出**：
1. 故障树：generate/tree/SA场景接入失败.md
2. 分析脚本：generate/scripts/SA场景接入失败/chr/xxx.py, generate/scripts/SA场景接入失败/kpi/xxx.py
3. SCOT计划：generate/ref/SA场景接入失败.txt

## 脚本生成规则

生成的分析脚本应：
1. 接受数据文件路径作为参数
2. 支持 --output 参数指定输出路径
3. 输出JSON格式的分析结果
4. 自动检测文件编码（UTF-8/GBK）
