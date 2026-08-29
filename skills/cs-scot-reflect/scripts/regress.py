#!/usr/bin/env python3
"""regress.py - 一键回归驱动:技能 + 用例库 → 逐用例[确定性执行 + 机械 RCA] → 判卷 → 汇总

三个入口(按手上有什么选,SKILL.md 的「一键回归」章有对照表):
    A. 只有 tester 交付包(还没有用例库):
       python regress.py --skill-dir <技能> --packages <包目录> [--map 对应表.xlsx] \\
                         --case-dir <建库输出目录> --out <工作目录>
    B. 已有用例库(日常回归主路径,case_gen 不再跑):
       python regress.py --skill-dir <技能> --case-dir <用例库> --out <工作目录>
    C. 已有执行结果只想判卷:
       python regress.py --skill-dir <技能> --case-dir <用例库> --out <工作目录> \\
                         --eval-only --result-dir <既有结果根>

职责边界(硬性,不是偷懒):
- 执行段只跑**确定性链**:runner(自动 --data raw=<用例data>,另认用例内 data_map.json 声明的表)
  → draft_rca 机械起草 → finalize_rca 机械合成(不给裁决单)。
- finalize 拦下未机械定档的候选(真冲突需权衡)→ 该用例记「pending_adjudication」挂起点名,
  **本脚本永不代裁**——裁决是模型/人的判断,照 fault-tree-rca 的法则在该结果目录里补
  decisions.json 后重跑 finalize+render,再单用例判卷即可。
- 一键回归供日常回归;要对外讲的量化数字仍按 A/B 双会话规格(诊断会话不读 expected.json)。

数据喂法(机械规则):
- 恒喂 --data raw=<该用例 data 目录>(S 类脚本的原始数据通道,实战高频事故就是漏它);
- 用例目录下若有 data_map.json({"cmd": "相对data的路径或绝对路径", ...}),逐条加 --data;
  没有就只喂 raw——单源表绑定会按「数据不足」对待(runner 有汇总),这是入口 B 的声明边界:
  表映射属「形态识别」模型步,机械层不猜;需要表的用例请落 data_map.json(一次落地反复复用)。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPTS_DIR.parents[1]                     # .../skills/


def _run(cmd, timeout, log_path: Path):
    """跑子命令,stdout/stderr 合并落文件(防长输出灌爆上下文),返回 (exitcode, 尾部摘要)。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([sys.executable] + [str(c) for c in cmd], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        text = (r.stdout or "") + ("\n--- stderr ---\n" + r.stderr if r.stderr else "")
        log_path.write_text(text, encoding="utf-8")
        return r.returncode, text[-1200:]
    except subprocess.TimeoutExpired:
        log_path.write_text(f"TIMEOUT after {timeout}s: {cmd}", encoding="utf-8")
        return 124, f"超时({timeout}s)"


def find_assets(skill_dir: Path) -> Path | None:
    """产物根 = 含 plan.json 的目录:<skill>/assets(打包形态)或 skill_dir 自身(裸产物)。"""
    for c in (skill_dir / "assets", skill_dir):
        if (c / "plan.json").exists():
            return c
    return None


_BUNDLE = "cs-scot-generate-optimize"                    # 打包发布的聚合技能,内含各组件副本


def find_runner(skill_dir: Path) -> Path:
    """执行器解析,按序:①打包技能自带副本(<skill>/scripts/runner.py,随产物同代分发,
    实战 B 会话用的就是它);②同门 cs-scot-execute;③聚合技能里的副本
    (cs-scot-generate-optimize 打包发布时内含各组件目录)。都没有才硬停并给修法。"""
    bundle = SKILLS_ROOT / _BUNDLE
    cands = [skill_dir / "scripts" / "runner.py",
             SKILLS_ROOT / "cs-scot-execute" / "scripts" / "runner.py",
             bundle / "cs-scot-execute" / "scripts" / "runner.py",
             bundle / "execute" / "scripts" / "runner.py"]
    for c in cands:
        if c.exists():
            return c
    if bundle.is_dir():                                   # 聚合技能的子目录名各版本不一,浅层兜底搜一把
        hits = sorted(bundle.glob("*/scripts/runner.py"))
        if hits:
            return hits[0]
    raise SystemExit("找不到执行器 runner.py(技能自带 scripts/、同门 cs-scot-execute、"
                     f"聚合技能 {_BUNDLE} 里都没有)——用最新打包重出技能,或部署 cs-scot-execute")


def find_rca_scripts(assets: Path) -> Path:
    """RCA 机械链脚本目录,按序:①产物内挂副本(与产物同代);②同门 fault-tree-rca;
    ③聚合技能 cs-scot-generate-optimize 里的副本。"""
    vend = assets / "fault-tree-rca" / "scripts"
    if (vend / "draft_rca.py").exists():
        return vend
    for d in (SKILLS_ROOT / "fault-tree-rca" / "scripts",
              SKILLS_ROOT / _BUNDLE / "fault-tree-rca" / "scripts"):
        if (d / "draft_rca.py").exists():
            print(f"[regress] 产物内挂 fault-tree-rca 缺 draft_rca.py(旧代副本)→ 用 {d}"
                  "(建议跑一次 migrate 刷新产物副本,保持版本同代)")
            return d
    raise SystemExit("找不到 draft_rca.py(产物 assets/fault-tree-rca、同门 fault-tree-rca、"
                     f"聚合技能 {_BUNDLE} 里都没有)——先用 migrate 刷新产物或补全部署")


def case_suffix(case_id: str, idx: int) -> str:
    """runner --result-suffix 只认[0-9A-Za-z_-]:取 case_id 首段(nX 惯例);清洗后为空退 c<序号>。
    对号不靠它兜底——run.json 会记数据路径(eval_run 对号阶梯②出生证明)。"""
    seg = case_id.split("-", 1)[0]
    return re.sub(r"[^0-9A-Za-z_-]", "", seg) or f"c{idx}"


_NE_KINDS = {"cmd", "config", "log", "diag", "logbuf"}


def find_raw_root(data_dir: Path) -> Path:
    """raw 通道的正确层级=网元层的父目录(其下是 <网元>/CMD|CONFIG|LOG/…)。
    用例数据常在外面多包一层(如 formated_result/),直接拿 data 目录当 raw 会让 S 适配器
    报"不合布局"整体判不了。这里用与 check_data 建议同源的**确定性**规则定位:本层任一
    子目录(网元)底下有 CMD/CONFIG/LOG 类子目录 → 本层就是 raw 根;否则仅当只有唯一
    子目录时才下探一层(多岔不猜),最多探 4 层;探不到原样返回(适配器的报错自带正确喂法)。"""
    cur = data_dir
    for _ in range(4):
        try:
            subs = [d for d in cur.iterdir() if d.is_dir()]
        except OSError:
            return data_dir
        for ne in subs:
            try:
                if any(g.is_dir() and g.name.lower() in _NE_KINDS for g in ne.iterdir()):
                    return cur
            except OSError:
                continue
        if len(subs) != 1:
            return data_dir
        cur = subs[0]
    return data_dir


def build_data_args(data_dir: Path):
    """--data 参数(机械):恒带 raw=<网元层父目录>(见 find_raw_root);
    data_map.json 在场则逐条追加(相对 data 解析)。"""
    raw_root = find_raw_root(data_dir)
    if raw_root != data_dir:
        print(f"  raw 根已定位到网元层父目录:{raw_root}(data 外多包了一层)")
    pairs = {"raw": str(raw_root)}
    mp = data_dir.parent / "data_map.json"
    if mp.exists():
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {mp} 解析失败({e}),忽略——本用例只喂 raw")
            m = {}
        for k, v in (m.items() if isinstance(m, dict) else []):
            p = Path(v)
            p = p if p.is_absolute() else data_dir / p
            if p.exists():
                pairs[str(k)] = str(p)
            else:
                print(f"[warn] data_map.json 的 {k} 指向不存在的文件 {p},跳过该条")
    return [a for k, v in pairs.items() for a in ("--data", f"{k}={v}")]


def newest_result_dir(result_root: Path, before: set):
    fresh = [p for p in result_root.glob("result-*") if p.is_dir() and p.name not in before]
    return max(fresh, key=lambda p: p.stat().st_mtime) if fresh else None


def find_rca_input(rdir: Path):
    cand = rdir / "rca_input.json"
    if cand.exists():
        return cand
    hits = sorted(rdir.glob("*_input.json"))
    return hits[0] if hits else None


def find_eval_report(eval_out: Path, case_id: str):
    """eval_run 输出目录形如 <case_id>-vN-<时间戳>/evaluation_report.json,取最新一份。"""
    dirs = [p for p in eval_out.glob(f"{case_id}-v*") if (p / "evaluation_report.json").exists()]
    if not dirs:
        return None
    latest = max(dirs, key=lambda p: p.stat().st_mtime)
    try:
        rep = json.loads((latest / "evaluation_report.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    qs = None
    qp = latest / "quality_score.json"
    if qp.exists():
        try:
            qs = json.loads(qp.read_text(encoding="utf-8")).get("total_score")
        except Exception:  # noqa: BLE001
            pass
    vd = rep.get("verdict_detail") or {}
    return {"report_dir": str(latest), "verdict": (rep.get("meta") or {}).get("verdict") or vd.get("verdict"),
            "error_class": vd.get("error_class"), "error_category": vd.get("error_category"),
            "quality_score": qs}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="一键回归:逐用例[确定性执行+机械RCA]→判卷→汇总(冲突挂起,不代裁)")
    ap.add_argument("--skill-dir", required=True, help="场景技能根(或裸产物目录)")
    ap.add_argument("--case-dir", required=True, help="用例库目录(入口A时=case_gen 的输出落点)")
    ap.add_argument("--packages", help="入口A:tester 交付包目录——先 case_gen 建库再回归")
    ap.add_argument("--map", dest="map_path", default="", help="入口A可选:节点↔zip 对应表(透传 case_gen)")
    ap.add_argument("--out", required=True, help="工作目录(结果/判卷/日志/汇总都落它下面)")
    ap.add_argument("--eval-only", action="store_true", help="入口C:跳过执行,只对既有结果判卷")
    ap.add_argument("--pending-as-fail", action="store_true",
                    help="(这已是默认口径,参数保留兼容)待裁决用例按不通过(FAIL)计入准确率分母")
    ap.add_argument("--pending-hold", action="store_true",
                    help="有人盯的场合用:待裁决用例挂起点名、不计准确率分母,按 fault-tree-rca"
                         " 法则补 decisions.json 后重跑 finalize+render 再补判。"
                         "默认不挂起——机械链定不了档=这轮没优化好,直接按不通过计(无人值守口径)")
    ap.add_argument("--result-dir", help="入口C:既有结果根目录(--eval-only 时必填)")
    ap.add_argument("--case", action="append", default=[], help="只跑这些用例(可多次;默认=库里全部)")
    a = ap.parse_args()

    skill_dir = Path(a.skill_dir).resolve()
    case_dir = Path(a.case_dir).resolve()
    out = Path(a.out).resolve()
    logs = out / "logs"
    out.mkdir(parents=True, exist_ok=True)

    # 入口A:先建库(一次性资产动作;库已在时别重复建——case_gen 自身有 --force 语义,这里不透传)
    if a.packages:
        cg = [SCRIPTS_DIR / "case_gen.py", "--product", skill_dir, "--packages", a.packages,
              "--cases-out", case_dir] + (["--map", a.map_path] if a.map_path else [])
        print(f"[regress] 入口A:先从交付包建用例库 → {case_dir}")
        code, tail = _run(cg, 900, logs / "case_gen.log")
        print(tail)
        if code != 0:
            print(f"[regress] case_gen 失败(exit {code},日志 {logs / 'case_gen.log'}),回归终止")
            return 2
    if not case_dir.exists():
        print(f"[regress] 用例库不存在:{case_dir}(先用 --packages 建库,或核对路径)")
        return 2

    cases = sorted(d.name for d in case_dir.iterdir()
                   if d.is_dir() and (d / "expected.json").exists())
    if a.case:
        pick = set(a.case)
        missing_pick = pick - set(cases)
        if missing_pick:
            print(f"[regress] --case 指定的用例不在库里:{', '.join(sorted(missing_pick))}")
            return 2
        cases = [c for c in cases if c in pick]
    if not cases:
        print(f"[regress] 用例库里没有带 expected.json 的用例:{case_dir}")
        return 2
    print(f"[regress] 用例 {len(cases)} 个;工作目录 {out}")

    if a.eval_only:
        if not a.result_dir:
            print("[regress] --eval-only 需要 --result-dir(既有结果根)")
            return 2
        result_root = Path(a.result_dir).resolve()
    else:
        result_root = out / "results"
        result_root.mkdir(parents=True, exist_ok=True)
        assets = find_assets(skill_dir)
        if assets is None:
            print(f"[regress] {skill_dir} 下找不到 plan.json(assets/ 或根)——不是可执行产物;"
                  "只判卷请用 --eval-only --result-dir")
            return 2
        runner = find_runner(skill_dir)
        rca_scripts = find_rca_scripts(assets)
        solutions = assets / "tree" / "solutions.json"

    eval_out = out / "eval"
    rows = []
    for i, cid in enumerate(cases):
        data_dir = case_dir / cid / "data"
        row = {"case_id": cid, "stage": "", "detail": ""}
        rows.append(row)
        print(f"\n===== [{i + 1}/{len(cases)}] {cid} =====")

        if not a.eval_only:
            if not data_dir.is_dir():
                row.update(stage="exec_fail", detail="用例没有 data/ 目录")
                print(f"  ✗ {row['detail']}")
                continue
            sfx = case_suffix(cid, i)
            before = {p.name for p in result_root.glob("result-*")}
            cmd = [runner, "--plan", assets / "plan.json", "--result-base", result_root,
                   "--result-suffix", sfx] + build_data_args(data_dir)
            code, tail = _run(cmd, 900, logs / f"{sfx}-runner.log")
            rdir = newest_result_dir(result_root, before)
            if code != 0 or rdir is None:
                row.update(stage="exec_fail",
                           detail=f"runner exit {code}(日志 {logs / f'{sfx}-runner.log'}):{tail[-300:]}")
                print(f"  ✗ 执行失败:{row['detail'][:200]}")
                continue
            ri = find_rca_input(rdir)
            if ri is None:
                row.update(stage="exec_fail", detail=f"{rdir.name} 里没有 rca_input(plan 无 RCA 步?)")
                print(f"  ✗ {row['detail']}")
                continue
            code, tail = _run([rca_scripts / "draft_rca.py", ri, "-o", rdir / "rca_draft.json"],
                              300, logs / f"{sfx}-draft.log")
            if code != 0:
                row.update(stage="exec_fail", detail=f"draft_rca exit {code}:{tail[-300:]}")
                print(f"  ✗ 起草失败:{row['detail'][:200]}")
                continue
            fin = [rca_scripts / "finalize_rca.py", rdir / "rca_draft.json",
                   "-o", rdir / "rca.json", "--rca-input", ri] \
                + (["--solutions", solutions] if solutions.exists() else [])
            code, tail = _run(fin, 300, logs / f"{sfx}-finalize.log")
            if code != 0:
                # finalize 的硬闸:有候选未机械定档(真冲突要权衡)→ 绝不代裁。
                # 默认(无人值守口径):裁决做不了=这轮没优化好,按不通过计——没替它定档,
                # 只是把"验证不了"如实记为不通过;--pending-hold 恢复挂起等人的旧口径
                if not a.pending_hold:
                    row.update(stage="evaluated", verdict="FAIL",
                               error_category="pending_adjudication",
                               detail=f"机械链定不了档(待裁决);无人值守口径按不通过计。"
                                      f"结果目录 {rdir.name};清单见日志 {logs / f'{sfx}-finalize.log'}")
                    print("  ✗ 待裁决 → 按不通过计(默认无人值守口径;要挂起等人裁决加 --pending-hold)")
                    continue
                row.update(stage="pending_adjudication",
                           detail=f"结果目录 {rdir.name};待裁决清单见日志 {logs / f'{sfx}-finalize.log'}:"
                                  + tail[-500:])
                print("  ⏸ 挂起待裁决(机械链定不了档,需模型/人按 fault-tree-rca 法则写 decisions.json"
                      " 后重跑 finalize+render,再单用例判卷)")
                continue
            _run([rca_scripts / "render_report.py", rdir / "rca.json"], 120,
                 logs / f"{sfx}-render.log")            # 报告渲染尽力而为,不拦回归

        ev = [SCRIPTS_DIR / "eval_run.py", "--case", cid, "--cases-dir", case_dir,
              "--result-dir", result_root, "--skill-dir", skill_dir, "--out", eval_out]
        code, tail = _run(ev, 600, logs / f"{case_suffix(cid, i)}-eval.log")
        rep = find_eval_report(eval_out, cid)
        if rep is None:
            row.update(stage="eval_error", detail=f"eval_run exit {code}:{tail[-300:]}")
            print(f"  ✗ 判卷异常:{row['detail'][:200]}")
            continue
        row.update(stage="evaluated", **rep)
        qs = rep.get("quality_score")
        print(f"  → {rep.get('verdict')}"
              + (f" / {rep.get('error_category')}" if rep.get("error_category") else "")
              + (f" / quality_score={qs}" if qs is not None else ""))

    done = [r for r in rows if r["stage"] == "evaluated"]
    passed = [r for r in done if str(r.get("verdict", "")).startswith("PASS")]
    pending = [r for r in rows if r["stage"] == "pending_adjudication"]
    broken = [r for r in rows if r["stage"] in ("exec_fail", "eval_error")]
    acc = (len(passed) / len(done)) if done else None
    summary = {"total": len(rows), "evaluated": len(done), "passed": len(passed),
               "accuracy": acc,
               "pending_adjudication": [r["case_id"] for r in pending],
               "exec_or_eval_failed": [r["case_id"] for r in broken], "cases": rows}
    sp = out / "regress_summary.json"
    sp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[regress] 汇总:{len(rows)} 用例 | 已判卷 {len(done)}(通过 {len(passed)})"
          f" | 挂起待裁决 {len(pending)} | 执行/判卷异常 {len(broken)}")
    if acc is not None:
        print(f"  准确率:{len(passed)}/{len(done)} = {acc:.1%}(口径:通过/已判卷;"
              f"挂起与异常用例不计入分母,清完它们数字才算完整)")
    if pending:
        print("  ⏸ 挂起用例(裁决后重跑 finalize+render,再 `--eval-only` 或 --case 补判):"
              + "、".join(r["case_id"] for r in pending))
    if broken:
        print("  ✗ 异常用例(看 logs/ 对应日志):" + "、".join(r["case_id"] for r in broken))
    print(f"  明细:{sp}")
    return 0 if not (pending or broken) and len(passed) == len(done) else 1


if __name__ == "__main__":
    sys.exit(main())
