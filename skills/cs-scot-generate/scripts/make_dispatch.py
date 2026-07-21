#!/usr/bin/env python3
"""把"并行绑定的派发准备"从模型手里收回来:机械生成每个子代理的自包含简报文件。

为什么要它:派发环节的三类实测事故——判据没读全就派发(贴不出原文)、EAV 源没带枚举
(子代理自编指标名,实测 PORT_RxCRC)、不拆批(单次生成过长 → 回复截断/600s 流挂)——
全是机械活没做对。散文规矩管不住弱模型,脚本管得住:简报由本脚本拼装,判据自动切批
(默认 ≤50 条/批)、该源概览/枚举原文自动嵌入、公共规约与 C/G·EAV 铁规是固定文本,
模型只剩「每份简报派一个子代理」一个动作。

产物:<OUT>/dispatch/<ind>_batch<N>.txt(自包含:公共规约 + 外挂知识(tree/knowledge.md
按「适用」标签匹配的条目)+ 本源要点 + 数据词汇原文 + 该批判据原文 + 输出格式)。派发句式(主代理用):
  「你的全部任务和材料在 <简报路径>,用 Read 读完它(可分段),照其中规约执行,结果放最终回复」
子代理读不了文件的环境(权限没配)→ 主代理把简报全文读入后原样贴进派发词(纯复制,零转述)。
简报只在绑定期有用,交付前清场(SKILL 步骤 4)会删掉 dispatch/。

EAV 窄表源(概览列含 DataItem/DataValue)缺枚举文件(overview/<ind>*枚举*.txt 等)时
**响亮拒产该源**并给出抽枚举的现成命令——没有词汇表的 EAV 绑定只会逼子代理编造指标名。

用法:
    make_dispatch.py --out "<OUT>" [--indicators cmd,dblg,...] [--batch-size 50] [--tree-index <path>]
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import re
import sys
from pathlib import Path

COMMON_RULES = """【公共规约——逐条遵守】
1. 本文件就是你的全部任务与材料,不要用 Bash;除了把结果写到文首【结果文件】路径外,**不要读写任何其他文件**。
2. 只绑你这个源({ind})能答的判据;明显不归本源答的**整条不写**;「确属本源该答、这次却答不了」才写
   decision:"insufficient_data" 并在 mapping_reason 里说明原因。
   **跨源联查的判据是第三种情况**:判据要"先查别处拿到值,才知道在本源查什么"(传参链)、或要与别的源
   的数据比对(两侧一致性)→ 本源别硬绑、也别当"不归我答"沉默,写一行标记(它会汇入跨源补绑环节,
   不进本源脚本):{{"node_id":"nN","decision":"cross_source","mapping_reason":"<一句话:缺哪一环>"}}
3. 树驱动:下面清单里的**每条判据都要过一遍**再决定绑/不绑;按判据要的列+值绑,**不管这次数据里出没出现**
   (值没出现 → 照绑,运行时 matched=0 = 确定排除,不是数据不足)。
4. 一条判据常含**多个子条件**(换行/且/①② 分隔):本源能答的每个子条件都要绑到(单源多子条件用 D 类组合),
   不是碰到一条就算认领了该节点。
5. 别的源绑不绑与你无关:多源同绑同一节点是期望行为,**不许谦让**。
6. 每条绑定写实 mapping_reason(为何这列能答这条判据/语义或单位差异)。
7. C/G 的 contains 铁规:每个 target 必须**独立是"判据成立的充分信号"**——禁止字段标签/命令名/协议名等
   泛词(如 "CRC:"/"LLDP"),禁止 ≤3 字符的 ASCII 短串;值否定语义("状态非UP/字段为空")禁用文本捞子串
   (数据有状态枚举列就绑 equals;状态藏在杂糅格里就加 extract 抽出字段值再 equals);趋势语义
   (增长/震荡/抖动)只能绑设备自带的检测告警码,没有就 decision。
