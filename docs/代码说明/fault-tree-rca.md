# fault-tree-rca 代码说明

## 技能概述

fault-tree-rca 是执行阶段的最后一步，负责根因分析。按 `docs/design.md` 的流程，cs-scot-execute 的 `runner.py` 先顺序运行各数据源的分析脚本得到逐条件事实，再按树索引把故障树剪成局部子树，把两者合写成本轮结果目录 `result-<运行号>/rca_input.json`；本技能从这个文件出发，产出根因分析结果 `rca.json` 和三份报告文件，下游的 cs-scot-reflect 用 `rca.json` 与用例库对账评估。它与指标种类无关，故障树换成任何场景，脚本和规则都不变。整个技能贯彻「代码算，模型判」：候选由脚本枚举，结论等级中程序能判定的部分由脚本直接给出，结果由脚本组装和渲染，模型只对候选自身证据矛盾的少数条目做判断，并在报告末尾写补充说明。

输入是 `rca_input.json`，有两种形态。常态是局部子树形态：`fault_tree.pruned_nodes` 是一个以节点编号（`n1`、`n2` 这类短编号）为键的字典，每个节点带 `name`、`parent`、`level`、`condition`、`combine`、`is_leaf`、`uncovered_children`、`pass_through`、可选的 `name_en`，以及挂在 `facts` 下的事实列表；另有 `fault_tree.outline`（节点编号到粗状态的导航表）、`tree_issues`、`unmapped_facts`、`uncovered_values`。备用形态是完整形态：没有剪枝树，只有 `fault_tree_md`（故障树正文）和 `indicator_results`（按指标分组的逐条件事实）。可选输入还有树索引 `assets/tree/<短名>.tree_index.json`（节点结构、`name_en`、`condition_en`）、解决方案表 `assets/tree/solutions.json`（形如 `{节点编号: {"zh": …, "en": …}}`）和模型判断结果文件 `decisions.json`。输出依次是候选清单 `rca_draft.json`、根因分析结果 `rca.json`（顶层固定四个键 `rca_result`、`probable_causes`、`undetermined`、`tree_issues`，有排除时另有 `excluded_note`）、完整报告 `rca_report.md`、树视图 `rca_tree.txt`、聊天回复稿 `rca_reply.md`。

六个脚本的调用关系如下，每轮数据只跑一遍：

```text
result-<运行号>/rca_input.json
   │  python scripts/draft_rca.py rca_input.json
   ▼
rca_draft.json（候选清单：候选、路径、证据、auto_verdict）
   │  conflict_nodes 非空时，模型写 decisions.json；否则跳过
   ▼
   python scripts/finalize_rca.py rca_draft.json [decisions.json] -o rca.json
          [--rca-input rca_input.json] [--solutions assets/tree/solutions.json]
   ▼
rca.json
   │  python scripts/render_report.py rca.json --tree <树索引> [--lang en]
   │     内部 import render_tree，调用 render_tree() 得到树视图文本
   ▼
rca_report.md、rca_tree.txt、rca_reply.md

辅助：rca_query.py 按节点抽取 rca_input.json 的条件与事实，供模型查看细节；
      validate_rca.py 对 rca.json 做事后检查，供人工抽查。
```

本目录会被整目录复制进场景技能的 `assets/fault-tree-rca/`（cs-scot-generate-optimize 的 `package_skill.py` 打包时复制，cs-scot-migrate 的 `sync_engine.py` 负责刷新）；`skills/cs-scot-migrate/engine/fault-tree-rca/` 是 `sync_engine.py` 生成的副本，只在 cs-scot-migrate 单独部署时使用，不要手改。cs-scot-reflect 的 `regress.py` 在评估时按 `draft_rca.py`、`finalize_rca.py`、`render_report.py` 的顺序重跑这条链。相关测试在 `tests/test_rca_bilingual.py`（中英并列字段）、`tests/test_render_tree.py`（树视图与回复稿）和 `tests/test_engine_sync.py`（副本一致性），都是标准库脚本，直接用 `python` 运行。

## 文件清单

| 文件 | 类型 | 作用 |
|---|---|---|
| `SKILL.md` | 技能说明 | 模型执行根因分析的流程指引：三步命令、术语、输入输出格式、渲染与汇报要求 |
| `references/rca_rules.md` | 参考文档 | 判定规则全文：字段映射、四个结论等级的条件、多数据源事实的汇总、输出字段含义 |
| `scripts/draft_rca.py` | 脚本 | 从 `rca_input.json` 枚举候选，预填路径与证据，给出程序能判定的结论等级，写出候选清单 `rca_draft.json` |
| `scripts/finalize_rca.py` | 脚本 | 把候选清单和模型判断结果组装成 `rca.json`，组装前逐条检查并拒绝不合规的判断 |
| `scripts/render_report.py` | 脚本 | 由 `rca.json` 渲染完整报告 `rca_report.md` 与聊天回复稿 `rca_reply.md`，带树索引时调用树视图并写 `rca_tree.txt` |
| `scripts/render_tree.py` | 脚本 | 由 `rca.json` 与树索引渲染文本树视图，只展开有结论的路径 |
| `scripts/rca_query.py` | 脚本 | 按节点或分支抽取 `rca_input.json` 的条件与事实，供模型查看细节而不整读文件 |
| `scripts/validate_rca.py` | 脚本 | 对已生成的 `rca.json` 做事后检查，供人工抽查；正常流程不需要运行 |

