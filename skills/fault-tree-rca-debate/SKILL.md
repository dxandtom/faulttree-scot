---
name: fault-tree-rca-debate
description: 辩论式多代理根因分析。读取 cs-scot-execute 备好的 rca_input.json 和 tree_index.json，自行调用 LLM 做条件匹配和多代理辩论评分，产出与 fault-tree-rca 同一格式的四档 rca.json，三种得分机制（original、softmax、ratio）同时输出。与 fault-tree-rca（模型按规则手工判断）接在同一位置、可互换。要用辩论引擎跑根因分析，或对比三种得分机制时使用。
---

# fault-tree-rca-debate：辩论式根因分析

## 任务

这是一个脚本引擎技能，不是只靠提示词的技能：它自己调用 LLM 接口（配置在 `vendor/config.yaml`），不需要当前会话的模型来判断。对上游没有依赖：业务 Excel 的读取在 cs-scot-generate，本技能只读归一化后的 `tree_index.json` 和 `rca_input.json`。

在流水线里的位置，与 fault-tree-rca 接在同一处、可互换：

```
runner 跑完 script 节点 → 本轮结果目录 result-<运行号>/rca_input.json
  ├─ 手工路径：用 fault-tree-rca 技能，模型逐节点判断      → rca.json（事实闭环，四档）
  └─ 本技能  ：跑下面这条命令，辩论引擎评分             → rca.json（评分排序，四档）
```

```bash
python "<本目录>/scripts/run_debate_rca.py" --rca-input "<结果目录>/rca_input.json" --mechanism all
```

两条路径写同一个结果目录的 `rca.json`，过同一道 `validate_rca.py` 检查，被同一套评估打分。确证档的规则也相同：候选自身被证据实测成立、路径上没有实测"不成立"或"冲突"的上级，才进 `rca_result`；未绑定或数据不足的上级默认放行并记入 caveats；上级实测不成立或冲突是层级矛盾，降为疑似并写卡点。升档凭 execute 的事实，不凭辩论得分。

## 术语

| 术语 | 含义 |
|---|---|
| 辩论引擎 | `vendor/` 下的评分引擎：对故障树各节点做条件匹配，再让多个 LLM 角色（辩护、攻击、回应、一致性、评委）辩论打分，输出 Top-K 根因路径 |
| 候选 | 判据成立的最深节点，由 fault-tree-rca 的 draft_rca.py 机械（不经模型判断）算出 |
| 干净候选 | 自身成立，且路径上没有实测"不成立"或"冲突"的上级 |
| 争议候选 | 自身证据冲突（成立与强反证并存），或有层级矛盾的候选 |
| 竞争候选 | 多个候选同时存在时，需要分主次、并发还是连锁的情况 |
| 预筛与分诊 | 跑引擎前先用候选清单机械分类，决定跳过引擎、只辩部分分支还是全树辩论；默认开，`--no-screen` 关 |
| 得分机制 | 引擎对路径打分的三种算法：original、softmax、ratio。分数横向不可比，只比各自的排序 |
| Top-K | 每种机制输出的前 K 条根因路径，`--k` 控制，默认 10 |
| 降级 | LLM 持续失败时引擎改用关键词匹配继续跑；出现降级的结果作废 |
| 角色材料 | 辩论各角色收到的分层材料：判据原文、实测与反证、按角色的任务说明；默认开，`--no-agent-question` 关 |

## 命令与参数

最小命令只需 `--rca-input`（`--input` 是别名）。标准产物布局下，`--tree-index` 自动在 `<产物>/tree/` 下找唯一的 `*.tree_index.json`，`--out-dir` 默认是 rca_input 所在目录；只有评测场景等非标准布局才需要显式给这两个参数。

