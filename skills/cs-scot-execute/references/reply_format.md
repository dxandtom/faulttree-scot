# 诊断结果怎么汇报

诊断结论一律由脚本渲染，不手写。本文说明渲染出哪三份文件、最终回复怎么组织、用户用英文提问时怎么处理。

## 1. 渲染三份文件

```bash
python "<fault-tree-rca 目录>/scripts/render_report.py" "<result>/rca.json" --tree "<assets>/tree/<场景>.tree_index.json"
```

| 文件 | 内容 |
|---|---|
| `rca_report.md` | 完整报告：确证、疑似、待确认、排除留痕、树面问题；证据是命中原文、实测值、来源，逐字来自 rca.json |
| `rca_tree.txt` | 树视图：只展开确诊、疑似、缺数据所在的路径，节点带判据、证据、方案 |
| `rca_reply.md` | 聊天框回复稿：树视图 + 确证根因表 + 疑似表 + 待确认 |

为什么不让模型自己总结：实测里证据会被压成"S 脚本 matched=true"，内部字段名（absent=True 之类）直接漏给用户。所以这些内容不自己写、不改写。

## 2. 最终回复的组织

最终回复的正文就是 `rca_reply.md` 的全文，原样贴出：用 Read 读出来整段复制，树视图保持在 ```text 代码块里，表格一行不删、一格不改。平台上用户只看得见聊天框，文件没人打开，树视图和结论表不进回复正文等于没做。

正文前面加"执行过程"：每步一行，写脚本输出的要点，含 draft_rca 和 finalize_rca 的候选与合成统计，另加数据缺口与被跳过的文件（计划里缺的数据源、没匹配上的文件），并说明结论只代表已提供数据能看到的部分。

正文末尾的"补充说明"是唯一可以自己写的地方：同一病根的归组、处理优先级、需要人复核的疑点。只解读，数字以渲染出来的段落为准。

除这两段外不另写结论、不省略树视图、不把表改写成自己的话。

## 3. 用户用英文提问时

树和回复稿目前只有中文素材，正式的英文支持见仓库 `docs/遗留任务.md`。过渡做法：仍先照上面渲染三份文件，再把 `rca_reply.md` 逐行翻译成英文作为回复正文。这不是自由发挥，是按下面六条规则做文字替换：

1. **结构逐一对应**：段落的顺序与数量、每段的标题、树视图所在的 ```text 代码块、表格的行列与格数、待确认列表的条数，都与 rca_reply.md 一致；树视图一行对一行，缩进、连线（├─ └─ │）、符号（✅ ⚠ ? ✓ ✗ ·）、折叠计数一个不改。不增删段落或行，不在段落之间插自己的话。
2. **固定文案按对照表翻，不换说法**：

   | 中文 | 英文 |
   |---|---|
   | 诊断结论 | Diagnosis |
   | 确证根因 | Confirmed root causes |
   | 疑似根因 | Probable root causes |
   | 待确认 | To be confirmed |
   | 补充说明 | Notes |
   | 树视图 | Tree view |
   | 确诊 / 疑似 / 缺数据 | Confirmed / Probable / Missing data |
   | 成立 / 不成立 / 未判定 | Holds / Does not hold / Not evaluated |
   | 判据 / 证据 / 依据 / 卡点 / 注意 / 方案 / 缺 | Criterion / Evidence / Basis / Blocked at / Caveat / Solution / Missing |
   | 根因 / 根因路径 / 推荐方案 | Root cause / Path / Recommended solution |
   | 原文匹配（实测 / 依据） | Match (observed / basis) |
   | 未满足确证条件，给出卡点 | did not meet the confirmation criteria; blocked-at is given |
   | 数据不足/无法判断 | insufficient data / cannot judge |
   | …（详见报告全文） | … (see full report) |
   | …（子树 N 个节点未判定，数据补齐后重跑） | … (N nodes in subtree not evaluated; rerun after data is supplied) |
   | …（本轮无确诊/疑似/缺数据节点，树未展开） | … (no confirmed/probable/missing-data node this round; tree not expanded) |

3. **节点名**译成英文后括注中文原名，如 `Interference (干扰导致)`；判据、卡点、注意、方案这类自由文本逐句直译，不概括、不补充、不解释；拿不准的词保留中文并括注。
4. **不翻的**：证据行（设备回显、实测值、来源文件名、命令名）、数字、节点编号、判定档位、表格分隔线。
5. 正文第一行固定写 "Translated by the model from the mechanical Chinese output; Chinese original: <result>/rca_reply.md"；"补充说明"照旧只在末尾写，用英文，仍只解读不改数字。
6. 启用条件：用户本轮提问整段是英文；中英混杂按中文处理，不翻。

这是过渡做法，不是许可改写结论。
