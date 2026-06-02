# SCoT Plan(执行计划)Schema v1.0

本文件定义 `cs-scot-generate` **产出**、`cs-scot-execute` **消费**的 SCoT 执行计划(`plan.json`)格式。两个 skill 都遵循本契约。

## 设计原则
- **直线**:故障树的分支判断发生在 RCA 内部(遍历子树),不在计划层。计划只描述"按顺序解析哪些指标,最后做一次根因分析"。
- **显式 IO**:每个 step 明确声明输入数据与输出文件,杜绝 legacy 里"生产者/消费者路径对不上"的问题。
- **按 kind 分派**:`cs-scot-execute` 依据 step 的 `kind` 决定怎么执行;新增 kind 只需在 execute 里加一个分支。

## 顶层结构
```json
{
  "version": "1.0",
  "scenario": "SA场景接入失败",
  "fault_tree": "tree/SA场景接入失败.md",
  "indicators": ["kpi", "chr"],
  "steps": [ /* 见下 */ ]
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `version` | 是 | 计划格式版本,当前 `"1.0"` |
| `scenario` | 是 | 场景名 |
| `fault_tree` | 是 | 故障子树 md 的相对路径(场景产物内) |
| `indicators` | 是 | 本场景涉及的指标列表(由用户数据决定),如 `["kpi","chr"]` |
| `steps` | 是 | 有序步骤数组,逐个执行 |

## Step kinds(v1 定义两种;`cs-scot-execute` 必须全部支持)

### kind = `script` —— 确定性解析脚本
```json
{"id":"kpi","kind":"script","run":"scripts/kpi_analyzer.py","data":"$data.kpi","out":"result/kpi.json","desc":"解析 kpi 指标"}
```
- 执行语义:`<python> <run> <实际数据文件> -o <out>`。
- `data` 用占位符 `$data.<指标名>`,执行时由调用方/harness 绑定到该指标的实际数据文件(xlsx/csv)。
- 用途:把某指标原始数据**抽取/计算**成规范 JSON,确定性计算都在这里完成。解析脚本是**自包含**的(内联读取,无外部依赖)。

### kind = `rca` —— 综合根因分析(唯一的模型判断步骤)
```json
{"id":"rca","kind":"rca","tree":"tree/SA场景接入失败.md","inputs":["result/kpi.json","result/chr.json"],"out":"result/rca.json","desc":"综合根因分析(用 fault-tree-rca 技能)"}
```
- 执行语义:把 `tree` 正文 + 各 `inputs` JSON 汇总成 `result/rca_input.json`,再由 **fault-tree-rca 技能**(模型判断)产出根因数组写到 `out`。
- 前面的 `script` 步骤都是确定性的;只有这一步是模型判断,用来吸收人工编写故障树的不规整/谬误。
- (注:RCA 规则与契约由 fault-tree-rca 技能自带,不放在场景产物里。)

## 引用占位
- `$data.<指标名>`:运行时绑定的该指标实际数据文件路径(xlsx 或 csv)。

## 一个完整示例
```json
{
  "version": "1.0",
  "scenario": "SA场景接入失败",
  "fault_tree": "tree/SA场景接入失败.md",
  "indicators": ["kpi", "chr"],
  "steps": [
    {"id":"kpi","kind":"script","run":"scripts/kpi_analyzer.py","data":"$data.kpi","out":"result/kpi.json","desc":"解析 kpi 指标"},
    {"id":"chr","kind":"script","run":"scripts/chr_analyzer.py","data":"$data.chr","out":"result/chr.json","desc":"解析 chr 指标"},
    {"id":"rca","kind":"rca","tree":"tree/SA场景接入失败.md","inputs":["result/kpi.json","result/chr.json"],"out":"result/rca.json","desc":"综合根因分析(用 fault-tree-rca 技能)"}
  ]
}
```