| 参数 | 说明 |
|---|---|
| `--rca-input <文件>` | 本轮结果目录的 rca_input.json，必填 |
| `--tree-index <文件>` | 树索引；标准布局下自动发现 |
| `--out-dir <目录>` | 产物目录；默认 rca_input 所在目录 |
| `--mechanism original/softmax/ratio/all` | 用哪种得分机制；默认 all |
| `--k <N>` | 每种机制输出的 Top-K 条数，默认 10；只影响最后切片，不增加 LLM 调用；引擎默认值与 config 的 top_k 都被它覆盖 |
| `--no-screen` | 关闭预筛与分诊，直接全树辩论（演示、验收用） |
| `--pruned-only` | 只喂剪枝后的局部子树；标准全绑定流水线上没有效果，不要主动加 |
| `--set K=V` | 临时覆盖引擎参数，盖在 config.yaml 之上，可多次；如 `--set num_judging_agents=2` |
| `--parallel <N>` | 并发 LLM 调用数，默认 4；`--parallel 1` 退回串行 |
| `--no-match-cache`、`--no-debate-cache`、`--no-seed-match`、`--no-agent-question`、`--no-llm-shield` | 关闭对应的默认优化，见"默认开着的优化" |
| `--no-validate` | 跳过产出后的 validate_rca 自检 |
| `--mock` | 用确定性假引擎跑通适配层，不调 LLM、不需要 config；自测和 CI 用 |
| `--vendor-dir <目录>`、`--config <文件>` | 引擎目录与配置文件；默认 `vendor/` 和其中的 `config.yaml` |

## 预筛与分诊

跑引擎前先借 fault-tree-rca 的 draft_rca 做机械分类，按候选情况分四路，判据全部机械、不经模型：

1. 唯一干净候选（自身成立、路径无实测不成立或冲突，且没有其他候选）：引擎整场跳过，0 次 LLM 调用，finalize 直接机械合成 rca.json。此时不写 `rca_debate_full.json`（评估侧靠它区分引擎，机械结果不冒充辩论结果），审计信息写 `debate_screen.json`。
2. 多候选竞争（候选总数不少于 2 且含干净候选）：全部候选进辩题，引擎辩其所在分支（祖先链与子树的并集，与 `--pruned-only` 取交集），分主次、并发、连锁并排序。确证仍凭机械事实，辩论得分只管排序；引擎 Top-K 漏掉的干净候选由机械合成补在末尾（不占辩论排序，并从疑似档剔重，防止同一节点占两档）。角色材料开头带"辩题聚焦"注记：不辩真伪只辩排序，背景节点已实测排除的结论不得推翻。
3. 纯争议候选（自身冲突或层级矛盾，一个或多个）：引擎只辩争议候选所在的分支（多个取并集），真伪凭辩论质证加人工复核。注记用争议款：点名争点；实测排除的结论不得推翻，未验证或数据不足的如实标注。
4. 没有成立候选、rca_input 是 md 形态、或找不到 fault-tree-rca：自动退回全树辩论。

这就是"1 个根因对 100 个节点"的解法：100 个节点里通常只剩几个真要辩，唯一明确根因时 0 轮辩论。

## 产物与版本管理

- `rca.json`（默认等于 original 机制的结果）、`rca.original.json`、`rca.softmax.json`、`rca.ratio.json`、`rca_debate_full.json`（引擎原始输出，含辩论记录，审计用）。每份含 Top-K 条根因路径。排名靠后的条目分数低、confidence 多为 low，它们是候选清单的尾巴，不是并列结论。
- 重跑时上一轮的辩论产物自动归档到 `<out-dir>/debate_runs/<时间戳>/`，out-dir 里永远只有当前轮。已存在的 rca*.json 一律是上一轮的，要新结论直接重跑，不把旧产物当本轮结果汇报。
- 跑完自动过与手工路径同一道检查：`fault-tree-rca/scripts/validate_rca.py`。

## 一次性配置

只在首次使用、换机器、换配置时做，做过就跳过：

