---
name: cs-scot-reflect
description: 评估反思。把 cs-scot-execute 的执行结果与用例库里的期望对比，判定每个用例通过与否，把没通过的原因归到六类错误之一，输出整批的通过率与得分，以及供优化技能处置的反馈。用户说"评估结果""对账""检查诊断是否正确""反思""运行评估""一键回归""生成用例库""更新用例库"时使用。本技能独立于执行链，只读 cases 目录下的 expected.json，期望不传给生成与执行环节，防止泄露答案。
---

# cs-scot-reflect：评估反思

## 任务

评估反思在 generate → execute 之后独立运行：把执行结果与期望对比，区分机械性错误（环境、数据、绑定）与模型性错误（判据、裁决），输出反馈供优化技能改进。本技能的全部动作就是运行 `scripts/` 下的脚本并读它们的产出文件；判定与分类都由脚本按规则完成，模型不参与。

按手上有什么选入口：

| 手上有 | 入口 | 做什么 |
|---|---|---|
| 交付包，还没有用例库 | `case_gen.py` | 从迁移台账与包内测试数据机械生成用例库 |
| 用例库加已跑好的执行结果 | `eval_run.py` | 判卷：单用例或批量 |
| 场景技能加用例库（或交付包），想直接看回归结果 | `regress.py` | 一键回归：一条命令把用例库里的用例逐个执行诊断并判卷，最后汇总通过率 |

## 术语

| 术语 | 含义 |
|---|---|
| 用例库 | `cases/<case_id>/expected.json` 的集合：每个用例写明期望的根因节点，可附测试数据 `data/` |
| 期望根因 | 用例里写明"跑出来就该是这个节点"的节点，按 node_id 与节点名记录 |
| 阴性用例 | 健康数据、期望没有根因的用例；`negative` 为 true |
| 对账 | 逐条期望根因与 rca.json 各档比对，得到命中、降级命中、漏判、假阳等结果 |
| 六类错误 | 按顺序检查、命中即停的六个原因：ENV_FAILURE、INPUT_GAP、BINDING_GAP、KNOWLEDGE_GAP、CRITERIA_GAP、VERDICT_GAP |
| verdict | 一个用例的评级：PASS、PASS_WITH_GAPS、PARTIAL_PASS、FAIL、EVAL_ERROR，只回答"这次跑完要不要管" |
| 轮次 round | 同一用例第几次评估；通过 `--prev-evaluation` 传上一轮报告来接续 |
| 熔断 | 同一用例、同一节点、同一类错误连续三轮没修好，标记转人工深查 |
| 一键回归 | 用一条命令把用例库里的每个用例依次执行诊断、判卷并汇总。"回归"指改动之后重跑同一批用例，确认原来通过的没有被改坏 |
| 待裁决 | 一键回归里 finalize 拦下需要权衡的真冲突候选，脚本不代为裁决 |
| 单用例评分 `quality_score.json` | 每个用例目录里的六维布尔评分 |
| 整批汇总 `quality_score_batch.json` | 评估输出根目录里整批的通过率与平均分，业务取数用 |
| 防泄题 | 期望只在 cases 目录，不传给生成与执行；读过期望的会话不做诊断 |

## 执行规矩

- 不用 `python -c` 解析任何 JSON 或 CSV（Windows 中文路径经 shell 会乱码，字段靠猜必错）。
- 不做手册外的预检：用例在不在、结果目录对不对，脚本会自己检查并报清楚；直接跑，按退出码办事。
- 汇报以脚本打印和产出文件的原文为准，不自行诊断、不重新组织结论。
- 防泄题两条：expected.json 只放在 cases 目录，不传 generate、不拷进 assets、不随 result 目录携带；读过 expected 或失败签名的会话不得做手工根因分析与回归，必须派独立会话。

## 入口一：生成或刷新用例库

```bash
python "<本目录>/scripts/case_gen.py" \
  --product <产物或打包技能目录> \
  --packages <交付包根目录、合集 zip 或放了一堆 zip 的目录> \
  --cases-out <cases 目录> [--copy-data] [--force] [--map <对应表>]
```

用例库不靠人手写：交付包天然是带标注的用例（单节点判据加"跑出来就该是该节点"的测试数据），migrate 的迁移台账 `tree/migrations.json` 已记录"节点对应哪个包"。脚本按台账为每个节点生成一个 `cases/<node_id>-<包节点名>/expected.json`。