8. 负存在判据(判据说"**没有配置X/未启用X**"):这是可判的,**缺行就是证据**,禁投 insufficient_data——
   绑 C/G 加 `"absent":true`(命中0行=故障成立;列全空时内核自动改判数据不足,不会误判);
   "配了A但没配B"= D 组合(AND)+ 存在子条件 + `absent` 子条件,**照抄此模板的字段名**
   (子条件列表叫 sub、目标叫 contains 列表;sub_conditions/target 等野字段内核不认、整条变数据不足):
   {{"class": "D", "combine": "AND", "sub": [
       {{"class": "G", "column": "<列>", "contains": ["要存在的内容"]}},
       {{"class": "G", "column": "<列>", "contains": ["要不存在的内容"], "absent": True}}]}}
   (第一条不带 absent=命中≥1行成立「配了」;第二条带 absent=命中0行成立「没配」。)
   判据要求"**同一接口/同一对象上**同时满足"时,给 D 加 {{"group_by": "<对象列名,如 level1>"}}:
   按该列切组、每组单独跑全部 sub,存在一个组全部满足才成立——防"整列有A也有B但不在同一接口"的
   假确证;group_by 组合的 sub 不得跨源。
9. 格内取值(extract)与正则:判据要"从一格杂糅文本里抽出某段值再判"→ 给 A/C/G/P 加
   `"extract": {{"kv": "<键名>"}}`(抓「键: 值」「键=值」)或 `"extract": {{"regex": "<带捕获组的正则>"}}`;
   A+extract=抽数比阈值(「回显计数器>阈值」的标准做法:filter 选中命令行 + extract 抽计数,千分位自动归一,
   **别用 G 捞 "CRC:" 这类恒在标签**),C/G+extract=抽出值再 equals/contains。判据自带正则/通配形态
   (如「DOWN state \\d+ times」)→ C/G 用 `"regex": ["<正则>"]` 代替 contains——但判据原文是**原料不是
   成品**:%u/%d 这类 printf 占位先换成 \\d+、HTML 渣剔掉,编译不过内核会响亮拒收。
   本端/对端一致性判据("查对端,看两侧X是否一致")→ P 类,照抄字段名:
   {{"class":"P","side_column":"<分侧列,含*标记=本端>","value_column":"<取值列>","fault_when":"not_equal"}}
   (可加 filter 选检查项、extract 抽格内值;查不到对端=数据不足,不是"不成立")。
10. 数据词汇只用来**挑列**,不用来判"值存不存在":概览可能截断(带「词汇不完整」标注的列尤其),
   **概览里没看到 ≠ 数据里没有**——列在、格式对就照绑,让内核去数(见第3条)。
11. 输出:把绑定结果(Python 列表字面量,格式见文末)用 **Write 工具**写到文首【结果文件】路径(UTF-8);
   **最终回复只报一行**「共 N 条,已写 <结果文件路径>」,N 必须等于列表实际长度——结果不要放回复里
   (上百条的回复会被截断,写文件不会)。"""

EAV_RULES = """【EAV 窄表铁规(本源适用)】
本源是"一行一个量"的窄表:DataItem 说这行是什么指标,DataValue 是值。绑定固定形态:
  {"class":"A","filter":{"column":"DataItem","equals":"<指标名>"},"column":"DataValue","op":">","threshold":0,"scope":"count_rows",...}
  {"class":"C","filter":{"column":"DataItem","equals":"<指标名>"},"column":"DataValue","contains":["<取值>"],...}
**filter.equals 的值必须逐字来自下文的枚举清单**;清单里没有的指标名,禁止绑、禁止自己编——
写 decision:"insufficient_data" 并注明「已查枚举无对应项」。filter 命中 0 行=该指标本次未采集=数据不足,不是"不成立"。"""

CROSS_RULES = """【跨源绑定规约——逐条遵守】
1. 本文件就是你的全部任务与材料;结果(Python 列表字面量)用 Write 写到文首【结果文件】路径(UTF-8),
   最终回复只报一行「共 N 条,已写 <路径>」。
2. 下面每条候选判据都要表态:能绑的按本规约绑;确实绑不了的写
   {{"node_id":"nN","decision":"insufficient_data","mapping_reason":"<为什么绑不了>"}}——不许漏、不许编。
3. 每条绑定必带 "source"=最终查询落点的源名;param_from/pair_from/sub 里的 source 各写各的。
   列名/枚举值只能用下方各源概览/枚举里出现过的;**外挂知识讲了"去哪查"的,照知识写**。
4. 传参链(先查A、拿结果再查B)→ param_from:参数按声明顺序解析,**后面的可用前面的**(@前参数)=多跳;
   查询体里 "@参数名" 整串占位会被代入(filter 值 / contains·equals·regex 目标里可用)。字面模板:
   {{"class":"G","source":"config","column":"<列>","contains":["<要找的内容>"],
     "filter":{{"column":"<列>","equals":"@p"}},
     "param_from":{{"p":{{"source":"cmd","column":"<列>","filter":{{...选行}},"extract":{{"kv":"<键>"}}}}}}}}