## 各文件说明

### SKILL.md

技能说明，模型做根因分析时读的流程文件：三步命令（`draft_rca.py` 生成候选清单；`conflict_nodes` 非空才写 `decisions.json`；`finalize_rca.py` 组装 `rca.json`）、术语表、两种输入形态、三条判定原则、输出格式、推荐解决方案的处理、渲染与汇报要求。它是模型调用本目录六个脚本的入口说明，细节指向 `references/rca_rules.md`。

### references/rca_rules.md

参考文档，SKILL.md 要求模型分析前读完。内容是与指标无关的判定规则：输入字段含义、每条条件「找 X 的实测值，找不到即数据不足」的四步判法、树里的量与数据列的映射规则、四个结论等级的条件与禁止的排除理由、同一节点多个数据源事实的汇总规则、输出字段写法。`draft_rca.py` 的状态汇总、`finalize_rca.py` 与 `validate_rca.py` 的检查项都是这份规则的代码实现，改规则要三处同步。

### scripts/draft_rca.py

**功能与作用**

候选清单生成器，根因分析的第一步，由模型按 SKILL.md 的指引在结果目录里运行：`python draft_rca.py <rca_input.json> [-o <rca_draft.json>]`，不给 `-o` 时写到输入同目录的 `rca_draft.json`。它读 `rca_input.json`，把每个有事实的条件节点归入四种节点状态之一，从中选出候选，预填根到候选的路径和逐节点证据，对程序能判定的候选直接写出结论等级（字段 `auto_verdict`），只把候选自身证据矛盾的条目留给模型。文件头注释记录了引入它的原因：让模型自己逐节点翻找候选时，能力较弱的模型会把 `predicate_satisfied` 为真的成立节点整个漏掉，或把成立写成「证据不足」。脚本同时生成说明文字的中英两版，英文放在同名加 `_en` 的字段里，中文字段保持原样供平台和评估解析。cs-scot-reflect 的 `regress.py` 在评估时也调用它。

**实现介绍**

处理顺序由 `main` 决定：读入 JSON（读不到或解析失败打印错误并返回退出码 2），若 `fault_tree.pruned_nodes` 存在走 `draft_pruned`，否则走 `draft_md`，写出结果并打印各类条目数，返回 0。

单个节点的状态汇总在 `_tri(facts)`。一个节点可能被多个数据源同时绑定，`facts` 里就有多条事实，每条带 `status`、`predicate_satisfied`、`class`（内核算子类别）、`absent` 等字段。只有 `status` 为 `ok` 的事实参与判断：有任何一条 `predicate_satisfied` 为真即有正向证据；`predicate_satisfied` 为假的事实再分强弱，由 `_weak_neg` 区分：

```python
    return str(f.get("class") or "").upper() in ("C", "G") and not f.get("absent")
```

即类别为 C（字段匹配）或 G（日志匹配）且不是 `absent` 缺失类判据的未命中算弱否定（代码里的 `_weak_neg`），只说明这份数据里没有看到，不构成反证；其他类别的不成立，包括 A（阈值比较）、P（成对比较）、D（多条件组合）、S（脚本类），以及 C、G 中 `absent=true` 的不成立（未出现才算成立，命中了就是确实不成立），都算强反证。`_tri` 的判断主体是：

```python
    ok_t = any(f.get("status") == "ok" and f.get("predicate_satisfied") is True for f in facts)
    ok_f = [f for f in facts if f.get("status") == "ok" and f.get("predicate_satisfied") is False]
    if ok_t and any(not _weak_neg(f) for f in ok_f):
        return "冲突"
    if ok_t:
        return "成立"
    if ok_f:
        return "不成立"
    return "数据不足"
```

四种节点状态对应：有正向证据且有强反证为证据矛盾（代码字面值 `冲突`）；有正向证据、反面只有弱否定或没有反面为成立，弱否定由 `_ev_holding` 记到证据条目的 `other_source_note` 里，不拉低判定；没有正向证据但有未命中（强弱都算）为不成立；全部事实都是 `insufficient_data` 或 `manual` 为数据不足。完全没有 `facts` 的节点不进候选清单，由 `tree_issues` 报出。

`_bucket_nodes` 遍历 `pruned_nodes`，跳过 `_no_own_cond` 判定为没有自身条件的节点（`pass_through` 为真，或 `condition` 为空、含「无条件」「无判断」），再跳过没有事实的节点，其余按 `_tri` 分到成立、不成立、数据不足、证据矛盾四个列表。候选的选取在 `_deepest_holding`：成立节点中没有成立后代的那些，即最深的成立节点；成立的上级体现在候选路径里，不单独成条。判断方法是对每个成立节点检查它是否出现在其他成立节点的路径（`_path`，沿 `parent` 上溯到根，剪枝保证祖先全在）的非末位。

