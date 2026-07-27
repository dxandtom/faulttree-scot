#!/usr/bin/env python3
"""业务脚本收编 · 第 2 步:纪要结果校验合并 → tree/script_digests.json(+solutions 合并)。

吃 align_scripts.py 派发、子代理写回的 <OUT>/adopt/digest_results/batch<N>.json,
机械校验后合并:
- node_id 必须在 tree_index 里(编造节点响亮拒收);digest 非空;
- 完成标记 # BATCH_COMPLETE 缺失 = 疑似截断,该批拒收点名(向后兼容:纯 JSON 数组无标记也认,
  但会提示);
- 合并为 <OUT>/tree/script_digests.json:{node_id: {digest, commands, regexes, depends?,
  source_script, name_zh?}}——make_dispatch 据此把纪要逐条附进绑定简报;
- solution_zh 非空的,并进 <OUT>/tree/solutions.json(已有条目**不覆盖**);
- 对账:adoption_map 里对齐了脚本却没交纪要的节点点名(消化不许静默漏)。

用法:merge_digests.py --out <产物目录>
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


def parse_result(p: Path):
    """结果文件 → (entries, 有无完成标记)。json 优先,ast 兜底(弱模型爱写单引号)。"""
    text = p.read_text(encoding="utf-8")
    has_marker = "# BATCH_COMPLETE" in text
    body = text.replace("# BATCH_COMPLETE", "").strip()
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        try:
            d = ast.literal_eval(body)
        except (ValueError, SyntaxError) as e:
            raise ValueError(f"解析失败:{e}")
    if isinstance(d, dict):
        d = d.get("results") or d.get("digests") or [d]
    if not isinstance(d, list):
        raise ValueError("结果须是 JSON 数组(每节点一条)")
    return d, has_marker


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="纪要结果校验合并 → tree/script_digests.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out).expanduser().resolve()

    tis = sorted((out / "tree").glob("*.tree_index.json"))
    if not tis:
        raise SystemExit(f"{out}/tree 下没有 tree_index——产物不完整")
    nodes = (json.loads(tis[0].read_text(encoding="utf-8")).get("nodes") or {})

    amap_p = out / "adopt" / "adoption_map.json"
    amap = json.loads(amap_p.read_text(encoding="utf-8")) if amap_p.exists() else []
    by_node = {str(e.get("node_id")): e for e in amap if isinstance(e, dict)}

    rdir = out / "adopt" / "digest_results"
    files = sorted(rdir.glob("batch*.json")) if rdir.is_dir() else []
    if not files:
        raise SystemExit(f"{rdir} 下没有结果文件——先按 digest_task_batch<N>.txt 派子代理产出")

    digests, problems, no_marker = {}, [], []
    for f in files:
        try:
            entries, marker = parse_result(f)
        except ValueError as e:
            problems.append(f"{f.name}:{e}")
            continue
        if not marker:
            no_marker.append(f.name)
        for e in entries:
            if not isinstance(e, dict):
                problems.append(f"{f.name}:非 dict 条目 {str(e)[:60]}")
                continue
            nid = str(e.get("node_id") or "")
            if nid not in nodes:
                problems.append(f"{f.name}:node_id「{nid}」不在树上(别编节点)")
                continue
            dg = str(e.get("digest") or "").strip()
            if not dg:
                problems.append(f"{f.name}:{nid} 的 digest 为空")
                continue
            rec = {"digest": dg,
                   "commands": [str(c) for c in (e.get("commands") or []) if str(c).strip()],
                   "regexes": [str(r) for r in (e.get("regexes") or []) if str(r).strip()],
                   "source_script": (by_node.get(nid) or {}).get("script_local")
                                    or (by_node.get(nid) or {}).get("script") or ""}
            if str(e.get("depends") or "").strip():
                rec["depends"] = str(e["depends"]).strip()
            if str(e.get("solution_zh") or "").strip():
                rec["solution_zh"] = str(e["solution_zh"]).strip()
            digests[nid] = rec

    if problems:
        print(f"不合格:{len(problems)} 处问题(全部修完才合并,别静默丢):")
        for p in problems:
            print(f"  - {p}")
        return 1

    dst = out / "tree" / "script_digests.json"
    dst.write_text(json.dumps(digests, ensure_ascii=False, indent=2), encoding="utf-8")

    sols_p = out / "tree" / "solutions.json"
    sols = {}
    if sols_p.exists():
        try:
            sols = json.loads(sols_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[warn] 已有 solutions.json 解析失败,不动它")
            sols = None
    added = 0
    if sols is not None:
        for nid, rec in digests.items():
            if rec.get("solution_zh") and nid not in sols:
                sols[nid] = {"zh": rec["solution_zh"]}
                added += 1
        if added:
            sols_p.write_text(json.dumps(sols, ensure_ascii=False, indent=2), encoding="utf-8")

    expected = [e for e in amap if e.get("script_local")]
    missing = [f"{e['node_id']}「{e.get('node')}」" for e in expected if str(e["node_id"]) not in digests]
    print(f"已合并 {len(digests)} 条纪要 → {dst}" + (f";solutions 新增 {added} 条" if added else ""))
    if no_marker:
        print(f"⚠ 无完成标记的结果文件:{', '.join(no_marker)}(可能被截断,建议核对条数)")
    if missing:
        print(f"⚠ 对齐了脚本但没交纪要的节点 {len(missing)} 个(消化别静默漏):")
        for m in missing[:10]:
            print(f"  - {m}")
    print("下一步:重跑 make_dispatch.py——绑定简报会把纪要逐条附在对应判据下(字符预算自动分批)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
