#!/usr/bin/env python3
"""把 cs-scot-generate 的中间产物归集成一个场景的诊断产物(资产,不是 skill)。

产物 = plan.json(真正的 SCoT 计划)+ manifest.json(格式契约铭牌)
       + scripts/<指标>_analyzer.py(逐条件分析骨架,模型在步骤 2.5 填 CONDITIONS)
       + 内核/适配器/概览引擎/绑定契约副本 + tree/<场景>.md。由 cs-scot-execute 来跑。
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sys
from pathlib import Path
from typing import List

# 与 excel_to_tree.py 保持一致的统一产物目录约定:<anchor>/scot产物/<场景名>
DEFAULT_OUT_CONTAINER = "scot产物"

# 外挂知识模板:首次归集时写入 tree/knowledge.md(已存在**永不覆盖**——它是业务同事手写的)。
# 两条示例是真实场景(M-LAG)的可用知识,照这个样子一条一条往下加即可。
KNOWLEDGE_TEMPLATE = """\
# 外挂知识(knowledge.md)——业务同事直接编辑本文件

一条知识 = 一段,固定结构,照下面的样子往后抄:

    ### 知识标题(一句话,名词短语)
    阶段:绑定 或 诊断(可选;不写 = 两个阶段都生效)
    适用:分支/场景关键词(可多个,用、分隔;写「通用」或不写这行 = 全场景生效)
    正文(人话,写清:去哪个数据、看哪一列、怎么查、什么算正常/不算故障)

说明:
- 「阶段」管**给谁看**:绑定=只进生成期的绑定简报(教怎么写绑定的手艺知识);诊断=只随 RCA 输入下发
  (教怎么解读事实的口径知识);不写=两边都载。别把绑定手艺灌进诊断白占上下文;
- 「适用」的关键词按**故障树节点名**匹配——只载入与当前分支相关的条目,不会全量灌;
- 知识是**口径不是事实**:它教系统"去哪查、怎么解读",查证结论仍以数据为准;
- **样例数据没出现过的告警/事件也能写知识**:绑定按知识落点照绑,样例里没有 ≠ 查不了
  (运行时没出现=不成立,真实数据出现即命中);
- 下面三条是真实示例(M-LAG 场景 2 条 + 告警类 1 条),可直接保留、修改或删除。

### M-LAG场景对端查询方法
适用:M-LAG
在 diag 数据中,筛选「DataItem」列为「MLAG_NodeId」的数据,当结果为两条时即可查询到本端与对端的
数据,少于两条即无法查询对端。其中「Object」列里的内容带*号的为本端,不带*的为对端。其余设备具体
信息通常从「ObjContent」列里获取。

### M-LAG场景本端对端信息使用方法
阶段:诊断
适用:M-LAG
通常使用本端对端信息的名字,即「NENAME」列,在其他数据源中查询有关该网元的相关信息。

### hwIfMonitorSymbolErrorRising(Symbol错包)告警的查法
阶段:绑定
适用:错包、Symbol、端口链路
该告警落在 log:Summary 列为裸码 hwIfMonitorSymbolErrorRising(_active=告警产生、_clear=告警清除,
后缀在 Log 列原始行里,Summary 列不带)。绑定写 log 源、Summary 列 contains 裸告警名。
参数 hwIfMonitorName 是产生告警的接口。本份样例数据没有该告警不代表查不了——照绑,
运行时没出现=不成立。分析口径:该日志表示接口 [hwIfMonitorName] 存在错包,原因通常是链路质量问题。
"""

# 产物格式契约版本(写进出厂铭牌 manifest.json):**只在改动使旧产物与新执行器不兼容时才 +1**
# (修 bug、改文案不动它)。bump 时同步 bump cs-scot-execute/scripts/runner.py 的 EXPECTED_PRODUCT_FORMAT。
# v2:绑定新增 filter 字段(EAV 行级预过滤)+ 空表守卫——旧产物里的旧 condition_kernels 副本会
#     **静默忽略 filter、在全表上算出假阳**,必须重新 assemble 刷新内核副本。
# v3:plan 新增 cross 节点(inputs.data="$input.*",跨源判据脚本要**全部**数据源)——
#     旧执行器解析不了 "$input.*" 会当场报错,须升级 cs-scot-execute。
PRODUCT_FORMAT_VERSION = 3

# 每个指标一份"逐条件分析脚本"的骨架:模型在 generate 时按类别模板(A/C/G/D/J/P)填 CONDITIONS。
# 默认直接套预制内核(evaluate_binding);模板不够用时,模型才为个别条件写 custom。
# execute 把它当普通脚本跑:python <ind>_analyzer.py <数据> -o out.json,直接吐该源逐条件事实。
ANALYZER_SCAFFOLD = '''#!/usr/bin/env python3
"""生成的「__IND__」逐条件分析脚本——读表 → 逐条件统计 → 输出事实。

