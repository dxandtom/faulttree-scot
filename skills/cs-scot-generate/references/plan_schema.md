# SCoT 计划格式（本项目采用 SCoT v4.0 的子集）

`cs-scot-generate` 产出、`cs-scot-execute` 消费的 **`plan.json` 是一份真正的 SCoT 计划**，遵循 SCoT plan schema（见仓库 `scot_reference/plan_schema.md`）。我们只用它的**子集**：

- 节点 `kind` 只用 `script`（跑生成的逐条件分析脚本）和 `agent_skill`（模型判断，fault-tree-rca）；
- 转移只用直线 `transition.next_on_success` / `end`；
- 节点间用 `$input` / `$memory` / `$step.<id>` / `$output` 引用传数据。

这样产出的就是**合法 SCoT 计划**，既能被本项目的轻量执行器跑，也兼容真 SCoT 执行器。

## 顶层结构

```json
{
  "version": "4.0",
  "plan_id": "plan_<短名>",
  "meta": {"goal": "...", "success_criteria": ["..."]},
  "entry": "<起始节点 id>",
  "memory_init": {"fault_tree_md": "tree/<短名>.md"},
  "nodes": [ /* 见下 */ ]
}
```

- `entry`：从哪个节点开始；`memory_init`：工作记忆初值（这里放故障树 md 路径）。
- `<短名>` 是场景的英文短名，取不到英文时是中文场景名，与 `tree/` 下的文件名一致。

## 节点（NodeSpec）

### kind = `script`：跑分析脚本（确定性）

```json
{"id":"kpi","kind":"script","name":"kpi_analyzer","purpose":"解析 kpi 指标",
 "inputs":{"data":"$input.kpi"},
 "outputs":{"result":"$memory.kpi_result"},
 "transition":{"next_on_success":"chr"}}
```

- `name` → `scripts/<name>.py`；runner 执行 `python scripts/<name>.py <inputs.data 解析出的数据文件> -o <临时>`，把产出 JSON 存到 `outputs` 指定的 `$memory` 位置。
- `inputs.data` 用 `$input.<指标>` 占位，运行时由 `--data <指标>=<文件>` 绑定。
- 特例：跨源判据脚本 `cross_analyzer` 的节点（步骤 2 组装时自动加入）`inputs.data` 写成 `"$input.*"`，runner 把本轮提供的全部数据源以 `--data <源>=<路径>` 逐个递给它；它不进"缺源即跳过"的清单，给多少数据就用多少，没有跨源绑定时秒过。

### kind = `agent_skill`：模型判断（fault-tree-rca）

```json
{"id":"rca","kind":"agent_skill","name":"fault-tree-rca","purpose":"综合根因分析",
 "inputs":{"fault_tree_md":"$memory.fault_tree_md","kpi":"$memory.kpi_result","chr":"$memory.chr_result"},
 "outputs":{"result":"$output.final_answer"},
 "transition":{"end":true}}
```

- runner 解析 inputs（`fault_tree_md` 若是 md 路径则读成正文，其余按指标名归到 `indicator_results`；上游被跳过或未产出的输入不进 `indicator_results`，改记顶层 `missing_indicators`，键是源名、值是原因），写到本轮结果目录的 `rca_input.json`，由 **fault-tree-rca 技能**依据它产出根因。
- 注意：`indicator_results` 是**完整形态**，只在没有 `tree_index.json` 或剪枝不划算时出现。产物带 `tree_index.json` 时（常态），runner 会把 rca_input 剪成局部子树形态（`fault_tree.outline`、`pruned_nodes` 等，没有 `indicator_results` 键）。两种形态的权威定义见 fault-tree-rca 的 SKILL.md。

## 典型流水线（本项目）

`各 <指标>_analyzer（script，读表 → 逐条件统计）` → `cross（script，跨源判据；没有跨源绑定时秒过）` → `rca（agent_skill，唯一的模型步骤）`。

script 节点跑的是 generate 生成的逐条件分析脚本，直接产出该源的逐条件事实；execute 不再加工数据。

## 引用命名空间

| 前缀 | 含义 |
|------|------|
| `$input.<key>` | 运行时 `--data key=<文件>` 绑定的输入 |
| `$memory.<path>` | 工作记忆（节点输出写这里、下游从这里读） |
| `$step.<id>.<field>` | 某节点的输出字段 |
| `$output.<key>` | 最终输出缓冲 |

## 暂未用到的（保持轻量，schema 兼容，需要再加）

SCoT 还有 `llm_text` / `ask_user` / `noop` / `for_each` 等 kind，以及 `transition.conditions` / `parallel_after` / `meta.budget` / `reflection` / `policy` 等。本项目是直线诊断，用不到这些，先不实现。
