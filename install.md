# 安装指南(FaultTree-SCoT skills)

## 这是什么
把本仓 `skills/` 下的**四个 skill** 安装(拷贝或软链)到外层 harness 的 skills 目录,
让 Claude Code / OpenClaw 等能加载它们:

- **cs-scot-generate** — 把故障树 Excel 编译成场景诊断 skill(生成解析脚本 + SCoT 计划;数通场景)
- **cs-scot-generate-wireless** — 同上,无线接入类场景版(内核多 B 差值、E 占比、H 信令流程三类)
- **cs-scot-execute** — 按 `plan.json` 顺序执行 SCoT 计划
- **fault-tree-rca** — 综合根因分析

> 注:以上都是**工具 skill**。用 `cs-scot-generate` 为某场景生成出来的"场景 skill"
> (如 `examples/sa-access-fail/`)是另外单独上传/使用的,不由本脚本安装。

## 前置条件
- Python ≥ 3.10(skill 脚本运行时)
- 一个支持本地 skills 的 harness(Claude Code / OpenClaw)
- 仅"读取 .xlsx"需要 `openpyxl`;**csv 开箱即用**

## 快速安装
```bash
cd /path/to/faulttree-scot
./install.sh
```
默认把这些 skill 拷到 `~/.claude/skills/`。

Windows(PowerShell):
```powershell
.\install.ps1
```

## 选项
```bash
./install.sh --link              # 软链代替拷贝(本地开发,改源即生效)
./install.sh --copy              # 强制拷贝(默认)
./install.sh --target=/custom    # 自定义目标目录
./install.sh --harness openclaw  # 装到 ~/.openclaw/skills
./install.sh --uninstall         # 移除已安装的拷贝/软链
./install.sh --help
```
PowerShell 对应开关:`-Link` / `-Target` / `-Harness` / `-Uninstall`。

## xlsx 支持
解析脚本默认用 Python 标准库读 csv。若要读 `.xlsx`(故障树常是 xlsx),安装 openpyxl:
```bash
pip install openpyxl
```

## 验证
```bash
ls ~/.claude/skills/
# 应看到:cs-scot-generate/  cs-scot-generate-wireless/  cs-scot-execute/  fault-tree-rca/
```
跑一个现成的场景示例(`examples/sa-access-fail/` 是已生成好的场景 skill):
```bash
cd examples/sa-access-fail
python scripts/runner.py --plan plan.json \
  --data kpi=fixtures/kpi.csv --data chr=fixtures/chr.csv
```
会产出 `result/kpi.json`、`result/chr.json`、`result/rca_input.json`(最后的 RCA 由 agent 完成)。

## 卸载
```bash
./install.sh --uninstall
```
等价于:
```bash
rm -rf ~/.claude/skills/cs-scot-generate ~/.claude/skills/cs-scot-generate-wireless \
       ~/.claude/skills/cs-scot-execute \
       ~/.claude/skills/fault-tree-rca
```
(若是 `--link` 安装,只删软链,不影响源仓。)

## 为什么这么简单(对比 SCoT_A 的 install)
本项目是**轻量自研**,不接入 SCoT_A 框架,也**不需要** venv / registry / MCP / adapters
那一整套——skill 脚本仅用 Python 标准库(+ 可选 openpyxl;zip2csv 处理散文件时另需 chardet、pandas)。所以安装就是"把这些 skill 拷过去",比 SCoT_A 的安装流程简单很多。
