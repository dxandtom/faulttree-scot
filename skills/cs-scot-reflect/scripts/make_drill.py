#!/usr/bin/env python3
"""make_drill.py - 生成六类错误的演练场(自带迷你产物+用例+结果,离线跑通 评估→分拣 全流程)

用途:演示/验证飞轮的错误分类与回流处置,不需要真实场景技能与真实数据。
生成一个自包含工作区:迷你故障树产物 + 8 个用例(六类错误各一 + 孤儿绑定 + PASS 基线),
每个用例配一份"已执行完"的结果目录(run.json/rca_input.json/rca.json 按该类错误特征捏好)。

用法:
    python make_drill.py --out <演练目录>
然后按生成的 演练指引.md 逐个跑 eval_run(评估)与 route_feedback(分拣),对照期望结果。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TREE = {
    "scenario": "演练场景", "root": "n1",
    "nodes": {
        "n1": {"name": "总故障", "parent": None, "level": 1, "children": ["n3", "n9"],
               "condition": "", "pass_through": True},
        "n3": {"name": "会话down", "parent": "n1", "level": 2, "children": ["n6", "n7"],
               "condition": "会话down判据", "data_source": "log"},
        "n6": {"name": "接口DOWN", "parent": "n3", "level": 3, "children": [],
               "condition": "OperStatus=DOWN", "data_source": "log"},
        "n7": {"name": "链路震荡", "parent": "n3", "level": 3, "children": [],
               "condition": "PORTPHYSICALDOWN", "data_source": "log"},
        "n9": {"name": "清绑定节点", "parent": "n1", "level": 2, "children": [],
               "condition": "某判据(绑定已被人清空)", "data_source": "config"},
    }
}
ANALYZER = ('CONDITIONS = [\n    {"node_id": "n3"},\n    {"node_id": "n6"},\n'
            '    {"node_id": "n7"},\n]\n')          # n9 有判据无绑定
LEDGER = {"n9": {"package_node": "清绑定节点包", "script": "custom/fc_n9.py",
                 "entry": "diagnose", "fingerprint": "drill", "migrated_at": "2026-01-01T00:00:00"}}

RCA_HIT = {"rca_result": [{
    "Root_cause": "接口DOWN", "Fault_tree_path": ["总故障", "会话down", "接口DOWN"],
    "node_id": "n6", "support": "高",
    "evidence_path": [{"node": "总故障", "node_id": "n1", "judgment": "通过"},
                      {"node": "会话down", "node_id": "n3", "judgment": "成立"},
                      {"node": "接口DOWN", "node_id": "n6", "judgment": "成立"}]}],
    "probable_causes": [], "undetermined": [], "tree_issues": {}}
RCA_EMPTY = {"rca_result": [], "probable_causes": [], "undetermined": [], "tree_issues": {}}
RCA_INPUT = {"fault_tree": {"scenario": "演练场景", "root": "n1", "outline": {},
                            "pruned_nodes": {
                                "n6": {"name": "接口DOWN", "parent": "n3", "facts": [
                                    {"id": "f1", "indicator": "log", "status": "ok",
                                     "predicate_satisfied": True}]},
                                "n7": {"name": "链路震荡", "parent": "n3", "facts": [
                                    {"id": "f2", "indicator": "log",
                                     "fact": {"status": "ok", "predicate_satisfied": False}}]}}},
             "missing_indicators": {}}


def _run(ok=True, missing=None, fail_step=None, data_path=""):
    st = {"log_analyzer": {"ok": True, "exitcode": 0}, "rca": {"ok": True, "exitcode": 0}}
    if fail_step:
        st[fail_step] = {"ok": False, "exitcode": 1,
                         "error": f"Traceback ... in {fail_step} KeyError: 'NEName'"}
    doc = {"run_id": "drill", "created_at": "2026-01-01T00:00:00Z", "execution_status": st,
           "inputs": {"raw": {"path": data_path}}}
    if missing:
        doc["missing_inputs"] = missing
    return doc


def _exp(cid, nid, name, facts=None, negative=False, data_source=""):
    return {"case_id": cid, "scenario": "演练场景", "negative": negative,
            "expected_root_causes": [] if negative else [{"node_id": nid, "node_name": name}],
            "expected_facts": facts or {}, "data_source": data_source}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="生成六类错误演练场")
    ap.add_argument("--out", required=True, help="演练工作区目录(会创建)")
    a = ap.parse_args()
    out = Path(a.out).expanduser().resolve()

    def wj(rel, obj):
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

    # 迷你产物(评估要看 tree_index/scripts/台账)
    wj("product/tree/演练.tree_index.json", TREE)
    wj("product/tree/migrations.json", LEDGER)
    (out / "product" / "scripts").mkdir(parents=True, exist_ok=True)
    (out / "product" / "scripts" / "log_analyzer.py").write_text(ANALYZER, encoding="utf-8")

    # 用例 + 结果:每个用例一份"已跑完"的结果目录,按错误特征捏好
    drills = [
        # (用例id, expected, run.json, rca_input, rca.json, 期望评估结论)
        ("drill-pass", _exp("drill-pass", "n6", "接口DOWN"),
         _run(missing=["config"]), RCA_INPUT, RCA_HIT,
         "PASS_WITH_GAPS——全命中+数据缺口,不生成工单"),
        ("drill-0-env", _exp("drill-0-env", "n7", "链路震荡"),
         _run(fail_step="custom/fc_n7", missing=None), RCA_INPUT, RCA_EMPTY,
         "FAIL·类0 ENV_FAILURE(脚本运行崩溃)→工单:自动迭代档,migrate 重迁+限定范围修复"),
        ("drill-1-input", _exp("drill-1-input", "n7", "链路震荡"),
         _run(missing=["log"]), RCA_INPUT, RCA_EMPTY,
         "FAIL·类1 INPUT_GAP(数据缺失且漏判)→工单:人工兜底档,补数据清单"),
        ("drill-2-orphan", _exp("drill-2-orphan", "n7", "链路震荡"),
         _run(), RCA_INPUT,
         {**RCA_EMPTY, "tree_issues": {"orphan_node_ids": ["n88"]}},
         "FAIL·类2 BINDING_GAP(孤儿绑定 n88)→工单:自动迭代档,调优表清残留绑定"),
        ("drill-2-binding", _exp("drill-2-binding", "n9", "清绑定节点"),
         _run(), RCA_INPUT, RCA_EMPTY,
         "FAIL·类2 BINDING_GAP(绑定清除冲突:台账有迁移记录+绑定为空)→工单:人工兜底档,二选一(恢复绑定/修订期望)"),
        ("drill-3-knowledge", _exp("drill-3-knowledge", "n999", "幽灵节点"),
         _run(), RCA_INPUT, RCA_EMPTY,
         "FAIL·类3 KNOWLEDGE_GAP(树上没有 n999)→工单:人工兜底档,故障树缺节点→tester 需求单"),
        ("drill-4-criteria", _exp("drill-4-criteria", "n7", "链路震荡",
                                  facts={"n7": {"predicate_satisfied": True}}),
         _run(), RCA_INPUT, RCA_EMPTY,
         "FAIL·类4 CRITERIA_GAP(期望成立实测不成立)→工单:自动迭代档,判定偏差修复(按证据修到与判据文档一致)"),
        ("drill-5-verdict", _exp("drill-5-verdict", "n7", "链路震荡"),
         _run(), RCA_INPUT, RCA_EMPTY,
         "FAIL·类5 VERDICT_GAP(事实无冲突仍漏判)→工单:人工兜底档,结论归档争议(+辩论引擎可选)"),
    ]
    for cid, exp, run_doc, ri, rca, note in drills:
        data_dir = out / "cases" / cid / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "占位.txt").write_text("演练数据占位\n", encoding="utf-8")
        if cid == "drill-1-input":
            # 类1 的 v11 语义:缺的源必须是用例数据**本可提供**的(数据里有、这轮没喂到);
            # 数据里天然没有该类内容的缺口不构成类1(穿透查绑定层)——所以教具得放一份 log 内容
            (data_dir / "logbuffer采集.txt").write_text("演练 log 数据\n", encoding="utf-8")
        exp["data_source"] = str(data_dir)
        run_doc["inputs"]["raw"]["path"] = str(data_dir)
        wj(f"cases/{cid}/expected.json", exp)
        wj(f"results/{cid}/result-drill-{cid}/run.json", run_doc)
        wj(f"results/{cid}/result-drill-{cid}/rca_input.json", ri)
        wj(f"results/{cid}/result-drill-{cid}/rca.json", rca)

    guide = ["# 演练指引\n",
             "每个用例=一份捏好的执行结果,按下列命令逐个评估,再对 FAIL 的报告跑分拣开票。\n",
             "```", ]
    for cid, *_ in drills:
        guide.append(f'python <reflect>/scripts/eval_run.py --case {cid} --cases-dir cases '
                     f'--result-dir results/{cid} --skill-dir product --out eval-out')
    guide += ["```", "\nFAIL 的用例接着分拣:\n", "```",
              'python <generate-optimize>/scripts/route_feedback.py '
              '--feedback eval-out/<用例>-v1-<时间戳>/evaluation_report.json --product product',
              "```", "\n## 每个用例的期望结果\n"]
    guide += [f"- **{cid}**:{note}" for cid, *_r, note in [(d[0], d[5]) for d in drills]]
    (out / "演练指引.md").write_text("\n".join(guide) + "\n", encoding="utf-8")
    print(f"[make_drill] 演练场已生成 → {out}(8 个用例;先看 演练指引.md)")


if __name__ == "__main__":
    main()