每个候选由 `_candidate_entry` 生成条目：`node`、`node_id`、`path_names`、`evidence_prefilled`（路径上每个节点经 `_ev` 生成的证据条目）、`support_hint`（正向事实经 `_actual` 拼出的实测值摘要）。`_ev` 对单个节点：无自身条件的写 `judgment` 为 `无独立条件`；否则 `judgment` 为 `_tri` 结果，成立和证据矛盾的用 `_pick` 取一条正向事实作代表填 `actual`、`how`（`how` 逐字取事实的 `explain`），证据矛盾的再取一条反面事实填 `conflict_actual`、`conflict_how`；不成立的用反面事实填 `actual`、`how`；数据不足的由 `_ev_insufficient` 处理，没有事实写固定提示，有 `reason` 用第一条 `reason` 原文，再由 `_apply_gap` 根据事实的 `gap_kind` 加上 `no_data`（缺数据）、`cannot_judge`（数据在但判不出）或 `mixed` 的分型并给 `actual` 加前缀。`_actual` 把事实里 `matched`、`total`、`ratio`、`value`、`op`、`threshold`、`count_satisfied` 等字段按「键=值」串起来，S 脚本的 `evidence` 列表取前两条，否则取 `hits` 第一条，整体截到 400 字符，数字不加工。

结论等级沿路径看上级状态。`_ancestor_notes` 扫描路径上除候选自身外的证据条目，把状态为数据不足、不成立、证据矛盾的上级分别写进 `ancestor_insufficient`、`ancestor_refuted`、`ancestor_conflict` 三个说明字段，并返回不成立与证据矛盾两组。`_auto_tier` 据此给出结论等级：两组都为空，`auto_verdict` 为 `confirmed`，未绑定或数据不足的上级视为默认放行（没有信息不当作反证），由 `finalize_rca.py` 记入 `caveats`；任一组非空，`auto_verdict` 为 `probable`，取离候选最近的那个不成立上级（没有不成立时取证据矛盾的）写进 `blocked_at_hint` 与 `note`（含该上级的实测值，各带 `_en` 版本）。注释记录了这条规则的依据：上级留着绑定却实测不成立，是有效反证；判断方法写错的中间节点，正确做法是在调优表里清掉它的绑定，下轮即按默认放行确证。

非叶子候选的处理在 `_candidate_entry` 后半段：候选 `is_leaf` 为假或带 `uncovered_children` 时，用 `_children_status` 列出直接子节点各自的状态（没有事实的子节点和覆盖报告里的未覆盖子节点标为未覆盖，名字从 `tree_issues.uncovered_condition_nodes` 回填），写进 `children_status`，并加 `leaf_pending` 说明。注释说明这是用户确定的规则：叶子没有一个成立时，中间层命中就按本层确证，叶子层原因待定由 `finalize_rca.py` 记入 `caveats`，数据补齐后重跑可细化。

`draft_pruned` 把候选按 `auto_verdict` 分成 `confirmed_candidates` 与 `probable_candidates`（按节点编号的数字排序），证据矛盾节点经 `_conflict_entry` 生成 `conflict_nodes`（有路径与证据、无 `auto_verdict`，交模型判断），不成立节点的证据条目放 `excluded_candidates`，数据不足节点的放 `undetermined_skeleton`，再加一段 `_readme` 说明模型该做什么。

输入是完整形态时走 `draft_md`：没有剪枝树就拼不出路径，退化为按节点状态分拣。它遍历 `indicator_results` 每个指标的 `conditions`，对每条的单个 `fact` 调 `_tri`，生成含 `node`、`node_id`、`indicator`、`condition`、`actual`、`how` 的条目，分到 `holding_candidates`、`conflict_nodes`、`excluded_candidates`、`undetermined_skeleton`。这一形态没有 `auto_verdict`，`holding_candidates` 全部需要模型逐条判断，路径要模型按 `fault_tree_md` 的层级自行回溯。

**修改时要注意**

- `_weak_neg` 的强弱划分与 `validate_rca.py` 的 `_strong_refuted` 是同一规则的两份实现，改一处必须同步另一处；注释记录的问题是告警源命中 2 行、日志源 0 命中时，节点曾被错标为证据矛盾。
- `_put` 只在英文与中文不同时写 `_en` 字段，纯数字的 `actual` 两边相同就没有 `actual_en`；读取方（`finalize_rca.py` 和两个渲染脚本）都按「有 `_en` 用英文、否则用原文」回退。新增说明字段要沿用 `_put`，中文字段逐字不变，否则 `tests/test_rca_bilingual.py` 里「带与不带英文名两次输出中文逐字一致」的检查会失败。
- `_L` 模板里的中文文案是平台与评估解析的依据，改字面会影响下游；`_apply_gap` 按 `actual` 是否已带「数据不足」「数据在,」「部分缺」前缀判重，改前缀文字要同时改这里的判断。
- `_no_own_cond` 用「无条件」「无判断」子串识别没有自身条件的节点，与 `validate_rca.py` 的 `_holding_map` 用同一子串；树里节点条件文本若用了别的写法，这些节点会被当成有条件而进入状态汇总。
- 中间节点候选按本层确证是有意的设计，不要为了「只确证叶子」改回去，理由见 `_candidate_entry` 的注释。

### scripts/finalize_rca.py

**功能与作用**

