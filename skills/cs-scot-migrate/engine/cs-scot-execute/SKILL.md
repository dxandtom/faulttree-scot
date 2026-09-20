---
name: cs-scot-execute
description: 执行一份 SCoT 诊断计划 plan.json（cs-scot-generate 生成的场景产物）：按计划依次运行各指标的解析脚本得到事实，再做根因分析，输出根因路径与判断依据。本技能也是每个场景技能的执行指引：用户给出数据要求诊断、要运行某个故障场景、或要对一个 plan.json 跑出根因时使用。
---

# cs-scot-execute：执行诊断计划

## 任务

执行 `plan.json` 描述的 SCoT 计划（SCoT v4.0 的子集，格式见 `references/plan_schema.md`）：从入口节点出发，按顺序运行各指标的分析脚本，把每个脚本输出的事实存入工作记忆，最后把全部事实和故障子树交给 fault-tree-rca 做根因分析。统计已经在生成阶段写进脚本，执行阶段不再加工数据；根因分析是唯一需要模型判断的步骤。

## 术语

| 术语 | 含义 |
|---|---|
| 计划 `plan.json` | 节点列表加转移关系；节点分 `script`（跑分析脚本）和 `agent_skill`（根因分析）两种 |
| 指标、数据源 | 一种输入数据（KPI 表、配置表、日志表……），计划里用 `$input.<指标>` 引用，命令里用 `--data <指标>=<文件>` 提供 |
| 分析脚本 | `scripts/<指标>_analyzer.py`，读一份数据，输出该指标的逐条件事实；cs-scot-generate 里叫"逐条件分析脚本"，是同一个东西 |
| 事实 | 分析脚本的输出：某个判据在数据里查到的真实值和是否命中，不含结论 |
| 原始日志目录 | 采集来的命令回显文件所在目录，用 `--data raw=<目录>` 提供，S 类节点脚本直接读它。必须传网元目录的父目录：zip 输入是 zip2csv 输出里的 `extracted/`，经 ifilter 归类的散文件是 `formated_result/`，传错一层 S 类节点脚本一个网元都找不到（检查脚本会打印该传哪一层） |
| 短名 | 场景的英文短名（如 `layer-3-service-issues`）：技能目录名和 `assets/tree/` 下文件名都用它；取不到英文时是中文场景名 |
| 结果目录 | 每轮运行新建的 `result-<运行号>/`，装本轮全部产出 |
| `rca_input.json`、`rca.json` | 交给根因分析的汇总输入，和根因分析的结果 |
| 缺口 | 计划需要但这次没提供的数据源，或转换不出的数据；结论只代表已提供数据能看到的部分 |
| 场景技能 | 由 package_scenario.py 打包出来的技能：本文件作执行指引，产物放在 `assets/` 下 |
| 外挂知识 `knowledge.md` | 业务同事写的判读规则，按"适用"标签匹配到分支后随 rca_input 下发 |

## 命令与参数

```bash
python scripts/runner.py --plan <场景产物>/plan.json --data kpi=<kpi数据> --data chr=<chr数据>
```

| 参数 | 说明 |
|---|---|
| `--data <指标>=<文件>` | 把计划里的 `$input.<指标>` 绑到实际数据文件（xlsx/csv）；有几个指标传几次。`--data raw=<目录>` 提供原始日志目录 |
| `--base <目录>` | 相对路径的基准目录，默认是 plan.json 所在目录 |
| `--result-base <目录>` | 结果输出的根目录，各轮 `result-*/` 和总索引 `index.jsonl` 都放在它下面；默认与 `--base` 相同。运行平台不允许往技能目录写文件时用它把结果放到别处，场景技能形态必带 |
| `--result-suffix <标签>` | 给本轮目录加一个便于辨认的标签（`result-<标签>-<运行号>/`，只接受字母、数字和 `-_`）。隔离不靠它，纯粹方便人认；多份数据并行时建议带站点名或数据文件夹名 |
| `--run-id <编号>` | 本轮运行号，默认自动生成（时间戳加随机数，并发和重跑都不会重复）；自定义编号与已有轮次重名会报错 |
| `--new-run` | 每轮新建结果目录已经是默认行为，这个开关只为兼容旧命令保留，带不带都一样 |

