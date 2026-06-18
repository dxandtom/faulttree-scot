#!/usr/bin/env python3
"""v3 跨规模统一打分 —— 复用两套已验证打分器,按【规模】与【情形】聚合成一张综合报告。
  · 绑定:精编树 suite_v2/binding.json vs 金标准(realistic 大白话→列,15 条);
  · RCA :精编 18(score_v2.score_rca)+ 多规模 30(score_accuracy.score_one),共 48,四维(命中/档位/无假阳/诚实)。
零打分重写:精编走 score_v2、多规模走 score_accuracy,本脚本只负责喂参数 + 按规模/情形聚合。
用法: python score_v3.py [--use-reference] [-o report.txt]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score_v2 as SV                      # noqa: E402
import score_accuracy as SA                # noqa: E402
from score_binding import _model_list      # noqa: E402

ROOT = HERE.parent
SV2, SREAL, SV3 = ROOT / "suite_v2", ROOT / "suite_real", ROOT / "suite_v3"
M = lambda x: " ✓ " if x else " ✗ "

_ST = {"hold": "hold", "成立": "hold", "fail": "fail", "不成立": "fail",
       "insufficient_data": "insufficient", "insufficient": "insufficient", "数据不足": "insufficient"}
def _nst(s):
    s = str(s)
    for k, v in _ST.items():
        if k in s:
            return v
    return s
def _score_corr(rca: dict, recs: list):
    """模型 corrections vs 注入的错误:揪对几个(节点对+方向对)、误纠几个。"""
    flagged = {str(c.get("node_id")): _nst(c.get("should_be") or c.get("true") or c.get("correct") or "")
               for c in (rca.get("corrections") or []) if isinstance(c, dict)}
    inj = {r["node_id"]: r["true"] for r in recs}
    caught = sum(1 for nid, tv in inj.items() if flagged.get(nid) == tv)
    false = sum(1 for nid in flagged if nid not in inj)
    return caught, len(inj), false


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="v3 跨规模综合打分(绑定 + 跨规模 RCA)")
    ap.add_argument("--use-reference", action="store_true", help="金标准/参考自测打分链路(应 100%)")
    ap.add_argument("--corrupt", action="store_true", help="纠错卷:额外评「抗错诊断 + 纠错率 + 误纠」(配合 v3_run.py --corrupt)")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    man = json.loads((SV3 / "manifest.json").read_text(encoding="utf-8"))
    ctruth = json.loads((SV3 / "_corrupt_truth.json").read_text(encoding="utf-8")) if args.corrupt else {}
    corr_acc = [0, 0, 0]   # caught, injected, false
    L = []
    L.append("=" * 90)
    L.append("v3 跨规模综合评测(精编树·全技能壳+绑定  ⊕  多规模树 27–320·真剪枝)")
    L.append("=" * 90)

    # ---------- 绑定(精编树,15 条 realistic 判据) ----------
    tidx = json.loads(next((SV2 / "scot产物").glob("*/tree/*.tree_index.json")).read_text(encoding="utf-8"))
    c2i = SV._code2id(tidx); nodes = tidx["nodes"]
    gold = json.loads((SV2 / "gold_tree.json").read_text(encoding="utf-8"))
    gold_list = []
    for code, g in gold["by_code"].items():
        b = dict(g["binding"]); b["node_id"] = c2i[code]; gold_list.append(b)
    bf = SV2 / "binding.json"
    L.append("\n【绑定】精编树 realistic 判据(大白话→系统列)")
    if args.use_reference or not bf.exists():
        ml = gold_list if args.use_reference else None
        if ml is None:
            L.append("  —— 待模型产出 suite_v2/binding.json ——")
            b = None
        else:
            b = SV.score_binding(ml, gold_list)
    else:
        b = SV.score_binding(_model_list(json.loads(bf.read_text(encoding="utf-8"))), gold_list)
    if b:
        L.append(f"  类别 {b['cls']}/{b['n']}、选列 {b['col']}/{b['n']}、参数 {b['par']}/{b['n']}、"
                 f"完全正确 {b['full']}/{b['n']} = {100*b['full']//b['n']}%")

    # ---------- RCA(48 场景,按规模/情形聚合) ----------
    rows = []  # (scale, kind, dims)
    for it in man["rca_scenarios"]:
        if it["suite"] == "suite_v2":
            d = SV2 / "scenarios" / it["dir"]
            tr = json.loads((d / "truth.json").read_text(encoding="utf-8"))
            roots = {c2i[c] for c in tr.get("root_cause_codes", []) if c in c2i}
            mnr = {c2i[c] for c in tr.get("must_not_report_codes", []) if c in c2i}
            ids = {c2i[c]: s for c, s in (tr.get("node_states") or {}).items() if c in c2i}
            tier = tr["tier"]
            if args.use_reference:
                rca = SV.build_ref_rca(roots, tier, ids, nodes)
            else:
                rf = d / "rca.json"
                rca = json.loads(rf.read_text(encoding="utf-8")) if rf.exists() else None
            r = SV.score_rca(rca, roots, mnr, tier, ids, nodes) if rca is not None else {
                "hit": False, "tier": False, "no_fp": False, "honest": False, "ok": False, "why": "待产出 rca.json"}
        else:
            r = SA.score_one(SREAL / it["dir"], args.use_reference)
        if args.corrupt:                                   # 纠错卷:额外统计纠错/误纠
            recs = ctruth.get(it["rel"], [])
            if args.use_reference:
                cc, ci, cf = len(recs), len(recs), 0
            else:
                sdir = (SV2 / "scenarios" / it["dir"]) if it["suite"] == "suite_v2" else (SREAL / it["dir"])
                rf = sdir / "rca.json"
                cc, ci, cf = _score_corr(json.loads(rf.read_text(encoding="utf-8")) if rf.exists() else {}, recs)
            corr_acc[0] += cc; corr_acc[1] += ci; corr_acc[2] += cf
        rows.append((it["scale"], it["kind"], r, it["dir"]))

    def agg(sel):
        sub = [r for sc, k, r, _ in rows if sel(sc, k)]
        n = len(sub)
        g = lambda key: sum(1 for r in sub if r.get(key))
        return n, g("hit"), g("tier"), g("no_fp"), g("honest"), g("ok")

    _hdr = "RCA · 抗错诊断(给的结论里掺了错,看还能不能诊断对)" if args.corrupt else "RCA · 跨规模"
    L.append(f"\n【{_hdr}】共 {len(rows)} 场景(精编17 + 多规模27/44/95/178/320),四维全过才算综合通过")
    L.append(f"  {'规模(节点)':<12}{'场景':<6}{'命中':<6}{'档位':<6}{'无假阳':<7}{'诚实':<6}{'综合通过'}")
    L.append("  " + "-" * 62)
    scales = sorted({sc for sc, _, _, _ in rows})
    for sc in scales:
        n, hit, ti, nf, ho, ok = agg(lambda s, k, sc=sc: s == sc)
        tag = f"{sc}(精编)" if sc == 17 else f"{sc}"
        L.append(f"  {tag:<12}{n:<6}{hit:<6}{ti:<6}{nf:<7}{ho:<6}{ok}/{n}")
    n, hit, ti, nf, ho, ok = agg(lambda s, k: True)
    L.append("  " + "-" * 62)
    L.append(f"  {'合计':<12}{n:<6}{hit:<6}{ti:<6}{nf:<7}{ho:<6}{ok}/{n} = {round(100*ok/n)}%")

    # 按情形(category 同一词表,两套可合并)
    L.append("\n【RCA · 按情形】")
    kinds = ["confirmed", "probable", "false_positive_resist", "excluded", "honest_insufficient"]
    namez = {"confirmed": "确证", "probable": "疑似", "false_positive_resist": "抗诱饵",
             "excluded": "排除", "honest_insufficient": "数据不足(诚实)"}
    for k in kinds:
        n, hit, ti, nf, ho, ok = agg(lambda s, kk, k=k: kk == k)
        if n:
            L.append(f"  {namez[k]:<14}{ok}/{n} 通过   (命中{hit} 档位{ti} 无假阳{nf} 诚实{ho})")

    # 未过清单
    bad = [(sc, dirn, r["why"]) for sc, k, r, dirn in rows if not r.get("ok")]
    if bad:
        L.append("\n  未过场景:")
        for sc, dirn, why in bad[:14]:
            L.append(f"    [{sc}节点] {dirn}: {why}")

    if args.corrupt:
        cc, ci, cf = corr_acc
        L.append(f"\n【纠错卷 · 纠错率】注入错误结论 {ci} 处,模型靠复核数据揪出 {cc} 处 = {round(100*cc/ci) if ci else 0}%;误纠(把对的也改)共 {cf} 处。")
        L.append("  说明:上面那张表是「抗错诊断」——结论里掺了错,看模型会不会被带偏;这里的「纠错率」是它主动指出了多少处错标签。")
    L.append("\n口径:受控合成下的跨规模诊断推理稳健性 + 端到端绑定;精编树走真实 excel_to_tree+assemble+runner 全技能壳,")
    L.append("      多规模树走真实 tree_prune 剪枝(大树真丢分支,如 320→约 19 节点)。必要不充分,非现网脏数据准确率。")
    out = "\n".join(L)
    print(out)
    if args.output:
        Path(args.output).write_text(out + "\n", encoding="utf-8")
        print(f"\n→ 报告已写 {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
