#!/usr/bin/env python3
"""业务脚本收编 · 第 1 步:规则 JSON ↔ 故障树节点 机械对齐 + 消化任务分批。

背景:业务平台上每个故障树节点已有一个验证过的处理脚本(python),规则 JSON(childrenRule)
记录了「节点中文名 nameZh → 脚本路径」。但 nameZh 与我们树 excel 的节点名常有微小出入
("M-LAG场景的限制" vs "M-LAG场景限制"),脚本路径是平台路径。本脚本做机械部分:
- 解析规则 JSON(可多份,合并 childrenRule),按文件名在本地脚本目录里解析出真实路径;
- 名字归一化(去空白/全半角/标点/「的」)后精确匹配 → 自动对齐;
- 残差(两边都没配上的)响亮列出,交模型裁决(直接补 adoption_map.json);
- 对已对齐节点产出「消化任务」分批简报:模型读脚本 → 写绑定纪要(digest),结果由
  merge_digests.py 校验合并。

用法:
    align_scripts.py --out <产物目录> --rules <rule.json 或目录,可多个> --scripts <脚本根目录>
        [--digest-batch-size 3]
产物(都在 <OUT>/adopt/ 下):adoption_map.json / unmatched_nodes.json / unmatched_rules.json /
    digest_task_batch<N>.txt(消化简报)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PUNCT = r""" \t　,,、。.::;;''""“”‘’()()【】\[\]{}<>《》—–\-_/\\"""


def norm_name(s: str) -> str:
    """节点名归一:去空白与标点、全角→半角、英文小写、去「的」。宁可保守,残差交模型。"""
    s = str(s or "")
    s = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in s)
    s = re.sub(f"[{_PUNCT}]", "", s)
    s = s.replace("的", "")
    return s.lower()


def load_rules(paths):
    """规则 JSON(文件或目录,多个)→ [{name_zh, rule_no, script(平台路径)}](childrenRule 展开)。"""
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files += sorted(p.rglob("*.json"))
        elif p.exists():
            files.append(p)
        else:
            print(f"[warn] 规则路径不存在:{p}")
    out, bad = [], []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            bad.append(f"{f}: {e}")
            continue
        rules = d.get("childrenRule") if isinstance(d, dict) else None
        if rules is None and isinstance(d, list):
            rules = d
        for r in rules or []:
            if not isinstance(r, dict):
                continue
            nm = str(r.get("nameZh") or "").strip()
            sc = str(r.get("script") or "").strip()
            if nm and sc:
                out.append({"name_zh": nm, "rule_no": r.get("ruleNo"), "script": sc, "rule_file": f.name})
    for b in bad:
        print(f"[warn] 规则文件解析失败:{b}")
    # 去重:同名同脚本只留一条
    seen, dedup = set(), []
    for r in out:
        k = (r["name_zh"], Path(r["script"]).name)
        if k not in seen:
            seen.add(k)
            dedup.append(r)
    return dedup


def resolve_scripts(rules, scripts_root: Path):
    """平台脚本路径 → 本地真实路径(按文件名在脚本根目录下搜);找不到/多义的记录下来。"""
    index = {}
    for p in scripts_root.rglob("*.py"):
        index.setdefault(p.name, []).append(p)
    missing, ambiguous = [], []
    for r in rules:
        base = Path(r["script"]).name
        hits = index.get(base, [])
        if not hits:
            r["script_local"] = None
            missing.append(f"{r['name_zh']} → {base}")
        elif len(hits) > 1:
            # 用平台路径的父目录名消歧(如 1_1_mlag)
            parent = Path(r["script"]).parent.name
            narrowed = [h for h in hits if h.parent.name == parent]
            r["script_local"] = str(narrowed[0]) if len(narrowed) == 1 else None
            if r["script_local"] is None:
                ambiguous.append(f"{r['name_zh']} → {base}(候选 {len(hits)} 个)")
        else:
            r["script_local"] = str(hits[0])
    return missing, ambiguous


def load_tree_nodes(out_dir: Path):
    """产物 tree_index → {node_id: {name, condition, pass_through}}(全部节点,含 pass_through:
    场景入口脚本常对齐到高层节点,其纪要虽不进绑定简报,留在 map 里供人/知识用)。"""
    tis = sorted((out_dir / "tree").glob("*.tree_index.json"))
    if not tis:
        raise SystemExit(f"产物 {out_dir}/tree 下没有 *.tree_index.json——先走 generate 步骤1-2(建树+归集)再收编")
    idx = json.loads(tis[0].read_text(encoding="utf-8"))
    return {nid: {"name": str(n.get("name") or ""), "condition": str(n.get("condition") or ""),
                  "pass_through": bool(n.get("pass_through"))}
            for nid, n in (idx.get("nodes") or {}).items()}


DIGEST_RULES = """\
【任务】你是"平台脚本收编"专家:逐个节点读业务脚本(路径在下面,自己去读文件),把脚本里的
判定逻辑提炼成「绑定纪要」——给后续绑定子代理当权威规格书。脚本是平台上验证过的,判据文本写得
再含糊,以脚本逻辑为准。