- `--packages` 的处理方式与 migrate 一致：zip 解到临时区，嵌套 zip 递归解，纯目录不拷贝；来自 zip 的测试数据自动拷入用例的 `data/`。
- 包与节点的匹配以包内 `判断方法*.md` 的标题为准，目录名、大小写、空格与下划线、多套一层批次目录都不影响；多个候选都像时点名歧义交人，不自行猜测。
- 产物没有迁移台账时（generate 直接生成、没走过 migrate 的技能）不停下，退回按名字匹配：判断方法里的节点名与树节点名相等且全树唯一才生成用例；同名或对不上的逐包点名。要零歧义就先对技能跑一次 migrate 再建库。
- 故障树结构调整（node_id 重排）后重跑一遍即全库刷新。
- 阴性用例没有机械来源，人工按 `references/expected_schema.md` 放少量即可。

跑完把"生成 N 个、跳过 M 个、未能生成清单"原样汇报给用户。

## 入口二：判卷

单用例：

```bash
python "<本目录>/scripts/eval_run.py" \
  --case <case_id> --result-dir <结果目录> --skill-dir <技能目录> \
  [--cases-dir cases] [--out <评估输出目录>] [--prev-evaluation <上一轮 evaluation_report.json>]
```

批量：

```bash
python "<本目录>/scripts/eval_run.py" \
  --case-dir <用例库目录> --result-dir <结果根目录> --skill-dir <技能目录> \
  [--out <评估输出目录>] [--prev-evaluation <上一轮 evaluation_report.json>]
```

| 参数 | 说明 | 示例 |
|---|---|---|
| `--case` | 用例 ID | `ldp-lsp-001` |
| `--case-dir` | 用例根目录（批量模式） | `cases/` |
| `--cases-dir` | 单用例模式下的用例根目录，默认 `cases/` | |
| `--result-dir` | 执行结果目录（单用例）或结果根目录（批量） | `output/ldp-lsp-result/result-20260810/` |
| `--skill-dir` | 场景技能目录 | `scot技能/ldp-lsp/` |
| `--out` | 评估输出目录，默认 `<result-dir 的父目录>/<场景>-eval/` | |
| `--prev-evaluation` | 上一轮的 evaluation_report.json 文件或其评估目录，用于接续轮次 | |

批量时每个用例要对到它自己的执行结果，按四级匹配：① 结果目录名含 case_id（执行时用 `--result-suffix` 标注的优先）；② run.json 记录的输入数据路径落在本用例的数据目录内（runner 每轮自动记录，这是主力通道）；③ 只有一个候选就用它并警告；④ 都对不上就报错，绝不拿别的用例的结果顶数。

读取的文件：`<cases-dir>/<case_id>/expected.json`；结果目录里的 `run.json`、`rca_input.json`、`rca.json`（结构见 `references/result_schema.md`）；`<skill-dir>/assets/tree/*.tree_index.json`。轮次规则：不传 `--prev-evaluation` 时 round 为 1、feedback_iteration 为 0；传入则 round 加 1，feedback_iteration 继承并在触发反馈时加 1，PASS 或 PASS_WITH_GAPS 时清零。

## 入口三：一键回归

一键回归指用一条命令把用例库里的每个用例依次执行诊断、判卷并汇总通过率，用来确认技能改动后原来通过的用例没有被改坏。用户给的是场景技能加用例材料、想直接看回归结果时，不必手工串执行与判卷：

| 手上有 | 命令 |
|---|---|
| 技能加交付包（没有用例库） | `regress.py --skill-dir <技能> --case-dir <建库落点> --packages <包目录> [--map <对应表>] --out <工作目录>`，先代跑 case_gen 建库再回归 |
| 技能加现成用例库（日常主路径） | `regress.py --skill-dir <技能> --case-dir <用例库> --out <工作目录>`，不再跑 case_gen，用例库是一次建、反复用的资产 |
| 技能、用例库、已有执行结果 | `regress.py … --eval-only --result-dir <既有结果根>`，只判卷，等价于批量 eval_run |

可加 `--case <case_id>`（可多次）只跑部分用例。

它逐用例只跑确定性链：runner（自动带 `--data raw=<网元层父目录>`，数据外多包一层如 `formated_result/` 时沿唯一子目录链下探定位，有多个分叉时不自行猜测；用例目录下有 `data_map.json`，如 `{"cmd": "相对 data 的路径"}`，则逐条追加表映射）→ draft_rca 机械起草 → finalize_rca 机械合成 → 判卷。