generate 时由模型按类别模板(A/C/G/D/J/P)填 CONDITIONS;execute 当普通脚本跑(快):
    python __IND___analyzer.py <数据文件> -o out.json
默认直接套预制内核;仅当数据格式特殊、模板不够用时,模型才为个别条件写 custom 函数
(会在 generate 校验时拿真实数据真跑、出错即暴露)。绑定字段与各类参数见同目录 condition_binding_schema.md。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from condition_kernels import load_table, evaluate_binding  # 预制模板库(A/C/G/D/J/P 内核)

INDICATOR = "__IND__"

# ===== 模型填这里:本场景路由到「__IND__」源的条件 =====
# 每条 = 公共字段(id/node/node_id/condition_text/class/mapping_reason)+ 该类内核参数(见 schema)。
# node_id = tree/<场景>.tree_index.json 里该节点的 **key(形如 nN,短、推荐)** 或其 `code` 字段(两者都认)。
# 必填:RCA 靠它把事实挂回树做路径回溯。**别填节点名、别瞎拼。**
# 默认直接套模板;模板不够用时给该条加 "mode":"custom",并在 CUSTOM 里按 id 写返回 fact 的函数。
#
# 【把"Att-Succ>0 / 有没有失败"绑到现成成功率列前,两条硬规则必须都过】
#  (1) 同流程:计数器流程段必须和列名里的流程词**逐字对上**——
#      N.UECntx<->UEContext率、N.RRC<->RRC率、N.NGSig<->NgSig率、N.QosFlow.Est<->QosFlow率。
#      流程对不上(如 UEContext建立失败 配 SA_RRC率)= 代理,禁。
#      注意 UEContext 常只有 *Drop Rate*(掉线!=建立)→ UEContext建立类一律 decision:insufficient_data。
#  (2) 非子因:计数器含 .FailEst.<原因> / .Fail<原因>(如 N.QosFlow.FailEst.RNL)= 特定失败子因,
#      成功率分不出子因 → 永不可用率重述;改用 C 类查错误码字段,或 decision:insufficient_data。
#  (3) 别丢区分量:树条件是"A 且 B"(如 DCErrType=RNL_Unspecified **且** RSRP<-120)、本源只能查 A →
#      别绑裸 A(丢了 B、会恒成立、几个不同节点一起命中);要么 D 类组合(把 B 一起绑)、要么 insufficient。
#  (4) 高频通用值≠特定子因(通用尺子):某分类列里一个取值占比畸高(≥90%/近乎恒在、正常数据里也是它),
#      拿它去坐实某个特定子因/信号就是代理(在正常样本上也恒为真=不分故障);对不上专属取值、或本源不含该信号 → decision:insufficient_data。
# decision:insufficient_data 时**不要再填 class**(尤其别填 J;J 是"人工确认 manual",不是数据不足)。
CONDITIONS = [
    # {"id": "第N行", "node": "RRC建立失败", "node_id": "n12", "condition_text": "<逐字树条件>",
    #  "class": "A", "column": "SA_RRC Setup Success Rate", "op": "<", "threshold": 100, "scope": "aggregate", "agg": "mean",
    #  "mapping_reason": "同流程重述:RRC建立失败<->RRC成功率<100(或:为何数据不足)"},
]

