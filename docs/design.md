# FaultTree-SCoT 总体设计

> 状态：与仓库当前实现一致 · 2026-09-18。早期的 v0 草案已被本版取代；本文是外人了解项目的第一份文档，细节以各技能的 SKILL.md 为准。

## 1. 一句话

网络保障（CS）场景下，基于业务同事人工编写的故障树和现场采集的数据，自动生成一套场景专属的诊断脚本与执行计划，在平台上执行，产出根因路径和逐节点的判断依据；并用评估反思和优化闭环持续改进。

## 2. 七个技能与分工

| 技能 | 阶段 | 做什么 | 由谁用 |
|---|---|---|---|
| cs-scot-generate | 生成 | 把故障树 Excel 和各数据源的表头编译成场景产物：故障树索引、执行计划 `plan.json`、每个数据源一份"逐条件分析脚本"，并直接生成可运行的场景技能 | 工具链开发者，一个场景生成一次 |
| cs-scot-execute | 执行 | 场景技能的执行指引：读计划，顺序运行各数据源的分析脚本得到事实，交给根因分析 | 平台上的用户，每来一批数据跑一次 |
| fault-tree-rca | 执行 | 根因分析：候选清单由脚本枚举并自动定档，模型只裁决冲突候选，脚本合成结果并渲染报告 | 由 cs-scot-execute 调用 |
| datacom-fault-tree-tester | 优化 | 业务同事把一个节点的判断方法写成规范文档，生成并验证判断脚本，打成节点交付包 | 业务同事 |
| cs-scot-migrate | 优化 | 把节点交付包迁移进场景技能：判据替换、绑定换成直接运行交付脚本；也按平台全量表增删节点、刷新工具链公共文件 | 工具链开发者或平台 |
| cs-scot-reflect | 评估 | 用用例库评估执行结果：对账、六类错误归因、通过率与得分；输出反馈 | 工具链开发者或平台 |
| cs-scot-generate-optimize | 入口 | 生成与优化的统一入口：按输入判断走生成、导入优化、增删节点还是反馈处理 | 平台 |

辅助部件：zip2csv（原始采集数据转成三张表，随场景技能内置）。

## 3. 一次完整的流程

```
故障树 Excel ──① cs-scot-generate──▶ 场景技能（SKILL.md + assets/：tree_index、plan.json、各源分析脚本）
                                            │
现场数据（csv/xlsx，或原始日志经 zip2csv 转表）
                                            │  ② cs-scot-execute
                                            ▼
                               result-<运行号>/rca_input.json    逐节点事实：成立 / 不成立 / 数据不足
                                            │  ③ fault-tree-rca
                                            ▼
                               rca.json（确证 / 疑似 / 待确认 / 树问题）+ rca_report.md + 树视图 + 回复稿

业务同事 ──④ datacom-fault-tree-tester──▶ 节点交付包 ──⑤ cs-scot-migrate──▶ 场景技能（判据与绑定更新）
用例库 + 场景技能 ──⑥ cs-scot-reflect──▶ 通过率、六类错误、反馈 ──⑦ cs-scot-generate-optimize──▶ 自动处理或交人
```

- ①：一个场景生成一次。绑定（每条判据用哪个数据源的哪一列、怎么算）由模型逐条判断后写进脚本，之后不再需要模型。
- ②③：每批数据跑一次。确定性的统计全在脚本里，模型只在根因分析这一步裁决冲突候选。
- ④⑤：业务同事的判断方法以脚本形式进入场景技能，判断逻辑由交付方负责，工具链只做格式检查和适配。
- ⑥⑦：评估把没通过的用例归到六类错误之一，优化入口按类给出处理方式，自动档直接执行，同一问题三轮不过转人工。

## 4. 技能之间靠哪些文件交接

| 文件 | 由谁写 | 由谁读 | 内容 |
|---|---|---|---|
| `assets/tree/<短名>.tree_index.json` | generate | execute、rca、migrate、reflect | 故障树结构：节点编号、名称、判据、层级 |
| `assets/plan.json` | generate | execute | 执行计划：先跑哪些分析脚本，最后做根因分析 |
| `assets/scripts/<数据源>_analyzer.py` | generate（绑定由模型填） | execute | 每个数据源的逐条件分析脚本 |
| `assets/tree/<短名>.tuning.xlsx` | generate（步骤 5 打包时自动导出；默认流程下在步骤 6 前用 `export_tree_excel.py` 导出）、migrate | 人工调优、migrate | 调优表：一行一个节点，树和绑定的可编辑事实源 |
| `result-<运行号>/rca_input.json` | execute | rca、reflect | 逐节点事实 |
| `result-<运行号>/rca.json` | rca | execute（渲染）、reflect | 四档根因结论 |
| `tree/migrations.json` | migrate | reflect（建用例库）、optimize | 迁移记录：节点对应哪个交付包 |
| `cases/<case_id>/expected.json` | reflect（case_gen） | reflect | 用例期望；不传给生成与执行 |
| `quality_score_batch.json`、`evaluation_report.json` | reflect | 平台、optimize | 整批通过率与得分；单用例评估报告 |