1. 把含 API 密钥的 `config.yaml` 放进本技能 `vendor/`（.gitignore 挡住，不入库）。放好后再安装：整目录复制，引擎与配置一起带走，之后固定用装好的路径。
2. 安装依赖：`pip install pandas numpy scipy pyyaml openpyxl requests`（scipy 是引擎置信区间计算的硬依赖）。
3. 验一次连通：`python scripts/probe_llm.py`（退出码 0 为通）→ 用 `--mock` 跑一次冒烟 → 真引擎。

## 执行要求

跑本技能的模型按以下六条操作，这些约束不需要用户在提示词里重复：

1. 探针和 `--mock` 只在首次配置、或真引擎行为异常（0 轮辩论、毫秒级完成、连续失败）时用于诊断；日常直接跑真引擎那条命令。
2. 超时给足。真引擎在大树上一跑几十分钟，Bash 的 timeout 要设到环境允许的最大值（BASH_MAX_TIMEOUT_MS，通常 3600000 即 1 小时，这是 Claude Code 的上限，设更大也会被压回），或后台运行。进度看 `<out-dir>/debate_progress.txt`（每评估一个节点追加一行，可 tail 跟踪），长跑时每隔几分钟读末尾几行向用户播报。分不清"还在跑"还是"已挂"时看 `<out-dir>/debate_heartbeat.txt`（每次 LLM 调用完成刷新；超过约 20 分钟不动才值得怀疑，因为 180 秒超时乘重试次数的最坏情况就有这么长）。若仍被超时杀掉（退出码 143），不要原地重试：把完整命令写进 `<OUT>\run_debate.bat`，请用户在自己的终端跑，确认完成后再继续。
3. 不加没让加的开关，不改配置。默认值已按最优配好（并行 4、匹配缓存、LLM 调用保护、重试收敛，开跑头几行会打印这些状态）。`--pruned-only` 在标准全绑定流水线上没有效果，不要主动加；任何情况下不修改 `vendor/config.yaml`。
4. 结果合格线。输出出现 `检测到API连接失败,自动切换到fallback模式`（引擎原话）或结尾的 ⚠ 降级警告，本次结果作废，如实报告，不拿它下结论。`rca_debate_full.json` 里 `llm_call_failures` 占总调用比例超过 10% 也要报告并建议错峰重跑；零星几次属正常，外层重试已自愈。
5. LLM 连续 5xx 或超时的处置。开跑就成串报 500、502、超时时停下（不要硬跑到降级出废结果），跑一次 `python <本目录>/scripts/probe_llm.py` 定位：探针也 5xx 是网关侧故障或过载，报告用户、建议错峰（会话与引擎共用网关，会话干重活时尤其容易挤兑）；探针通则是瞬时抖动，直接重跑（脚本会自动归档上一轮产物）。这是探针的正确用途：诊断，不是每次跑前的仪式。
6. 汇报内容。validate_rca 的结果，加 `rca.original.json`、`rca.softmax.json`、`rca.ratio.json` 三份的 Top-K 根因路径并排对比（三种机制的分数横向不可比，只比各自的排序；详见 `references/engine_ops.md`）。

## 默认开着的优化

以下优化默认全开，通常一个开关都不用带；每项的原理、实测数据和调参依据见 `references/engine_ops.md`：

| 优化 | 做什么 | 关闭开关 |
|---|---|---|
| 并行 | 匹配、辩护、评委等彼此独立的 LLM 循环并发执行，默认 4 路；网关回 429 或 503 时自动减半，恢复后逐档回升 | `--parallel 1` |
| 匹配缓存 | 同一（问题、判据集）只问一次 LLM，辩论里重复的兄弟检查全走缓存 | `--no-match-cache` |
| 匹配预播种 | execute 已判定的节点结论直接写进匹配缓存，匹配层不再调 LLM 重新发现已知答案（30 个场景实测与理想匹配零分歧，还消除了采样随机性） | `--no-seed-match` |
| 辩护记忆化 | 同（节点、组、问题）的辩护只生成一次 | `--no-debate-cache` |
| 角色材料 | 各角色收到分层材料：判据原文全员必读（含被证伪、缺数据的节点，附实测与反证），任务框架按角色，信息全员对称；机械匹配器不受影响 | `--no-agent-question` |
| LLM 调用保护 | 瞬时 502 不再让引擎永久降级：外层重试，成功即撤销降级标记；config 里过猛的内层重试自动收敛为快失败（`--set` 显式指定的不动） | `--no-llm-shield` |
| 回程事实感知 | rca.json 的 `evidence_path.actual` 回填实测证据；叶子实证成立托底 medium，被数据证伪封顶 low 并挂 ⚠ 冲突标记；`blocked_at` 点名数据不足的节点；全场景没有任何成立事实时候选降入 undetermined，不硬报疑似 | 无 |