5. 参数默认须收敛到**唯一值**(多值=歧义→数据不足);判据要"**每个**X都查"才加 "expand":"all"
   (全部满足才成立)或 "any"(任一满足即成立),一条绑定只许一个参数带 expand。
6. 两侧一致性(本端/对端在**不同文件**)→ P 类 pair_from,两侧各自「选行→取值」:
   {{"class":"P","source":"<任一侧源>","fault_when":"not_equal","pair_from":{{
      "local":{{"source":"<本端源>","column":"<列>","filter":{{...}},"extract":{{...}}}},
      "peer":{{"source":"<对端源>","column":"<列>","filter":{{...}},"extract":{{...}}}}}}}}
   (同一张表里靠标记分侧的,用普通 P:side_column+peer_marker,不用 pair_from。)
7. filter 子句除 equals/contains 还认 **excludes**(不含才保留,如挑「Object 不带 * 」的对端行)。
8. 诚实:参数抽不到/歧义、对端查不到、源没提供 → 内核自动报数据不足,**别**为了"绑上"硬凑;
   mapping_reason 写实(链条每一跳从哪来、到哪去)。"""

CROSS_OUTPUT_FORMAT = """【输出格式】
把结果用 Write 写到文首【结果文件】路径,内容是一个 Python 列表字面量(带不带 `CONDITIONS = ` 前缀都行);
写完后最终回复只报一行「共 N 条,已写 <路径>」。每条字段:
{"id":"cross-<node_id>","node":"<节点名>","node_id":"<nN>","condition_text":"<逐字抄候选清单的判据原文>",
 "class":"…","source":"<最终查询落点的真实源名,如 config——绝不是 cross>",...类参数/param_from...,
 "mapping_reason":"<链条每一跳写实:参数从哪个源怎么抽、代到哪查什么>"}
示例(传参链):
{"id":"cross-n2","node":"peer-link配置缺失","node_id":"n2","condition_text":"...","class":"G","source":"config",
 "column":"level2","contains":["mode lacp-static"],"absent":True,
 "filter":{"column":"level1","equals":"@plink"},
 "param_from":{"plink":{"source":"cmd","column":"CmdResult",
               "filter":{"column":"CmdName","contains":"peer-link"},"extract":{"kv":"Port Name"}}},
 "mapping_reason":"回显抽 Port Name → 查该接口配置块,缺 mode lacp-static 即故障"}
示例(绑不了):
{"id":"cross-n9","node":"…","node_id":"n9","condition_text":"...","decision":"insufficient_data",
 "mapping_reason":"链条第2跳要的数据本场景未采集"}"""

OUTPUT_FORMAT = """【输出格式】
把结果用 Write 写到文首【结果文件】路径,内容是一个 Python 列表字面量(带不带 `CONDITIONS = ` 前缀都行,
合并脚本两种都认);写完后最终回复只报一行「共 N 条,已写 <路径>」。每条字段:
{{"id":"{ind}-<node_id>","node":"<节点名>","node_id":"<nN>","condition_text":"<逐字抄上面清单的判据原文>",
 "class":"A/C/D/G/J/P 之一(B/E/F/H/I 已封存,别用)","source":"{ind}",...类参数...,"mapping_reason":"<写实>"}}
