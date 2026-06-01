---
name: fault-tree-analyzer
description: 输入故障名称，自动从scot-me-generate/result目录读取文件里的数据分析结果，根据故障树匹配节点并返回多个根因路径和判断依据
version: 1.2.0
inputs:
  - name: fault_name
    type: string
    required: true
    description: 故障名称，如"SA场景接入失败"
  - name: data_source_path
    type: string
    required: false
    description: JSON数据来源路径，默认从scot-me-generate/result目录读取
output:
  type: json
  description: 返回匹配的节点路径、根因和判断依据的JSON数组
---

# 故障树分析器

## 技能说明

本技能根据输入的JSON监控指标数据和故障名称，分析故障原因并返回根因路径。

## 执行流程

### 步骤1：读取JSON数据

从数据源目录读取所有 JSON 文件：
- 默认目录：`~\.claude\skills\scot-me-generate\generate\result\[故障名称]\`
- 读取目录下所有 `.json` 文件
- 合并所有 JSON 数据用于故障匹配

### 步骤2：读取故障树

读取对应的故障树`.md`文件，位置 `~\.claude\skills\scot-me-generate\generate\ref\[故障名称].md`

### 步骤3：解析故障树并匹配节点

**由LLM直接处理，无需代码**：

1. **解析故障树markdown文件**：
   - 识别 `#` 标题确定节点层级
   - 提取每个节点标题及其下方的判断条件文本

2. **解析JSON数据**：
   - 从 result 目录下所有 JSON 文件读取数据
   - 解析 KPI 计数器统计、CHR 错误类型分布、内部原因分布等

3. **计算并匹配判断条件**：
   - 支持运算：加法(+)、减法(-)
   - 支持比较符：>、<、>=、<=、=、!=
   - 将JSON中的指标值代入条件表达式进行计算
   - **遍历故障树所有分支，找出所有满足条件的节点**
   
4. **追溯路径**：
   - 从满足条件的节点向上追溯到根节点的完整路径
   - 排除根节点，输出从根节点到匹配节点的路径
   - **所有满足条件的路径都要输出**

### 步骤4：输出结果

返回JSON格式的分析结果：

```json
[
  {
    "Root_cause": "根因节点名称",
    "Fault_tree_path": ["父节点1", "父节点2", "当前根因节点名称"],
    "evidence_path": ["从父节点1正文中提取的判断依据", "从父节点2正文中提取的判断依据", "从当前根因节点正文中提取的判断依据"]
  }
]
```

~~**evidence_path 与 Fault_tree_path 一一对应规则**：
- `Fault_tree_path` 中的每个元素为对应的节点名称
- `Fault_tree_path` 第 N 个元素对应的判断依据为 `evidence_path` 第 N 个元素
- `evidence_path` 的每个元素**必须严格从对应节点的故障树正文中逐字提取**，不能自行添加、修改、推断或组合任何内容
- 如果某节点正文中没有明确的判断依据（如纯描述性节点），则对应 `evidence_path` 元素为空字符串 `""`，不能自行根据JSON数据生成判断依据
- 正确示例：若 `Fault_tree_path` 为 `["上下文建立失败", "RNL异常", "No Radio Resource Available"]`
  - `evidence_path[0]` 应为从"上下文建立失败"正文**逐字提取**的关键判断依据，如"N.UECntx.Est.Att - N.UECntx.Est.Succ > 0"（如果正文中有这个表达式）
  - `evidence_path[1]` 应为从"RNL异常"正文**逐字提取**的关键判断依据，如"N.QosFlow.FailEst.RNL > 0"
  - `evidence_path[2]` 应为从"No Radio Resource Available"正文**逐字提取**的关键判断依据，如"N.NsaDc.SgNB.Add.Fail.Radio.NoRes > 0"
- **错误示例**：从JSON数据中提取数值附加到判断依据中，如"N.UECntx.Est.Att - N.UECntx.Est.Succ > 0 (CU_UEM_PROC_INTERRUPTION: 12390)" —— 括号内的内容来自JSON，非故障树正文，应删除

## 判断条件匹配示例

输入：
- fault_name: "SA场景接入失败"
- 自动从 `~\.claude\skills\scot-me-generate\generate\result\[故障名称]\` 读取所有JSON文件

处理：
1. 从 result 目录读取所有 JSON 分析结果
2. 根据故障树中的判断条件匹配数据
3. 统计所有满足条件的节点路径

## 使用示例

请使用fault-tree-analyzer技能分析以下数据：
- fault_name: "SA场景接入失败"
- 数据自动从 `~\.claude\skills\scot-me-generate\generate\result\[故障名称]\` 目录下的所有 JSON 文件读取
