#!/usr/bin/env python3
"""把"并行绑定的派发准备"从模型手里收回来:机械生成每个子代理的自包含任务说明文件。

为什么要它:派发环节的三类实测事故——判据没读全就派发(贴不出原文)、EAV 源没带枚举
(子代理自编指标名,实测 PORT_RxCRC)、不拆批(单次生成过长 → 回复截断/600s 流挂)——
全是机械活没做对。写在文档里的规矩管不住弱模型,脚本管得住:任务说明由本脚本拼装,判据自动切批
(默认 ≤12 条/批——小批防子代理写盘超时被掐+抬并行度)、该源概览/枚举原文自动嵌入、
公共规约与 C/G、EAV 规则是固定文本,
模型只剩「每份任务说明派一个子代理」一个动作。

产物:<OUT>/dispatch/<ind>_batch<N>.txt(自包含:公共规约 + 外挂知识(tree/knowledge.md
按「适用」标签匹配的条目)+ 本源要点 + 数据词汇原文 + 该批判据原文 + 输出格式)。派发句式(主代理用):
  「你的全部任务和材料在 <任务说明路径>,用 Read 读完它(可分段),照其中规约执行,结果放最终回复」
子代理读不了文件的环境(权限没配)→ 主代理把任务说明全文读入后原样贴进派发词(纯复制,零转述)。
任务说明只在绑定期有用,交付前清理临时文件(SKILL 步骤 4)会删掉 dispatch/。

EAV 窄表源(概览列含 DataItem/DataValue)缺枚举文件(overview/<ind>*枚举*.txt 等)时
**明确拒绝生成该源**并给出抽枚举的现成命令——没有词汇表的 EAV 绑定只会逼子代理编造指标名。

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
import time
from pathlib import Path

COMMON_RULES = """【公共规约——逐条遵守】
1. 本文件就是你的全部任务与材料,不要用 Bash;除了把结果写到文首【结果文件】路径外,**不要读写任何其他文件**。
2. 只绑你这个源({ind})能答的判据;明显不归本源答的**整条不写**;「确属本源该答、这次却答不了」才写
   decision:"insufficient_data" 并在 mapping_reason 里说明原因。
   **跨源联查的判据是第三种情况**:判据要"先查别处拿到值,才知道在本源查什么"(传参链)、或要与别的源
   的数据比对(两侧一致性)→ 本源别硬绑、也别当"不归我答"沉默,写一行标记(它会汇入跨源补绑环节,
   不进本源脚本):{{"node_id":"nN","decision":"cross_source","mapping_reason":"<一句话:缺哪一环>"}}
   **边界别搞混(实测有源把几十条误标)**:"这判据要的数据在别的源、本源答不了" = 普通的**不归你答,
   整条不写**——不是 cross_source。cross_source 的判别标准只有一个:**单靠任何一个源都答不完**
   (要两个源接力/比对)。"归别的源单独答"的判据,让那个源去绑就行。
3. 树驱动:下面清单里的**每条判据都要过一遍**再决定绑/不绑;按判据要的列+值绑,**不管这次数据里出没出现**
   (值没出现 → 照绑,运行时 matched=0 = 确定排除,不是数据不足)。**绑的是规则,不是对这份样例的判词**:
   成对/两端比对判据在样例只有单侧设备时也照绑(P/cross 口径),运行时缺对端自然报数据不足(理由由内核
   当场生成,永远新鲜);把「本数据仅有某设备」这类样例观察写死进 decision=永久化石——换了齐全数据它也
   醒不来(实测:双设备数据齐了,判据还在背当年的"仅Switch02")。
   **概览只用来定"列的形状"**(有哪些列、值长什么格式),不用来定"值在不在"——样例不可能集齐所有
   告警/事件码,「概览里没见过这个告警」不构成 insufficient_data(告警类判据照绑对应列的裸码,运行时
   没出现=不成立)。insufficient_data 只用于:判据要的**数据种类/字段在本源表结构里根本不存在**。
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
   (上百条的回复会被截断,写文件不会)。

【各类参数速查(字段名照抄;写清单之外的字段/拼错=内核当场拒收)】
- A 阈值:column + op(< <= > >= == !=)+ threshold(数字不带引号)+ scope 二选一:
  "count_rows"(逐行比、数满足行数,配 require:至少几行;**判据是"存在越线/出现N次"就用它,首选**)
  或 "aggregate"(先聚合再比,配 agg:mean/min/max/sum/first/last;注意 mean 无加权,整体水平才用);
  **require 只在 count_rows 下有效**。可选 transforms/filter/extract。
- C/G 匹配:column + equals/contains/regex 三选一(都是列表)+ 可选 match/require/absent/filter/extract。
- D 组合:combine(只认 "AND"/"OR")+ sub:[子绑定…] + 可选 group_by(同对象关联)。
- P 成对:side_column + value_column + 可选 peer_marker(默认"*")/fault_when/filter/extract。
- J 人工:note。查不了:{{"decision":"insufficient_data","mapping_reason":"…"}}(不带 class)。

