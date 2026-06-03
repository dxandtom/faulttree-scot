# SCoT Plan Schema(本项目采用 SCoT v4.0 的子集)

`cs-scot-generate` 产出、`cs-scot-execute` 消费的 **`plan.json` 是一份真正的 SCoT 计划**,
遵循 SCoT plan schema(见仓库 `scot_reference/plan_schema.md`)。我们只用它的**子集**:
- 节点 `kind` 用 `script`(跑解析脚本)、`condition_eval`(逐条件绑定求值)、`agent_skill`(模型步骤:绑定 / RCA);
- 转移只用直线 `transition.next_on_success` / `end`;
- 节点间用 `$input` / `$memory` / `$step.<id>` / `$output` 引用传数据。

这样产出的就是**合法 SCoT 计划**,既能被本项目的轻量执行器跑,也兼容真 SCoT 执行器。

## 顶层结构
```json
{
  "version": "4.0",
  "plan_id": "plan_<场景>",
  "meta": {"goal": "...", "success_criteria": ["..."]},
  "entry": "<起始节点 id>",
  "memory_init": {"fault_tree_md": "tree/<场景>.md"},
  "nodes": [ /* 见下 */ ]
}
```
- `entry`:从哪个节点开始;`memory_init`:工作记忆初值(这里放故障树 md 路径)。

## 节点(NodeSpec)

### kind = `script` —— 跑解析脚本(确定性)
```json
{"id":"kpi","kind":"script","name":"kpi_analyzer","purpose":"解析 kpi 指标",
 "inputs":{"data":"$input.kpi"},
 "outputs":{"result":"$memory.kpi_result"},
 "transition":{"next_on_success":"chr"}}
```
- `name` → `scripts/<name>.py`;runner 执行 `python scripts/<name>.py <inputs.data 解析出的数据文件> -o <临时>`,把产出 JSON 存到 `outputs` 指定的 `$memory` 位置。
- `inputs.data` 用 `$input.<指标>` 占位,运行时由 `--data <指标>=<文件>` 绑定。

### kind = `agent_skill` —— 模型判断(fault-tree-rca)
```json
{"id":"rca","kind":"agent_skill","name":"fault-tree-rca","purpose":"综合根因分析",
 "inputs":{"fault_tree_md":"$memory.fault_tree_md","kpi":"$memory.kpi_result","chr":"$memory.chr_result"},
 "outputs":{"result":"$output.final_answer"},
 "transition":{"end":true}}
```
- runner 解析 inputs(`fault_tree_md` 若是 md 路径则读成正文,其余按指标名归到 `indicator_results`),
  备到 `result/rca_input.json`,由 **fault-tree-rca 技能**依据它产出根因。

### kind = `condition_eval` —— 逐条件绑定求值(确定性)
```json
{"id":"eval_conditions","kind":"condition_eval","purpose":"按绑定+各源数据算 condition_facts",
 "inputs":{"conditions":"conditions.json","data":{"kpi":"$input.kpi","chr":"$input.chr"}},
 "outputs":{"result":"$memory.condition_facts"},
 "transition":{"next_on_success":"rca"}}
```
- runner 跑同目录 `eval_conditions.py --conditions <inputs.conditions> --data <源>=<文件>… -o result/condition_facts.json`(再跑 `validate_conditions.py` 自检),把事实存入 `$memory`。
- `inputs.conditions` 是绑定文件(相对产物目录),由上一步 `bind_conditions` 产出。

### agent_skill 可重入(`produces`)
agent_skill 节点可带 `"produces":"<产物路径>"`(如 `conditions.json` / `result/rca.json`):
- 产物**已存在** → runner 载入它、继续;
- **不存在** → 备好 `result/<id>_input.json`、打印待办、**暂停整条计划**;你产出后**重跑同一命令**即从此处续上。

## 典型流水线(本项目)
`各指标 analyzer(script)` → `bind_conditions(agent_skill, produces conditions.json)` → `eval_conditions(condition_eval)` → `rca(agent_skill, produces result/rca.json)`。

## 引用命名空间
| 前缀 | 含义 |
|------|------|
| `$input.<key>` | 运行时 `--data key=<文件>` 绑定的输入 |
| `$memory.<path>` | 工作记忆(节点输出写这里、下游从这里读) |
| `$step.<id>.<field>` | 某节点的输出字段 |
| `$output.<key>` | 最终输出缓冲 |

## 暂未用到的(保持轻量,schema 兼容,需要再加)
SCoT 还有 `llm_text` / `ask_user` / `noop` / `for_each` 等 kind,以及
`transition.conditions` / `parallel_after` / `meta.budget` / `reflection` / `policy` 等。
本项目是直线诊断,用不到这些,先不实现。
