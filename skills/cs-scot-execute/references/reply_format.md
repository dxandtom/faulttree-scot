# 诊断结果怎么汇报

诊断结论一律由脚本渲染，不手写。本文说明渲染出哪三份文件、最终回复怎么组织、用户用英文提问时怎么处理。

## 1. 渲染三份文件

```bash
python "<fault-tree-rca 目录>/scripts/render_report.py" "<result>/rca.json" --tree "<assets>/tree/<短名>.tree_index.json"
```

| 文件 | 内容 |
|---|---|
| `rca_report.md` | 完整报告：确证、疑似、待确认、排除留痕、树面问题；证据是命中原文、实测值、来源，逐字来自 rca.json |
| `rca_tree.txt` | 树视图：只展开确证、疑似、缺数据所在的路径，节点带判据、证据、方案。树视图里打印的标签是"确诊/疑似/缺数据"，对应四档里的确证根因、疑似、待确认 |
| `rca_reply.md` | 聊天框回复稿：树视图 + 确证根因表 + 疑似表 + 待确认 |

为什么不让模型自己总结：实测里证据会被压成"S 脚本 matched=true"，内部字段名（absent=True 之类）直接漏给用户。所以这些内容不自己写、不改写。

## 2. 最终回复的组织

最终回复的正文就是 `rca_reply.md` 的全文，原样贴出：用 Read 读出来整段复制，树视图保持在 ```text 代码块里，表格一行不删、一格不改。平台上用户只看得见聊天框，文件没人打开，树视图和结论表不进回复正文等于没做。

正文前面加"执行过程"：每步一行，写脚本输出的要点，含 draft_rca 和 finalize_rca 的候选与合成统计，另加数据缺口与被跳过的文件（计划里缺的数据源、没匹配上的文件），并说明结论只代表已提供数据能看到的部分。

正文末尾的"补充说明"是唯一可以自己写的地方：同一病根的归组、处理优先级、需要人复核的疑点。只解读，数字以渲染出来的段落为准。

除这两段外不另写结论、不省略树视图、不把表改写成自己的话。

## 3. 用户用英文提问时

启用条件：用户本轮提问整段是英文；中英混杂按中文处理。

1. 裁决单 `decisions.json` 里的理由、注意、卡点、支持证据用英文写。脚本会把这些原样带进 rca.json 的中英两个版本。
2. 渲染时加 `--lang en`：

   ```bash
   python "<fault-tree-rca 目录>/scripts/render_report.py" "<result>/rca.json" --tree "<assets>/tree/<短名>.tree_index.json" --lang en
   ```

   脚本负责的部分：标题、表头、标签（Criterion / Observed / Basis / Verdict / Blocked at / Caveat / Suggested solution / Note，反证行标 Counter-evidence）、树视图头行与折叠提示、判定词（holds / does not hold / insufficient data / conflict）；节点名用树里的 `name_en`，判据用 `condition_en`，方案用 `solutions.json` 的 `en`；脚本写进 rca.json 的机械说明（祖先未验证、层级矛盾、叶子因待定、缺口分型、待确认原因、排除留痕）用同名 `_en` 字段的英文版。树里没有英文的节点回退中文，不算错误。
3. 回复稿里可能剩下的中文只有分析脚本给出的事实原文：`how`（判断依据的说明）和事实自带的 reason。这些由你逐句直译成英文：不概括、不补充、不解释，拿不准的词保留中文并括注。回退成中文的节点名，译成英文后括注中文原名，如 `Interference (干扰导致)`。
4. 不翻的：证据行（设备回显、实测值、来源文件名、命令名）、数字、节点编号、判定档位、表格分隔线。
5. 结构一个不改：段落顺序与数量、树视图的每一行、缩进、连线（├─ └─ │）、符号（✅ ⚠ ? ✓ ✗ ·）、折叠计数、表格的行列与格数。
6. 正文第一行固定写 "Rendered in English from the stored bilingual fields; fact texts translated by the model. Chinese original: <result>/rca_reply.md"。"补充说明"照旧只在末尾写，用英文，只解读不改数字。

树里没有英文时（生成时母表没有英文列，迁移时平台表也没给），节点名和判据仍是中文，其余已是英文；这时按第 3 条逐句直译节点名与判据，并在补充说明里提一句"该场景技能未存英文，节点名与判据为临时翻译"。要让英文稳定，把带英文列的平台全量表交给 cs-scot-migrate 同步一次即可。
