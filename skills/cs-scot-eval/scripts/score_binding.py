#!/usr/bin/env python3
"""绑定准确率打分 —— 模型产的 CONDITIONS(binding.json)对【金标准绑定】逐节点比。

测的是 generate 第 2.5 步那件最易错的活:把"大白话树判据"映射到"系统数据列",并选对内核类别、参数。
三维:
  类别命中  —— 选对 A/B/C/E/G/H 内核类别;
  选列命中  —— **选对系统列**(语义对列,最难;含 B 的 minuend/subtrahend、E 的 group_by、H 的 msg_column);
  参数命中  —— op/阈值 / contains 值 / lacks 等关键参数对;
完全正确 = 三者都对。数据缺列的节点(金标准 decision:insufficient_data)→ 模型应判数据不足,判对才算。

用法:
  score_binding.py [--suite <目录,默认 ../suite_real>] [--use-reference]
  模型答案放 <场景>/binding.json(CONDITIONS 列表);--use-reference 用金标准当输入自测(应 100%)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SUITE = HERE.parent / "suite_real"


def _norm(s):
    return str(s or "").strip().lower().replace(" ", "")


def _cols(e: dict) -> set:
    s = set()
    for k in ("column", "group_by", "msg_column", "param", "feature"):
        if e.get(k):
            s.add(_norm(e[k]))
    for k in ("minuend", "subtrahend"):
        for x in e.get(k) or []:
            s.add(_norm(x))
    return s


def _cls(e: dict) -> str:
    """内核类别:认 class 及常见同义 key(category/kind/cls)——key 名写错不该把"选对了类别"判成错。"""
    for k in ("class", "category", "kind", "cls"):
        if e.get(k):
            return _norm(e[k])
    return ""


def _is_insuf(e: dict) -> bool:
    return str(e.get("decision", "")).startswith("insufficient") or str(e.get("status", "")).startswith("insufficient")


def _params(e: dict) -> set:
    """关键参数集合(归一化):op/阈值、取值匹配。取值认 contains 及同义写法(value/values/equals,
    标量或列表都行)统一归到 contains;lacks/has 保留。"""
    s = set()
    if e.get("op") is not None:
        s.add("op=" + _norm(e["op"]))
    if e.get("threshold") is not None:
        try:
            s.add("thr=" + format(float(e["threshold"]), ".4g"))
        except Exception:
            s.add("thr=" + _norm(e["threshold"]))
    for k in ("contains", "equals", "value", "values", "lacks", "has"):
        v = e.get(k)
        if v is None:
            continue
        tag = k if k in ("lacks", "has") else "contains"     # value/values/equals/contains 同义 → contains
        for x in (v if isinstance(v, list) else [v]):
            s.add(tag + ":" + _norm(x))
    return s


def _model_list(rca):
    if isinstance(rca, list):
        return rca
    for k in ("CONDITIONS", "conditions", "bindings", "binding"):
        if isinstance(rca, dict) and isinstance(rca.get(k), list):
            return rca[k]
    return []


def score_one(scn_dir: Path, use_reference: bool) -> dict:
    ans = json.loads((scn_dir.parent / "_answers" / f"{scn_dir.name}.json").read_text(encoding="utf-8"))
    gold = ans.get("gold_binding") or []
    base = {"scenario": scn_dir.name, "category": ans.get("category"), "tree_nodes": ans.get("tree_nodes")}
    if use_reference:
        model = gold
    else:
        f = scn_dir / "binding.json"
        if not f.exists():
            return {**base, "status": "pending"}
        model = _model_list(json.loads(f.read_text(encoding="utf-8")))
    mby = {str(m.get("node_id")): m for m in model if isinstance(m, dict)}

    n = cls_ok = col_ok = par_ok = full_ok = 0
    misses = []
    for g in gold:
        nid = str(g.get("node_id"))
        n += 1
        m = mby.get(nid)
        if _is_insuf(g):                                   # 金标准:该判数据不足(数据里没这列)
            ok = (m is None) or _is_insuf(m) or not _cols(m)
            cls_ok += ok; col_ok += ok; par_ok += ok; full_ok += ok
            if not ok:
                misses.append(f"{nid}(应判数据不足却绑了 {sorted(_cols(m))})")
            continue
        if not m:
            misses.append(f"{nid}(漏绑)")
            continue
        c_ok = _cls(m) == _cls(g) and bool(_cls(g))
        l_ok = _cols(m) == _cols(g) and bool(_cols(g))
        p_ok = _params(m) >= _params(g) and bool(_params(g) or True)   # 模型参数需覆盖金标准关键参数
        cls_ok += c_ok; col_ok += l_ok; par_ok += p_ok
        full = c_ok and l_ok and p_ok
        full_ok += full
        if not full:
            why = [x for x, ok in (("类别", c_ok), ("选列", l_ok), ("参数", p_ok)) if not ok]
            misses.append(f"{nid}({'/'.join(why)}错;选列{sorted(_cols(m))}vs{sorted(_cols(g))})")
    return {**base, "status": "scored", "n": n, "cls": cls_ok, "col": col_ok, "par": par_ok, "full": full_ok,
            "ok": full_ok == n and n > 0, "misses": misses}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="绑定准确率打分(模型 CONDITIONS vs 金标准)")
    ap.add_argument("--suite", default=str(DEFAULT_SUITE))
    ap.add_argument("--use-reference", action="store_true", help="用金标准当输入自测(应 100%)")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    suite = Path(args.suite)
    scns = sorted(d for d in suite.iterdir() if d.is_dir() and not d.name.startswith("_")
                  and (d / "binding_question.json").exists())
    rows = [score_one(d, args.use_reference) for d in scns]
    scored = [r for r in rows if r["status"] == "scored"]

    L = ["=" * 84, "绑定准确率评测(大白话树判据 → 系统列;模型 CONDITIONS vs 金标准)"
         + ("· 金标准自测" if args.use_reference else ""), "=" * 84,
         f"{'场景':<34}{'判据数':<7}{'类别':<9}{'选列':<9}{'参数':<9}{'完全正确'}"]
    tot = dict(n=0, cls=0, col=0, par=0, full=0)
    for r in scored:
        for k in tot:
            tot[k] += r[k]
        L.append(f"{r['scenario']:<34}{r['n']:<7}{r['cls']}/{r['n']:<7}{r['col']}/{r['n']:<7}{r['par']}/{r['n']:<7}{r['full']}/{r['n']}"
                 + ("" if r["ok"] else "  ✗ " + "; ".join(r["misses"][:2])))
    pend = [r for r in rows if r["status"] == "pending"]
    L.append("-" * 84)
    if tot["n"]:
        L.append(f"合计({len(scored)} 场景 / {tot['n']} 条判据): "
                 f"类别命中 {tot['cls']}/{tot['n']}({100*tot['cls']/tot['n']:.0f}%)、"
                 f"选列命中 {tot['col']}/{tot['n']}({100*tot['col']/tot['n']:.0f}%)、"
                 f"参数命中 {tot['par']}/{tot['n']}({100*tot['par']/tot['n']:.0f}%)")
        L.append(f"完全正确(类别+选列+参数都对): {tot['full']}/{tot['n']} = {100*tot['full']/tot['n']:.0f}%")
    if pend:
        L.append(f"\n待评分 {len(pend)} 个(先让模型读 binding_question+data 产出 binding.json):{[r['scenario'] for r in pend]}")
    L.append("注:测的是 generate 绑定阶段——把大白话判据映射到系统列、选对内核与参数;选列命中率反映语义对列能力。")
    L.append("=" * 84)
    report = "\n".join(L)
    print(report)
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
        print(f"\n[score_binding] → {args.output}")
    return 0 if not pend and tot.get("full") == tot.get("n") else (0 if pend else 1)


if __name__ == "__main__":
    sys.exit(main())