# 仅当模板不够用时:按条件 id 写自定义统计,返回与内核同形的 fact(含 status / predicate_satisfied 等)。
CUSTOM = {}  # 形如 {"第N行": lambda table: {...}}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=f"{INDICATOR} 逐条件分析(读表→统计)")
    ap.add_argument("data")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--indicator", default=INDICATOR)
    args = ap.parse_args()
    # 多页签 xlsx:条件用 source=<页签名> 指到某页签;不指=活动页(键 INDICATOR)。
    # 懒加载:用到哪个页签才读哪个;decision/J 不读数据 → 满屏"数据不足"的脚本几乎零 IO。
    sources = {}

    def _table(name):
        key = name or INDICATOR
        if key not in sources:
            try:
                sources[key] = load_table(args.data, sheet=(None if key == INDICATOR else key))
            except Exception:                 # 页签不存在/读取失败 → 空表,该条自然数据不足,不崩
                sources[key] = ([], [])
        return sources[key]

    conds = []
    for c in CONDITIONS:
        cls = str(c.get("class", "")).upper()
        if c.get("decision") in ("insufficient_data", "manual", "not_applicable") or cls == "J":
            fact = evaluate_binding(c, {}, c.get("source", INDICATOR))      # 不需数据,直接出
        elif c.get("mode") == "custom" and c.get("id") in CUSTOM:
            cf = CUSTOM[c["id"]]
            import inspect
            try:
                _n = len(inspect.signature(cf).parameters)
            except (ValueError, TypeError):
                _n = 1
            # 1 参:custom(table)(老约定);≥2 参:custom(table, load),load(<源/MO类型>) 可再取别的源(跨 CHR+配置)
            fact = cf(_table(c.get("source")), _table) if _n >= 2 else cf(_table(c.get("source")))
        else:
            _table(c.get("source"))                                        # 按需加载本条页签
            for sub in (c.get("sub") or []):
                _table(sub.get("source"))                                  # 及组合子条件页签
            fact = evaluate_binding(c, sources, c.get("source", INDICATOR))
        meta = {k: c[k] for k in ("id", "node", "node_id", "condition_text", "class", "mapping_reason") if k in c}
        conds.append({**meta, "fact": fact})
    ok = sum(1 for x in conds if x["fact"].get("status") == "ok")
    insuf = sum(1 for x in conds if x["fact"].get("status") == "insufficient_data")
    manual = sum(1 for x in conds if x["fact"].get("status") == "manual")
    result = {"indicator": args.indicator, "source": INDICATOR,
              "summary": {"total": len(conds), "ok": ok, "insufficient_data": insuf, "manual": manual},
              "conditions": conds}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{args.indicator}_analyzer] {len(conds)} 条件 (ok={ok} insuf={insuf} manual={manual}) -> {out}")


if __name__ == "__main__":
    main()