根因分析的第三步，把候选清单和模型判断结果组装成 `rca.json`。用法：`finalize_rca.py <rca_draft.json> [<decisions.json>] -o <rca.json> [--rca-input <rca_input.json>] [--solutions <solutions.json>]`。候选清单全部由程序给出结论等级时可以不传 `decisions.json`。`--rca-input` 用来抄 `tree_issues`，`--solutions` 用来按根因节点查推荐解决方案。文件头注释说明了设计动机：候选清单解决了漏候选，但模型手写几百行 `rca.json` 仍会抄错，于是改成模型只交判断结果、机械字段由代码填，诚实性从事后校验变成「结构上做不出假的」。组装成功即为最终交付，不需要再运行 `validate_rca.py`。

**实现介绍**

`main` 的顺序是：`_load` 读候选清单；`_read_decisions` 读模型判断结果；`_adjudicables` 求必须判断的集合；`_apply_auto_verdicts` 合并程序已给的结论等级；`_check_coverage` 查覆盖；`_synthesize` 逐条组装确证、疑似与排除；`_undetermined_items` 生成待确认条目；拼出文档后 `_attach_extras` 附加 `tree_issues` 与 `recommend_solution`；`_write_doc` 写文件并打印统计。所有拒绝都用 `raise SystemExit("…")` 带中文说明退出，退出码为 1；正常返回 0。

`_load` 先按 JSON 解析，失败再用 `ast.literal_eval`，注释说明是因为能力较弱的模型常写 Python 风格的单引号和 `True`；两种都失败就退出。`_read_decisions` 接受列表或 `{"decisions": [...]}`，每条必须有 `node_id`，`verdict` 经 `_VERDICT` 归一到 `confirmed`、`probable`、`excluded`、`undetermined` 四个值（接受中英文同义词，如「确证」「疑似根因」「排除」「待确认」，也接受 `rca_result`、`probable_causes` 这类输出键名），归一结果存到条目的 `_v`。`_adjudicables` 把 `confirmed_candidates`、`probable_candidates`、`holding_candidates`（完整形态）、`conflict_nodes` 合成一个以 `node_id` 为键的字典，这些条目都有成立事实。

程序对结果的检查分布在以下函数，每一条都是拒绝条件：

1. `_read_decisions`：判断条目缺 `node_id`，或 `verdict` 不在同义词表里，拒绝。
2. `_apply_auto_verdicts`：候选带 `auto_verdict` 而模型给了不同的 `verdict`，拒绝，提示有异议应修数据或绑定后重跑，或在报告补充说明里请人复核；相同时保留模型补充的 `caveats`、`support` 等字段；没给判断的自动补一条，并计入「零模型判断」的条数。
3. `_check_coverage`：必须判断的集合里有候选没有判断结果，拒绝并列出；判断结果里出现候选清单（必判集合、`undetermined_skeleton`、`excluded_candidates`）之外的节点编号，拒绝，防止编造节点。
4. `_check_confirmable`（由 `_confirmed_item` 调用）：候选自身不干净，拒绝。「干净」由 `_dirty` 判定，只看路径末位即候选自身：

```python
    if not ev:
        return []
    e = ev[-1]
    j = str(e.get("judgment", ""))
    if any(c in j for c in _CLEAN) and "不成立" not in j:
        return []
    return [e]
```

   即 `judgment` 必须含「成立」或「无独立条件」且不含「不成立」，被 `overrides` 改成「存疑」「语义近似」的也算不干净。此外路径上任何上级的 `judgment` 含「不成立」或「冲突」，拒绝，提示层级矛盾只能归疑似。数据不足的上级不拦。
5. `_synthesize`：`verdict` 为 `excluded` 而 `reason` 为空，拒绝；有成立事实的候选被判为 `undetermined`，拒绝（待确认的定义是既无正向证据又缺数据）。
6. `_undetermined_items`：待确认条目被判为 `excluded` 而没有 `reason`，拒绝。

组装部分：`_ev_final` 把候选清单的 `evidence_prefilled` 逐字复制成 `evidence_path`，只保留白名单字段（`node`、`node_en`、`node_id`、`condition`、`actual`、`actual_en`、`how`、`judgment`、`conflict_actual`、`conflict_actual_en`、`conflict_how`、`other_source_note`、`other_source_note_en`），模型判断结果里的 `overrides`（形如 `{节点编号: 文字}`）只能改某节点的 `judgment`。完整形态的候选没有 `evidence_prefilled`，`evidence_path` 就只含候选条目自身一项，`Fault_tree_path` 只有候选名。`_confirmed_item` 写 `Root_cause`、`Fault_tree_path`（候选清单的 `path_names`）、`node_id`、`support`（模型给的优先，否则 `support_hint`）、`evidence_path`；`caveats` 由 `_confirmed_caveats` 拼成：模型给的 `caveats`，加 `_anc_notes` 按数据不足、不成立、证据矛盾、其他存疑四组生成的上级说明（每组最多列 5 个节点），候选是中间节点时再加 `_leaf_note` 的叶子层原因待定说明（最多列 6 个子节点状态）。`_probable_item` 的 `confidence` 默认 `medium`，`blocked_at` 取模型给的，否则候选清单预填的 `blocked_at_hint`；`caveats` 取模型给的或预填的 `note`，再附上级说明中含「数据不足」的那几条。排除的候选经 `_excl_note` 生成一句「『节点名』(编号):理由」，全部用分号连成顶层 `excluded_note`。`_undetermined_items` 遍历 `undetermined_skeleton`，每条生成 `Fault_tree_path`（只含该节点名）、`blocked_at`（「节点名(编号)」）、`reason`（模型给的，否则候选清单的 `actual`，否则「数据不足」）、空的 `observed`，有 `gap_kind` 一并带上。

