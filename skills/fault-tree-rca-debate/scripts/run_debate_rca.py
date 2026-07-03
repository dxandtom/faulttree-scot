#!/usr/bin/env python3
"""辩论式 RCA 的执行壳:吃 rca_input.json + tree_index.json,吐 rca.json(四档契约)。

在流水线里的位置 = 原 fault-tree-rca 手工步骤的**平替**(runner/plan/generate 零改动):
    runner 备好 result/rca_input.json 后,原来"请用 fault-tree-rca 技能手工产出",
    现在跑本脚本即可(它自己调 LLM,不需要 harness 模型)。

流程:facts_to_question 渲染证据文本 → tree_to_vendor_xlsx 生成引擎格式故障树 →
      调 vendor 引擎(--mock 用假引擎,真引擎见 vendor/README.md)→
      result_adapter 出 rca.json(--mechanism all 时三份都出,便于 A/B/C 评测)→
      原始引擎输出落 rca_debate_full.json(排查/审计用)。

用法:
  run_debate_rca.py --rca-input <result/rca_input.json> --tree-index <tree/<场景>.tree_index.json> \
      --out-dir <result/> [--mechanism original|softmax|ratio|all] [--k 3] [--mock] \
      [--vendor-dir <默认 ../vendor>] [--config <默认 vendor/config.yaml>]
产物:<out-dir>/rca.json(=所选机制;all 时=original)+ rca.<机制>.json ×3(all 时)+ rca_debate_full.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from facts_to_question import build_question          # noqa: E402
from result_adapter import to_rca_json, _MECH_KEY     # noqa: E402
from tree_to_vendor_xlsx import write_xlsx            # noqa: E402


def _load_processor(vendor_dir: Path, config_path: Path | None, mock: bool):
    """→ (processor, 描述串)。真实 vendor 的接口差异只会打到这一个函数里(见 INTEGRATION.md)。"""
    if mock:
        from mock_vendor import FaultTreeProcessor
        return FaultTreeProcessor({}), "mock(确定性假引擎,不调 LLM)"
    sys.path.insert(0, str(vendor_dir))
    import yaml
    cfg_file = config_path or (vendor_dir / "config.yaml")
    cfg = (yaml.safe_load(Path(cfg_file).read_text(encoding="utf-8")) or {}) if Path(cfg_file).exists() else {}
    if not cfg:
        print(f"[warn] 未读到配置 {cfg_file}(不存在或为空)——引擎参数将全用默认/空。")

    # 接口适配钩子:真引擎接口与文档不符时,本地**只新建** vendor/bridge.py 包一层(暴露文档形状的
    # FaultTreeProcessor(cfg_dict) 三方法),不改本壳、不改引擎原件。有 bridge 就优先走 bridge。
    if (vendor_dir / "bridge.py").exists():
        from bridge import FaultTreeProcessor as BridgeProcessor  # type: ignore
        return BridgeProcessor(cfg), f"vendor 引擎(经 vendor/bridge.py 适配,config={cfg_file})"

    try:
        from fault_tree_evaluator_bfs_advanced import FaultTreeProcessor, EvaluationConfig  # type: ignore
    except ImportError as e:
        raise SystemExit(f"未找到真实引擎:{vendor_dir}/fault_tree_evaluator_bfs_advanced.py({e})。"
                         f"把同事的两个文件放进 vendor/(见其 README),或先用 --mock 自测适配层。")
    import dataclasses
    known = {f.name for f in dataclasses.fields(EvaluationConfig)}
    dropped = sorted(set(cfg) - known)
    if dropped:
        print(f"[warn] config.yaml 里有 EvaluationConfig 不认识的键,已忽略:{dropped}")
    return FaultTreeProcessor(EvaluationConfig(**{k: v for k, v in cfg.items() if k in known})), \
        f"vendor 引擎(config={cfg_file})"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="辩论式 RCA 执行壳(rca_input → rca.json)")
    ap.add_argument("--rca-input", required=True)
    ap.add_argument("--tree-index", required=True)
    ap.add_argument("--out-dir", required=True, help="通常就是产物的 result/ 目录")
    ap.add_argument("--mechanism", default="all", choices=list(_MECH_KEY) + ["all"])
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--mock", action="store_true", help="用确定性假引擎跑通适配层(不调 LLM)")
    ap.add_argument("--vendor-dir", default=str(_HERE.parent / "vendor"))
    ap.add_argument("--config", help="vendor 配置(默认 <vendor-dir>/config.yaml)")
    ap.add_argument("--leaf-only", action="store_true", help="生成 xlsx 时中间节点条件不单独成行")
    ap.add_argument("--no-validate", action="store_true", help="跳过产出后的 validate_rca 自检")
    a = ap.parse_args()

    rca_input = json.loads(Path(a.rca_input).read_text(encoding="utf-8"))
    tree_index = json.loads(Path(a.tree_index).read_text(encoding="utf-8"))
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    question = build_question(rca_input, tree_index)
    print(f"[1/4] 证据 question 已渲染({len(question)} 字符)")

    proc, desc = _load_processor(Path(a.vendor_dir), Path(a.config) if a.config else None, a.mock)
    # xlsx 落在 out_dir(不用临时目录):真/假引擎都可能持有文件句柄,Windows 上临时目录退出清理
    # 会 WinError 32;落盘产物顺带可审计(引擎实际吃到的树长什么样,一看便知)。
    xlsx = out_dir / "vendor_fault_tree.xlsx"
    n_rows = write_xlsx(tree_index, xlsx, a.leaf_only)
    print(f"[2/4] vendor 格式故障树已生成({n_rows} 行 → {xlsx.name});引擎={desc}")
    df = proc.load_fault_tree(str(xlsx))
    tree = proc.build_tree_structure(df)
    top_orig, top_soft, top_ratio, debate_records = proc.find_top_k_paths(tree, question, a.k)

    full = {"timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "question": question, "k": a.k,
            "top_k_paths_original": top_orig, "top_k_paths_softmax": top_soft,
            "top_k_paths_ratio": top_ratio, "debate_records": debate_records}
    (out_dir / "rca_debate_full.json").write_text(json.dumps(full, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
    print(f"[3/4] 引擎原始输出 → {out_dir / 'rca_debate_full.json'}"
          f"(路径 {len(top_orig)}/{len(top_soft)}/{len(top_ratio)},辩论 {len(debate_records)} 节点)")

    mechs = list(_MECH_KEY) if a.mechanism == "all" else [a.mechanism]
    for m in mechs:
        doc = to_rca_json(full, m, tree_index, rca_input)
        (out_dir / f"rca.{m}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    chosen = "original" if a.mechanism == "all" else a.mechanism
    (out_dir / "rca.json").write_text(
        (out_dir / f"rca.{chosen}.json").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[4/4] rca.json(机制={chosen})+ {', '.join('rca.' + m + '.json' for m in mechs)} → {out_dir}/")

    # 顺手机械自检(与老路径同一道闸门;闸门脚本不在预期位置就只提示、不阻断)
    if not a.no_validate:
        vr = _HERE.parent.parent / "fault-tree-rca" / "scripts" / "validate_rca.py"
        if vr.exists():
            import subprocess
            r = subprocess.run([sys.executable, str(vr), str(out_dir / "rca.json"), a.rca_input],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            tailline = (r.stdout or r.stderr or "").strip().splitlines()
            print(f"[validate_rca] exit {r.returncode}:{tailline[-1] if tailline else ''}")
            if r.returncode != 0:
                return r.returncode
        else:
            print(f"[validate_rca] 未找到 {vr},跳过自检(手动跑:validate_rca.py <rca.json> <rca_input.json>)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
