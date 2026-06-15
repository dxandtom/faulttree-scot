#!/usr/bin/env python3
"""诊断准确率打分 —— 把每个场景的 RCA 输出(rca.json)对标准答案(truth)打分,聚合成准确率表。

四个维度(都对 ground truth,合成注入才有):
  命中    —— 埋下的根因有没有出现在【应在的档位】(确证→rca_result;疑似→probable_causes;
             无因类→不报确证);
  档位准  —— 没有过度确证(该疑似的别报成确证)/ 没有漏档;
  无假阳  —— 没把"路径断了的干扰叶子"等 must_not_report 节点报成根因;
  诚实    —— 复用 validate_rca:确证路径无数据不足/不成立、无伪造成立。
综合 = 四项全过。**这是"受控合成故障注入下"的准确率,是必要不充分(现网更脏)。**

用法: python score_accuracy.py [--suite <目录,默认 ../suite>] [--use-reference]
  --use-reference: 用每个场景的 reference_rca.json 当输入做自测(应满分),用于验证打分链路本身。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS_ROOT = HERE.parent.parent
DEFAULT_SUITE = HERE.parent / "suite"


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


def _validate_rca(rca_path: Path, input_path: Path) -> bool:
    vr = SKILLS_ROOT / "fault-tree-rca" / "scripts" / "validate_rca.py"
    if not vr.exists():
        return True  # 没装校验器不阻断打分(诚实项记为通过)
    r = subprocess.run([sys.executable, str(vr), str(rca_path), str(input_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0


def score_one(scn_dir: Path, use_reference: bool) -> dict:
    gold = json.loads((scn_dir / "gold.json").read_text(encoding="utf-8"))
    t = gold["truth"]
    rca_file = scn_dir / "result" / ("reference_rca.json" if use_reference else "rca.json")
    if use_reference:
        rca_file = scn_dir / "reference_rca.json"
    inp = scn_dir / "result" / "rca_input.json"
    base = {"scenario": scn_dir.name, "category": gold["category"]}
    if not rca_file.exists():
        return {**base, "status": "pending", "hit": False, "tier": False, "no_fp": False,
                "honest": False, "ok": False, "why": "缺 rca.json(模型还没判)"}
    rca = json.loads(rca_file.read_text(encoding="utf-8"))
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
    honest = _validate_rca(rca_file, inp)
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
    scns = sorted(d for d in suite.iterdir() if d.is_dir() and (d / "gold.json").exists())
    rows = [score_one(d, args.use_reference) for d in scns]

    L = ["=" * 78,
         "诊断准确率评测(受控合成故障注入 · Mode A 隔离 RCA" + ("· 参考答案自测" if args.use_reference else "") + ")",
         "=" * 78,
         f"{'场景':<30}{'类别':<22}{'命中':<5}{'档位':<5}{'无假阳':<7}{'诚实':<5}{'综合'}"]
    M = lambda b: " ✓  " if b else " ✗  "
    for r in rows:
        if r["status"] == "pending":
            L.append(f"{r['scenario']:<30}{r['category']:<22}{'—— 待模型产出 rca.json ——'}")
            continue
        L.append(f"{r['scenario']:<30}{r['category']:<22}{M(r['hit'])}{M(r['tier'])}{M(r['no_fp'])+'  '}"
                 f"{M(r['honest'])}{'✓ 通过' if r['ok'] else '✗ ' + r['why']}")
    scored = [r for r in rows if r["status"] == "scored"]
    n = len(scored) or 1
    agg = {k: sum(1 for r in scored if r[k]) for k in ("hit", "tier", "no_fp", "honest", "ok")}
    L.append("-" * 78)
    L.append(f"合计({len(scored)} 个已评分): 命中 {agg['hit']}/{len(scored)}、档位 {agg['tier']}/{len(scored)}、"
             f"无假阳 {agg['no_fp']}/{len(scored)}、诚实 {agg['honest']}/{len(scored)}")
    L.append(f"综合准确率(四项全过): {agg['ok']}/{len(scored)} = {100*agg['ok']/n:.0f}%")
    pend = [r for r in rows if r["status"] == "pending"]
    if pend:
        L.append(f"\n待评分 {len(pend)} 个(先用 fault-tree-rca 产出其 result/rca.json):{[r['scenario'] for r in pend]}")
    L.append("注:这是【受控合成】下的准确率,证明推理链在干净事实上成立(必要不充分);现网准确率需真实标注案例。")
    L.append("=" * 78)
    report = "\n".join(L)
    print(report)
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
        print(f"\n[score] 报告已写入 → {args.output}")
    return 0 if not pend and agg["ok"] == len(scored) else (0 if pend else 1)


if __name__ == "__main__":
    sys.exit(main())