中英文说明字段的生成：模板集中在 `_L`，`_t(key, lang)` 按语言取模板。`_anc_notes`、`_leaf_note`、`_excl_note` 都分别以 `zh` 和 `en` 各生成一遍，节点名由 `_nm` 取（英文时优先 `node_en`，没有回退中文名），`_put_en(item, key, en)` 只在英文版与中文版不同时写 `<key>_en`。因此 `caveats_en`、`blocked_at_en`、`support_en`、`reason_en`、`excluded_note_en`、`recommend_solution_en` 这些字段是条件性存在的。模型在判断结果里写的 `caveats`、`blocked_at`、`support`、`reason` 是自由文本，按当前对话语言写，原样进入两种版本。`_attach_extras` 读 `rca_input.json` 失败或读 `solutions.json` 失败都只打印 `[warn]` 不中断；`recommend_solution` 取 `zh` 优先、没有用 `en`，只给 `rca_result` 和 `probable_causes` 的条目加。

**修改时要注意**

- `_dirty` 和 `_check_confirmable` 的上级检查都用子串匹配（「成立」「不成立」「冲突」），「不成立」含「成立」这一点要单独排除；新增 `judgment` 字面值时要核对这两处。
- `overrides` 的约定是只把 `judgment` 改成存疑，但 `_ev_final` 对任何节点（包括候选自身）都原样写入 `overrides` 给的文字，代码没有限制内容和方向；如果写成「成立」，可以让证据矛盾的候选通过确证检查，这条约定目前靠文档而不是代码保证。
- 证据矛盾候选的自身 `judgment` 是 `冲突`，判 `confirmed` 会被 `_dirty` 拒绝，模型只能判疑似或排除。完整形态的 `holding_candidates` 条目没有 `judgment` 字段，按代码现状判 `confirmed` 同样会被当作不干净拒绝，除非用 `overrides` 给该节点写 `judgment`；这是否有意，代码未说明。
- 待确认条目的 `Fault_tree_path` 只有该节点名一项，`render_tree.py` 靠 `blocked_at` 末尾的「(编号)」定位节点，改 `blocked_at` 格式会让树视图找不到待确认节点。
- `undetermined_skeleton` 里的节点不在必判集合内，模型没写判断时自动带出；只有写了 `excluded` 才转成排除记录。

### scripts/render_report.py

**功能与作用**

由 `rca.json` 渲染完整报告 `rca_report.md` 和聊天回复稿 `rca_reply.md`，带 `--tree` 时调用 `render_tree.py` 生成树视图并写 `rca_tree.txt`。用法：`render_report.py <rca.json> [-o <rca_report.md>] [--tree <tree_index.json>] [--lang zh|en]`，不给 `-o` 写到 `rca.json` 同目录，`rca_tree.txt` 与 `rca_reply.md` 写在输出报告的同目录。cs-scot-execute 的第 4 步调用它，cs-scot-reflect 的 `regress.py` 也调用。文件头注释说明了为什么不让模型总结：实测中证据被压成「S脚本matched=true」，内部字段名直接漏给业务同事；所以证据栏从 `evidence_path` 逐字拼出，模型只在报告末尾「补充说明」段落写解读。

**实现介绍**

`main` 读 `rca.json`；若给了 `--tree`，把脚本所在目录加入 `sys.path`，从 `render_tree` 导入 `_load_tree`、`load_solutions`、`render_tree`，得到树节点字典、根节点、解决方案表与树视图文本，并写 `rca_tree.txt`；然后 `render` 生成报告写入文件，`render_reply` 生成回复稿写入 `rca_reply.md`，把报告全文打印到标准输出并打印文件路径，返回 0。不带 `--tree` 时报告没有树视图节，`--lang en` 的节点名也无法从树里取英文，只能回退中文。

模块级常量：`_SYM` 把 `judgment` 映射为符号（`✗` 不成立、`?` 数据不足、`⚠` 证据矛盾、`·` 无独立条件、`✓` 成立，其余为 `~`），元组顺序先长后短，注释说明「不成立」含「成立」子串，必须先匹配；`_JD_EN` 是判定词的英译表，同样先长后短；`_T` 是中英两套固定文案（标题、表头、标签、占位文字），注释要求中文一字不改，英文与 `cs-scot-execute/references/reply_format.md` 的对照表一致。

三份报告的内容：