运行行为：

- **每轮一个独立的结果目录**。每次运行自动新建 `result-[标签-]<运行号>/`，本轮全部产出（`rca_input.json`、`rca.json`、`run.json`、`_compression.txt`）都在里面，多个人或多份数据同时跑同一份产物互不覆盖。结果路径以 runner 启动时打印的"结果目录：…"为准，后面写 rca.json、汇报都用它。历史轮次是同级的其他 `result-*` 目录，总索引在结果根目录的 `index.jsonl`（一行一轮：运行号、目录、输入指纹）。
- **每轮写 `run.json`**：运行号、输入数据指纹（路径、大小、sha1）、`missing_inputs`（缺口）、`execution_status`（逐节点执行状态：正常、失败及错误、跳过及原因，每步跑完立即写入，半途失败时已跑步骤的状态也在）。
- **部分数据也能跑**。计划需要的数据源只给了一部分时不报错：缺源的分析脚本步骤自动跳过并标 `[skip]`，其余照跑，结尾打印"数据源缺口汇总"（缺了什么、跳过了哪些步骤、补数据的命令）。缺口同时写进 `run.json` 的 `missing_inputs` 和 `rca_input.json` 顶层的 `missing_indicators`，根因分析把这些源的判据按"数据不足"处理，不是"已排除"。向用户汇报时必须带上缺口。一个数据源都没给仍然报错；`--data` 的指标名打错会触发"计划用不到的数据源"告警，看到它先核对名字。
- **启动时比对产物的 `manifest.json`**：产物格式版本较旧或缺失只告警不阻断，提示"由旧工具链生成，建议重新 generate"。

## 场景技能形态：一句话诊断

本技能常以场景技能的形态发布：技能目录名是场景的英文短名（如 `layer-3-service-issues`），本文件作为执行指引，诊断资源在 `assets/` 下：`assets/plan.json`、`assets/scripts/`（各指标的分析脚本）、`assets/tree/`（故障树、可选的 solutions.json、可编辑的 knowledge.md）、`assets/fault-tree-rca/`（fault-tree-rca 技能副本，含规则与脚本）。用户只需一句话（如"数据在这里，帮我诊断"），按下面五步跑完全程，不需要用户指路。

| 步骤 | 做什么 | 关键命令或产出 |
|---|---|---|
| 1 数据入口 | 先跑 `scripts/check_data.py` 认数据形态；已解析的表直接用，原始数据用 `assets/zip2csv/scripts/zip2csv.py` 转成三表，原始目录另外用 `--data raw=<目录>` 传入 | 数据传入建议；`csv/<网元>/CMD、CONFIG、LOG/*.csv` |
| 2 数据与指标匹配 | 数据文件按文件名对上 `assets/scripts/<指标>_analyzer.py` 的指标名；对不上的和缺的都记入汇报 | 一组 `--data <指标>=<文件>` |
| 3 执行 | `scripts/runner.py --plan assets/plan.json --result-base <平级结果目录> --data ...`；随后进入根因分析：先 `draft_rca.py`，只裁决冲突候选，再 `finalize_rca.py` | `result-<运行号>/rca_input.json`、`rca.json` |
| 4 输出汇报 | `render_report.py rca.json --tree <tree_index>`，英文提问加 `--lang en`；`rca_reply.md` 全文原样贴进回复，前面加执行过程，末尾写补充说明 | `rca_report.md`、`rca_tree.txt`、`rca_reply.md` |
| 5 用户反馈 | 用户说某节点判错：定位、分诊、修改、验证 | 改绑定、加 `knowledge.md` 规则，或回 generate 改树 |

### 1. 数据入口

数据以用户给的形式为准：目录或散文件直接用，压缩包先解开。第 0 步必做：跑数据入口检查 `python "<本目录>/scripts/check_data.py" <数据路径>`，按它给出的数据传入建议继续；退出码 2 就把报告原样给用户，不强行继续。数据有三种形态：已解析的表直接传给执行器；原始日志目录（网元目录下有 CONFIG、CMD、LOG 子目录）先用 `assets/zip2csv/scripts/zip2csv.py` 转成三张表，再连同原始目录 `--data raw=<目录>` 一起传入；零散的原始文件同样交给 zip2csv，它会先归类。三种形态的判定、转换命令、失败时怎么办、缺口怎么向用户措辞，见 `references/data_entry.md`。数据转换只用 `assets/zip2csv/` 里的脚本，不去环境里找别的技能。

