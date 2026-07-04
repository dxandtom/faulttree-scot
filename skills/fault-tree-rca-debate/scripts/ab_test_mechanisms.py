#!/usr/bin/env python3
"""三种得分机制(original / softmax / ratio)的一键 A/B/C 测评(合作方点名要的实测)。

数据集 = cs-scot-eval/suite_real:30 个**带标准答案**的冻结场景(5 棵树 17–320 节点 × 6 类情形),
每个场景的 rca_input.json / tree_index / 答案都已入库 —— 开箱即测,不必重造数据。

⚠ 实验设计要点(为什么不用 eval 的四档打分来比机制):
- 本引擎是**纯排序器**:输出 Top-K 评分假设,没有"确证/弃权"语义(适配层诚实地全放 probable 档)。
  四档打分的「命中」要求确证情形的根因出现在 rca_result 档 → 排序器**结构性失分**,
  分数反映的是"排序器 vs 事实闭环"的语义差,而不是三种机制谁好。
- 所以**机制对比用档位无关的排名指标**:hit@1 / hit@k / MRR(真根因落在第几条路径)+
  FP@k(must_not_report 的诱饵是否被排进 Top-K)。
- 真根因为 null 的情形(excluded 等,正确答案="别报")→ 排序器没有弃权能力,单列为
  「弃权能力缺口」统计,不掺进机制对比 —— 这是"全盘替代 fault-tree-rca 会失去什么"的量化,
  单独看、如实报。

用法:
  ab_test_mechanisms.py [--engine mock|real] [--k 3] [--only <场景名子串>]
      [--suite <suite_real 目录>] [--trees <trees 目录>] [--out /tmp/ab_result.json]
      [--vendor-dir ... --config ...]
mock 引擎只验证管线与指标口径,机制优劣结论必须以真引擎跑出的数字为准。
"""
from __future__ import annotations

import argparse
import json
import sys

import time
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent.parent
sys.path.insert(0, str(_HERE))
from facts_to_question import build_question                    # noqa: E402
from result_adapter import _MECH_KEY                            # noqa: E402
from run_debate_rca import _load_processor                      # noqa: E402
from tree_to_vendor_xlsx import enrich_conditions, write_xlsx   # noqa: E402

MECHS = list(_MECH_KEY)


def _path_ids(path: list) -> list:
    return [x["code"] for x in path if isinstance(x, dict) and x.get("code")]