示例(C 类):
{{"id":"{ind}-n6","node":"链路改包告警","node_id":"n6","condition_text":"...","class":"C","source":"{ind}","column":"Summary","contains":["hwLinkHeartbeatChangeAlarm"],"mapping_reason":"判据给出具体告警名,本源 Summary 列是告警码枚举,匹配名段即可"}}
示例(decision):
{{"id":"{ind}-n93","node":"LDP会话down","node_id":"n93","condition_text":"...","class":"C","source":"{ind}","decision":"insufficient_data","mapping_reason":"确属本源该答,但已查数据词汇无对应列/枚举项"}}"""


def _fail(msg: str) -> None:
    print(f"✗ {msg}", file=sys.stderr)


def load_conditions(ti_path: Path) -> list:
    """tree_index → [(node_id, 名字, 判据全文)],带非空判据的全部节点(与绑定认领口径一致)。"""
    idx = json.loads(ti_path.read_text(encoding="utf-8"))
    out = []
    for nid, n in (idx.get("nodes") or {}).items():
        cond = str(n.get("condition") or "").strip()
        if cond:
            out.append((nid, str(n.get("name") or ""), cond))
    return out


def find_enums(ov_dir: Path, ind: str) -> list:
    pats = [f"{ind}*枚举*.txt", f"{ind}*enum*.txt", f"{ind}*dataitem*.txt", f"{ind}*DataItem*.txt"]
    seen, files = set(), []
    for p in pats:
        for f in glob.glob(str(ov_dir / p)):
            if f not in seen:
                seen.add(f)
                files.append(Path(f))
    return files


def parse_knowledge(text: str):
    """knowledge.md 正文 → [{title, tags(或None), body}]。条目=「### 标题」起头,紧跟可选
    「适用:标签1、标签2」行,其余为正文;首个 ### 之前的说明文字忽略。"""
    entries, cur = [], None
    for line in text.splitlines():
        if line.startswith("### "):
            cur = {"title": line[4:].strip(), "tags": None, "body": []}
            entries.append(cur)
        elif cur is not None:
            s = line.strip().replace(":", ":")
            if cur["tags"] is None and not cur["body"] and s.startswith("适用:"):
                cur["tags"] = [t for t in re.split(r"[、,,/\s]+", s.split(":", 1)[1]) if t]
            elif line.strip() or cur["body"]:
                cur["body"].append(line)
    return [e for e in entries if "\n".join(e["body"]).strip()]


def match_knowledge(entries, name_pool: str):
    """按「适用」标签挑与当前树相关的条目:无标签/含「通用」恒选;有标签则任一标签
    出现在 name_pool(节点名+判据文本拼串)才选——按分支载入,不全量灌。"""
    picked = []
    for e in entries:
        if e["tags"] and "通用" not in e["tags"] and not any(t in name_pool for t in e["tags"]):
            continue
        tagline = f"(适用:{'、'.join(e['tags'])})" if e["tags"] else ""
        picked.append(f"◆ {e['title']}{tagline}\n" + "\n".join(e["body"]).strip())
    return picked


def load_knowledge(out: Path, name_pool: str):
    """<OUT>/tree/knowledge.md(业务同事手写的外挂知识)→ 匹配本树的条目文本块;无文件/无匹配 → None。"""
    kf = out / "tree" / "knowledge.md"
    if not kf.exists():
        return None
    try:
        picked = match_knowledge(parse_knowledge(kf.read_text(encoding="utf-8")), name_pool)
    except OSError:
        return None
    return "\n\n".join(picked) if picked else None