## 5. 设计原则

1. **代码算，模型判。** 计数、比阈值、匹配这类确定性统计写进生成的脚本；模型只做两处判断：生成时把判据绑定到数据列，执行时裁决有冲突的根因候选。
2. **一次生成，反复执行。** 绑定依据数据的格式和判据的语义，不依赖某一批数据里碰巧出现的值；同一场景的脚本生成一次，换数据反复跑。
3. **诚实性由脚本结构保证。** 根因候选由脚本枚举，结果由脚本合成，模型改不了数字；数据拿不到就是"数据不足"，不用相近的量顶替，也不把"没查到"当成"正常"。
4. **调优表是人机接口。** 树的增删、判据与绑定的修改都通过调优表往返完成，导入时统一校验，写入是原子的。
5. **判断逻辑归交付方。** 业务同事的判断脚本原样运行，工具链只检查格式、做输入输出适配，问题退回交付方修改。
6. **文档分层。** 每个技能的 SKILL.md 只写流程，细节放在 references 并说明何时去读；术语先定义，不用项目内部说法。

## 6. 目录地图

```
skills/
  cs-scot-generate/            生成：SKILL.md、scripts/、references/（绑定格式、派发、绑定规则、数通接入）
  cs-scot-execute/             执行指引：SKILL.md、scripts/runner.py、check_data.py、references/
  fault-tree-rca/              根因分析：SKILL.md、scripts/（draft、finalize、render）、references/rca_rules.md
  datacom-fault-tree-tester/   业务侧：SKILL.md、判断方法_规范.md、scripts/（共享解析库、格式检查）
  cs-scot-migrate/             迁移：SKILL.md、scripts/、references/、engine/（公共文件副本，单独安装用）
  cs-scot-reflect/             评估反思：SKILL.md、scripts/、references/
  cs-scot-generate-optimize/   统一入口：SKILL.md、scripts/（route_feedback、package_skill）、references/
  zip2csv/                     原始数据转表：SKILL.md、scripts/、vendor/ifilter.py
integration/platform1/         与平台联调的驱动、接入服务、交接说明
tests/                         七套测试；integration/platform1/tests/ 是端到端测试
docs/                          本文、交接文档、遗留任务
```

## 7. 现状与遗留

- 七个技能与一个辅助部件已实现并在平台联调；发行包由 `cs-scot-generate-optimize/scripts/package_skill.py` 打出。
- 已讨论、尚未动手的改动记录在 `docs/遗留任务.md`。
- 平台接入见 `integration/platform1/README.md` 与同目录的交接说明。

## 8. 几个容易混淆的说法

| 说法 | 指什么 |
|---|---|
| 格式检查 | datacom-fault-tree-tester 的 `check_contract.py` 对交付脚本输入输出格式的检查；cs-scot-migrate 迁入前也跑它 |
| 格式约定 | 绑定怎么写的规定，即 cs-scot-generate 的 `references/condition_binding_schema.md`（产物 `scripts/` 下带副本），调优表里的绑定列按它填 |
| 产物格式版本 | 产物 `manifest.json` 里的 `product_format_version`，执行与评估启动时比对，旧产物只告警不阻断 |
| 短名 | 场景的英文短名（slug），技能目录名和 `assets/tree/` 下文件名都用它；取不到英文时是中文场景名 |
| 分析脚本 | 产物 `scripts/<指标>_analyzer.py`，每个数据源一份，逐条件输出事实；各技能文档统一叫这个名字 |
| 局部子树形态、完整形态 | `rca_input.json` 的两种形态：常态是按 tree_index 剪出的局部子树，没有 tree_index 或剪枝不划算时是完整形态 |
| 定档 | 根因分析把候选归入确证、疑似、待确认、排除四档之一，由脚本按机械规则完成，只有冲突候选交模型 |
| 处理项、处理清单 | 反馈处理线由 route_feedback.py 给出的每条问题的处理方式，以及装着全部处理项的 `feedback_tickets.json` |