- finalize 拦下需要权衡的真冲突时，该用例记为待裁决并点名，脚本不代为裁决。默认按无人值守规则：待裁决直接按不通过计（FAIL，error_category 为 `pending_adjudication`）进通过率分母，裁决做不了等于这轮没优化好；事后有人了可按 fault-tree-rca 的规则在该结果目录补 `decisions.json`、重跑 finalize 与 render，再用 `--eval-only` 或 `--case <该用例>` 补判。有人盯着的场合加 `--pending-hold`，恢复"挂起点名、不计分母、裁决后补判"。`--pending-as-fail` 已是默认，保留只为兼容。
- 执行器与根因分析链优先用场景技能自带的副本（`<skill>/scripts/runner.py`、`<skill>/assets/fault-tree-rca/`，与产物同代），其次是同一 skills 目录下部署的 cs-scot-execute 与 fault-tree-rca，最后到 cs-scot-generate-optimize 发行包内的组件副本里找。不需要为回归单独部署执行技能。
- 产出：整批汇总 `<工作目录>/eval/quality_score_batch.json`，回归自己的明细（待裁决、异常清单）`<工作目录>/regress_summary.json`（也带 pass_rate 与 total_score），逐用例日志 `<工作目录>/logs/`。
- 一键回归供日常回归。要对外发布的量化数字仍按双会话规格跑：诊断会话不读 expected.json。

## 评估怎么判

四步，全部由脚本按规则完成：

1. 读取 expected.json、run.json、rca_input.json、rca.json、tree_index.json，可选上一轮报告。
2. 对账：逐条期望根因，与确证根因的候选自身 node_id 比对，路径祖先单列。结果类型：HIT_CONFIRMED（期望就是确证根因的候选自身）、HIT_ANCESTOR（期望是确证根因的路径祖先，算通过但单独计数，并给出"用例期望粒度偏粗"的 case_quality 提示）、HIT_DEGRADED（期望在疑似档）、MISSED（不在任何档）、FALSE_CONFIRM（阴性用例却有确证根因）。
3. 有 MISSED 或 FALSE_CONFIRM 时才查错，按 0 到 5 的顺序，命中即停：

   | 类 | 名称 | 检测条件 | 处置 |
   |---|---|---|---|
   | 0 | ENV_FAILURE | 某步骤 `ok` 为 false，或 run.json 不存在 | 脚本崩溃：自动（优化技能重迁）；环境故障：人工 |
   | 1 | INPUT_GAP | `missing_inputs` 非空且有期望根因漏判，且缺的源本可提供。采集本就不含该类（`missing_kind` 为 `collection_absent`）或用例数据里本来没有的机械剔除；剔完为空则继续查绑定层。缺的源在用例数据里只有原始日志形态时改判类 2 绑定形态不匹配 | 人工（使用方补数据）；改判类 2 的自动 |
   | 2 | BINDING_GAP | 孤儿绑定（产出了事实但树上无此节点），或漏判的期望根因自身有判据无绑定。祖先未绑定不算，全树其余未绑定节点也不算错 | 孤儿绑定、绑定缺失、绑定形态不匹配：自动；绑定清除冲突：人工 |
   | 3 | KNOWLEDGE_GAP | tree_index 里没有该节点 | 用例库过期：自动（重建用例库）；故障树缺节点：人工（退回 tester） |
   | 4 | CRITERIA_GAP | 判据算出的事实与 expected_facts 不相等 | 自动（按证据修到与判据文档一致，判定标准不动） |
   | 5 | VERDICT_GAP | 事实全对却归错档，含阴性用例假阳 | 人工（结论归档争议） |

   "自动"指优化技能 cs-scot-generate-optimize 的反馈处理线按处理清单执行，同节点同类三轮不过熔断转人工；"人工"指自动化本来做不了的情况，汇总交人。各类问题的处理方式见优化技能的 `references/feedback_routing.md`；各类的检测逻辑与证据字段见 `references/error_classification.md`。
4. 综合评级 verdict：

   | verdict | 含义 | 触发反馈 |
   |---|---|---|
   | PASS | 全部命中（含祖先命中），无数据缺口 | 否 |
   | PASS_WITH_GAPS | 全部命中，有 `missing_inputs` | 否 |
   | PARTIAL_PASS | 有漏判或假阳，但六类都没命中 | 是 |
   | FAIL | 有漏判或假阳，且命中了某一类 | 是 |
   | EVAL_ERROR | 评估自身失败（子脚本没产出结果），`success` 为 false | 否，修评估输入后重评 |

   verdict 只做评级，永不复用六类错误的名字。