def build_briefing(ind, scenario, batch_no, n_batches, batch, total, overview_text, is_eav, enum_texts, result_path,
                   knowledge_text=None):
    sec = []
    sec.append(f"# 绑定简报:{ind} 源 · 第 {batch_no}/{n_batches} 批(本批 {len(batch)} 条判据,全树共 {total} 条;"
               f"其余批由别的子代理处理,你只管本批)\n【结果文件】{result_path}")
    sec.append(f"你是诊断绑定专家,任务:把下面这批故障树判据映射(或明确不映射)到 {ind} 数据源,"
               f"产出 {ind}_analyzer.py 的 CONDITIONS 片段。场景:{scenario}。")
    sec.append(COMMON_RULES.format(ind=ind))
    if is_eav:
        sec.append(EAV_RULES)
    if knowledge_text:
        sec.append("【外挂知识(业务同事手写,绑定时优先参考)】\n"
                   "下面的条目讲了本场景「去哪个源查、看哪列、怎么查、什么算正常」。用法:\n"
                   "- **指路可信**:绑定选哪个源/哪列/filter 值,按知识说的写(它比你猜的准);\n"
                   "  知识指明「该信息在别的源」而本源没有 → 本源老实不认领或写 decision 指路;\n"
                   "- **不是事实**:不能因为知识说了什么就直接给判断,查证仍靠数据跑出来。\n\n"
                   + knowledge_text)
    sec.append("【本源数据词汇——概览原文(列名/量纲/类别取值,绑定只能用这里出现的列)】\n" + overview_text.strip())
    for name, text in enum_texts:
        sec.append(f"【本源数据词汇——枚举清单原文:{name}】\n" + text.strip())
    lines = [f"【本批判据清单原文(共 {len(batch)} 条;condition_text 必须逐字取自这里)】"]
    for nid, name, cond in batch:
        lines.append(f"\n[{nid}] {name}\n{cond}")
    sec.append("\n".join(lines))
    sec.append(OUTPUT_FORMAT.format(ind=ind))
    return "\n\n" .join(sec) + "\n"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")   # 拒产信息走 stderr,漏配会在 Windows 上变乱码(实测)
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="机械生成并行绑定的子代理简报(判据切批+概览/枚举嵌入+固定规约)")
    ap.add_argument("--out", required=True, help="场景产物目录 <OUT>")
    ap.add_argument("--indicators", default="", help="逗号分隔;缺省=按 <OUT>/scripts/*_analyzer.py 自动发现")
    ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--tree-index", default="", help="缺省=<OUT>/tree/*.tree_index.json(须唯一)")
    ap.add_argument("--cross", action="store_true",
                    help="生成跨源补绑简报(读 dispatch/cross_candidates.json,由 merge_conditions 汇集)")
    args = ap.parse_args()

    out = Path(args.out)
    if args.tree_index:
        ti = Path(args.tree_index)
    else:
        cands = sorted(out.glob("tree/*.tree_index.json"))
        if len(cands) != 1:
            _fail(f"tree_index 不唯一/不存在({len(cands)} 个),用 --tree-index 指定")
            return 2
        ti = cands[0]
    conditions = load_conditions(ti)
    if not conditions:
        _fail("tree_index 里没有任何带判据的节点——先回步骤 1 检查建树输出的「带判据节点 N」")
        return 2
    scenario = ti.stem.replace(".tree_index", "")

    if args.indicators:
        inds = [x.strip() for x in args.indicators.split(",") if x.strip()]
    else:
        inds = sorted(p.stem[:-len("_analyzer")] for p in out.glob("scripts/*_analyzer.py")
                      if p.name != "cross_analyzer.py")  # cross 走 --cross 补绑简报,不按单源派
    if not inds:
        _fail("没找到任何指标(--indicators 或 <OUT>/scripts/*_analyzer.py)")
        return 2

    ov_dir = out / "overview"
    # 外挂知识(tree/knowledge.md):按树的节点名+判据文本匹配「适用」标签,命中的条目进每份简报
    name_pool = scenario + "\n" + "\n".join(f"{name} {cond}" for _, name, cond in conditions)
    knowledge_text = load_knowledge(out, name_pool)
    if knowledge_text:
        n_kn = knowledge_text.count("◆ ")
        print(f"外挂知识:tree/knowledge.md 命中 {n_kn} 条,已嵌入全部简报")

    if args.cross:                                       # 跨源补绑简报:一份装下全部候选 + 所有源的词汇
        ddir = out / "dispatch"
        cand_p = ddir / "cross_candidates.json"
        if not cand_p.exists():
            _fail(f"没有 {cand_p} —— 先完成单源绑定并跑 merge_conditions.py"
                  "(子代理的 cross_source 标记会汇成候选);没有候选文件=没有跨源判据,无需补绑")
            return 2
        cands = json.loads(cand_p.read_text(encoding="utf-8"))
        if not cands:
            print("cross_candidates.json 为空——没有跨源判据,无需补绑。")
            return 0
        cond_by_id = {nid: (name, cond) for nid, name, cond in conditions}
        rp = ddir / "cross_batch1_result.py"
        sec = [f"# 跨源补绑简报:cross · 共 {len(cands)} 条候选判据\n【结果文件】{rp}",
               f"你是诊断绑定专家,任务:为下面的跨源判据写 param_from/pair_from 绑定(或明确绑不了),"
               f"产出 cross_analyzer.py 的 CONDITIONS 片段。场景:{scenario}。",
               CROSS_RULES.format()]
        if knowledge_text:
            sec.append("【外挂知识(业务同事手写:指路可信——去哪查/怎么查照它;但不是事实,查证靠数据)】\n"
                       + knowledge_text)
        for ind in inds:
            ov = ov_dir / f"{ind}_overview.json"
            if ov.exists():
                sec.append(f"【{ind} 源数据词汇——概览原文】\n" + ov.read_text(encoding="utf-8").strip())
            else:
                sec.append(f"【{ind} 源概览缺失】overview/{ind}_overview.json 不存在——该源列名未知,"
                           "涉及它的绑定老实写 decision:insufficient_data 并注明缺概览")
            for f in find_enums(ov_dir, ind):
                sec.append(f"【{ind} 源数据词汇——枚举清单原文:{f.name}】\n"
                           + f.read_text(encoding="utf-8").strip())
        lines = [f"【候选判据清单(共 {len(cands)} 条;condition_text 必须逐字取自这里)】"]
        for c in cands:
            nid = c.get("node_id")
            name, cond = cond_by_id.get(nid, (c.get("node") or "?",
                                              "(tree_index 里查不到判据原文——node_id 可能填错,核实后表态)"))
            srcs = "、".join(c.get("sources") or [])
            reasons = ";".join(c.get("reasons") or [])
            lines.append(f"\n[{nid}] {name}(由 {srcs} 源标记" + (f":{reasons}" if reasons else "") + f")\n{cond}")
        sec.append("\n".join(lines))
        sec.append(CROSS_OUTPUT_FORMAT)
        ddir.mkdir(parents=True, exist_ok=True)
        bp = ddir / "cross_batch1.txt"
        bp.write_text("\n\n".join(sec) + "\n", encoding="utf-8")
        print(f"跨源补绑简报 → {bp}(候选 {len(cands)} 条)。派一个子代理处理,结果收 {rp.name},"
              "然后重跑 merge_conditions.py 合并进 cross_analyzer.py。")
        return 0

    n_batches = max(1, math.ceil(len(conditions) / args.batch_size))
    batches = [conditions[i * args.batch_size:(i + 1) * args.batch_size] for i in range(n_batches)]
    ddir = out / "dispatch"
    ddir.mkdir(parents=True, exist_ok=True)

    errors, written = [], []
    for ind in inds:
        ov = ov_dir / f"{ind}_overview.json"
        if not ov.exists():
            errors.append(f"{ind}:缺概览 {ov} —— 先跑步骤 2.5:"
                          f"python \"<OUT>/scripts/analyzer.py\" \"<该源数据>\" --lean -o \"{ov}\"")
            continue
        ov_text = ov.read_text(encoding="utf-8")
        try:
            cols = (json.loads(ov_text).get("summary") or {}).get("columns") or []
        except json.JSONDecodeError:
            cols = []
        is_eav = ("DataItem" in cols and "DataValue" in cols)
        enum_files = find_enums(ov_dir, ind)
        if is_eav and not enum_files:
            errors.append(
                f"{ind}:EAV 窄表源缺枚举清单(概览列含 DataItem/DataValue,但 overview/ 下没有 {ind}*枚举*.txt)。"
                f"没有词汇表的 EAV 绑定只会逼子代理编造指标名,拒产。先抽枚举:\n"
                f"  python -c \"import csv,sys;csv.field_size_limit(10**9);"
                f"print('\\n'.join(sorted({{r.get('DataItem','') for r in "
                f"csv.DictReader(open(sys.argv[1],encoding='utf-8',errors='ignore'))}})))\" "
                f"\"<{ind}的csv>\" > \"{ov_dir / (ind + '_dataitem枚举.txt')}\"")
            continue
        enum_texts = [(f.name, f.read_text(encoding="utf-8")) for f in enum_files]
        for i, batch in enumerate(batches, 1):
            p = ddir / f"{ind}_batch{i}.txt"
            rp = ddir / f"{ind}_batch{i}_result.py"
            p.write_text(build_briefing(ind, scenario, i, n_batches, batch, len(conditions),
                                        ov_text, is_eav, enum_texts, str(rp), knowledge_text), encoding="utf-8")
            written.append((ind, i, len(batch), p))

    print(f"判据共 {len(conditions)} 条 → 每源 {n_batches} 批(≤{args.batch_size} 条/批)。已生成简报 {len(written)} 份:")
    for ind, i, n, p in written:
        print(f"  {ind} 批{i}({n} 条) → {p}")
    if written:
        print("\n派发句式(一份简报派一个子代理;简报多就分波派——一波 4~8 个、收完一波再下一波;不许合并简报、不许转述):")
        print("  「你的全部任务和材料在 <简报路径>,用 Read 读完它(可分段),照其中规约执行——")
        print("    结果写到简报指定的结果文件,最终回复只报『共 N 条,已写 <路径>』」")
        print("收货(可反复跑,先到先合):python merge_conditions.py --out \"<OUT>\" ——机械验收+合并+写进各 <ind>_analyzer.py。")
        print("子代理读不了文件的环境 → 主代理把简报全文读入后原样贴进派发词(纯复制)。")
    if errors:
        print(f"\n✗ {len(errors)} 个源拒产(缺原料,补齐后重跑本脚本):", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
