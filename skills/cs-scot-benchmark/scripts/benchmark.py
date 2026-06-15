#!/usr/bin/env python3
"""cs-scot-benchmark —— 对一份 generate 产物跑 6 项可量化基准,结果按条分类写进 txt。

**不需要 ground truth、不调模型**(纯确定性,可反复跑出同一份数据),证明:
  1) 压缩有效:局部子树 vs 整树+全量事实,省多少字符/token、保留几个节点。
  2) 稳定一致:确定性链路(各分析脚本 + 剪枝)重跑 K 次,是否全部跑通、有无空/崩。
  3) 可复现:K 次重跑,事实 + 剪枝结果是否逐字节一致(SHA 比对)。
  4) 诚实护栏有效:① 真产物过 validate_conditions;② 一组已知好/坏例,护栏必须"拦坏放好"(自测)。
  5) 转换质量:故障树→子树 结构一致 + 整洁度;给 --excel 再与原表逐节点对账。
  6) 内核回归:A–J 内核 30 例合成用例(成立/不成立/数据不足/边界),改动不会静默改坏统计。

用法:
  benchmark.py --produce <generate产物目录> --data chr=<file> --data kpi=<file> [...] [--runs 3] [-o report.txt]

说明:`--data <指标>=<文件>` 的"指标"要和 `scripts/<指标>_analyzer.py` 的名字对上(如 chr ↔ chr_analyzer.py)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS_ROOT = HERE.parent.parent   # .../skills


def _sibling(skill: str, fname: str, override: str | None):
    if override:
        return Path(override)
    cand = SKILLS_ROOT / skill / "scripts" / fname
    return cand if cand.exists() else None


def _norm_hash(text: str) -> str:
    """对 JSON 文本做规范化(排序键)后哈希,避免键序噪声造成假"不一致"。"""
    try:
        obj = json.loads(text)
        text = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    except Exception:
        pass
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _run(cmd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def _run_analyzer(py, script: Path, data: str, out: Path):
    p = _run([py, str(script), str(data), "-o", str(out)])
    if p.returncode != 0 or not out.exists():
        return False, None, (p.stderr or p.stdout or "")[-400:]
    return True, out.read_text(encoding="utf-8"), ""


# ---------- 护栏自测样例(已知好/坏)----------
def _vc_doc(cond):
    return {"summary": {"total": 1, "ok": 1, "insufficient_data": 0, "manual": 0}, "conditions": [cond]}


_VC_BAD = [
    ("流程不匹配", _vc_doc({"id": "x", "class": "A", "condition_text": "N.UECntx.Est.Att - N.UECntx.Est.Succ > 0",
        "fact": {"class": "A", "status": "ok", "predicate_satisfied": True, "resolved_column": "SA_RRC Setup Success Rate"}})),
    ("子因配总率", _vc_doc({"id": "x", "class": "A", "condition_text": "N.QosFlow.FailEst.RNL > 0",
        "fact": {"class": "A", "status": "ok", "predicate_satisfied": True, "resolved_column": "SA_QosFlow Setup Success Rate(22B)"}})),
    ("建立配掉线", _vc_doc({"id": "x", "class": "A", "condition_text": "N.UECntx.Est.Att - N.UECntx.Est.Succ > 0",
        "fact": {"class": "A", "status": "ok", "predicate_satisfied": True, "resolved_column": "SA UEContext Drop Rate"}})),
    ("丢了区分量", _vc_doc({"id": "x", "class": "C", "condition_text": "DCErrType为RNL_Unspecified 且 SUSRLastAvgRsrp<-120",
        "fact": {"class": "C", "status": "ok", "predicate_satisfied": True, "resolved_column": "DCErrType"}})),
    ("无中生有", _vc_doc({"id": "x", "class": "A", "condition_text": "x",
        "fact": {"class": "A", "status": "insufficient_data", "predicate_satisfied": True, "reason": "r"}})),
]
_VC_GOOD = [
    ("合法重述(同流程率)", _vc_doc({"id": "x", "class": "A", "condition_text": "N.QosFlow.Est.Att - N.QosFlow.Est.Succ > 0",
        "fact": {"class": "A", "status": "ok", "predicate_satisfied": True, "resolved_column": "SA_QosFlow Setup Success Rate(22B)"}})),
    ("诚实数据不足", _vc_doc({"id": "x", "class": "A", "condition_text": "N.X.FailEst.AMF > 0",
        "fact": {"class": "A", "status": "insufficient_data", "reason": "KPI 无此计数器列"}})),
]

_VR_INPUT = {"fault_tree": {"pruned_nodes": {
    "n1": {"condition": "无条件进入", "facts": []},
    "n4": {"condition": "CHR DCErrType=RNL_Unspecified", "facts": [{"status": "ok", "predicate_satisfied": True}]},
    "n9": {"condition": "N.QosFlow.FailEst.AMF > 0", "facts": []},
}}}
_VR_BAD = [
    ("伪造成立(无事实节点判成立)", {"rca_result": [], "undetermined": [], "probable_causes": [
        {"Root_cause": "X", "blocked_at": "n9", "support": "y",
         "evidence_path": [{"node": "AMF异常", "node_id": "n9", "judgment": "成立"}]}]}),
    ("假根因(确证路径含数据不足)", {"rca_result": [
        {"Root_cause": "X", "evidence_path": [{"node_id": "n4", "judgment": "成立"},
                                              {"node_id": "n9", "judgment": "数据不足"}]}],
        "probable_causes": [], "undetermined": []}),
]
_VR_GOOD = [
    ("诚实疑似(含不成立节点、理由齐)", {"rca_result": [], "undetermined": [], "probable_causes": [
        {"Root_cause": "RNL异常", "blocked_at": "上下文建立失败", "caveats": "上游信令可能假阴性", "support": "CHR 87 条",
         "evidence_path": [{"node": "上下文建立失败", "node_id": "nX", "judgment": "不成立"},
                           {"node": "RNL异常", "node_id": "n4", "judgment": "成立"}]}]}),
]


def _guard_selftest(vc_path, vr_path, tmp):
    lines, bad_caught, bad_total, good_pass, good_total = [], 0, 0, 0, 0
    if vc_path and vc_path.exists():
        lines.append("  validate_conditions 自测:")
        for name, doc in _VC_BAD:
            bad_total += 1
            f = Path(tmp) / "vc.json"; f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            r = _run([sys.executable, str(vc_path), str(f)])
            hit = r.returncode == 1
            bad_caught += hit
            lines.append(f"    [坏例] {name:<16}: {'拦下 ✓' if hit else '漏过 ✗'}")
        for name, doc in _VC_GOOD:
            good_total += 1
            f = Path(tmp) / "vc.json"; f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            r = _run([sys.executable, str(vc_path), str(f)])
            ok = r.returncode == 0
            good_pass += ok
            lines.append(f"    [好例] {name:<16}: {'放行 ✓' if ok else '误杀 ✗'}")
    else:
        lines.append("  validate_conditions: 未找到(跳过自测)")
    if vr_path and vr_path.exists():
        ip = Path(tmp) / "vr_input.json"; ip.write_text(json.dumps(_VR_INPUT, ensure_ascii=False), encoding="utf-8")
        lines.append("  validate_rca 自测:")
        for name, doc in _VR_BAD:
            bad_total += 1
            f = Path(tmp) / "vr.json"; f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            r = _run([sys.executable, str(vr_path), str(f), str(ip)])
            hit = r.returncode == 1
            bad_caught += hit
            lines.append(f"    [坏例] {name:<22}: {'拦下 ✓' if hit else '漏过 ✗'}")
        for name, doc in _VR_GOOD:
            good_total += 1
            f = Path(tmp) / "vr.json"; f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            r = _run([sys.executable, str(vr_path), str(f), str(ip)])
            ok = r.returncode == 0
            good_pass += ok
            lines.append(f"    [好例] {name:<22}: {'放行 ✓' if ok else '误杀 ✗'}")
    else:
        lines.append("  validate_rca: 未找到(跳过自测)")
    return lines, bad_caught, bad_total, good_pass, good_total


def _node_path(nodes, nid):
    path, seen, cur = [], set(), nid
    while cur and cur in nodes and cur not in seen:
        seen.add(cur); path.append(nodes[cur].get("name")); cur = nodes[cur].get("parent")
    return tuple(reversed(path))


def verify_conversion(index, md_text, excel_path):
    """转换质量:结构一致性 + 整洁度(残留 HTML/图片/空条件/测试名);给 --excel 再和原表逐节点对账。"""
    nodes = index.get("nodes", {})
    lines, problems = [], 0
    # 结构一致性
    struct = []
    for nid, n in nodes.items():
        p = n.get("parent")
        if p is not None and p not in nodes:
            struct.append(f"{nid}.parent 缺失")
        elif p is not None and (n.get("level") or 0) != (nodes[p].get("level") or 0) + 1:
            struct.append(f"{nid} 层级断裂")
        seen, cur = set(), nid
        while cur and cur in nodes:
            if cur in seen:
                struct.append(f"{nid} 成环"); break
            seen.add(cur); cur = nodes[cur].get("parent")
    lines.append(f"  结构一致性: 节点 {len(nodes)};父子/层级/无环 "
                 + ("✓" if not struct else f"✗ {len(struct)} 处({'; '.join(struct[:3])})"))
    problems += len(struct)
    # 整洁度
    tagre = re.compile(r"<[a-zA-Z/][^>]*>")
    html = [n["name"] for n in nodes.values() if n.get("condition") and tagre.search(n["condition"])]
    img = [n["name"] for n in nodes.values() if n.get("condition") and re.search(r"!\[|https?://|<img", n["condition"], re.I)]
    empty = [n["name"] for n in nodes.values() if (not n.get("pass_through")) and not (n.get("condition") or "").strip()]
    testn = [n["name"] for n in nodes.values() if re.search(r"萃取新增节点|测试", n.get("name") or "")]
    lines.append(f"  整洁度: 残留HTML标签 {len(html)}、图片/链接 {len(img)}、空条件判定节点 {len(empty)}、测试节点名 {len(testn)}")
    if html:
        lines.append(f"    HTML 残留(示例): {html[:2]}")
    problems += len(html) + len(img) + len(empty)
    if md_text:
        mdt = len(tagre.findall(md_text))
        lines.append(f"  md 文本残留 HTML 标签: {mdt} 处")
        problems += mdt
    # 节点对账(需 Excel)
    if excel_path:
        try:
            ext = _sibling("cs-scot-generate", "excel_to_tree.py", None)
            sys.path.insert(0, str(ext.parent))
            from excel_to_tree import _read_table, find_level_columns, DEFAULT_EXCLUDE
            headers, rows = _read_table(excel_path)
            lvl = find_level_columns(headers)
            scen = index.get("scenario")
            exre = re.compile(DEFAULT_EXCLUDE) if DEFAULT_EXCLUDE else None
            expected = set()
            for row in rows:
                nms = [(row.get(c) or "").strip() for c, _ in lvl]
                nms = [x for x in nms if x]
                if not nms or (exre and any(exre.search(x) for x in nms)):
                    continue
                if scen in nms:
                    path = tuple(nms[nms.index(scen):])
                    for k in range(1, len(path) + 1):
                        expected.add(path[:k])
            idxp = {_node_path(nodes, nid) for nid in nodes}
            dropped, extra = expected - idxp, idxp - expected
            lines.append(f"  节点对账(vs Excel): Excel 子树 {len(expected)}、index {len(idxp)};"
                         f"匹配 {len(expected & idxp)}、漏 {len(dropped)}、多 {len(extra)}")
            if dropped:
                lines.append(f"    漏(Excel有/index无,示例): {['/'.join(p) for p in list(dropped)[:2]]}")
            if extra:
                lines.append(f"    多(index有/Excel无,示例): {['/'.join(p) for p in list(extra)[:2]]}")
            problems += len(dropped) + len(extra)
        except Exception as e:  # noqa: BLE001
            lines.append(f"  节点对账: 跳过(读 Excel/对账失败: {e})")
    else:
        lines.append("  节点对账: 未给 --excel,跳过(只做结构+整洁度;给 --excel 才能和原表逐节点对账)")
    ok = (problems == 0)
    tail = "、与原表完全对账" if (excel_path and ok) else ""
    lines.append(f"  结论: {('✓ 转换干净、结构一致' + tail) if ok else '⚠ 见上,有残留/不一致/漏多,建议用最新转换器重生成'}")
    return lines, ok


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="cs-scot 产物基准(压缩/稳定/可复现/护栏)")
    ap.add_argument("--produce", required=True, help="generate 产物目录(含 scripts/ tree/)")
    ap.add_argument("--data", action="append", default=[], help="指标=数据文件(可多次),指标对应 <指标>_analyzer.py")
    ap.add_argument("--runs", type=int, default=3, help="确定性链路重跑次数(默认3)")
    ap.add_argument("-o", "--output", help="报告 txt(默认 <produce>/benchmark_report.txt)")
    ap.add_argument("--excel", help="原始故障树 Excel/csv 路径(给了才做'与原表逐节点对账';不给只做结构+整洁度)")
    ap.add_argument("--tree-prune"); ap.add_argument("--validate-conditions"); ap.add_argument("--validate-rca")
    args = ap.parse_args()

    produce = Path(args.produce).resolve()
    scripts_dir, tree_dir = produce / "scripts", produce / "tree"
    analyzers = sorted(scripts_dir.glob("*_analyzer.py"))
    if not analyzers:
        print(f"未找到 {scripts_dir}/*_analyzer.py", file=sys.stderr); return 2
    indicators = {a.stem[:-len("_analyzer")]: a for a in analyzers}
    data_map = {}
    for item in args.data:
        if "=" not in item:
            print(f"--data 应为 指标=文件: {item}", file=sys.stderr); return 2
        k, v = item.split("=", 1); data_map[k.strip()] = v.strip()
    missing = [i for i in indicators if i not in data_map]
    if missing:
        print(f"缺少这些指标的 --data: {missing}(需要: {list(indicators)})", file=sys.stderr); return 2

    idx_files = list(tree_dir.glob("*.tree_index.json"))
    md_files = [p for p in tree_dir.glob("*.md")]
    if not idx_files:
        print(f"未找到 {tree_dir}/*.tree_index.json", file=sys.stderr); return 2
    index = json.loads(idx_files[0].read_text(encoding="utf-8"))
    md_text = md_files[0].read_text(encoding="utf-8") if md_files else ""

    prune_path = _sibling("cs-scot-execute", "tree_prune.py", args.tree_prune)
    if not prune_path:
        print("未找到 tree_prune.py(用 --tree-prune 指定)", file=sys.stderr); return 2
    sys.path.insert(0, str(prune_path.parent))
    from tree_prune import prune  # noqa: E402
    vc_path = _sibling("cs-scot-generate", "validate_conditions.py", args.validate_conditions)
    vr_path = _sibling("fault-tree-rca", "validate_rca.py", args.validate_rca)

    # ---- 跑 K 次确定性链路 ----
    runs = []
    with tempfile.TemporaryDirectory() as td:
        for k in range(args.runs):
            rec = {"ok": {}, "fhash": {}, "facts": {}, "empty": [], "err": {}}
            for ind, script in indicators.items():
                ok, txt, err = _run_analyzer(sys.executable, script, data_map[ind], Path(td) / f"{ind}_{k}.json")
                rec["ok"][ind] = ok
                if ok:
                    rec["fhash"][ind] = _norm_hash(txt)
                    doc = json.loads(txt)
                    rec["facts"][ind] = doc
                    if not doc.get("conditions"):
                        rec["empty"].append(ind)
                else:
                    rec["err"][ind] = err
            if all(rec["ok"].values()):
                pr = prune(index, rec["facts"])
                payload = {"fault_tree": {"scenario": pr["scenario"], "root": pr["root"],
                                         "outline": pr["outline"], "pruned_nodes": pr["pruned_nodes"]},
                           "unmapped_facts": pr["unmapped"], "tree_issues": pr["coverage"]}
                rec["pr"] = pr
                rec["phash"] = _norm_hash(json.dumps(payload, ensure_ascii=False))
                rec["payload_len"] = len(json.dumps(payload, ensure_ascii=False))
            runs.append(rec)

        # ---- 护栏自测 ----
        gt_lines, bc, bt, gp, gt = _guard_selftest(vc_path, vr_path, td)
        # ---- 真产物过 validate_conditions(用 run0 的事实)----
        real_vc = []
        if vc_path and runs and all(runs[0]["ok"].values()):
            for ind in indicators:
                f = Path(td) / "realvc.json"; f.write_text(json.dumps(runs[0]["facts"][ind], ensure_ascii=False), encoding="utf-8")
                r = _run([sys.executable, str(vc_path), str(f)])
                tail = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
                real_vc.append((ind, r.returncode, tail))

    # ---- 汇总 ----
    run0 = runs[0]
    all_ok = all(all(r["ok"].values()) for r in runs)
    # T1 压缩
    t1 = {}
    if "pr" in run0:
        cov = run0["pr"]["coverage"]
        before = len(json.dumps({"fault_tree_md": md_text, "indicator_results": run0["facts"]}, ensure_ascii=False))
        after = run0["payload_len"]
        floored = bool(before) and after >= before          # 零损失下界:剪枝反而更大 → runner 会回退完整树
        eff_after = before if floored else after             # 实际发送的体积(取局部子树/完整树中较小者)
        t1 = {"total": cov["total_nodes"], "kept": cov["kept_nodes"], "before": before, "after": after,
              "eff_after": eff_after, "floored": floored,
              "saved": (100 * (1 - eff_after / before)) if before else 0}
    # T3 可复现
    repro = {}
    for ind in indicators:
        hs = {r["fhash"].get(ind) for r in runs if ind in r["fhash"]}
        repro[ind] = (len(hs) == 1 and None not in hs, next(iter(hs)) if hs else "-")
    phs = {r.get("phash") for r in runs if "phash" in r}
    repro_prune = (len(phs) == 1 and None not in phs, next(iter(phs)) if phs else "-")

    # ---- 第6项:A–J 内核回归自测(地基)----
    ks_path = _sibling("cs-scot-generate", "kernel_selftest.py", None)
    if ks_path:
        sys.path.insert(0, str(ks_path.parent))
        try:
            from kernel_selftest import run_battery  # noqa: E402
            kpassed, ktotal, klines = run_battery()
        except Exception as e:  # noqa: BLE001
            kpassed, ktotal, klines = 0, 0, [f"    内核自测加载失败: {e}"]
    else:
        kpassed, ktotal, klines = 0, 0, ["    未找到 kernel_selftest.py(跳过)"]
    kernel_ok = ktotal > 0 and kpassed == ktotal

    # ---- 产物铭牌(manifest)指纹:哪版格式契约、何时编译;缺 = 旧工具链产物 ----
    mf_line = "无 manifest.json(旧工具链产物;建议重新 generate,带上格式契约版本)"
    mf_path = produce / "manifest.json"
    if mf_path.exists():
        try:
            mf = json.loads(mf_path.read_text(encoding="utf-8"))
            mf_line = f"格式契约 v{mf.get('product_format_version')} · 生成于 {mf.get('generated_at')}"
            asm = _sibling("cs-scot-generate", "assemble_scenario.py", None)
            if asm:
                sys.path.insert(0, str(asm.parent))
                from assemble_scenario import PRODUCT_FORMAT_VERSION  # noqa: E402
                if mf.get("product_format_version") != PRODUCT_FORMAT_VERSION:
                    mf_line += f"  ⚠ ≠ 当前工具链 v{PRODUCT_FORMAT_VERSION},建议重新 generate"
        except Exception as e:  # noqa: BLE001
            mf_line = f"manifest.json 读取失败({e})"

    # ---- 写报告 ----
    L = []
    L.append("=" * 64)
    L.append("cs-scot 产物基准报告(无 ground truth · 纯确定性)")
    L.append(f"产物   : {produce}")
    L.append(f"铭牌   : {mf_line}")
    L.append(f"时间   : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"重跑   : {args.runs} 次   指标: {', '.join(indicators)}")
    L.append("=" * 64)

    L.append("\n【1. 压缩有效】")
    if t1:
        L.append(f"  故障树节点: 总 {t1['total']} → 局部子树保留 {t1['kept']}")
        L.append(f"  RCA 输入(字符): 压缩前(整树+全量事实) ~{t1['before']} → 局部子树 ~{t1['after']}")
        if t1["floored"]:
            L.append("  零损失下界: 局部子树 ≥ 完整树 → 实际回退完整树发送,压缩 0%(此树小/密,本就无可剪)")
        L.append(f"  实际节省: {t1['saved']:.0f}%")
        L.append(f"  结论: {'✓ 有效,省 %.0f%%(树越大省越多)' % t1['saved'] if t1['saved'] > 0 else '≈ 零损失下界生效:本树小/密,保证不倒贴(0%)'}")
    else:
        L.append("  (分析脚本未全部跑通,无法计算)")

    L.append("\n【2. 稳定一致】")
    for ind in indicators:
        cnt = sum(1 for r in runs if r["ok"].get(ind))
        L.append(f"  {ind+'_analyzer':<22}: {cnt}/{args.runs} 次跑通")
    if "pr" in run0:
        pcnt = sum(1 for r in runs if "pr" in r)
        L.append(f"  {'剪枝(tree_prune)':<22}: {pcnt}/{args.runs} 次跑通")
    empties = sorted({i for r in runs for i in r["empty"]})
    L.append(f"  空输出(0 条件)源: {empties if empties else '无'}")
    L.append(f"  结论: {'✓ %d 次全部跑通、无空/崩' % args.runs if all_ok and not empties else '⚠ 见上,有未跑通/空输出'}")

    L.append("\n【3. 可复现(确定性)】")
    for ind in indicators:
        same, h = repro[ind]
        L.append(f"  {ind+' facts':<22}: {'一致 ✓' if same else '不一致 ✗'}  sha={h}")
    if "pr" in run0:
        L.append(f"  {'pruned(局部子树)':<22}: {'一致 ✓' if repro_prune[0] else '不一致 ✗'}  sha={repro_prune[1]}")
    all_repro = all(repro[i][0] for i in indicators) and (repro_prune[0] if "pr" in run0 else True)
    L.append(f"  结论: {'✓ 同输入同输出,逐字节一致(可复现可审)' if all_repro else '✗ 存在不一致,需查非确定性'}")

    L.append("\n【4. 诚实护栏有效】")
    L.append("  (a) 真产物过 validate_conditions:")
    for ind, rc, tail in real_vc:
        L.append(f"    {ind:<10}: {'通过' if rc == 0 else '不合格(见下)'}  {tail}")
    if not real_vc:
        L.append("    (跳过:脚本未全跑通或未找到 validate_conditions)")
    L.append("  (b) 护栏自测(已知好/坏例,必须拦坏放好):")
    L.extend(gt_lines)
    L.append(f"  自测合计: 拦坏 {bc}/{bt}、放好 {gp}/{gt}")
    guard_ok = (bc == bt and gp == gt and bt > 0)
    L.append(f"  结论: {'✓ 护栏对已知坏例 100% 拦截、好例 100% 放行' if guard_ok else '⚠ 见上,护栏有漏/误杀'}")

    L.append("\n【5. 转换质量(故障树→子树)】")
    vlines, verify_ok = verify_conversion(index, md_text, args.excel)
    L.extend(vlines)

    L.append("\n【6. 内核回归自测(A–J 地基)】")
    L.append(f"  合成用例 {kpassed}/{ktotal} 通过(覆盖 成立/不成立/数据不足/边界:空表·缺列·%值·transform·require·三态组合)")
    if not kernel_ok:
        L.extend(klines)          # 过了只报总数;有回归才铺开逐条,方便定位
    L.append(f"  结论: {'✓ A–J 内核全部通过回归(改动有护栏、不会静默改坏统计)' if kernel_ok else '✗ 见上,内核有回归,先修再用'}")

    L.append("\n" + "=" * 64)
    verdict = all([bool(t1) and t1.get("saved", 0) >= 0, all_ok, all_repro, guard_ok, verify_ok, kernel_ok])
    L.append(f"总评: {'✓ 六项基准均达标' if verdict else '⚠ 部分项需关注(见各条结论)'}")
    L.append("注:本基准只证明 省/稳/可复现/诚实,**不证明诊断准确率**(无 ground truth);准确率需另用合成故障注入评测。")
    L.append("=" * 64)

    report = "\n".join(L)
    out = Path(args.output) if args.output else (produce / "benchmark_report.txt")
    out.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\n[benchmark] 报告已写入 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