【每个节点交一条 JSON,所有节点写完后整体存为一个 JSON 数组到下面的结果文件】
{"node_id": "nX",
 "digest": "多行文本,写清:①查哪条命令/哪个数据源(cmd 回显/config 层级/dblg/log 告警);\
②判定规则(什么状态字/什么值=异常,**极性别搞反:先写清正常态是什么**);③复杂逻辑\
(本端对端配对/取最新一次采集/跨命令合并)标注「复杂逻辑:建议 cross/代理口径」并一句话说明。\
**正则只放 regexes 字段、正文别重复罗列;前置采集清单不用写**(缺采集=内核运行时自动判数据不足)",
 "unusable": "**脚本不可用时改交此字段、并省略 digest**(一句原因,如:run() 纯透传无判定逻辑)——\
该节点绑定自动回落判据原文;别把「脚本不可用」写进 digest 顶着权威帽子进简报",
 "commands": ["display …", …],            // 该节点涉及的采集命令(命令名归这里,别混进 regexes)
 "regexes": ["脚本里的现成正则,原样"],     // 没有就 [];这是最值钱的部分
 "sources": ["cmd", "diag"],              // 该节点该去哪些本地源绑定(见下「源路由」),拿不准就 []
 "solution_zh": "脚本 OutPut_map 里的中文 solution 文本,没有就省略此键",
 "depends": "入参出参链一句话(如:需要上家的 causation/对端设备名),没有省略——\
只随跨源补绑简报下发(param_from 的字段名原料),单源绑定简报不带它"}

【基础知识:平台取数函数 → 平台数据表(读脚本先对号入座,搞清它吃的是什么形态的数据)】
- self.getCmdPre(...) → CMD 表:命令行及其**原始回显**;
- self.getCmd(...) → DIAG 表:DataItem 四元组(Object\\SubObject\\DataItem\\DataValue,平台解析好的
  指标值——**本地没有这张表**,这些值归根到底源自命令/诊断回显);
- self.getCfg(...) → CONFIG 表:配置层级(Level1\\Level2\\Level3);
- self.getLog(...) → LOG 表:日志(Module\\Summary\\Content)。

【源路由——按脚本的取数调用标 sources,派发会据此只派对应的源,省大量无用功】
- getCmdPre(命令行+原始回显)→ ["cmd"];
- getCmd(DataItem 指标值)→ 本地无 DataItem 表,值来自回显 → **["cmd","diag"] 两个都标**
  (本地哪条命令进哪个文件看采集,不强分,宁多勿漏);纪要必须把"脚本看哪个 DataItem"翻译成
  "查哪条 display 命令的回显、看什么关键词"(见铁律2);
- getCfg(配置层级)→ ["config"];
- getLog / 查告警日志(Summary/告警名匹配类)→ ["dblg","log"] 都标;
- self.getKpi(...) → ["kpi"];
- 脚本查了多个类别 → 全标上;**拿不准 → 空 []**(空=回落广播,每个源都会派,不会漏)。
- 每个节点下面给了机械预扫的调用提示,核对后照标;sources 只能取本产物真实存在的源名(见下清单)。