【参考示例(few-shot;是参考写法不是铁律,遇到判据确有例外就按语义变通,mapping_reason 写清)】
◇「回显里出现某告警/事件字样」(存在即证据):
  {{"node_id":"n6","class":"G","column":"CmdResult","filter":{{"column":"CmdName","contains":"display alarm"}},
    "contains":["hwBaseSocAttackTrap"],"mapping_reason":"告警名只在发生时出现,filter 选中该命令的回显行再找"}}
◇「根据 display X 查看没有对应的Y」(判定落在**回显内容特征**上;判据下方的回显示例通常就写着
  故障长什么样——本例特征是 Number of vxlan tunnel : 0):
  {{"node_id":"n14","class":"G","column":"CmdResult","filter":{{"column":"CmdName","contains":"display vxlan tunnel"}},
    "contains":["Number of vxlan tunnel : 0"],"mapping_reason":"判据回显示例给出故障特征行,命中即隧道数为0"}}
◇「未配置/缺失某命令」(缺行即证据):
  {{"node_id":"n18","class":"G","column":"<配置列>","contains":["advertise encap-type vxlan"],"absent":true,
    "mapping_reason":"判据='未配置该命令',absent=命中0行才成立;列全空内核自动改判数据不足"}}
◇反面参考:把"命令名出现在命令列"当故障信号(如 contains:["display vxlan tunnel"] 绑在 CmdName)
  ——健康设备同样执行巡检命令,基本必假阳;命令列一般只当 filter 用,判定落到内容列上。"""

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
   **列名**只能用下方各源概览里出现过的;**要匹配的值**(告警码/特征串)按判据语义写——概览/枚举里
   没出现照写(样例不可能集齐所有告警,运行时没有=不成立,不是数据不足);**外挂知识讲了"去哪查"的,照知识写**。
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
   mapping_reason 写实(链条每一跳从哪来、到哪去)。
9. **「上游传参/平台脚本入参链/平台JSON」不是"绑不了"的理由**:平台的"上游出参"落到本地,就是
   某条 display 回显或配置/EAV 表里的字段——把链条写成 param_from(先查A抽参数、代入B查)或
   pair_from(两侧比对);带◆【脚本纪要】的候选以纪要为规格。真绑不了只有一种:链条某一跳要的
   数据本场景**没采集**,mapping_reason 写清缺哪一跳(实测:拿"依赖上游传参"投降的判词会被冻进
   产物,数据齐了也醒不来)。"""

CROSS_OUTPUT_FORMAT = """【输出格式——只交"判定",别抄判据】
把结果用 Write 写到文首【结果文件】路径(**文件名逐字照抄,以 `_result.py` 结尾——别写成 `.json`**),
内容是一个 Python 列表字面量(带不带 `CONDITIONS = ` 前缀都行);
写完后最终回复只报一行「共 N 条,已写 <路径>」。每条字段:
{"node_id":"<nN>","class":"…","source":"<最终查询落点的真实源名,如 config——绝不是 cross,必写>",
 ...类参数/param_from...,"mapping_reason":"<链条每一跳写实:参数从哪个源怎么抽、代到哪查什么>"}
(id/node/condition_text 不用写——合并脚本从 tree_index 机械回填,写了也会被覆盖。)
示例(传参链):
{"node_id":"n2","class":"G","source":"config",
 "column":"level2","contains":["mode lacp-static"],"absent":True,
 "filter":{"column":"level1","equals":"@plink"},
 "param_from":{"plink":{"source":"cmd","column":"CmdResult",
               "filter":{"column":"CmdName","contains":"peer-link"},"extract":{"kv":"Port Name"}}},
 "mapping_reason":"回显抽 Port Name → 查该接口配置块,缺 mode lacp-static 即故障"}
示例(复杂组合,few-shot 参考:多要件判据 = 前置条件 AND (缺失 OR 跨源值比对)。
判据原文:"①配置有 stp v-stp enable;②display stp 回显含 This bridge is the root;③配置有
stp bridge-address;④且该 MAC = display stp v-stp 回显 Bridge Mac 的 Active 值"——故障 = ①②成立
且(③缺失 或 ③≠④):
{"node_id":"n68","class":"D","source":"config","combine":"AND","sub":[
   {"class":"G","source":"config","column":"level1","contains":["stp v-stp enable"]},
   {"class":"G","source":"cmd","column":"CmdResult",
    "filter":{"column":"CmdName","contains":"display stp"},"contains":["This bridge is the root"]},
   {"class":"D","combine":"OR","source":"config","sub":[
      {"class":"G","source":"config","column":"level1","contains":["stp bridge-address"],"absent":True},
      {"class":"P","source":"config","fault_when":"not_equal","pair_from":{
         "local":{"source":"config","column":"level1",
                  "filter":{"column":"level1","contains":"stp bridge-address"},
                  "extract":{"regex":"stp bridge-address ([0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4})"}},
         "peer":{"source":"cmd","column":"CmdResult",
                 "filter":{"column":"CmdName","contains":"display stp v-stp"},
                 "extract":{"regex":"Bridge Mac\\s*:\\s*Config=[0-9a-fA-F-]+\\s*/\\s*Active=([0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4})"}}}}]}],
 "mapping_reason":"四要件:config 有 v-stp enable + 回显含根桥标记 +(config 无 bridge-address【absent】或 config 桥MAC ≠ display stp v-stp 回显 Active 值【P 跨源比对,正则锚在 Bridge Mac 上防误抓其它 Active=】)"}
示例(绑不了):
{"node_id":"n9","decision":"insufficient_data","mapping_reason":"链条第2跳要的数据本场景未采集"}
**结果文件的最后一行**必须是完成标记(单独一行,写在列表 `]` 之后):
# BATCH_COMPLETE
(合并脚本靠它识别"写完了"还是"半路截断"——缺这行整批按截断重派。)"""

