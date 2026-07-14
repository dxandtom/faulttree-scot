#!/usr/bin/env python3
"""把"并行绑定的派发准备"从模型手里收回来:机械生成每个子代理的自包含简报文件。

为什么要它:派发环节的三类实测事故——判据没读全就派发(贴不出原文)、EAV 源没带枚举
(子代理自编指标名,实测 PORT_RxCRC)、不拆批(单次生成过长 → 回复截断/600s 流挂)——
全是机械活没做对。散文规矩管不住弱模型,脚本管得住:简报由本脚本拼装,判据自动切批
(默认 ≤50 条/批)、该源概览/枚举原文自动嵌入、公共规约与 C/G·EAV 铁规是固定文本,
模型只剩「每份简报派一个子代理」一个动作。

产物:<OUT>/dispatch/<ind>_batch<N>.txt(自包含:公共规约 + 本源要点 + 数据词汇原文
+ 该批判据原文 + 输出格式)。派发句式(主代理用):
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
import sys
from pathlib import Path

COMMON_RULES = """【公共规约——逐条遵守】
1. 本文件就是你的全部任务与材料:**不要读写任何其他文件、不要用 Bash**——需要的东西已全部在下文。
2. 只绑你这个源({ind})能答的判据;明显不归本源答的**整条不写**;「确属本源该答、这次却答不了」才写
   decision:"insufficient_data" 并在 mapping_reason 里说明原因。
3. 树驱动:下面清单里的**每条判据都要过一遍**再决定绑/不绑;按判据要的列+值绑,**不管这次数据里出没出现**
   (值没出现 → 照绑,运行时 matched=0 = 确定排除,不是数据不足)。
4. 一条判据常含**多个子条件**(换行/且/①② 分隔):本源能答的每个子条件都要绑到(单源多子条件用 D 类组合),
   不是碰到一条就算认领了该节点。
5. 别的源绑不绑与你无关:多源同绑同一节点是期望行为,**不许谦让**。
6. 每条绑定写实 mapping_reason(为何这列能答这条判据/语义或单位差异)。
7. C/G 的 contains 铁规:每个 target 必须**独立是"判据成立的充分信号"**——禁止字段标签/命令名/协议名等
   泛词(如 "CRC:"/"LLDP"),禁止 ≤3 字符的 ASCII 短串;否定/为空语义禁用文本捞子串(数据有状态枚举列就绑
   equals 或 F 类 not_equals);趋势语义(增长/震荡/抖动)只能绑设备自带的检测告警码,没有就 decision。
8. 输出:只在**最终回复**里给出 Python 列表(格式见文末)+ 末尾一行「共 N 条」,N 必须等于列表实际长度。"""

EAV_RULES = """【EAV 窄表铁规(本源适用)】
本源是"一行一个量"的窄表:DataItem 说这行是什么指标,DataValue 是值。绑定固定形态:
  {"class":"A","filter":{"column":"DataItem","equals":"<指标名>"},"column":"DataValue","op":">","threshold":0,"scope":"count_rows",...}
  {"class":"C","filter":{"column":"DataItem","equals":"<指标名>"},"column":"DataValue","contains":["<取值>"],...}
**filter.equals 的值必须逐字来自下文的枚举清单**;清单里没有的指标名,禁止绑、禁止自己编——
写 decision:"insufficient_data" 并注明「已查枚举无对应项」。filter 命中 0 行=该指标本次未采集=数据不足,不是"不成立"。"""

OUTPUT_FORMAT = """【输出格式】
最终回复只包含:一个 Python 列表字面量 + 末尾一行「共 N 条」。每条字段:
{{"id":"{ind}-<node_id>","node":"<节点名>","node_id":"<nN>","condition_text":"<逐字抄上面清单的判据原文>",
 "class":"A–J 之一","source":"{ind}",...类参数...,"mapping_reason":"<写实>"}}
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


def build_briefing(ind, scenario, batch_no, n_batches, batch, total, overview_text, is_eav, enum_texts):
    sec = []
    sec.append(f"# 绑定简报:{ind} 源 · 第 {batch_no}/{n_batches} 批(本批 {len(batch)} 条判据,全树共 {total} 条;"
               f"其余批由别的子代理处理,你只管本批)")
    sec.append(f"你是诊断绑定专家,任务:把下面这批故障树判据映射(或明确不映射)到 {ind} 数据源,"
               f"产出 {ind}_analyzer.py 的 CONDITIONS 片段。场景:{scenario}。")
    sec.append(COMMON_RULES.format(ind=ind))
    if is_eav:
        sec.append(EAV_RULES)
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
        inds = sorted(p.stem[:-len("_analyzer")] for p in out.glob("scripts/*_analyzer.py"))
    if not inds:
        _fail("没找到任何指标(--indicators 或 <OUT>/scripts/*_analyzer.py)")
        return 2

    ov_dir = out / "overview"
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
            p.write_text(build_briefing(ind, scenario, i, n_batches, batch, len(conditions),
                                        ov_text, is_eav, enum_texts), encoding="utf-8")
            written.append((ind, i, len(batch), p))

    print(f"判据共 {len(conditions)} 条 → 每源 {n_batches} 批(≤{args.batch_size} 条/批)。已生成简报 {len(written)} 份:")
    for ind, i, n, p in written:
        print(f"  {ind} 批{i}({n} 条) → {p}")
    if written:
        print("\n派发句式(一份简报派一个子代理,别合并、别转述):")
        print("  「你的全部任务和材料在 <简报路径>,用 Read 读完它(可分段),照其中规约执行,结果放最终回复」")
        print("子代理读不了文件的环境 → 主代理把简报全文读入后原样贴进派发词(纯复制)。")
    if errors:
        print(f"\n✗ {len(errors)} 个源拒产(缺原料,补齐后重跑本脚本):", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