【铁律】
1. 只抄脚本里有的事实,不发挥:命令名/状态字/正则/阈值逐字来自脚本;
2. 我们的本地数据是**原始回显**(cmd 表 CmdName+CmdResult)与 config 层级表(level1~5)、
   dblg/log 表(Summary/Content/Log)——脚本里的 DataItem 名(如 EthTrunk_Operatestatus)本地
   **没有**,纪要要落到"查哪条 display 命令的回显、看什么关键词/字段"这一层;
3. 脚本读不懂/文件缺失/纯透传壳的节点,交 unusable 字段(见模板)并省略 digest,别编——
   让它响亮地缺着,绑定回落判据原文;
4. **JSON 字符串里别放裸的英文双引号**:正文要引用状态字/取值时用中文「」(如 缺一侧即为「单归」),
   别写 …为"单归"…(会把 JSON 截断,整批合并失败——实测高频翻车点);正则里的反斜杠记得双写(\\d);
5. **regexes 只放判定用的正则**(从回显/配置里抠值、匹配故障特征的那些),**别带 Python 的 r'' 包装**
   (裸字符串,如 "\\\\(\\\\*\\\\)");采集命令名归 commands,别混进 regexes(实测:checkCmdCollect 的命令名
   正则被塞进 regexes,绑定照抄就错);
6. **solution 抄脚本原文**:URL 保持纯文本,**别转成 markdown 链接**(实测被写成 [http](url) 丢了原文);
7. **sources 只按脚本真正取数的调用标**:继承链/无关 import 不算(实测:只查配置的脚本被标了 dblg/log,
   白白广播);