OUTPUT_FORMAT = """【输出格式——只交"判定",别抄判据】
把结果用 Write 写到文首【结果文件】路径(**文件名逐字照抄,以 `_result.py` 结尾——别写成 `.json`**,
那是路由批的格式;实测写错扩展名整批要返工),内容是一个 Python 列表字面量(带不带 `CONDITIONS = ` 前缀都行);
写完后最终回复只报一行「共 N 条,已写 <路径>」。**每条只写四类字段**:
{{"node_id":"<nN>","class":"A/C/D/G/J/P 之一(B/E/F/H/I 已封存,别用)",...类参数...,"mapping_reason":"<写实>"}}
- **id/node/condition_text/source 都不用写**——合并脚本会从 tree_index 机械回填(逐字、权威;
  你抄反而会截断/抄错,写了也会被覆盖)。这让你的输出短一半以上,别再超长截断;
- 答不了又确属本源该答 → {{"node_id":"<nN>","decision":"insufficient_data","mapping_reason":"<原因>"}};
- 明显不归本源答的判据 → **整条不写**(不写=不认领;禁止逐条表态灌水)。
示例(C 类):
{{"node_id":"n6","class":"C","column":"Summary","contains":["hwLinkHeartbeatChangeAlarm"],"mapping_reason":"判据给出具体告警名,本源 Summary 列是告警码枚举,匹配名段即可"}}
示例(decision):
{{"node_id":"n93","decision":"insufficient_data","mapping_reason":"确属本源该答,但已查数据词汇无对应列/枚举项"}}
**结果文件的最后一行**必须是完成标记(单独一行,写在列表 `]` 之后):
# BATCH_COMPLETE
(合并脚本靠它识别"写完了"还是"半路截断"——缺这行整批按截断重派。)"""

ROUTE_RULES = """【任务框架——只做路由,不做绑定】
你要做的是"预分诊":逐条判断每个判据**可能**由下面哪些数据源回答,产出路由表。
后续每个源会派专门的子代理做绑定(逐条读判据原文、语义映射、写映射理由)——那不是你的活,
你只决定"这条判据要不要进某个源的批"。判断口径:
- **多选宁多勿漏**:拿不准就把可能的源都选上——选多了只是该源的绑定代理多判一条(几十秒);
  选漏了该判据不会进被漏源的批,要靠后面的覆盖校验和跨源补绑才捞得回来(拖慢整体);
- 完全没把握的判据 → sources 填全部源(等同不裁剪,判据不会丢);
- 判断依据:判据原文提到的对象对照各源列名词汇——告警名/告警码 → 有告警列的源;
  配置项/接口/协议状态 → 配置类源;日志关键词/错误码 → 日志类源;跨源联查 → 涉及的源都选。"""

ROUTE_OUTPUT = """【输出格式】
把结果用 Write 写到文首【结果文件】路径,内容是一个 JSON 数组,每条:
{"node_id": "<nN>", "sources": ["<源名>", ...]}
- 本批**每条判据都必须出现**(漏条=该条回落广播,不报错但你这单白干一半);
- 源名只能取自上方【可选源清单】的名字,原样照抄(写错的源名会被机械过滤掉);
- 不写别的字段,不写理由;写完后最终回复只报一行「共 N 条,已写 <路径>」。"""


def _record_timing(out, step: str, seconds: float, **extra) -> None:
    """耗时打点 → <OUT>/timing.json(追加)。提效汇报的数据来源:数字从这里拿,不靠人掐表。"""
    import datetime as _dt
    p = Path(out) / "timing.json"
    try:
        recs = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:  # noqa: BLE001
        recs = []
    recs.append(dict({"step": step, "seconds": round(seconds, 1),
                      "at": _dt.datetime.now().isoformat(timespec="seconds")}, **extra))
    p.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")


def _fail(msg: str) -> None:
    print(f"✗ {msg}", file=sys.stderr)