def _rank_metrics(paths: list, truth_id: str, must_not: list, k: int) -> dict:
    """排名指标:真根因 id 出现在第几条路径(路径任一节点命中即算,兼容根因埋在中间节点的情形)。"""
    rank = None
    for i, p in enumerate(paths[:k]):
        if truth_id in _path_ids(p):
            rank = i
            break
    fp = sorted({m for p in paths[:k] for m in must_not if m in _path_ids(p)})
    return {"rank": rank, "hit1": rank == 0, "hitk": rank is not None,
            "rr": (1.0 / (rank + 1)) if rank is not None else 0.0, "fp": fp}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="三种得分机制 A/B/C 测评(suite_real,带标准答案)")
    ap.add_argument("--engine", default="mock", choices=["mock", "real"])
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--only", default="", help="只跑名字含该子串的场景(如 synth_中 / confirmed)")
    ap.add_argument("--suite", default=str(_REPO / "skills/cs-scot-eval/suite_real"))
    ap.add_argument("--trees", default=str(_REPO / "skills/cs-scot-eval/trees"))
    ap.add_argument("--out", default="/tmp/ab_result.json")
    ap.add_argument("--vendor-dir", default=str(_HERE.parent / "vendor"))
    ap.add_argument("--config")
    a = ap.parse_args()

    # 路径全转绝对:真引擎运行时工作目录会切到 vendor/(复刻"右键直跑"环境),相对路径会失效
    suite, trees = Path(a.suite).resolve(), Path(a.trees).resolve()
    a.out = str(Path(a.out).resolve())
    vendor_dir = Path(a.vendor_dir).resolve()
    cfg_path = Path(a.config).resolve() if a.config else None
    answers_dir = suite / "_answers"
    scens = sorted(d for d in suite.iterdir()
                   if d.is_dir() and d.name != "_answers" and (d / "rca_input.json").exists()
                   and (not a.only or a.only in d.name))
    if not scens:
        print("没有匹配的场景", file=sys.stderr)
        return 2

    import os
    prev_cwd = os.getcwd()
    if a.engine == "real":
        os.chdir(vendor_dir)          # 引擎内部相对路径(自读 config.yaml 等)按 vendor/ 解析
    proc, desc = _load_processor(vendor_dir, cfg_path, a.engine == "mock")
    print(f"引擎 = {desc};场景 {len(scens)} 个;k={a.k}")
    if a.engine == "mock":
        print("⚠ MOCK 引擎:以下数字只验证管线与指标口径,机制优劣以真引擎结果为准。\n")

    rows, gap_rows = [], []
    for sd in scens:
        ans_p = answers_dir / f"{sd.name}.json"
        if not ans_p.exists():
            print(f"[skip] {sd.name}:无答案文件")
            continue
        ans = json.loads(ans_p.read_text(encoding="utf-8"))
        truth = ans.get("truth") or {}
        tree_name = ans.get("tree") or sd.name.split("__")[0]
        ti_p = trees / f"{tree_name}.tree_index.json"
        if not ti_p.exists():
            print(f"[skip] {sd.name}:无树索引 {ti_p.name}")
            continue
        tree_index = json.loads(ti_p.read_text(encoding="utf-8"))
        rca_input = json.loads((sd / "rca_input.json").read_text(encoding="utf-8"))
        # 评测树 condition 全空,判据文本在各场景的 rca_input/binding_question 里 → 逐场景合回。
        # 因此 xlsx/建树也逐场景做(不能按树缓存:同树不同场景的判据集不同)。
        bq_p = sd / "binding_question.json"
        bq = json.loads(bq_p.read_text(encoding="utf-8")) if bq_p.exists() else None
        enriched = enrich_conditions(tree_index, rca_input, bq)

        question = build_question(rca_input, enriched)
        t0 = time.time()
        work = Path(a.out).resolve().parent / "_ab_xlsx"      # 落盘不删:免 Windows 句柄清理坑 + 可审计
        work.mkdir(parents=True, exist_ok=True)
        xlsx = work / f"{sd.name}.xlsx"
        write_xlsx(enriched, xlsx)
        tree = proc.build_tree_structure(proc.load_fault_tree(str(xlsx)))
        top = dict(zip(MECHS, proc.find_top_k_paths(tree, question, a.k)[:3]))
        dt = time.time() - t0

        base = {"scenario": sd.name, "tree": tree_name, "nodes": ans.get("tree_nodes"),
                "tier": truth.get("tier"), "category": ans.get("category"), "seconds": round(dt, 2)}
        if not truth.get("root_cause_node_id"):               # 弃权情形:排序器结构性做不到,单列
            fp_any = {m: _rank_metrics(top[m], "__none__", truth.get("must_not_report") or [], a.k)["fp"]
                      for m in MECHS}
            gap_rows.append({**base, "note": "真值=不报根因;纯排序引擎无弃权语义", "fp": fp_any})
            continue
        per = {m: _rank_metrics(top[m], truth["root_cause_node_id"],
                                truth.get("must_not_report") or [], a.k) for m in MECHS}
        rows.append({**base, "truth_id": truth["root_cause_node_id"], "metrics": per})
        marks = " ".join(f"{m}:{'@' + str(per[m]['rank'] + 1) if per[m]['hitk'] else '✗'}"
                         f"{'!FP' if per[m]['fp'] else ''}" for m in MECHS)
        print(f"  {sd.name:42s} [{base['tier']}] {marks}")

    # ---- 汇总 ----
    def agg(rs, key=None, val=None):
        sel = [r for r in rs if key is None or r.get(key) == val]
        out = {}
        for m in MECHS:
            ms = [r["metrics"][m] for r in sel]
            n = len(ms) or 1
            out[m] = {"n": len(ms),
                      "hit@1": round(sum(x["hit1"] for x in ms) / n, 3),
                      f"hit@{a.k}": round(sum(x["hitk"] for x in ms) / n, 3),
                      "MRR": round(sum(x["rr"] for x in ms) / n, 3),
                      "FP场景数": sum(bool(x["fp"]) for x in ms)}
        return out

    print(f"\n===== 机制对比(可埋根因场景 {len(rows)} 个;排名指标,档位无关)=====")
    overall = agg(rows)
    hdr = f"{'机制':10s} {'n':>3s} {'hit@1':>7s} {'hit@' + str(a.k):>7s} {'MRR':>7s} {'FP场景':>7s}"
    print(hdr)
    for m in MECHS:
        o = overall[m]
        print(f"{m:10s} {o['n']:3d} {o['hit@1']:7.3f} {o[f'hit@{a.k}']:7.3f} {o['MRR']:7.3f} {o['FP场景数']:7d}")
    by_tier = {}
    for t in sorted({r["tier"] for r in rows if r.get("tier")}):
        by_tier[t] = agg(rows, "tier", t)
        line = " | ".join(f"{m} MRR={by_tier[t][m]['MRR']}" for m in MECHS)
        print(f"  按情形 {t:22s}(n={by_tier[t][MECHS[0]]['n']}):{line}")
    if gap_rows:
        print(f"\n===== 弃权能力缺口(真值=不报根因的场景 {len(gap_rows)} 个,排序器必然给出 Top-K)=====")
        for g in gap_rows:
            fps = {m: v for m, v in g["fp"].items() if v}
            print(f"  {g['scenario']:42s} 引擎仍会输出 Top-{a.k}"
                  + (f";且诱饵进榜:{fps}" if fps else ""))
        print("  → 这是评分制替代事实闭环 RCA 要付的语义代价,决策时与机制优劣分开看。")

    Path(a.out).write_text(json.dumps(
        {"engine": a.engine, "k": a.k, "overall": overall, "by_tier": by_tier,
         "rows": rows, "abstention_gap": gap_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已写 {a.out}")
    os.chdir(prev_cwd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