- `rca_report.md`（`render`）：标题；一行汇总「确证根因 c 条 · 疑似 p 条 · 待确认 u 条」（条目带 `_engine` 时附引擎名）；有树视图时放在 text 代码块里的「树视图」节；「确证根因」节由 `_render_confirmed` 生成，先一张摘要表（`_confirmed_row`：根因、关键证据摘要、推荐解决方案），再逐条详情（`_confirmed_detail`：三级标题带 ✅，路径行由 `_path_line` 把节点名与状态符号串起，候选证据由 `_ev_block` 列出判定、判据、实测、依据、反证、注各行，未验证上级提示最多 5 个，注意，建议方案）；「疑似根因」节逐条 `_probable_detail`（标题带 ⚠ 和 `confidence`，受阻位置 `blocked_at`，路径，原因/注意，候选证据，最多 3 个与之矛盾的上级各带证据块）；「待确认」节 `_render_undetermined`（标题附 `_gap_kind` 的分型计数，最多 30 条，其余指向 `rca.json`）；`_render_tail` 输出排除记录、树的结构问题计数（无事实节点数、绑定指向已不存在节点的数量）和「补充说明（模型撰写）」占位。
- `rca_reply.md`（`render_reply`）：汇总行、树视图代码块、确证根因表（`_reply_confirmed`：根因、根因路径、原文匹配即实测与依据、推荐方案）、疑似表（`_reply_probable`：根因、受阻位置、证据、注意）、待确认列表最多 20 条、三者全空时的提示、补充说明占位。注释说明平台上用户只看得见聊天框，所以结论必须进回复正文，且每次结构一样。
- `rca_tree.txt`：由 `render_tree.py` 生成，见下一节。

文本处理函数：`_one` 合并空白并截到 n 字符加省略号；`_cell` 再把竖线转义为 `\|`，注释说明设备回显的命中原文常含竖线，不转义会把表格串列；`_cell_full` 是回复稿用的版本，超长时指向报告全文。

英文输出的回退规则：`_txt(obj, key, lang)` 在英文且存在 `<key>_en` 时取英文，否则取原文；`_jd_text` 只在英文时把判定词按 `_JD_EN` 替换；`_Names` 按语言取节点名，英文时先用 `node_id` 查树里的 `name_en`，再按中文名查 `name_en`，都没有就用原名；`_solution` 在英文且 `solutions.json` 有该节点的 `en` 时用它，否则用 `rca.json` 里的 `recommend_solution`（经 `_txt` 回退）。`condition`、`how`、`conflict_how` 这些事实原文不译，`actual`、`conflict_actual`、`other_source_note` 只在有对应 `_en` 字段时换成英文。

**修改时要注意**

- `_SYM` 和 `_JD_EN` 的顺序是语义的一部分，新增判定词要放在被它包含的短词之前。
- 中文固定文案被平台侧解析，改字面要与 `reply_format.md` 以及 `tests/test_render_tree.py`、`tests/test_rca_bilingual.py` 里的断言同步；回复稿确证表是五列、报告确证表是四列，表头与分隔线的列数要一致。
- 报告里所有数字和证据都逐字来自 `rca.json`，不要在渲染时加工数字。
- `render_tree` 是运行时按路径导入的，两个脚本必须在同一目录；场景技能里的副本靠整目录复制保证这一点。

### scripts/render_tree.py

**功能与作用**

由 `rca.json` 和树索引渲染文本树视图，可单独运行 `render_tree.py <rca.json> --tree <tree_index.json> [-o rca_tree.txt] [--lang zh|en]`，也被 `render_report.py` 以函数方式调用。文件头注释说明目的：业务专家要看「结果落在树的哪儿」，几十个节点不能全铺开，只展开有结论的路径。符号约定：`✅` 确证根因（标签「确诊」）、`⚠` 疑似根因（标签「疑似」）、`?` 待确认（标签「缺数据」）、`✓` 成立、`✗` 不成立、`·` 未判定或无独立条件。

**实现介绍**

`_load_tree` 读树索引的 `nodes` 与 `root`，`root` 不在 `nodes` 里时取第一个没有 `parent` 的节点。`_kids` 优先用节点的 `children` 列表（过滤掉不存在的编号），没有就按 `parent` 反查。`_ancestors` 沿 `parent` 上溯最多 64 层。

`collect` 从 `rca.json` 收集三张表：`flags` 是标记节点到（标签, 条目）的映射，`ev` 是节点编号到证据条目的映射，`failed` 是已知不成立节点到原因的映射。`_collect_flagged` 处理 `rca_result`（标签 `confirmed`）与 `probable_causes`（标签 `probable`），节点编号优先用 `node_id`，否则按 `Fault_tree_path` 末位名字在树里唯一匹配（`_by_name`）；`_collect_evidence` 把 `evidence_path` 每项记进 `ev`（确证的覆盖疑似的），`judgment` 含「不成立」的记进 `failed`。`_collect_undetermined` 用正则 `_NID_RE` 取 `blocked_at` 末尾括号里的编号，否则按路径末位名字匹配，已标记的节点不覆盖。`_collect_excluded` 用正则从 `excluded_note` 里解析「『名』(编号):原因」，记进 `failed`。

展开与折叠规则在 `render_tree` 和内部函数 `walk`：