'''


# 跨源判据分析脚本骨架:需要**多个源**才能判的条件(param_from 传参链 / P pair_from 跨源比较 /
# 跨源 D 组合)都放这里,由步骤 2.5 的「跨源补绑」环节填(make_dispatch --cross 产简报)。
# 执行时 runner 把本轮全部 --data 源都递进来;没有跨源判据时 CONDITIONS 为空、秒过。
CROSS_SCAFFOLD = '''#!/usr/bin/env python3
"""生成的「cross」跨源判据分析脚本——一条判据要动用多个数据源时的家。

execute 当普通脚本跑(runner 自动把全部数据源递进来):
    python cross_analyzer.py --data cmd=<文件> --data config=<文件> ... -o out.json
绑定写法见同目录 condition_binding_schema.md 的「参数化查询(param_from)」与 P 类 pair_from。
S 类(代码类)补充:--data raw=<原始数据目录>(如 zip2csv 输出的 extracted/)是可选通道,
S 脚本经 load("raw_dir") 拿到该路径直接读原始 txt;没提供也能跑(custom_lib 会从三表兜底重建)。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from condition_kernels import load_table, evaluate_binding, alias_sources

INDICATOR = "cross"

# ===== 跨源补绑环节填这里:每条必带 "source"=最终查询落点的源名 =====
# param_from/pair_from/sub 里的 source 各写各的源名;本轮没提供的源,内核自动报数据不足。
CONDITIONS = [
]

CUSTOM = {}  # 最后手段:param_from 多跳都表达不了的,按 id 写返回 fact 的函数


def _referenced_sources(c):
    """收集一条绑定引用到的所有源名(自身 source + 嵌套 param_from/pair_from/sub 里的 source)。"""
    out = set()

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("source"), str):
                out.add(o["source"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(c)
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="cross 跨源判据分析(多源→逐条件统计)")
    ap.add_argument("--data", action="append", default=[], help="源名=文件,可多次")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--indicator", default=INDICATOR)
    args = ap.parse_args()
    paths = {}
    for item in args.data:
        if "=" not in item:
            print(f"--data 格式应为 源名=文件: {item}", file=sys.stderr)
            sys.exit(2)
        k, v = item.split("=", 1)
        paths[k.strip()] = v.strip()
    # 「raw」是保留通道:原始数据**目录**(zip2csv 的 extracted/),不是表,不进 load_table;
    # 单独摘出,以 raw_dir 键挂进 sources,S 脚本用 load("raw_dir") 拿路径。
    raw_dir = paths.pop("raw", None) or paths.pop("raw_dir", None)
    if paths.get("cmd") and paths.get("cmd") == paths.get("diag"):
        paths.pop("cmd")                                 # 同一文件挂了两代源名(runner 兼容补挂):只读一份,别名回填
    # 懒加载:只读 CONDITIONS 真正引用到的源(cmd 这类大 CSV 不白读);没提供的源不造空表,
    # 让内核报「无数据源」而不是把"没喂"错当"空表"。
    # 例外:S 类(代码类)拿的是全部源(load 想取哪张取哪张,不靠声明)→ 有 S 条目就全量加载。
    load_all = any(str(c.get("class", "")).upper() == "S" and not c.get("decision")
                   for c in CONDITIONS)
    sources = {}
    for c in CONDITIONS:
        if c.get("decision") in ("insufficient_data", "manual", "not_applicable"):
            continue
        for s in (paths if load_all else _referenced_sources(c)):
            if s in paths and s not in sources:
                try:
                    sources[s] = load_table(paths[s])
                except Exception:                        # 读坏了当空表:该条自然数据不足,不崩整脚本
                    sources[s] = ([], [])
    alias_sources(sources)                               # cmd/diag 两代源名互认(同一张表,不复制)
    if raw_dir:
        sources["raw_dir"] = raw_dir
    conds = []
    for c in CONDITIONS:
        if c.get("mode") == "custom" and c.get("id") in CUSTOM:
            fact = CUSTOM[c["id"]](sources)
        else:
            fact = evaluate_binding(c, sources, c.get("source", INDICATOR))
        meta = {k: c[k] for k in ("id", "node", "node_id", "condition_text", "class", "mapping_reason") if k in c}
        conds.append({**meta, "fact": fact})
    ok = sum(1 for x in conds if x["fact"].get("status") == "ok")
    insuf = sum(1 for x in conds if x["fact"].get("status") == "insufficient_data")
    manual = sum(1 for x in conds if x["fact"].get("status") == "manual")
    result = {"indicator": args.indicator, "source": INDICATOR,
              "summary": {"total": len(conds), "ok": ok, "insufficient_data": insuf, "manual": manual},
              "conditions": conds}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[cross_analyzer] {len(conds)} 条件 (ok={ok} insuf={insuf} manual={manual}) -> {out}")


if __name__ == "__main__":
    main()
'''


def _extract_block(src: str, name: str):
    """顶层 `NAME = ...` 赋值块的原文;找不到/语法错 → None。(cross 骨架升级手术用)"""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                                for t in node.targets):
            lines = src.splitlines(keepends=True)
            return "".join(lines[node.lineno - 1: node.end_lineno])
    return None


def _upgrade_cross_scaffold(existing: str, pristine: str):
    """已填绑定的 cross_analyzer.py 骨架升级:引擎部分换当前版,CONDITIONS/CUSTOM 赋值块**原文保留**。
    返回 (结果, 原因):结果=新文本(需写盘)/ existing(引擎已是当前版,不动)/ None(结构超出骨架
    认知——如手写了辅助函数——不敢动,附原因;绑定安全第一,拿不准就不碰)。"""
    import ast
    try:
        et, pt = ast.parse(existing), ast.parse(pristine)
    except SyntaxError as e:
        return None, f"解析失败:{e}"

    def _names(tree):
        out = set()
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)
            elif isinstance(n, ast.Assign):
                out.update(t.id for t in n.targets if isinstance(t, ast.Name))
        return out
    extra = _names(et) - _names(pt)
    if extra:
        return None, f"文件里有骨架之外的顶层定义 {sorted(extra)}(可能是手写的 CUSTOM 辅助函数)"
    out = pristine
    for key in ("CONDITIONS", "CUSTOM"):
        old_blk, new_blk = _extract_block(existing, key), _extract_block(out, key)
        if old_blk is None or new_blk is None:
            return None, f"定位不到 {key} 赋值块"
        out = out.replace(new_blk, old_blk, 1)
    if out == existing:
        return existing, ""                              # 引擎部分一字不差 → 已是当前版
    return out, ""


def scenario_out_dir(anchor_dir: Path, scenario: str) -> Path:
    """统一的场景产物目录:<anchor_dir>/scot产物/<场景名>。"""
    return Path(anchor_dir) / DEFAULT_OUT_CONTAINER / scenario


def build_plan(scenario: str, indicators: List[str], tree_rel: str, stem: str = "") -> dict:
    """生成一份符合 SCoT plan schema 的直线计划。

    - 每个指标 → 一个 kind=script 节点(跑**生成的**逐条件分析脚本 <ind>_analyzer.py,
      读表→逐条件统计,产出该源的事实存入 $memory.<指标>_result);脚本调用快;
    - 末尾 → 一个 kind=agent_skill 节点(fault-tree-rca:唯一的模型步骤,慢);
    - 节点用 transition.next_on_success 串成直线,引用用 $input/$memory/$output。
    execute 只负责按序跑脚本 + 末尾 RCA;统计已在脚本里算完,execute 不再加工数据。
    """
    nodes = []
    for i, ind in enumerate(indicators):
        nxt = indicators[i + 1] if i + 1 < len(indicators) else "cross"
        nodes.append({
            "id": ind,
            "kind": "script",
            "name": f"{ind}_analyzer",
            "purpose": f"读 {ind} 数据,逐条件统计出事实(条件已在生成脚本里按类别模板算好)",
            "inputs": {"data": f"$input.{ind}"},
            "outputs": {"result": f"$memory.{ind}_result"},
            "transition": {"next_on_success": nxt},
        })
    nodes.append({                                       # 跨源判据(param_from/pair_from):要全部源;没跨源绑定时秒过
        "id": "cross",
        "kind": "script",
        "name": "cross_analyzer",
        "purpose": "跨源判据统计(一条判据动用多个数据源:传参链/两侧比较);CONDITIONS 为空时秒过",
        "inputs": {"data": "$input.*"},
        "outputs": {"result": "$memory.cross_result"},
        "transition": {"next_on_success": "rca"},
    })
    nodes.append({
        "id": "rca",
        "kind": "agent_skill",
        "name": "fault-tree-rca",
        "purpose": "综合根因分析(消费各源逐条件事实:字面命中只是输入,最终成立/不成立/数据不足由你裁决、并合跨源组合)",
        "inputs": {"fault_tree_md": "$memory.fault_tree_md",
                   **{ind: f"$memory.{ind}_result" for ind in indicators},
                   "cross": "$memory.cross_result"},
        "outputs": {"result": "$output.final_answer"},
        "transition": {"end": True},
    })
    return {
        "version": "4.0",
        "plan_id": f"plan_{stem or scenario}",
        "meta": {
            "goal": f"诊断「{scenario}」的根因",
            "success_criteria": ["输出所有成立的根因路径与逐条判断依据"],
        },
        "entry": indicators[0] if indicators else "cross",
        "memory_init": {"fault_tree_md": tree_rel},
        "nodes": nodes,
    }


def copy_into(src: Path, dst: Path) -> None:
    """拷贝单个文件,缺失则告警跳过(不阻断);源与目标同一文件则免拷(work==out 的常态)。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        print(f"  [warn] 缺少源文件,跳过: {src}", file=sys.stderr)
        return
    if src.resolve() == dst.resolve():
        return  # 已在目标位置,无需拷贝
    shutil.copy2(src, dst)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="归集场景诊断产物")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--indicators", required=True, help="逗号分隔,如 kpi,chr")
    ap.add_argument("--out", help="场景产物目录(中间产物也在这里;默认 = <out-base 或 xlsx 所在目录>/scot产物/<场景名>)")
    ap.add_argument("--out-base", help="产物根目录;与 --out 二选一,默认配合 --xlsx 推导")
    ap.add_argument("--xlsx", help="故障树 xlsx 路径;省略 --out 时用它所在目录作为产物根")
    ap.add_argument("--work", help="中间产物目录;默认与 --out 相同(work 与 out 合并为同一目录)")
    ap.add_argument("--keep", action="store_true",
                    help="保留已存在的 <ind>_analyzer.py(即便已填/是旧版),不刷新骨架;"
                         "默认:重跑会用当前空骨架覆盖、旧脚本备份为 .py.bak,以便用上新拷进来的 kernels/mo_config")
    args = ap.parse_args()

    scenario = args.scenario
    indicators = [s.strip() for s in args.indicators.split(",") if s.strip()]
    if "cross" in indicators:
        print("不合格:「cross」是保留源名(跨源判据脚本 cross_analyzer.py 自动生成)——给数据源换个名字。",
              file=sys.stderr)
        sys.exit(1)

    # 统一产物目录:--out 优先;否则按 <out-base 或 xlsx 所在目录>/scot产物/<场景> 推导(与步骤1一致)。
    if args.out:
        out = Path(args.out)
    else:
        if args.out_base:
            anchor: Path = Path(args.out_base).expanduser()
        elif args.xlsx:
            anchor = Path(args.xlsx).expanduser().resolve().parent
        else:
            print("需要 --out,或用 --xlsx/--out-base 让我推导默认产物目录", file=sys.stderr)
            sys.exit(2)
        out = scenario_out_dir(anchor, scenario)
        # 扫容器对账(**无条件**,不能只在推导目录缺失时扫):步骤1目录名可能是英文短名(slug)
        # 而 --scenario 输的是中文名;更危险的是**新旧两代产物并存**(升级后旧中文名目录没删)——
        # 若只在"推导目录不存在"时才扫,旧中文名目录恰好存在就会被静默选中、归集出过期的树。
        # 按 tree_index 内容认领:恰一个 → 用它;多个 → 硬拦;零个 → 沿用推导目录(下游闸门给指引)。
        hits = []
        for ti in sorted(list((Path(anchor) / DEFAULT_OUT_CONTAINER).glob("*/tree/*.tree_index.json"))
                         + list((Path(anchor) / "scot技能").glob("*/assets/tree/*.tree_index.json"))):
            # 两个容器都扫:老形态 scot产物/<场景>/,新默认 scot技能/<短名>/assets/(步骤1直出技能)
            try:
                d = json.loads(ti.read_text(encoding="utf-8"))
            except Exception:
                continue
            if scenario in (d.get("scenario"), d.get("slug")):
                hits.append(ti.parent.parent)
        if len(hits) == 1:
            out = hits[0]
        elif len(hits) > 1:
            print(f"歧义:多个产物目录都声称场景「{scenario}」:{[str(h) for h in hits]}\n"
                  "  多半是新旧两代产物并存(如旧中文名目录没删)——删掉过期目录再归集,"
                  "或用 --out 明确指定。静默选错的代价是归集出过期的树,所以这里必须停。",
                  file=sys.stderr)
            sys.exit(2)
    work = Path(args.work) if args.work else out  # 默认 work==out,产物只此一处

    # 硬闸门:0 判据的树不许归集——判据是整条流水线的原料,0 判据多半是步骤1格式识别问题
    # (判据列没被认出被静默丢弃)。此时继续会诱发"模型自由发挥硬绑/写脚本量产绑定"的事故,
    # 必须在这里停下、回去查 Excel 表头,而不是往下走。
    # 定位步骤1产物:目录内唯一的 *.tree_index.json(文件名=英文短名或中文场景名,以内容为准)。
    tis = sorted((work / "tree").glob("*.tree_index.json")) if (work / "tree").is_dir() else []
    if not tis:                                          # 步骤1产物缺失/错位:比 0 判据更糟,同样硬拦
        print(f"不合格:{work / 'tree'} 下找不到步骤1产物 *.tree_index.json。\n"
              "  排查:① 步骤1(excel_to_tree*)跑过了吗;② --scenario 与步骤1的 --start-node 名字\n"
              "  完全一致吗(含空格/括号全半角);③ --out/--work 是否指向步骤1的产物目录。\n"
              "  先把步骤1产物放对,再归集——没有树的产物只会诱发下游自由发挥。", file=sys.stderr)
        sys.exit(1)
    if len(tis) > 1:
        print(f"歧义:{work / 'tree'} 下有多份 tree_index:{[t.name for t in tis]}"
              "——一个场景目录只放一棵树,清掉多余的再归集。", file=sys.stderr)
        sys.exit(2)
    ti_path = tis[0]
    stem = ti_path.name[: -len(".tree_index.json")]      # 文件短名(slug 或中文场景名),树文件同名
    tree_rel = f"tree/{stem}.md"
    try:
        _ti = json.loads(ti_path.read_text(encoding="utf-8"))
        _n_cond = sum(1 for nd in (_ti.get("nodes") or {}).values() if nd.get("condition"))
    except Exception as e:  # noqa: BLE001
        print(f"tree_index 读取失败({e}),无法核验判据数——先修复步骤1产物再归集。", file=sys.stderr)
        sys.exit(1)
    if scenario not in (_ti.get("scenario"), _ti.get("slug")):
        print(f"不合格:--scenario「{scenario}」与产物不符(index.scenario=「{_ti.get('scenario')}」,"
              f" slug={_ti.get('slug')!r})——名字打错或目录指错,核对后再归集。", file=sys.stderr)
        sys.exit(1)
    if _n_cond == 0:
        print(f"不合格:tree_index 里 {len(_ti.get('nodes') or {})} 个节点**一条判据都没有**。\n"
              "  多半是步骤1没认出判据列(如混血表的 Judgment Condition 单列/自定义列名)——\n"
              "  升级 excel_to_tree_v2.py 重跑步骤1,确认输出里「带判据节点 N>0」再归集。\n"
              "  **不要**继续绑定、更不要自己读 Excel 写脚本量产绑定(那会产出无法审计的假绑定)。",
              file=sys.stderr)
        sys.exit(1)

    for sub in ("scripts", "tree"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    copy_into(work / "tree" / f"{stem}.md", out / "tree" / f"{stem}.md")
    # 推荐解决方案表(可选,RCA 报根因时点查):树侧为准、**增量并入**——out 侧可能有 cs-scot-adopt
    # 从平台脚本并入的方案,整表覆盖/删除都会把收编成果静默清掉(实测审计发现);
    # 树上已不存在的节点条目留着无害(点查不中)。
    wsol, osol = work / "tree" / "solutions.json", out / "tree" / "solutions.json"
    if wsol.exists():
        merged_sol = json.loads(wsol.read_text(encoding="utf-8"))
        kept = 0
        if osol.exists():
            try:
                for _nid, _v in json.loads(osol.read_text(encoding="utf-8")).items():
                    if _nid not in merged_sol:
                        merged_sol[_nid] = _v
                        kept += 1
            except json.JSONDecodeError:
                print("[warn] out 侧 solutions.json 解析失败,按树侧全量重写")
        osol.write_text(json.dumps(merged_sol, ensure_ascii=False, indent=2), encoding="utf-8")
        if kept:
            print(f"solutions.json:树侧 {len(merged_sol) - kept} 条 + 保留 out 侧既有 {kept} 条(如 adopt 并入的方案)")
    elif osol.exists():
        print("solutions.json:树侧本次没有,out 侧既有的**保留不删**(可能含 adopt 并入的方案)")
    # 拷预制模板库 + 概览引擎 + 绑定契约进产物;再为每个指标写一份待填的"逐条件分析脚本"骨架。
    here = Path(__file__).parent
    copy_into(here / "condition_kernels.py", out / "scripts" / "condition_kernels.py")  # 类别模板库
    copy_into(here / "xlsx_reader.py", out / "scripts" / "xlsx_reader.py")              # 零依赖 xlsx 读取器(内核/导入都靠它读表)
    copy_into(here / "custom_lib.py", out / "scripts" / "custom_lib.py")                # S 类预制函数库(人手调优用)
    copy_into(here / "mo_config.py", out / "scripts" / "mo_config.py")                  # 配置拓扑适配器:MO文本→表 + IP/追溯原语(.txt 配置 + custom 用)
    copy_into(here / "analyzer.py", out / "scripts" / "analyzer.py")                    # 概览辅助:模型先看列再填条件
    copy_into(here.parent / "references" / "condition_binding_schema.md",
              out / "scripts" / "condition_binding_schema.md")                          # 绑定契约
    copy_into(here / "export_tree_excel.py", out / "scripts" / "export_tree_excel.py")  # 调优表:产物→Excel
    copy_into(here / "import_tree_excel.py", out / "scripts" / "import_tree_excel.py")  # 调优表:Excel→产物(自包含)
    copy_into(here.parent / "references" / "tuning_excel_guide.md",
              out / "tree" / "tuning-guide.md")                                         # 调优表动手说明(业务读者)
    copy_into(here.parent / "references" / "binding_handbook.md",
              out / "tree" / "binding-handbook.md")                                     # 绑定手册(人手改绑定 JSON 时带例子查)
    kn = out / "tree" / "knowledge.md"                   # 外挂知识:人手写的"去哪查/怎么查/什么算正常"
    if not kn.exists():                                  # 手写文件——重跑**永不覆盖**(现场增补不丢)
        skill_kn = here.parent / "knowledge.md"          # 技能级出厂知识库(与 SKILL.md 平级,生成前预置)
        if skill_kn.exists():
            copy_into(skill_kn, kn)
            print(f"已拷入技能级出厂知识 → {kn}(来源 {skill_kn};绑定简报与 RCA 输入按「适用」标签自动载入)")
        else:
            kn.write_text(KNOWLEDGE_TEMPLATE, encoding="utf-8")
            print(f"已建外挂知识模板 → {kn}(业务同事可编辑;绑定简报与 RCA 输入会按分支自动载入)")
    # 重跑 = 重新编译:默认用当前版本的空骨架覆盖旧脚本(这样才能用上刚拷进来的新 condition_kernels/mo_config);
    # 覆盖前若旧脚本不是"全新空骨架"(填过/手改过/上一版骨架)→ 先备份成 .py.bak,绝不静默丢失。
    # 想原样保留已填脚本:加 --keep(老的 skip-if-exists 行为)。
    scaffolded, regenerated = [], []
    for ind in indicators + ["cross"]:                   # cross = 跨源判据脚本(补绑环节填),同一套备份规矩
        target = out / "scripts" / f"{ind}_analyzer.py"
        pristine = (CROSS_SCAFFOLD if ind == "cross" else ANALYZER_SCAFFOLD.replace("__IND__", ind))
        if not target.exists():
            target.write_text(pristine, encoding="utf-8")
            scaffolded.append(ind)
            continue
        try:
            existing = target.read_text(encoding="utf-8")
        except Exception:
            existing = None
        if existing == pristine:
            continue                                    # 还是全新空骨架,没填过 → 无需动
        if ind == "cross":                              # 已填过的跨源绑定是补绑环节的手工产物,重跑 assemble
            upgraded, why = _upgrade_cross_scaffold(existing, pristine)
            if upgraded is not None and upgraded != existing:
                bakc = target.with_name("cross_analyzer.py.bak")
                try:
                    shutil.copy2(target, bakc)
                except Exception:
                    pass
                target.write_text(upgraded, encoding="utf-8")
                print(f"  [upgrade] cross_analyzer.py 骨架已升级到当前版本,**已填绑定原样保留**"
                      f"(旧文件备份 → {bakc.name})")
            elif upgraded is None:
                print(f"  [keep] 保留已填的跨源绑定(cross_analyzer.py 骨架无法自动升级:{why};"
                      f"绑定不动。若需 S 类/raw 通道等新能力,先手工迁出 CONDITIONS 再删文件重铺): {target}")
            else:
                print(f"  [keep] 保留已填的跨源绑定(cross_analyzer.py 骨架已是当前版本,不重铺): {target}")
            continue                                    # 绑定绝不清空——此前重铺会把 param_from 链静默打回空骨架
        if args.keep:
            print(f"  [keep] 保留已存在脚本(--keep,未刷新骨架/库依赖): {target}")
            continue
        bak = target.with_name(f"{ind}_analyzer.py.bak")
        try:
            shutil.copy2(target, bak)                    # 旧脚本(可能含模型填的 CONDITIONS)留个底
            note = f"  (旧脚本已备份 → {bak.name})"
        except Exception:
            note = "  (备份失败,已直接覆盖)"
        target.write_text(pristine, encoding="utf-8")   # 铺当前版本空骨架,模型在步骤2.5重新填
        regenerated.append(ind)
        print(f"  [regen] 重铺空骨架: {target}{note}")

    plan = build_plan(scenario, indicators, tree_rel, stem)
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    # 出厂铭牌:这批产物由哪版格式契约、何时编译。execute/benchmark 启动时比对,旧产物只告警不阻断。
    manifest = {"product_format_version": PRODUCT_FORMAT_VERSION,
                "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "scenario": scenario, "slug": _ti.get("slug"),
                "indicators": indicators, "tree": tree_rel}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已归集场景产物: {out}")
    print(f"  plan.json(SCoT v4.0)+ manifest.json(产物铭牌,格式契约 v{PRODUCT_FORMAT_VERSION})"
          f"+ scripts/condition_kernels.py(类别模板库)"
          f"+ scripts/<指标>_analyzer.py(待填骨架)+ tree/{stem}.md")
    to_fill = scaffolded + regenerated
    if to_fill:
        if regenerated:
            print(f"  (重跑重铺了 {regenerated},旧脚本各留了 .py.bak;它们现在是空骨架,需重新填 CONDITIONS)")
        fill_src = [i for i in to_fill if i != "cross"]
        print(f"\n下一步(generate 的关键一步):为 {fill_src} 各自的 <指标>_analyzer.py 填 CONDITIONS——")
        print("  先用 analyzer.py 看各源真实列;再按 condition_binding_schema.md,把故障树每条判据")
        print("  路由到对应源、选 A/C/G/D/J/P 内核、对哪列(裸名即可,组前缀自动剥)、给阈值/变换;对不上就标数据不足。")
        print("  默认直接套模板;模板不够用才为个别条件写 custom。填好后用真实数据各跑一遍校验:")
        for ind in fill_src:
            print(f"    python {out}/scripts/{ind}_analyzer.py <{ind}数据> -o /tmp/{ind}.json && "
                  f"python {here}/validate_conditions.py /tmp/{ind}.json")
        if "cross" in to_fill:
            print("  cross_analyzer.py(跨源判据)不在这一步填:单源绑定收齐并 merge 后,若有 cross_source")
            print("  标记 → python make_dispatch.py --out \"<OUT>\" --cross 生成跨源补绑简报,补绑后再 merge。")
    run_hint = " ".join(f"--data {ind}=<文件>" for ind in indicators)
    print(f"\n填好后执行: python <cs-scot-execute>/scripts/runner.py --plan {out}/plan.json {run_hint}")


if __name__ == "__main__":
    main()