def load_conditions(ti_path: Path) -> list:
    """tree_index → [(node_id, 名字, 判据全文)],带非空判据的**诊断节点**(pass_through 不参与绑定:
    高层分类/选路节点的判据只供人读,绑成判定会把全树确证卡死)。"""
    idx = json.loads(ti_path.read_text(encoding="utf-8"))
    out = []
    for nid, n in (idx.get("nodes") or {}).items():
        cond = str(n.get("condition") or "").strip()
        if cond and not n.get("pass_through"):
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
    """knowledge.md 正文 → [{title, tags(或None), stage(或None), body}]。条目=「### 标题」起头,
    紧跟可选「阶段:绑定/诊断」与「适用:标签1、标签2」行(顺序不限),其余为正文;
    首个 ### 之前的说明文字忽略。"""
    entries, cur = [], None
    for line in text.splitlines():
        if line.startswith("### "):
            cur = {"title": line[4:].strip(), "tags": None, "stage": None, "body": []}
            entries.append(cur)
        elif cur is not None:
            s = line.strip().replace(":", ":")
            if cur["tags"] is None and not cur["body"] and s.startswith("适用:"):
                cur["tags"] = [t for t in re.split(r"[、,,/\s]+", s.split(":", 1)[1]) if t]
            elif cur["stage"] is None and not cur["body"] and s.startswith("阶段:"):
                cur["stage"] = s.split(":", 1)[1].strip()
            elif line.strip() or cur["body"]:
                cur["body"].append(line)
    return [e for e in entries if "\n".join(e["body"]).strip()]


def match_knowledge(entries, name_pool: str):
    """按「阶段」与「适用」双维筛条目——阶段管给谁看:标「诊断」且没提「绑定/生成」的是
    诊断阶段的判读规则,不进绑定任务说明;无阶段=两边都载。适用管哪个分支看:无标签/含「通用」恒选;
    有标签则任一标签出现在 name_pool(节点名+判据文本拼串)才选——按分支载入,不全量塞入。"""
    picked = []
    for e in entries:
        st = e.get("stage") or ""
        if st and "诊断" in st and not ("绑定" in st or "生成" in st):
            continue                                     # 诊断期专属知识,不进绑定任务说明
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


def load_digests(out: Path) -> dict:
    """<OUT>/tree/script_digests.json(历史 adopt 流程导入的平台脚本纪要;该流程已删,
    存量纪要继续生效)→ {node_id: rec};没有/坏了 → 空 dict(纪要是增益,不因它坏主流程,
    但坏文件要明确提示)。"""
    p = out / "tree" / "script_digests.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"[warn] script_digests.json 读取失败({e}),本轮简报不带脚本纪要", file=sys.stderr)
        return {}