### 2. 数据与指标匹配

指标名是 `assets/scripts/` 下 `<指标>_analyzer.py` 的前缀；数据文件按文件名与指标名匹配（相等或包含，如 `config_20250701.csv` → `config`）。匹配不上的文件跳过，计划需要但没给的数据源自动跳过；两类都要写进汇报，不能不提。

### 3. 执行

```bash
python "<本目录>/scripts/runner.py" --plan "<本目录>/assets/plan.json" \
  --result-base "<本目录>/../<本技能目录名>-result" --data <指标>=<文件> ...
```

`--result-base` 必带：平台不允许技能往自身目录写文件，结果写到与本技能平级的 `<本技能目录名>-result/` 下，每轮自动新建独立的 `result-<运行号>/`。并行诊断多份数据时建议加 `--result-suffix <数据标识>`。

进入根因分析步骤后，第一个动作是运行 `assets/fault-tree-rca/scripts/draft_rca.py` 生成候选清单，不是先读 rca_input 把握全局。rca_input 不整读进上下文（超长必出错）；不用 `python -c` 解析任何产物 JSON（Windows 反斜杠路径进字符串会转义出错，中文输出乱码）；要看节点细节只用 `rca_query.py`。

### 4. 输出汇报

诊断结论一律由脚本渲染，不手写：

```bash
python "<fault-tree-rca 目录>/scripts/render_report.py" "<result>/rca.json" --tree "<assets>/tree/<短名>.tree_index.json" [--lang en]
```

