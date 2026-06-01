---
name: scot-me-execute
description: >
  Execute a SCOT (Semi-automatic Computerized Operation Tree) plan by fault name.
  Use this skill when users want to run a SCOT plan for fault diagnosis. It reads the scot file from ref/ folder,
  executes each step, and returns the root cause analysis result. Triggers when users say "execute SCOT for [故障名称]",
  "run fault analysis", or want to execute a previously generated SCOT plan.
version: 1.0.0
inputs:
  - name: fault_name
    type: string
    required: true
    description: 故障树名称
output:
  type: json
  description: 根因分析结果JSON，包含Root_cause、Fault_tree_path、evidence_path
---

# SCOT Plan 执行器

## 技能说明

本技能根据故障名称，从 `/generate/ref/` 目录读取预生成的SCOT计划文本，解析并执行每个步骤，最终输出根因分析结果。

## 执行流程

### 步骤1：读取SCOT文件

从 `/generate/ref/{fault_name}.txt` 读取SCOT计划文本。

**SCOT格式**：
```text
Step-1: [输入文件]#tool
执行[命令]
<说明文字>

Step-2: [输入文件]#tool
执行[命令]
<说明文字>

Step-3: [故障名称, 结果文件...]#skill
执行[技能调用说明]
<说明文字>
```

### 步骤2：解析并执行每个Step

逐条解析SCOT文本中的Step，执行类型：
- `tool` - 执行Python命令
- `skill` - 调用其他技能

### 步骤3：执行最后的故障树分析

当执行到 `kind: skill` 的步骤时，调用 `fault-tree-analyzer` 技能进行故障树分析。

### 步骤4：输出结果

返回根因分析结果JSON：

```json
[{
  "Root_cause": "根因节点名称",
  "Fault_tree_path": ["父节点1", "父节点2", "当前节点"],
  "evidence_path": ["从父节点1提取的判断依据", "从父节点2提取的判断依据", "从当前节点提取的判断依据"]
}]
```

## 目录结构

```
scot-me-execute/
├── SKILL.md
├── data/
│   └── ...
└── generate/
    └── ...
```

**注意**：SCOT计划文本存储在 `scot-me-generate/generate/ref/` 目录下。

## 使用示例

**输入**：
```
故障名称：{fault_name}
```

**执行流程**：
1. 读取 `scot-me-generate/generate/ref/{fault_name}.txt`
2. 执行 Step-1 (tool)
3. 执行 Step-2 (tool)
4. 执行 Step-3 (skill)：调用 `fault-tree-analyzer` 技能
5. 输出根因分析结果

**输出**：
```json
[{
  "Root_cause": "根因节点名称",
  "Fault_tree_path": ["父节点1", "父节点2", "当前节点"],
  "evidence_path": ["从父节点1提取的判断依据", "从父节点2提取的判断依据", "从当前节点提取的判断依据"]
}]
```

## 执行模式

### run (默认)
首次执行，从Step-1开始按顺序执行所有步骤。

### dry_run
仅校验SCOT文件格式和步骤可行性，不实际执行。