- `expand` 是所有标记节点及其祖先的集合，`deep` 是标签不为待确认的标记节点集合。
- `walk` 从根开始，每个节点打印一行「连线 + 符号 + 名字 + 标签」，符号由 `symbol` 决定：标记节点用标签符号，`failed` 里的用 `✗`，`ev` 里的按 `judgment` 取符号，其余 `·`。标记节点下紧跟 `details` 的详情行。
- 进入 `deep` 的子树（确证或疑似落在中间层时）全部展开到叶子，不再折叠，以显示待定的叶子层原因。
- 不在 `expand` 里的节点不往下走。
- 待确认节点若没有任何子节点在 `expand` 里，子树折叠成一行「子树 n 个节点未判定」，`n` 由 `_count` 统计；注释说明若子树里另有标记节点则继续展开，否则挂在待确认上级下的确证根因会从树视图里消失，首行计数与树对不上。
- 其他节点的子节点分三类：在 `expand` 里的递归展开；不在 `expand` 但在 `failed` 里的（已知不成立的兄弟）各列一行，不带详情；其余折叠成一行「其余 n 个分支未涉及」。

`details` 给标记节点列出：判据（优先 `ev` 里的 `condition`，否则树里的 `condition`；英文且树里有 `condition_en` 用它；等于「(无独立条件)」的不列）；待确认节点列「缺」行（`reason`，没有写「数据不足」）；确证与疑似列证据（`actual`，没有用条目的 `support`）、依据（`how`）、疑似的受阻位置（`blocked_at`）、注意（`caveats`）、方案（`recommend_solution`，英文且 `solutions.json` 有 `en` 时用它）。每项文字经 `_compact` 压成一行（去空行、合并空白、多行用分号连）并按上限截断（判据 120、证据 300、依据 160、受阻位置 160、注意 200、方案 200、缺 200 字符），再经 `_block` 按 72 字符宽折行，续行悬挂缩进到冒号之后。

首行是`树视图:确诊 c · 疑似 p · 缺数据 u(…)`，没有任何标记节点时只输出根节点名和一行「树未展开」提示；树索引没有根节点时返回固定提示`(tree_index 无根节点,树视图略)`。`load_solutions` 读树索引同目录的 `solutions.json`，不存在或损坏返回空字典，不影响树视图。

英文输出的回退规则：`txt` 与 `node_name` 分别处理说明字段和节点名，英文且有 `_en` 字段或 `name_en` 时用英文，否则用中文；判据用 `condition_en`、方案用 `solutions.json` 的 `en`，没有就回退；固定文案（首行、标签、折叠提示）取 `_TEXT["en"]`；证据原文一律不译。`tests/test_render_tree.py` 的第五组用例专门验证「没有英文的节点回退中文」和「中文输出不受英文字段影响」。

**修改时要注意**

- `_collect_undetermined` 依赖 `finalize_rca.py` 写的 `blocked_at` 格式「节点名(编号)」，`_collect_excluded` 依赖 `excluded_note` 的「『名』(编号):理由」格式并以分号分段，理由里含分号会被截断；三处格式要一起改。
- 折叠规则里「待确认子树里另有标记节点时不折叠」是修过的问题，改 `walk` 时保留。
- 中文首行与标签字面被平台解析，`tests/test_render_tree.py` 对首行、标签、折叠提示、折行宽度（单行不超过 90 字符）都有断言。
- `_TAG` 与 `_TEXT[lang]["tag"]` 各存一份标签，符号取自 `_TAG`，文字取自 `_TEXT`，改标签要两处一起改。
- 本文件不在 `package_skill.py` 的 `REQUIRED` 清单里（清单只列了另外五个脚本），打包靠整目录复制带上它，`tests/test_engine_sync.py` 检查包里有它；若将来改成按清单复制，要把它补进清单。

### scripts/rca_query.py

**功能与作用**

查询工具，供模型在不整读 `rca_input.json` 的前提下按节点或分支查看条件与事实。文件头注释说明大场景的 `rca_input.json` 很长，一次读进上下文会超长报错。三种用法：`--outline` 打印全树节点编号、粗状态、名字与父节点；`--holding` 列出有成立事实的节点及各自根到叶的路径；`--node nX` 打印该节点整条根到叶路径上每个节点的条件和事实。SKILL.md 规定要看节点细节只用它，不用 `python -c` 解析。

**实现介绍**

`_fault_tree` 取 `fault_tree.pruned_nodes`、`outline`、`outline_legend`；`_ff` 兼容事实的扁平结构和嵌套 `{"fact": {...}}` 结构；`_path_to_root` 沿 `parent` 上溯，遇到环或缺父节点即停，返回叶到根的编号列表，显示时反转。`_holding` 的判断是任意一条事实 `status` 为 `ok` 且 `predicate_satisfied` 为真，不区分弱否定与强反证，所以证据矛盾的节点也会列出。`_fact_brief` 按 `_FACT_KEYS` 的顺序打印事实里存在的字段，S 脚本的 `evidence` 取前两条，末尾附 `mapping_reason`。`_leafmark` 标注叶子或中间节点，中间节点附未覆盖子节点编号。`--node` 对每个路径节点打印层级、叶子标记、`combine`、`pass_through`、条件前 220 字符与各条事实；编号不存在时打印提示。不给任何动作参数时打印用法。`main` 没有返回值，正常与查不到节点都是退出码 0，文件读取失败会抛出异常。

**修改时要注意**

