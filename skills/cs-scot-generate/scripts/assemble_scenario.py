#!/usr/bin/env python3
"""把 cs-scot-generate 的中间产物归集成一个场景的诊断产物(资产,不是 skill)。

产物 = plan.json(真正的 SCoT 计划)+ scripts/<指标>_analyzer.py(自包含解析脚本)
       + tree/<场景>.md + codegen_prompts/(留痕)。由 cs-scot-execute 来跑。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import List

# 与 excel_to_tree.py 保持一致的统一产物目录约定:<anchor>/scot产物/<场景名>
DEFAULT_OUT_CONTAINER = "scot产物"

# 每个指标一份"逐条件分析脚本"的骨架:模型在 generate 时按 A–J 模板填 CONDITIONS。
# 默认直接套预制内核(evaluate_binding);模板不够用时,模型才为个别条件写 custom。
# execute 把它当普通脚本跑:python <ind>_analyzer.py <数据> -o out.json,直接吐该源逐条件事实。
ANALYZER_SCAFFOLD = '''#!/usr/bin/env python3
"""生成的「__IND__」逐条件分析脚本——读表 → 逐条件统计 → 输出事实。

generate 时由模型按 A–J 模板填 CONDITIONS;execute 当普通脚本跑(快):
    python __IND___analyzer.py <数据文件> -o out.json
默认直接套预制内核;仅当数据格式特殊、模板不够用时,模型才为个别条件写 custom 函数
(会在 generate 校验时拿真实数据真跑、出错即暴露)。绑定字段与 A–J 参数见同目录 condition_binding_schema.md。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from condition_kernels import load_table, evaluate_binding  # 预制模板库(A–J 内核)

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
            fact = CUSTOM[c["id"]](_table(c.get("source")))
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


def scenario_out_dir(anchor_dir: Path, scenario: str) -> Path:
    """统一的场景产物目录:<anchor_dir>/scot产物/<场景名>。"""
    return Path(anchor_dir) / DEFAULT_OUT_CONTAINER / scenario


def build_plan(scenario: str, indicators: List[str], tree_rel: str) -> dict:
    """生成一份符合 SCoT plan schema 的直线计划。

    - 每个指标 → 一个 kind=script 节点(跑**生成的**逐条件分析脚本 <ind>_analyzer.py,
      读表→逐条件统计,产出该源的事实存入 $memory.<指标>_result);脚本调用快;
    - 末尾 → 一个 kind=agent_skill 节点(fault-tree-rca:唯一的模型步骤,慢);
    - 节点用 transition.next_on_success 串成直线,引用用 $input/$memory/$output。
    execute 只负责按序跑脚本 + 末尾 RCA;统计已在脚本里算完,execute 不再加工数据。
    """
    nodes = []
    for i, ind in enumerate(indicators):
        nxt = indicators[i + 1] if i + 1 < len(indicators) else "rca"
        nodes.append({
            "id": ind,
            "kind": "script",
            "name": f"{ind}_analyzer",
            "purpose": f"读 {ind} 数据,逐条件统计出事实(条件已在生成脚本里按 A–J 模板算好)",
            "inputs": {"data": f"$input.{ind}"},
            "outputs": {"result": f"$memory.{ind}_result"},
            "transition": {"next_on_success": nxt},
        })
    nodes.append({
        "id": "rca",
        "kind": "agent_skill",
        "name": "fault-tree-rca",
        "purpose": "综合根因分析(消费各源逐条件事实:字面命中只是输入,最终成立/不成立/数据不足由你裁决、并合跨源组合)",
        "inputs": {"fault_tree_md": "$memory.fault_tree_md",
                   **{ind: f"$memory.{ind}_result" for ind in indicators}},
        "outputs": {"result": "$output.final_answer"},
        "transition": {"end": True},
    })
    return {
        "version": "4.0",
        "plan_id": f"plan_{scenario}",
        "meta": {
            "goal": f"诊断「{scenario}」的根因",
            "success_criteria": ["输出所有成立的根因路径与逐条判断依据"],
        },
        "entry": indicators[0] if indicators else "rca",
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
    args = ap.parse_args()

    scenario = args.scenario
    indicators = [s.strip() for s in args.indicators.split(",") if s.strip()]

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
    work = Path(args.work) if args.work else out  # 默认 work==out,产物只此一处
    tree_rel = f"tree/{scenario}.md"

    for sub in ("scripts", "tree"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    copy_into(work / "tree" / f"{scenario}.md", out / "tree" / f"{scenario}.md")
    # 拷预制模板库 + 概览引擎 + 绑定契约进产物;再为每个指标写一份待填的"逐条件分析脚本"骨架。
    here = Path(__file__).parent
    copy_into(here / "condition_kernels.py", out / "scripts" / "condition_kernels.py")  # A–J 模板库
    copy_into(here / "analyzer.py", out / "scripts" / "analyzer.py")                    # 概览辅助:模型先看列再填条件
    copy_into(here.parent / "references" / "condition_binding_schema.md",
              out / "scripts" / "condition_binding_schema.md")                          # 绑定契约
    scaffolded = []
    for ind in indicators:
        target = out / "scripts" / f"{ind}_analyzer.py"
        if target.exists():
            print(f"  [skip] 已存在(可能模型已填),不覆盖: {target}")
        else:
            target.write_text(ANALYZER_SCAFFOLD.replace("__IND__", ind), encoding="utf-8")
            scaffolded.append(ind)

    plan = build_plan(scenario, indicators, tree_rel)
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已归集场景产物: {out}")
    print(f"  plan.json(SCoT v4.0)+ scripts/condition_kernels.py(A–J 模板库)"
          f"+ scripts/<指标>_analyzer.py(待填骨架)+ tree/{scenario}.md")
    if scaffolded:
        print(f"\n下一步(generate 的关键一步):为 {scaffolded} 各自的 <指标>_analyzer.py 填 CONDITIONS——")
        print("  先用 analyzer.py 看各源真实列;再按 condition_binding_schema.md,把故障树每条判据")
        print("  路由到对应源、选 A–J 内核、对哪列(裸名即可,组前缀自动剥)、给阈值/变换;对不上就标数据不足。")
        print("  默认直接套模板;模板不够用才为个别条件写 custom。填好后用真实数据各跑一遍校验:")
        for ind in indicators:
            print(f"    python {out}/scripts/{ind}_analyzer.py <{ind}数据> -o /tmp/{ind}.json && "
                  f"python {here}/validate_conditions.py /tmp/{ind}.json")
    run_hint = " ".join(f"--data {ind}=<文件>" for ind in indicators)
    print(f"\n填好后执行: python <cs-scot-execute>/scripts/runner.py --plan {out}/plan.json {run_hint}")


if __name__ == "__main__":
    main()