8. 写完结果文件最后一行加一行注释 # BATCH_COMPLETE(截断自检)。
"""


_ACCESSOR_HINTS = (("getCmdPre(", "getCmdPre(CMD 表:命令行+原始回显)→ 建议 sources 含 cmd"),
                   ("getCmd(", "getCmd(DIAG 表 DataItem 指标值,本地无此表,值源自回显)→ 建议 sources 含 cmd/diag"),
                   ("getCmdStr(", "getCmdStr(补采命令话术,不算取数)"),
                   ("getCfg(", "getCfg(CONFIG 配置层级)→ 建议 sources 含 config"),
                   ("getKpi(", "getKpi → 建议 sources 含 kpi"),
                   ("getLog(", "getLog(LOG 表:Module/Summary/Content)→ 建议 sources 含 dblg/log"),
                   ("getOpr(", "getOpr(操作记录)→ 本地无此源,标注即可"))


def _scan_accessors(script_path: str) -> str:
    """机械预扫脚本里的取数调用,给消化子代理当 sources 提示(只提示不定案,模型核对后标)。"""
    try:
        text = Path(script_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "(脚本读取失败,预扫缺席)"
    hits = [hint for key, hint in _ACCESSOR_HINTS if key in text]
    return ";".join(hits) if hits else "(未扫到已知取数调用,sources 留空=广播)"


def _available_indicators(out_dir: Path) -> list:
    """本产物真实存在的源名 = scripts/<ind>_analyzer.py 的前缀(cross 除外)。"""
    return sorted(p.name[:-len("_analyzer.py")] for p in (out_dir / "scripts").glob("*_analyzer.py")
                  if p.name != "cross_analyzer.py" and not p.name.startswith("analyzer"))


def build_digest_batches(aligned, nodes, out_dir: Path, batch_size: int):
    ddir = out_dir / "adopt"
    ddir.mkdir(parents=True, exist_ok=True)
    rdir = ddir / "digest_results"
    rdir.mkdir(exist_ok=True)
    inds = _available_indicators(out_dir)
    ind_line = ("【本产物的源清单(sources 只能从这里选)】" + "、".join(inds)) if inds else \
        "【本产物源清单未知(scripts/ 下没找到 *_analyzer.py)——sources 一律留空,回落广播】"
    items = [a for a in aligned if a.get("script_local")]
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    for i, b in enumerate(batches, 1):
        sec = [f"# 脚本消化任务 · 第 {i}/{len(batches)} 批(本批 {len(b)} 个节点)",
               f"【结果文件】{rdir / f'batch{i}.json'}", DIGEST_RULES, ind_line,
               "【本批节点】"]
        for a in b:
            n = nodes.get(a["node_id"], {})
            sec.append(f"--- {a['node_id']}「{n.get('name')}」 ---\n"
                       f"脚本文件:{a['script_local']}\n"
                       f"取数调用预扫:{_scan_accessors(a['script_local'])}\n"
                       f"判据原文(仅参考,以脚本为准):\n{(n.get('condition') or '(无)')[:1500]}")
        (ddir / f"digest_task_batch{i}.txt").write_text("\n\n".join(sec) + "\n", encoding="utf-8")
    return len(batches)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="规则JSON↔树节点机械对齐 + 消化任务分批")
    ap.add_argument("--out", required=True, help="场景产物目录(须已有 tree/*.tree_index.json)")
    ap.add_argument("--rules", required=True, nargs="+", help="规则 JSON 文件或目录(可多个)")
    ap.add_argument("--scripts", required=True, help="业务脚本根目录(按文件名递归搜)")
    ap.add_argument("--digest-batch-size", type=int, default=3,
                    help="每批消化几个节点(脚本长,默认 3;一次最多并行派 5 个子代理)")
    a = ap.parse_args()
    out = Path(a.out).expanduser().resolve()
    nodes = load_tree_nodes(out)
    rules = load_rules(a.rules)
    if not rules:
        raise SystemExit("规则 JSON 里没解析到任何 childrenRule 条目——核对文件")
    missing, ambiguous = resolve_scripts(rules, Path(a.scripts).expanduser().resolve())

    # 手工裁决保留:既有 adoption_map.json 里 auto:false 的条目是模型/人补的残差裁决,
    # **重跑不冲掉**(实测:v1 每次重生成,把 17 条手工裁决全灭了——补完重跑=白补)。
    manual, bad_manual = [], []
    amap_p = out / "adopt" / "adoption_map.json"
    if amap_p.exists():
        try:
            for e in json.loads(amap_p.read_text(encoding="utf-8")):
                if isinstance(e, dict) and e.get("auto") is False:
                    nid = str(e.get("node_id") or "")
                    if nid in nodes:
                        manual.append(e)
                    else:
                        bad_manual.append(f"{nid}(不在树上)← {e.get('name_zh')}")
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit(f"既有 adoption_map.json 解析失败:{e}\n"
                             "手工裁决是全流程最贵的人工资产——解析失败还继续跑,会无声从 0 重来"
                             "(实测 22 条手工裁决全灭)。先修好 JSON(常见病:整文件重写引入 // 注释/尾逗号/"
                             "裸英文双引号),再重跑本脚本;手工条目一条都不许静默丢。")
    manual_keys = {(norm_name(e.get("name_zh") or ""), Path(str(e.get("script") or "")).name) for e in manual}
    manual_nodes = {str(e["node_id"]) for e in manual}

    by_norm = {}
    for nid, n in nodes.items():
        by_norm.setdefault(norm_name(n["name"]), []).append(nid)
    aligned, un_rules = [], []                           # 自动条目在前;手工条目最后 extend 到**文件末尾**
    used_nodes = set(manual_nodes)                       # (与「手工条目追加到数组末尾」的操作习惯一致——
                                                         # 实测弱模型爱读文件尾核对,手工排开头会被误判"被清空")
    for r in rules:
        if (norm_name(r["name_zh"]), Path(r["script"]).name) in manual_keys:
            continue                                     # 手工已裁,不重复也不进残差
        cand = by_norm.get(norm_name(r["name_zh"]), [])
        if len(cand) == 1:
            nid = cand[0]
            aligned.append({"node_id": nid, "node": nodes[nid]["name"], "name_zh": r["name_zh"],
                            "rule_no": r.get("rule_no"), "script": r["script"],
                            "script_local": r.get("script_local"), "auto": True,
                            "pass_through": nodes[nid]["pass_through"]})
            used_nodes.add(nid)
        else:
            un_rules.append({"name_zh": r["name_zh"], "script": Path(r["script"]).name,
                             "script_local": r.get("script_local"),
                             "note": "归一后无唯一匹配" if not cand else f"归一后命中 {len(cand)} 个节点:{cand}"})
    aligned.extend(manual)
    un_nodes = [{"node_id": nid, "node": n["name"], "pass_through": n["pass_through"]}
                for nid, n in nodes.items() if nid not in used_nodes and n["condition"]]
    dup = {}
    for e in aligned:
        dup.setdefault(str(e["node_id"]), []).append(Path(str(e.get("script") or "")).name)
    multi = {k: v for k, v in dup.items() if len(v) > 1}

    ddir = out / "adopt"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "adoption_map.json").write_text(json.dumps(aligned, ensure_ascii=False, indent=2), encoding="utf-8")
    (ddir / "unmatched_rules.json").write_text(json.dumps(un_rules, ensure_ascii=False, indent=2), encoding="utf-8")
    (ddir / "unmatched_nodes.json").write_text(json.dumps(un_nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    # 分批快照:记下"这次分批是按哪份 node→脚本 生成的"——merge_digests 据此拦"map 改了、批还是旧的"
    snap = {str(e["node_id"]): Path(str(e.get("script_local") or e.get("script") or "")).name
            for e in aligned if e.get("script_local")}
    # 快照变了 → 旧消化结果整体作废(改名 .stale,可救回):旧批按旧 map 生成,直接留着会被"重跑 align 刷新
    # manifest"洗白混进合并(实测:n28 换了脚本,旧批纪要照样验过)。分批组成会整体挪动,不做逐批甄别。
    mfp = ddir / "digest_manifest.json"
    old_snap = {}
    if mfp.exists():
        try:
            old_snap = json.loads(mfp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old_snap = {}
    rd = ddir / "digest_results"
    if old_snap and old_snap != snap and rd.is_dir():
        stale = sorted(rd.glob("batch*.json"))
        for f in stale:
            t = f.with_name(f.name + ".stale")
            if t.exists():
                t.unlink()
            f.rename(t)
        if stale:
            print(f"⚠ 分批口径变了(node→脚本对照与上次不同):旧消化结果 {len(stale)} 个已改名 .stale——"
                  "旧批按旧 map 生成,直接合并会喂错脚本;按新简报重新消化(确认无用后可删 .stale 文件)")
    mfp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    nb = build_digest_batches(aligned, nodes, out, a.digest_batch_size)

    print(f"对齐:{len(aligned)} 对(自动 {len(aligned) - len(manual)} + 手工保留 {len(manual)};"
          f"规则共 {len(rules)} 条、树上带判据节点共 "
          f"{sum(1 for n in nodes.values() if n['condition'])} 个)→ {ddir / 'adoption_map.json'}")
    if manual:
        print(f"  (手工条目 {len(manual)} 条已保留、排在文件**末尾**;以本行统计为准,别再读文件肉眼核对。"
              "改过 adoption_map 后**必须重跑本脚本重新分批**再消化——消化批内容按 map 生成,map 变批不变=喂错脚本)")
    if multi:
        print(f"ℹ 同节点配了多个脚本 {len(multi)} 处(合法;消化后 merge 会合并纪要):"
              + "; ".join(f"{k}←{v}" for k, v in list(multi.items())[:5]))
    if missing:
        print(f"⚠ 脚本文件缺失 {len(missing)} 个(规则有、脚本目录里没有):")
        for m in missing[:10]:
            print(f"  - {m}")
    if ambiguous:
        print(f"⚠ 脚本文件多义 {len(ambiguous)} 个(同名文件多处,父目录也消不了歧):")
        for m in ambiguous[:10]:
            print(f"  - {m}")
    if un_rules:
        print(f"⚠ 未对齐的规则 {len(un_rules)} 条(nameZh 归一后配不上树节点)→ unmatched_rules.json;"
              "逐条人工/模型裁决:确属某节点的,把条目补进 adoption_map.json(auto:false + mapping_reason)")
    if un_nodes:
        print(f"⚠ 树上没配到脚本的判据节点 {len(un_nodes)} 个 → unmatched_nodes.json"
              "(这些节点走老路:凭判据绑定,没纪要)")
    print(f"消化任务:{nb} 批 → {ddir}/digest_task_batch<N>.txt;逐批派子代理(一次最多 5 个),"
          f"结果收 {ddir}/digest_results/batch<N>.json,收齐后跑 merge_digests.py 校验合并。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
