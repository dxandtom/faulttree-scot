#!/usr/bin/env python3
"""v3 端到端编排 —— 把两套评测【融合成一次跨规模综合测评】:
  · 精编树(17 节点):真 excel_to_tree+assemble+runner 全技能壳 + 绑定题 + 18 个因果连贯场景(= v2);
  · 多规模树(27/44/95/178/320 节点):真 tree_prune 剪枝(大树真丢分支)+ 6 类情形 ×5 = 30 个场景(= v1)。
合计 1 道绑定 + 48 道 RCA。一条命令全建好,模型逐场景作答后用 score_v3.py 出【跨规模统一报告】。

用法:
  python v3_run.py                 先建两套底座(v2_handcrafted+v2_run+inject_on_tree),再汇总 manifest
  python v3_run.py --skip-build     底座已在,只重算 manifest
之后:逐场景读 rca_input.json → 写同目录 rca.json(⛔一次一个,别串场景);绑定读 suite_v2/binding_question.json
      → 写 suite_v2/binding.json;最后 python score_v3.py。
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SV2 = HERE.parent / "suite_v2"
SREAL = HERE.parent / "suite_real"
SV3 = HERE.parent / "suite_v3"
ROOT = HERE.parent


def _run(script: str):
    p = subprocess.run([sys.executable, str(HERE / script)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"{script} 失败:\n{p.stdout[-1200:]}\n{p.stderr[-1200:]}")
    return p.stdout


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="v3 跨规模综合评测:精编树(全技能壳+绑定)⊕ 多规模树(真剪枝)")
    ap.add_argument("--skip-build", action="store_true", help="底座已建好,只重算 manifest")
    ap.add_argument("--hard", action="store_true",
                    help="hard 模式:删掉 outline/predicate_satisfied 等答案标签,逼模型自判每个节点(能失败、能区分强弱)")
    ap.add_argument("--corrupt", action="store_true",
                    help="纠错卷:给的判定结论里注入错误(原始数据仍真实),看模型能不能拿数据复核、纠正,而不是盲信标签")
    args = ap.parse_args()

    if not args.skip_build:
        # ① 精编树:只跑 v2_run(读【已冻结的】suite_v2 源 → 重生 gitignore 的产物/rca_input);
        #    不跑 v2_handcrafted——那是维护题库用的,会改动已提交的源,日常评测不该动。
        print("① 精编树(17 节点):v2_run(真 excel_to_tree+assemble+runner,读冻结的 suite_v2 源)…")
        _run("v2_run.py")
        # ② 多规模树:suite_real 是【冻结考题】(已提交),直接复用;只有缺失时才生成,避免改动冻结答案。
        if (SREAL / "_answers").exists():
            print("② 多规模树:复用已冻结的 suite_real(27/44/95/178/320,不重生)。")
        else:
            print("② 多规模树缺失 → inject_on_tree 生成(真 tree_prune 剪枝)…")
            _run("inject_on_tree.py")

    SV3.mkdir(exist_ok=True)
    items = []
    # 精编树(全技能壳 + 绑定):scale=17
    for d in sorted((SV2 / "scenarios").iterdir()):
        if d.is_dir():
            tr = json.loads((d / "truth.json").read_text(encoding="utf-8"))
            items.append({"suite": "suite_v2", "dir": d.name, "scale": 17, "n_nodes": 17,
                          "kind": tr.get("category", ""), "tier": tr.get("tier", ""),
                          "mech": "full_pipeline", "rel": f"suite_v2/scenarios/{d.name}"})
    # 多规模树(真剪枝):scale=tree_nodes
    if SREAL.exists():
        for d in sorted(SREAL.iterdir()):
            if d.is_dir() and d.name != "_answers" and (d / "rca_input.json").exists():
                ans = json.loads((SREAL / "_answers" / f"{d.name}.json").read_text(encoding="utf-8"))
                items.append({"suite": "suite_real", "dir": d.name, "scale": ans["tree_nodes"],
                              "n_nodes": ans["tree_nodes"], "kind": ans["category"],
                              "tier": ans["truth"]["tier"], "mech": "prune_inject",
                              "rel": f"suite_real/{d.name}"})
    items.sort(key=lambda x: (x["scale"], x["dir"]))
    if args.hard:                                            # 删答案标签 → 逐场景 rca_input_hard.json
        sys.path.insert(0, str(HERE)); from harden_rca import harden
        for it in items:
            p = ROOT / it["rel"] / "rca_input.json"
            (p.with_name("rca_input_hard.json")).write_text(
                json.dumps(harden(json.loads(p.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2),
                encoding="utf-8")
            it["input"] = it["rel"] + "/rca_input_hard.json"
    elif args.corrupt:                                       # 注入错误结论 → 逐场景 rca_input_corrupt.json
        sys.path.insert(0, str(HERE)); from corrupt_rca import corrupt
        ctruth = {}
        for it in items:
            p = ROOT / it["rel"] / "rca_input.json"
            doc, recs = corrupt(json.loads(p.read_text(encoding="utf-8")))
            (p.with_name("rca_input_corrupt.json")).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            it["input"] = it["rel"] + "/rca_input_corrupt.json"; ctruth[it["rel"]] = recs
        (SV3 / "_corrupt_truth.json").write_text(json.dumps(ctruth, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        for it in items:
            it["input"] = it["rel"] + "/rca_input.json"
    by_scale = {}
    for it in items:
        by_scale.setdefault(it["scale"], 0)
        by_scale[it["scale"]] += 1
    (SV3 / "manifest.json").write_text(json.dumps(
        {"binding": {"suite": "suite_v2", "question": "suite_v2/binding_question.json",
                     "answer": "suite_v2/binding.json", "n": 15},
         "mode": "hard" if args.hard else "corrupt" if args.corrupt else "easy",
         "rca_scenarios": items, "by_scale": by_scale, "total_rca": len(items)},
        ensure_ascii=False, indent=2), encoding="utf-8")

    _m = 'HARD 自判节点' if args.hard else ('纠错卷·结论可能有错' if args.corrupt else 'easy 给标签')
    print(f"\n→ v3 就绪【{_m}模式】:1 道绑定 + {len(items)} 道 RCA,跨 {len(by_scale)} 个规模")
    print("   规模分布(节点数→场景数):", dict(sorted(by_scale.items())))
    print("   作答(⛔一次一个,别把多个场景一起读进上下文):")
    print("     · 绑定:读 suite_v2/binding_question.json → 写 suite_v2/binding.json")
    print("     · RCA :逐条按 manifest.rca_scenarios 读各自 input(easy=rca_input.json / hard=rca_input_hard.json)→ 写同目录 rca.json")
    if args.hard:
        print("   ⚠ HARD:输入无 outline/predicate_satisfied,需自己判每个节点 hold/fail/insufficient(见 SKILL「v3 hard」)。")
    if args.corrupt:
        print("   ⚠ 纠错卷:给定结论可能有错,需对照原始数据复核、以数据为准,并在 rca.json 里加 corrections 列出纠正(见 SKILL「纠错卷」);打分加 --corrupt。")
    print("   打分:python score_v3.py   (--use-reference 自测应 100%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