def _load_route(ddir: Path, inds) -> dict:
    """dispatch/route_batch*_result.json(--route 小步的产物)→ {node_id: set(源名)}。
    路由只决定"判据进谁的批",不是绑定——绑定仍由各源子代理逐条读原文做语义映射。
    容错从宽:文件坏/不是数组 → 整批作废并警告(相关判据回落广播);条目缺 node_id 跳过;
    源名过滤到本产物源集,过滤后为空 = 等同广播,不入表。路由永远只减重复、不丢判据。"""
    routes = {}
    if not ddir.is_dir():
        return routes
    for p in sorted(ddir.glob("route_batch*_result.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[warn] 路由结果 {p.name} 读取失败({e}),该批路由作废(相关判据回落广播)",
                  file=sys.stderr)
            continue
        if not isinstance(data, list):
            print(f"[warn] 路由结果 {p.name} 不是 JSON 数组,作废(相关判据回落广播)", file=sys.stderr)
            continue
        for e in data:
            if not isinstance(e, dict) or not e.get("node_id"):
                continue
            srcs = {s for s in (e.get("sources") or []) if s in inds}
            if srcs:
                routes[str(e["node_id"])] = srcs
    return routes


def digest_block(nid: str, dg: dict, include_depends: bool = False) -> str:
    """一条纪要 → 任务说明里的 ◆ 块(单源批与 --cross 补绑任务说明共用,防两处不一致)。
    depends 只在 include_depends=True(跨源补绑任务说明)时附——单源批里它唯一的实测作用是诱导子代理放弃
    ("需上游传参"→直接写成 insufficient_data),本地绑定落点由纪要正文的命令/字段给出,不需要入参链;
    跨源批则相反:param_from 传参链要靠这些字段名当原料。"""
    extra = ""
    if dg.get("regexes"):
        extra += "\n现成正则(原样可用):" + " ; ".join(dg["regexes"][:8])
    if include_depends and dg.get("depends"):
        extra += ("\n依赖(平台入参链,仅供理解定位字段):" + dg["depends"]
                  + "\n(平台靠上游传参,本地绑定**直接查对应命令回显/配置字段**;不许以"
                    "「需上游传参/需平台JSON/DataItem 本地没有」为由写 insufficient_data——"
                    "实测这类投降判词会被冻进产物,换了齐全数据它也醒不来)")
    return f"◆【脚本纪要 [{nid}]|平台已验证逻辑,规格以此为准】\n{dg.get('digest', '')[:2000]}{extra}"


def split_batches(conditions, digests: dict, batch_size: int, char_budget: int = 30000):
    """切批:无纪要时按条数(老行为);有纪要时再叠一个字符预算——带纪要的判据体积大,
    固定条数会把任务说明撑爆,按「判据+纪要」总字符封顶自动少装几条。"""
    if not digests:
        import math as _m
        n = max(1, _m.ceil(len(conditions) / batch_size))
        return [conditions[i * batch_size:(i + 1) * batch_size] for i in range(n)]
    batches, cur, cur_chars = [], [], 0
    for c in conditions:
        nid = c[0]
        w = len(c[2]) + len((digests.get(nid) or {}).get("digest", ""))
        if cur and (len(cur) >= batch_size or cur_chars + w > char_budget):
            batches.append(cur)
            cur, cur_chars = [], 0
        cur.append(c)
        cur_chars += w
    if cur:
        batches.append(cur)
    return batches


def build_briefing(ind, scenario, batch_no, n_batches, batch, total, overview_text, is_eav, enum_texts, result_path,
                   knowledge_text=None, digests=None):
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
    has_digest = False
    for nid, name, cond in batch:
        lines.append(f"\n[{nid}] {name}\n{cond}")
        dg = (digests or {}).get(nid)
        if dg and str(dg.get("digest") or "").strip():   # 空条目(unusable)不注入:没有规格内容的不能冒充权威依据
            has_digest = True
            lines.append(digest_block(nid, dg, include_depends=False))
    if has_digest:
        lines.insert(1, "(注:◆【脚本纪要 [nN]】块头的 [nN] 就是它所属的判据号,只作用于同号判据。纪要提炼自"
                        "平台上验证过的处理脚本——判据文本再含糊也以纪要为准:命令/关键词/正则/极性照纪要绑,"
                        "与判据矛盾时在 mapping_reason 写明按纪要。)")
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
    ap.add_argument("--batch-size", type=int, default=12,
                    help="每批判据条数上限(默认 12)。小批双收益:①子代理最后一次性 Write 结果文件,"
                         "批大=这一次生成要十几分钟、期间零工具输出,会被主进程按'10 分钟无输出'掐死"
                         "(实测);②批多=并行度高(61 条判据从 2 批并行变 5~6 批并行)。")
    ap.add_argument("--tree-index", default="", help="缺省=<OUT>/tree/*.tree_index.json(须唯一)")
    ap.add_argument("--cross", action="store_true",
                    help="生成跨源补绑简报(读 dispatch/cross_candidates.json,由 merge_conditions 汇集)")
    ap.add_argument("--route", action="store_true",
                    help="生成路由简报(预分诊小步):子代理只判每条判据可能归哪些源,写 "
                         "dispatch/route_batch<N>_result.json;收货后重跑本脚本(不带 --route),"
                         "各源批只装归自己的判据——砍掉'每源都判全量判据'的 N 倍重复")
    ap.add_argument("--no-route", action="store_true",
                    help="显式跳过路由按广播派发(判断量 ×源数)。路由简报在场却没有结果时,"
                         "正常模式默认拒产硬拦防跳步——确要广播才用这个放行")
    args = ap.parse_args()
    if args.route and args.cross:
        _fail("--route 与 --cross 互斥:路由是绑定前的预分诊,cross 是绑定后的补绑")
        return 2
    if args.route and args.no_route:
        _fail("--route 与 --no-route 互斥")
        return 2
    t0 = time.time()

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
                      if p.name != "cross_analyzer.py")  # cross 走 --cross 补绑任务说明,不按单源派
    if not inds:
        _fail("没找到任何指标(--indicators 或 <OUT>/scripts/*_analyzer.py)")
        return 2

    ov_dir = out / "overview"

    if args.route:                                       # 路由小步:只出"预分诊"任务说明,不出绑定任务说明
        digests = load_digests(out)
        pool = [c for c in conditions
                if not any(s in inds for s in (digests.get(c[0]) or {}).get("sources") or [])]
        if not pool:
            print("全部判据都有纪要 sources 路由,无需路由批——直接跑本脚本(不带 --route)出简报。")
            return 0
        ddir = out / "dispatch"
        ddir.mkdir(parents=True, exist_ok=True)
        old = _load_route(ddir, inds)
        if old:
            print(f"⚠ dispatch/ 已有路由结果覆盖 {len(old)} 条判据——本次会重写路由简报,"
                  "收到新结果后以新为准")
        src_lines = ["【可选源清单(sources 只能从这些名字里选,原样照抄)】"]
        for ind in inds:
            ov = ov_dir / f"{ind}_overview.json"
            cols = []
            if ov.exists():
                try:
                    cols = (json.loads(ov.read_text(encoding="utf-8")).get("summary") or {}).get("columns") or []
                except (OSError, json.JSONDecodeError):
                    cols = []
            hint = ("列名:" + "、".join(str(c) for c in cols[:30])) if cols \
                else "(概览缺失,列名未知——拿不准是否归它时把它也选上)"
            src_lines.append(f"- {ind}:{hint}")
        src_block = "\n".join(src_lines)
        batches = split_batches(pool, {}, 60)            # 路由是快判,批可以大:60 条/批
        for i, batch in enumerate(batches, 1):
            rp = ddir / f"route_batch{i}_result.json"
            lines = [f"# 路由简报:第 {i}/{len(batches)} 批(本批 {len(batch)} 条判据,路由池共 {len(pool)} 条)\n"
                     f"【结果文件】{rp}",
                     ROUTE_RULES, src_block,
                     f"【本批判据清单(共 {len(batch)} 条)】"]
            for nid, name, cond in batch:
                lines.append(f"\n[{nid}] {name}\n{cond}")
            lines.append(ROUTE_OUTPUT)
            (ddir / f"route_batch{i}.txt").write_text("\n\n".join(lines) + "\n", encoding="utf-8")
        print(f"路由简报 {len(batches)} 份 → {ddir}/route_batch<N>.txt(判据 {len(pool)} 条,≤60 条/批)。")
        print("派发句式(一份简报派一个子代理,批少通常 1 个就够):")
        print("  「你的全部任务和材料在 <简报路径>,用 Read 读完它,照其中规约执行——")
        print("    结果写到简报指定的结果文件,最终回复只报『共 N 条,已写 <路径>』」")
        print("收货后重跑本脚本(不带 --route)出各源绑定简报;路由结果坏/缺 → 对应判据自动回落广播,不丢。")
        _record_timing(out, "make_dispatch_route", time.time() - t0,
                       judgments=len(pool), briefs=len(batches))
        return 0

    # 外挂知识(tree/knowledge.md):按树的节点名+判据文本匹配「适用」标签,命中的条目进每份任务说明
    name_pool = scenario + "\n" + "\n".join(f"{name} {cond}" for _, name, cond in conditions)
    knowledge_text = load_knowledge(out, name_pool)
    if knowledge_text:
        n_kn = knowledge_text.count("◆ ")
        print(f"外挂知识:tree/knowledge.md 命中 {n_kn} 条,已嵌入全部简报")

    if args.cross:                                       # 跨源补绑任务说明:一份装下全部候选 + 所有源的词汇
        ddir = out / "dispatch"
        cand_p = ddir / "cross_candidates.json"
        cands = json.loads(cand_p.read_text(encoding="utf-8")) if cand_p.exists() else []
        # 机械补漏:没被**任何**源认领的诊断节点也进候选——cross_source 标记靠子代理自觉,实测会失灵
        # (没人打标记/合并被绕过),机械补位保证跨源判据不静默消失;补绑代理逐条判"真跨源还是真缺数据"。
        claimed = set()
        import ast as _ast
        for ap_ in sorted(out.glob("scripts/*_analyzer.py")):
            if ap_.name == "cross_analyzer.py":
                continue
            try:
                for node in _ast.parse(ap_.read_text(encoding="utf-8")).body:
                    if isinstance(node, _ast.Assign) and any(
                            isinstance(t, _ast.Name) and t.id == "CONDITIONS" for t in node.targets):
                        claimed |= {str(e.get("node_id")) for e in _ast.literal_eval(node.value)
                                    if isinstance(e, dict) and e.get("node_id")}
            except Exception:  # noqa: BLE001
                continue
        cand_ids = {c.get("node_id") for c in cands}
        unclaimed = [(nid, name) for nid, name, _ in conditions
                     if nid not in claimed and nid not in cand_ids]
        for nid, name in unclaimed:
            cands.append({"node_id": nid, "node": name, "sources": [],
                          "reasons": ["机械兜底:未被任何源认领——请判断是跨源联查还是确实缺数据"]})
        if unclaimed:
            print(f"机械兜底:{len(unclaimed)} 个无人认领的诊断节点已并入候选(不靠标记自觉)")
        if not cands:
            print("没有跨源候选(无 cross_source 标记,诊断节点也都已被认领)——无需补绑。")
            return 0
        cond_by_id = {nid: (name, cond) for nid, name, cond in conditions}
        ddir.mkdir(parents=True, exist_ok=True)
        # 候选清单写回(含机械补漏扫进来的)——这是"cross 正在处理谁"的权威清单,
        # check_tree_coverage 靠它给未绑叶子标"已进跨源候选",把"流程中"和"真漏"分开。
        cand_p.write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
        shared = [f"你是诊断绑定专家,任务:为下面的跨源判据写 param_from/pair_from 绑定(或明确绑不了),"
                  f"产出 cross_analyzer.py 的 CONDITIONS 片段。场景:{scenario}。",
                  CROSS_RULES.format()]
        if knowledge_text:
            shared.append("【外挂知识(业务同事手写:指路可信——去哪查/怎么查照它;但不是事实,查证靠数据)】\n"
                          + knowledge_text)
        for ind in inds:
            ov = ov_dir / f"{ind}_overview.json"
            if ov.exists():
                shared.append(f"【{ind} 源数据词汇——概览原文】\n" + ov.read_text(encoding="utf-8").strip())
            else:
                shared.append(f"【{ind} 源概览缺失】overview/{ind}_overview.json 不存在——该源列名未知,"
                              "涉及它的绑定老实写 decision:insufficient_data 并注明缺概览")
            for f in find_enums(ov_dir, ind):
                shared.append(f"【{ind} 源数据词汇——枚举清单原文:{f.name}】\n"
                              + f.read_text(encoding="utf-8").strip())
        digests = load_digests(out)
        digests = {k: v for k, v in digests.items() if str(v.get("digest") or "").strip()}  # 空条目不注入
        if digests:
            n_hit = sum(1 for c in cands if str(c.get("node_id")) in digests)
            print(f"脚本纪要:跨源候选命中 {n_hit}/{len(cands)} 条——◆块已附进补绑简报(规格以纪要为准,"
                  "含依赖字段供 param_from 取材)")
        # 切批(≤12 条/批,与单源批同因):实测 27 条候选塞单代理,一跑 13 分钟、输出截断炸格式,整批返工。
        # 词汇/规约每份任务说明原样重复——任务说明自包含,子代理互相看不见。
        chunks = [cands[i:i + args.batch_size] for i in range(0, len(cands), args.batch_size)]
        (ddir / "manifest.json").write_text(json.dumps({"complete_marker": True}, ensure_ascii=False),
                                            encoding="utf-8")
        for i, chunk in enumerate(chunks, 1):
            rp = ddir / f"cross_batch{i}_result.py"
            lines = [f"【候选判据清单(共 {len(chunk)} 条;condition_text 必须逐字取自这里)】"]
            for c in chunk:
                nid = c.get("node_id")
                name, cond = cond_by_id.get(nid, (c.get("node") or "?",
                                                  "(tree_index 里查不到判据原文——node_id 可能填错,核实后表态)"))
                srcs = "、".join(c.get("sources") or [])
                reasons = ";".join(c.get("reasons") or [])
                lines.append(f"\n[{nid}] {name}(由 {srcs} 源标记" + (f":{reasons}" if reasons else "") + f")\n{cond}")
                dg = digests.get(str(nid))
                if dg:
                    lines.append(digest_block(str(nid), dg, include_depends=True))
            sec = ([f"# 跨源补绑简报:cross · 第 {i}/{len(chunks)} 批(本批 {len(chunk)} 条,候选共 {len(cands)} 条;"
                    f"其余批由别的子代理处理,你只管本批)\n【结果文件】{rp}"]
                   + shared + ["\n".join(lines), CROSS_OUTPUT_FORMAT])
            (ddir / f"cross_batch{i}.txt").write_text("\n\n".join(sec) + "\n", encoding="utf-8")
        print(f"跨源补绑简报 {len(chunks)} 份 → {ddir / 'cross_batch<N>.txt'}(候选 {len(cands)} 条,"
              f"≤{args.batch_size} 条/批)。一份简报派一个子代理(分波规矩同单源批),"
              "结果收各批 cross_batch<N>_result.py,然后重跑 merge_conditions.py 合并进 cross_analyzer.py。")
        return 0

    digests = load_digests(out)
    # 可注入 = 有正文的;空条目(unusable,脚本不可用)只留档——没有规格内容却标着"规格以此为准"进任务说明是自相矛盾,
    # 还白吃字符预算撑批(实测 500+ 字/条)。路由(sources)仍用全量:不可用脚本的取数预扫也可能是对的。
    inject = {k: v for k, v in digests.items() if str(v.get("digest") or "").strip()}
    if digests:
        n_un = len(digests) - len(inject)
        n_hit = sum(1 for c in conditions if c[0] in inject)
        print(f"脚本纪要:{len(inject)} 条可注入(本树判据命中 {n_hit} 条)——逐条附进简报,按字符预算分批"
              + (f";另有 {n_un} 条脚本不可用,留档不注入(对应节点按判据原文绑)" if n_un else ""))
        trunc = [k for k, v in inject.items()
                 if len(str(v.get("digest") or "")) > 2000 or len(v.get("regexes") or []) > 8]
        if trunc:
            print(f"⚠ 纪要超长截断 {len(trunc)} 条(正文>2000字或正则>8条,超出部分不进简报,"
                  f"必要时精简纪要):{'、'.join(trunc[:6])}")

    route_map = {} if args.no_route else _load_route(out / "dispatch", inds)  # 路由批结果(--route 小步产出)
    # 跳步拦截:路由任务说明生成了、结果却一条没解析出来 → 拒绝生成。实测弱模型出完路由任务说明 19 秒就直接
    # 重跑本命令(没派子代理、没收结果),脚本按"结果缺→回落广播"静默放行,路由白做、判断量 ×源数
    # 全数回来。广播必须是显式选择(--no-route),不能是跳步的默认奖励。
    if not args.no_route and not route_map:
        rb = sorted((out / "dispatch").glob("route_batch*.txt")) if (out / "dispatch").is_dir() else []
        if rb:
            has_res = any((out / "dispatch").glob("route_batch*_result.json"))
            _fail("路由简报已生成但路由没走完——"
                  + ("结果文件在,但一条路由都没解析出来(全坏/全空,看上方 [warn])"
                     if has_res else f"还没派子代理收结果({len(rb)} 份简报,0 份 route_batch*_result.json)")
                  + "。两条路(二选一):\n"
                    "  ① 按路由简报派子代理(派发句式见 --route 的输出),结果落盘后重跑本命令;\n"
                    "  ② 确要跳过路由按广播派(判断量 ×源数,不推荐)→ 重跑本命令加 --no-route。")
            return 1

    def routed_conditions(ind):
        """源路由三级:①纪要声明了 sources 的判据只进对应源的批(最强,来自交付包实测);
        ②路由批标注(模型快判"可能归哪些源",多选宁多勿漏);③都没有 → 广播(进每个源的批,
        老行为,谁能答谁答——三源全量重复=3× 判断量,路由批就是为砍它)。"""
        outc = []
        for c in conditions:
            srcs = (digests.get(c[0]) or {}).get("sources") or []
            known = [s for s in srcs if s in inds]
            if known:
                if ind in known:
                    outc.append(c)
                continue
            rt = route_map.get(c[0])
            if rt and ind not in rt:
                continue
            outc.append(c)
        return outc

    if route_map:
        routed_n = sum(1 for c in conditions if c[0] in route_map
                       and not [s for s in (digests.get(c[0]) or {}).get("sources") or [] if s in inds])
        total_evals = sum(len(routed_conditions(i)) for i in inds)
        print(f"路由批生效:{routed_n} 条判据按路由裁剪,其余按纪要/广播;"
              f"各源判断量合计 {total_evals}(广播口径为 {len(conditions) * len(inds)},"
              f"省 {100 - round(total_evals * 100 / max(1, len(conditions) * len(inds)))}%)")

    if digests:
        orphan = [c for c in conditions
                  if (digests.get(c[0]) or {}).get("sources")
                  and not any(s in inds for s in (digests.get(c[0]) or {}).get("sources", []))]
        if orphan:
            print(f"⚠ {len(orphan)} 条判据的纪要 sources 与本产物源集全对不上(将回落广播):"
                  + "、".join(f"{c[0]}" for c in orphan[:8]))
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
        ind_conds = routed_conditions(ind)
        if (digests or route_map) and len(ind_conds) < len(conditions):
            print(f"  {ind}:源路由后 {len(ind_conds)}/{len(conditions)} 条(其余判据由纪要/路由批归到别的源)")
        if not ind_conds:
            print(f"  {ind}:没有归本源的判据(纪要/路由批全路由走了),本源不出简报")
            continue
        batches = split_batches(ind_conds, inject, args.batch_size)
        n_batches = len(batches)
        for i, batch in enumerate(batches, 1):
            p = ddir / f"{ind}_batch{i}.txt"
            rp = ddir / f"{ind}_batch{i}_result.py"
            p.write_text(build_briefing(ind, scenario, i, n_batches, batch, len(ind_conds),
                                        ov_text, is_eav, enum_texts, str(rp), knowledge_text,
                                        digests=inject), encoding="utf-8")
            written.append((ind, i, len(batch), p))

    # 派发标记文件:声明"结果文件须带完成标记"(merge 据此发现半路截断的、看似合法的短列表;
    # 老 dispatch 目录没有标记文件 → merge 不启用该检查,向后兼容)
    (ddir / "manifest.json").write_text(json.dumps({"complete_marker": True}, ensure_ascii=False),
                                        encoding="utf-8")
    print(f"判据共 {len(conditions)} 条 → 简报 {len(written)} 份(≤{args.batch_size} 条/批;路由/纪要生效时各源批数不同):")
    for ind, i, n, p in written:
        print(f"  {ind} 批{i}({n} 条) → {p}")
    if written:
        print("\n派发句式(一份简报派一个子代理;简报多就分波派——一波 4~8 个、收完一波再下一波;不许合并简报、不许转述):")
        print("  「你的全部任务和材料在 <简报路径>,用 Read 读完它(可分段),照其中规约执行——")
        print("    结果写到简报指定的结果文件,最终回复只报『共 N 条,已写 <路径>』」")
        print("收货(可反复跑,先到先合):python merge_conditions.py --out \"<OUT>\" ——机械验收+合并+写进各 <ind>_analyzer.py。")
        print("子代理读不了文件的环境 → 主代理把简报全文读入后原样贴进派发词(纯复制)。")
    _record_timing(out, "make_dispatch", time.time() - t0,
                   judgments=len(conditions), briefs=len(written), batch_size=args.batch_size)
    if errors:
        print(f"\n✗ {len(errors)} 个源拒产(缺原料,补齐后重跑本脚本):", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
