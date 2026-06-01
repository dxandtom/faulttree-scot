# SCoT Plan(执行计划)Schema v1.0 —— 执行侧视图

> 本文件与 `scot-generate/references/plan_schema.md` 保持一致(同一份契约的执行侧镜像)。`scot-execute` 依据它消费 `plan.json`。

## 顶层结构
```json
{
  "version": "1.0",
  "scenario": "SA场景接入失败",
  "fault_tree": "tree/SA场景接入失败.md",
  "indicators": ["kpi", "chr"],
  "steps": [ /* 有序,逐个执行 */ ]
}
```

## Step kinds(execute 必须全部支持)

### `script` —— 确定性解析脚本
```json
{"id":"kpi","kind":"script","run":"scripts/kpi_analyzer.py","data":"$data.kpi","out":"result/kpi.json"}
```
执行:`<python> <run> <绑定数据> -o <out>`。`data` 为 `$data.<指标>` 占位,运行时由 `--data <指标>=<文件>` 绑定到实际 xlsx/csv。

### `rca` —— 综合根因分析(唯一模型判断步骤)
```json
{"id":"rca","kind":"rca","tree":"tree/x.md","inputs":["result/kpi.json","result/chr.json"],"rules":"references/rca_rules.md","out":"result/rca.json"}
```
执行:汇总 `tree` 正文 + 各 `inputs` JSON + `rules` → `result/rca_input.json`;再按规则产出 `out`。

## 路径解析
所有相对路径相对 `--base`(默认 `plan.json` 所在目录)解析。

## 扩展
新增 step kind 时:① 在本 schema 增加定义;② 在 `scripts/runner.py` 的分派里加一个分支。当前 v1 仅 `script` / `rca`。
