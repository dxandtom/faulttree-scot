#!/usr/bin/env python3
"""诊断准确率打分 —— 把每个场景的 RCA 输出(rca.json)对标准答案(truth)打分,聚合成准确率表。

四个维度(都对 ground truth,合成注入才有):
  命中    —— 埋下的根因有没有出现在【应在的档位】(确证→rca_result;疑似→probable_causes;
             无因类→不报确证);
  档位准  —— 没有过度确证(该疑似的别报成确证)/ 没有漏档;
  无假阳  —— 没把"路径断了的干扰叶子"等 must_not_report 节点报成根因;
  诚实    —— 复用 validate_rca:确证路径无数据不足/不成立、无伪造成立。
综合 = 四项全过。**这是"受控合成故障注入下"的准确率,是必要不充分(现网更脏)。**

用法: python score_accuracy.py [--suite <目录,默认 ../suite_real>] [--use-reference]
  --use-reference: 用每个场景的 reference_rca.json 当输入做自测(应满分),用于验证打分链路本身。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SUITE = HERE.parent / "suite_real"


def _norm(s):
    return str(s or "").strip().replace(" ", "")


def _entry_ids(entry: dict):
    ids, names = set(), set()
    for e in entry.get("evidence_path") or []:
        if e.get("node_id"):
            ids.add(e["node_id"])
        if e.get("node"):
            names.add(_norm(e["node"]))
    for k in ("Root_cause", "blocked_at"):
        if entry.get(k):
            names.add(_norm(entry[k]))
    fp = entry.get("Fault_tree_path") or []
    if fp:
        names.add(_norm(fp[-1]))
    return ids, names


def _covers(entry: dict, nid, name) -> bool:
    ids, names = _entry_ids(entry)
    return bool((nid and nid in ids) or (name and _norm(name) in names))


def _judged_hold_ids(conf, prob) -> set:
    """模型在 evidence_path 里判为"成立"的节点 id 集合(用于对真值查"伪造成立")。"""
    out = set()
    for e in conf + prob:
        for step in (e.get("evidence_path") or []):
            j = str(step.get("judgment", ""))
            if "成立" in j and "不成立" not in j and step.get("node_id"):
                out.add(step["node_id"])
    return out


def score_one(scn_dir: Path, use_reference: bool) -> dict:
    # 模型只读 scn_dir/rca_input.json,把判断写 scn_dir/rca.json;答案(truth/node_states/参考)在 _answers/<名>.json。
    ans = json.loads((scn_dir.parent / "_answers" / f"{scn_dir.name}.json").read_text(encoding="utf-8"))
    category, t, ref = ans["category"], ans["truth"], ans.get("reference_rca")
    rca_file = scn_dir / "rca.json"
    base = {"scenario": scn_dir.name, "category": category, "tree": ans.get("tree"),
            "tree_nodes": ans.get("tree_nodes")}
    if use_reference:
        rca = ref
    elif rca_file.exists():
        rca = json.loads(rca_file.read_text(encoding="utf-8"))
    else:
        rca = None
    if rca is None:
        return {**base, "status": "pending", "hit": False, "tier": False, "no_fp": False,
                "honest": False, "ok": False, "why": "缺 rca.json(模型还没判)"}
    conf = rca.get("rca_result") or []
    prob = rca.get("probable_causes") or []
    nid, name = t.get("root_cause_node_id"), t.get("root_cause_name")
    mnr = set(t.get("must_not_report") or [])

    reported_ids = set()
    for e in conf + prob:
        reported_ids |= _entry_ids(e)[0]
    fp = bool(mnr & reported_ids)

    in_conf = any(_covers(e, nid, name) for e in conf) if nid else False
    in_prob = any(_covers(e, nid, name) for e in prob) if nid else False

    tier = t["tier"]
    if tier == "confirmed":
        hit, tier_ok = in_conf, in_conf
        no_fp = not fp
    elif tier == "probable":
        hit = in_prob
        tier_ok = in_prob and not in_conf            # 没过度确证
        no_fp = not fp
    else:                                            # excluded / undetermined:不该有确证根因
        hit = (len(conf) == 0)
        tier_ok = (len(conf) == 0)
        no_fp = (len(conf) == 0) and not fp
    # 诚实(对真值查"伪造成立"):凡模型判"成立"的节点,必须确实是埋下的 hold 节点;
    # 在 fail/数据不足 节点上判成立 = 伪造/自相矛盾。直接对真值 node_states 查,比 validate_rca 启发式更精确。
    planted_hold = {nid for nid, s in (ans.get("node_states") or {}).items() if s == "hold"}
    honest = _judged_hold_ids(conf, prob) <= planted_hold
    why = []
    if not hit:
        why.append("没命中应在档位")
    if not tier_ok:
        why.append("档位错(过度确证/漏档)")
    if not no_fp:
        why.append("假阳性(报了不该报的)")
    if not honest:
        why.append("诚实校验未过")
    return {**base, "status": "scored", "hit": hit, "tier": tier_ok, "no_fp": no_fp,
            "honest": honest, "ok": hit and tier_ok and no_fp and honest, "why": "; ".join(why) or "全过"}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="诊断准确率打分(对合成注入的 ground truth)")
    ap.add_argument("--suite", default=str(DEFAULT_SUITE))
    ap.add_argument("--use-reference", action="store_true", help="用参考满分答案自测打分链路")
    ap.add_argument("-o", "--output", help="报告 txt(默认打印到终端)")
    args = ap.parse_args()
    suite = Path(args.suite)
    scns = sorted(d for d in suite.iterdir() if d.is_dir() and not d.name.startswith("_")
                  and (d / "rca_input.json").exists())
    rows = [score_one(d, args.use_reference) for d in scns]

    L = ["=" * 84,
         "诊断准确率评测(受控合成故障注入 · 多树 · 事实不带 predicate,模型须自判)"
         + ("· 参考答案自测" if args.use_reference else ""),
         "=" * 84,
         f"{'场景':<34}{'类别':<22}{'命中':<5}{'档位':<5}{'无假阳':<7}{'诚实':<5}{'综合'}"]
    M = lambda b: " ✓  " if b else " ✗  "
    for r in rows:
        if r["status"] == "pending":
            L.append(f"{r['scenario']:<34}{r['category']:<22}{'—— 待模型产出 rca.json ——'}")
            continue
        L.append(f"{r['scenario']:<34}{r['category']:<22}{M(r['hit'])}{M(r['tier'])}{M(r['no_fp'])+'  '}"
                 f"{M(r['honest'])}{'✓ 通过' if r['ok'] else '✗ ' + r['why']}")
    scored = [r for r in rows if r["status"] == "scored"]
    n = len(scored) or 1
    agg = {k: sum(1 for r in scored if r[k]) for k in ("hit", "tier", "no_fp", "honest", "ok")}
    L.append("-" * 84)
    L.append(f"合计({len(scored)} 个已评分): 命中 {agg['hit']}/{len(scored)}、档位 {agg['tier']}/{len(scored)}、"
             f"无假阳 {agg['no_fp']}/{len(scored)}、诚实 {agg['honest']}/{len(scored)}")
    L.append(f"综合准确率(四项全过): {agg['ok']}/{len(scored)} = {100 * agg['ok'] / n:.0f}%")
    # 按树规模分档(看"准确率 vs 规模"曲线)
    by = {}
    for r in scored:
        by.setdefault((r.get("tree_nodes") or 0, r.get("tree")), []).append(r)
    if len(by) > 1:
        L.append("\n按树规模分档(综合通过率):")
        for (sz, tag), rs in sorted(by.items()):
            ok = sum(1 for r in rs if r["ok"])
            L.append(f"  {str(sz)+' 节点':<10} {str(tag):<22} {ok}/{len(rs)}")
    pend = [r for r in rows if r["status"] == "pending"]
    if pend:
        L.append(f"\n待评分 {len(pend)} 个(先用 fault-tree-rca 产出其 rca.json):{[r['scenario'] for r in pend]}")
    L.append("注:【受控合成】下的 RCA 大树推理稳健性(判条件+导航+抗诱饵+诚实),必要不充分;现网准确率需真实标注案例。")
    L.append("=" * 84)
    report = "\n".join(L)
    print(report)
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
        print(f"\n[score] 报告已写入 → {args.output}")
    return 0 if not pend and agg["ok"] == len(scored) else (0 if pend else 1)


if __name__ == "__main__":
    sys.exit(main())