产出 `rca_report.md`（完整报告）、`rca_tree.txt`（树视图）、`rca_reply.md`（聊天框回复稿：树视图 + 确证根因表 + 疑似表 + 待确认）。最终回复的正文就是 `rca_reply.md` 的全文，原样贴出，树视图保持在 ```text 代码块里，表格一格不改；正文前面加"执行过程"，末尾的"补充说明"是唯一可以自己写的地方。用户整段用英文提问时，渲染命令加 `--lang en`：标题、表头、标签、节点名、判据、方案和脚本生成的说明（祖先注记、卡点、待确认原因）都由脚本按树里存的英文输出，树里没有英文的地方回退中文；裁决单里你自己写的理由、注意、卡点用对话的语言写；回复稿里剩下的中文只有分析脚本给出的事实原文，由你逐句直译，其余一字不动。三份文件的内容、回复的组织、英文提问的处理规则，见 `references/reply_format.md`。

根因分析按 fault-tree-rca 的流程走：候选清单自动定档 → 只裁决冲突候选 → 脚本合成 → 脚本渲染。带 `auto_verdict` 的候选不需要裁决；finalize_rca 报错（冲突条目漏裁决、自动定档被改写、排除没写理由）就改裁决单重跑。判定规则：未绑定或数据不足的上级节点默认放行，记入 caveats，报告提示"此处无判断"；上级实测"不成立"或"冲突"是层级矛盾，候选降为疑似并写明卡点。

### 5. 用户反馈

用户指着某个节点说"判错了""应该是 XXX"时，按定位、分诊、修改、验证四步处理，判断只在分诊一步：答案不在事实里是绑定问题，改对应分析脚本里的绑定；答案在事实里但判错是判读规则问题，往 `assets/tree/knowledge.md` 加规则；判据本身不对是树的问题，回 generate 走调优表；事实反驳用户说法时摆出原文说明分歧。步骤细节见 `references/feedback_fix.md`。

## 执行模型

1. 从 `entry` 节点开始，用 `memory_init` 初始化工作记忆。
2. 按节点 `kind` 分派：`script` 节点按 `name` 找 `scripts/<name>.py`，解析 `inputs.data` 得到数据文件，运行脚本，把逐条件事实按 `outputs` 写入 `$memory`；`agent_skill` 节点把解析好的输入（故障树正文加各源事实）写到本轮结果目录的 `rca_input.json`，由 `name` 指定的技能（fault-tree-rca）产出结果。这一步由模型按 fault-tree-rca 的流程判断：读 rca_input，写 rca.json。
3. 沿 `transition.next_on_success` 前进，`end=true` 结束。
4. 节点间的数据全靠 `$input`、`$memory`、`$step`、`$output` 引用传递。

未定义的 `kind` 直接报错，不静默跳过。典型链：各 `<指标>_analyzer`（script，快）→ `cross`（script，跨源判据：传参链、两侧比较，runner 把全部源递给它，没有跨源绑定时秒过）→ `rca`（agent_skill，唯一的模型步骤，慢）。

## 输出

- `script` 节点的产出存入 `$memory`，不单独写文件。
- `agent_skill` 节点：汇总输入写到本轮结果目录的 `rca_input.json`，fault-tree-rca 在同一目录产出 `rca.json`。每轮目录独立新建、只装本轮文件，不会读到别轮的过期结论。

`rca.json` 的权威定义在 fault-tree-rca 技能：四档字典，顶层四个键恒在。

```json
{
  "rca_result": [ {"Root_cause":"…","Fault_tree_path":["…"],"node_id":"<候选自身的 node_id>","support":"…",
     "evidence_path":[{"node":"…","node_id":"…","condition":"<逐字>","actual":"<逐字引用事实>",
        "how":"<怎么判的说明（抄事实的 explain）>","judgment":"成立/不成立/数据不足"}]} ],
  "probable_causes": [ {"Root_cause":"…","Fault_tree_path":["…"],"node_id":"<候选自身的 node_id>","confidence":"high/medium/low","blocked_at":"…","caveats":"…","support":"…","evidence_path":[…]} ],
  "undetermined": [ {"Fault_tree_path":["…"],"blocked_at":"…","reason":"缺哪个量","observed":["…"]} ],
  "tree_issues": { "uncovered_condition_nodes":[…], "orphan_node_ids":[…] }
}
```

`rca_result` 为 `[]` 是正常输出（上游缺话统、信令时，强证据在 `probable_causes`），不是失败。

## 设计要点

- 计划是直线的：故障树的分支判断在根因分析内部完成，执行层只顺序遍历节点。
- 根因分析输入默认是局部子树形态：按 tree_index 剪掉与本轮事实无关的分支。剪枝后若不比完整树小（小而密的树本就无可剪），或事实能对上 node_id 的不足一半（剪枝不可信），自动改用完整形态，压缩记为 0%；孤儿事实瘦身，未覆盖清单只留 id 和名字。
- 确定性计算都在 `script` 节点的脚本里；`agent_skill` 是唯一的模型判断步骤。
- 暂不实现 conditions、parallel、budget、reflection 等 SCoT 的重型特性（schema 兼容，需要再加）。

## 完整例子

场景技能 `layer-3-service-issues`，用户说"数据在 D:\采集\站点A.zip，帮我诊断"。

| 步骤 | 做什么 | 看什么 |
|---|---|---|
| 1 | 解开 zip → `check_data.py D:\采集\站点A` | 报告说是原始日志目录，给出 raw 的路径 |
| 1 | `assets/zip2csv/scripts/zip2csv.py D:\采集\站点A -o D:\采集\站点A_csv` | `csv/<网元>/CMD|CONFIG|LOG/*.csv` |
| 2 | 按文件名匹配指标：cmd、config、log | 三份数据各对应一个 `<指标>_analyzer.py` |
| 3 | `runner.py --plan assets/plan.json --result-base ../layer-3-service-issues-result --data cmd=… --data config=… --data log=… --data raw=D:\采集\站点A --result-suffix 站点A` | 打印"结果目录：…"，各脚本步骤正常，缺口汇总 |
| 3 | 进入 rca 步骤：`draft_rca.py` → 有冲突候选才写裁决单 → `finalize_rca.py` | 结果目录里出现 rca.json |
| 4 | `render_report.py rca.json --tree assets/tree/<短名>.tree_index.json`（英文提问加 `--lang en`） | 三份文件；把 rca_reply.md 全文贴进回复，前面加执行过程，末尾补充说明 |