- 这是只读工具，输出是给模型看的文字，不是机器解析格式；但 SKILL.md、`rca_rules.md` 和 `runner.py` 打印的指引都提到 `--node`、`--outline`、`--holding` 三个参数名，改参数名要同步这些文档。
- `_FACT_KEYS` 是本脚本自己维护的显示字段清单，与 `cs-scot-execute/scripts/tree_prune.py` 精简事实时保留的 `_FACT_KEEP` 并不一致：`class`、`op`、`threshold`、`nulls`、`hits` 在 `_FACT_KEEP` 里却不在这里，所以 `--node` 的事实摘要不显示算子类别、阈值、空值数和命中原文；上游增减事实字段时要同步检查这里，否则新字段看不到。

### scripts/validate_rca.py

**功能与作用**

对已生成的 `rca.json` 做事后检查的独立脚本，用法 `validate_rca.py <rca.json> [rca_input.json]`，不合格返回退出码 1，用法错误或读不到文件返回 2。SKILL.md 说明正常流程以 `finalize_rca.py` 组装成功为交付，本脚本保留给人工抽查。它只查格式底线与诚实性，不管置信强弱、排序这类模型判断的内容。带 `rca_input.json` 才能做「成立必须有事实」等需要原始事实的核对。

**实现介绍**

`main` 读文档，若带 `rca_input.json` 则预先构造四份辅助数据：`_holding_map`（节点编号到「是否 `pass_through`、是否有成立事实、是否有真条件」三元组的映射）、`_parent_map`（节点编号到父编号）、`_node_universe`（`rca_input.json` 全文出现过的全部 `nN` 编号）、`_real_blocks`（有真条件、非 `pass_through`、且没有任何 `status` 为 `ok` 事实的节点，即真正缺数据的节点）。完整形态没有 `pruned_nodes` 时这些都为空，相关检查跳过。`_confirmed` 与 `_evidence` 兼容旧键名（`root_causes`、`results`、`evidence`）。

硬检查记入 `problems`，有任一条返回 1：

- 空诊断：`rca_result`、`probable_causes`、`undetermined` 全空。
- 确证根因（`_check_confirmed_entry`）：缺 `evidence_path`；某节点判成立却在 `judgment` 或 `actual` 里含 `MISSING` 列表中「未找到」「数据不足」等字样（自相矛盾）；候选自身的 `judgment` 含 `NOT_PROVEN` 列表中「数据不足」「不成立」「语义近似」「存疑」等字样（假根因，`pass_through` 或无真条件的节点豁免），上级不拦，与 `finalize_rca.py` 规则一致；`_check_fabrication` 查有真条件、非 `pass_through`、局部子树里没有成立事实的节点被判成立（伪造）；`_check_path_chain` 查 `evidence_path` 相邻两节点不在同一条根到叶链上（跨分支拼路径）；`_check_unknown_nodes` 查 `node_id` 不在 `rca_input.json` 全文里（编造节点），注释记录曾有纯编造 `node_id` 的确证全部通过旧检查。
- 疑似根因（`_check_probable_entry`）：`blocked_at` 与 `caveats` 都没有；`support` 与 `evidence_path` 都没有；`_check_probable_rule` 查证据链全成立且 `blocked_at` 为空或没有指向 `_real_blocks` 里的真实节点（注释记录能力较弱的模型曾把全成立候选整批降为疑似，禁止空值后又改写成散文式理由）；再加伪造、路径链、未知节点三项。

软提示只打印，带 `rca_input.json` 才做：`_hint_probables_without_block` 列出路径里没有真实缺数据节点的疑似；`_hint_undet_holds` 列出把有成立事实且无强反证的节点写进 `blocked_at`、`reason`、`observed` 的待确认条目；`_hint_erased_holds` 列出有成立事实且无强反证、却被写成「数据不足」「证据不足」类判断且没有「语义近似」「存疑」字样的证据条目；`_hint_unaddressed` 列出有成立事实、有真条件、非 `pass_through`、在 `rca.json` 全文里按编号（词边界匹配）和名字都找不到的节点（漏判断）；这四项计入 `soft_hints`。`_hint_excluded_holds` 列出成立且无强反证、却没进确证或疑似而只在排除或观察里出现的节点，单独返回，不计入，注释说明排除是合法出口，计入会让有一条合法排除就永远清不了。`_strong_refuted` 与 `draft_rca.py` 的 `_weak_neg` 是同一规则。`_verdict` 收尾：有硬问题列出并返回 1；没有硬问题但有软提示，打印「硬检查通过，但有未处置软提示」并返回 0，注释说明「通过」二字曾让模型忽略全部提示；否则打印通过行，附排除核对的提醒。

**修改时要注意**

- 本脚本的多项检查与 `finalize_rca.py` 重复实现（候选自身干净、强反证定义、无条件节点识别），改一处要同步，否则两个脚本对同一份 `rca.json` 会给出相反结论。
- 关键词列表 `MISSING`、`NOT_PROVEN`、`_DATA_ABSENT_WORDS`、`_SEMANTIC_DOUBT_WORDS` 都是子串匹配，新增判定词写法要补进去。
- 各软提示的设计都对应注释里记录的某次问题，去掉任一项前先读对应注释。
- 候选对账（`_unaddressed_holds`）故意不做硬拦，注释说明硬拦会误拦合法排除，并曾导致模型编造节点。