## 输出

```
<out>/                                # 默认 <result-dir 父目录>/<场景>-eval/
├── quality_score_batch.json          # 整批汇总：pass_rate、total_score、六维通过比例
├── feedback_to_generate.json         # 反馈，只在有问题时生成
├── failure_signatures.json           # 全部失败签名，只在有问题时生成
├── fuse_history.jsonl                # 熔断历史，跨轮累计
└── <case_id>-v<round>-<时间戳>/
    ├── evaluation_report.json        # 完整评估报告，唯一对外出口
    ├── quality_score.json            # 单用例六维评分
    ├── failure_signatures.json       # 本用例失败签名
    └── eval_run.json                 # 本次评估元数据
```

业务取数的两个值都在 `quality_score_batch.json`：准确率取 `pass_rate`（PASS 加 PASS_WITH_GAPS 的用例数除以全部用例数，执行失败、判卷异常、待裁决都算不通过）；多用例总得分取 `total_score`（各用例 total_score 的算术平均，没评出分的按 0）。单用例的 total_score 是"六关过了几关"（通过维度数除以 6 乘 100 取整）；六维与六类错误一一对应，单用例是布尔，整批是比例。

每个文件的完整例子和字段的计算规则见 `references/output_files.md`；六维的键名、三语名称与描述见 `references/score_dimensions.md`；反馈文件的完整结构见 `references/feedback_schema.md`。对任意历史报告可手跑 `python scripts/score_report.py <evaluation_report.json 或其目录>` 补出单用例评分。

## 与上下游的配合

- 上游 generate 生成场景技能（plan.json、解析脚本、tree_index）；中游 execute 执行诊断，产出 result 目录；本技能读产物与 expected.json 对账，输出反馈。
- 反馈由 cs-scot-generate-optimize 的反馈处理线承接：运行 route_feedback.py 得到每条问题的处理方式，自动档直接执行后全库重跑重评进入下一轮。
- 轮次通过 evaluation_report.json 传递：第二轮把第一轮的报告作为 `--prev-evaluation` 传入。

## 完整例子

用户说：

```
请帮我评估执行结果（第二轮，接上轮评估）。
- 用例：cases/ldp-lsp-001/
- 执行结果：output/ldp-lsp-result/result-20260811T120000Z-8b3d21/
- 技能包：scot技能/ldp-lsp-is-not-established/
- 上一轮评估：output/ldp-lsp-eval/ldp-lsp-001-v1-20260810T034800Z/evaluation_report.json
```

执行：

```bash
python "<本目录>/scripts/eval_run.py" \
  --case ldp-lsp-001 \
  --result-dir output/ldp-lsp-result/result-20260811T120000Z-8b3d21 \
  --skill-dir scot技能/ldp-lsp-is-not-established \
  --out output/ldp-lsp-eval \
  --prev-evaluation output/ldp-lsp-eval/ldp-lsp-001-v1-20260810T034800Z/evaluation_report.json
```

预期：`output/ldp-lsp-eval/ldp-lsp-001-v2-<时间戳>/` 下出现 evaluation_report.json 与 quality_score.json，round 为 2；根目录的 quality_score_batch.json 更新。汇报时原样给出脚本打印的 verdict、对账计数、命中的错误类（如有）与两个文件的路径；有反馈文件就指出它的位置，由优化技能承接。

日常回归的例子：

```bash
python "<本目录>/scripts/regress.py" --skill-dir scot技能/ldp-lsp-is-not-established \
  --case-dir cases --out output/ldp-lsp-regress
```

预期：逐用例打印执行与判卷状态，结尾打印通过数、通过率与总得分；`output/ldp-lsp-regress/eval/quality_score_batch.json` 与 `regress_summary.json` 写出。

## 注意

- 每轮评估创建独立目录，互不覆盖；中间产物放系统临时目录，不回拷。
- 所有路径通过参数传入，没有硬编码；默认输出目录由 result-dir 推导。
- 与具体指标类型无关；rca_input 的剪枝形态（`fault_tree.pruned_nodes`）与完整形态（`indicator_results`）都能识别。
- 评估失败不冒充通过：子脚本没产出结果就是 EVAL_ERROR，绝不算 PASS。