评分公式逐字保留，实测结果与串行逐位一致，小样本提速 4.4 倍。耗时大头是"零匹配兄弟组的平方级辩论"，全树串行直跑动辄几十分钟，不要在 10 分钟超时的 Bash 里跑；先读 `references/engine_ops.md` 的"提速手册"。

## 内部构造

`vendor/` 是评分引擎核心（算法约定不在本仓库修改，上游发新版整文件替换，见 `vendor/README.md`）；`scripts/` 是本技能的输入输出层：

| 文件 | 职责 |
|---|---|
| `scripts/facts_to_question.py` | 证据到文本的桥：把 rca_input 的确定性事实渲染成引擎的 question。回声策略：只复述"成立"的判据原文（让文本匹配命中），"不成立"和"数据不足"只报节点名，防止误匹配 |
| `scripts/tree_to_vendor_xlsx.py` | tree_index 转引擎格式的 xlsx；`Ln Code` 列等于我们的 node_id，让输出天然带主键；带 L6 深度守卫与成本预估 |
| `scripts/result_adapter.py` | 引擎 Top-K 乘三种机制转四档 rca.json。默认进 probable_causes；候选被实测成立且路径无实测不成立或冲突的上级才进 rca_result；未升档条目的 judgment 用中性词，不冒用"成立""数据不足"这些保留字 |
| `scripts/run_debate_rca.py` | 编排、引擎装载（`_load_processor` 是唯一的接口耦合点）、配置展平与 `--set` 覆盖、LLM 调用保护、降级检测 |
| `scripts/engine_boost.py` | 并行与缓存补丁；带源码哈希守卫，上游改了引擎方法就自动退回串行并告警 |
| `scripts/mock_vendor.py` | 引擎的确定性假实现（形状对、算法不复刻），支撑无 LLM 自测 |
| `scripts/probe_llm.py` | LLM 连通性探针，输出脱敏 |
| `scripts/ab_test_mechanisms.py` | 三种得分机制的一键对比评测。原配的冻结场景已随 cs-scot-eval 从本仓库移除，需自备带答案的场景，用 `--suite`、`--trees` 显式传入 |

## 三种得分机制怎么选

需要实测定夺：逐场景跑本引擎（`--mechanism all`），用排名指标（hit@1、hit@3、MRR）对比，赢的当默认。一键工具、指标口径、original 机制的层级偏置等注意事项见 `references/engine_ops.md`。成绩标注 `engine=debate`，不与"模型手工 RCA"的成绩混在一张表。

## 完整例子

场景技能 `layer-3-service-issues`，runner 已跑完，结果目录是 `<产物>/../layer-3-service-issues-result/result-20260918T090000Z-1a2b3c/`。

```bash
python "<本目录>/scripts/run_debate_rca.py" \
  --rca-input "<结果目录>/rca_input.json" --mechanism all
```

预期过程：开头打印默认状态（并行 4、缓存开、调用保护开）和自动发现的 tree_index；预筛打印候选分类结果。若是唯一干净候选，直接打印"辩论引擎跳过（0 次 LLM 调用）"并写出 rca.json 与 `debate_screen.json`；否则进入辩论，`debate_progress.txt` 逐节点追加进度。结束时打印 validate_rca 的结果。汇报时给出 validate 结果与三份 `rca.<机制>.json` 的 Top-K 并排对比，并说明有没有出现降级警告。
