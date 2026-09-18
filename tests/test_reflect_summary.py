#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评估反思整批汇总(评估输出根目录 quality_score_batch.json)算法测试(标准库,直接 python 跑):
① build_batch_score 纯函数:通过 = PASS + PASS_WITH_GAPS;pass_rate = 通过 ÷ 全部用例;total_score = 各用例
   总分平均,没评出分按 0;六维 = 通过该关的用例比例;没判卷的记 NOT_EVALUATED;
② 用自带演练场(make_drill)跑两条入口——eval_run 批量判卷、regress --eval-only——两边写出的整批
   quality_score_batch.json 汇总字段完全一致,且与逐用例数据的算术吻合。"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills" / "cs-scot-reflect" / "scripts"
PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  {extra}")


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(args):
    r = subprocess.run([sys.executable] + [str(a) for a in args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(REPO))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


KEYS = ("total_cases", "passed", "pass_rate", "total_score", "verdicts", "dimensions")

print("\n== ① build_batch_score 算法 ==")
bs = load("batch_summary")
ALL_T = {k: True for k in ("env_failure", "input_gap", "binding_gap", "knowledge_gap", "criteria_gap", "verdict_gap")}
idx = bs.build_batch_score([
    {"case_id": "a", "verdict": "PASS", "quality_score": 100, "dimensions": ALL_T},
    {"case_id": "b", "verdict": "PASS_WITH_GAPS", "quality_score": 100, "dimensions": ALL_T},
    {"case_id": "c", "verdict": "FAIL", "quality_score": 83, "dimensions": dict(ALL_T, verdict_gap=False)},
    {"case_id": "d", "verdict": None, "quality_score": None},           # 没判卷
    {"case_id": "e", "verdict": "EVAL_ERROR", "quality_score": None},
], "2026-09-17T00:00:00")
check("通过 = PASS + PASS_WITH_GAPS = 2", idx["passed"] == 2, idx)
check("pass_rate = 2/5 = 0.4(分母含没判卷与评估失败)", idx["pass_rate"] == 0.4, idx)
check("total_score = (100+100+83+0+0)/5 = 56.6", idx["total_score"] == 56.6, idx)
check("六维 = 通过该关的用例比例:env_failure 3/5、verdict_gap 2/5",
      idx["dimensions"]["env_failure"]["passed_cases"] == 3 and idx["dimensions"]["env_failure"]["score"] == 60
      and idx["dimensions"]["verdict_gap"]["passed_cases"] == 2 and idx["dimensions"]["verdict_gap"]["score"] == 40, idx["dimensions"])
check("verdicts 计数:没判卷记 NOT_EVALUATED,合计等于总数",
      idx["verdicts"]["NOT_EVALUATED"] == 1 and idx["verdicts"]["EVAL_ERROR"] == 1
      and sum(idx["verdicts"].values()) == idx["total_cases"] == 5, idx["verdicts"])
check("空批次不除零", bs.build_batch_score([], "t")["pass_rate"] == 0.0 and bs.build_batch_score([], "t")["total_score"] == 0.0)
check("format_line 带通过数与算法说明", "2/5" in bs.format_line(idx) and "PASS_WITH_GAPS" in bs.format_line(idx))

print("\n== ② 演练场:两条入口写出同一算法的整批 quality_score_batch.json ==")
W = Path(tempfile.mkdtemp(prefix="reflect-idx-"))
rc, o = run([SCRIPTS / "make_drill.py", "--out", W / "drill"])
check("演练场生成", rc == 0, o[-300:])
flat = W / "flat"                                     # 结果目录平铺(目录名含 case_id → 批量匹配规则①)
flat.mkdir()
for d in sorted((W / "drill" / "results").glob("*/result-drill-*")):
    shutil.copytree(d, flat / d.name)
rc1, o1 = run([SCRIPTS / "eval_run.py", "--case-dir", W / "drill" / "cases", "--result-dir", flat,
               "--skill-dir", W / "drill" / "product", "--out", W / "eval-out"])
rc2, o2 = run([SCRIPTS / "regress.py", "--skill-dir", W / "drill" / "product", "--case-dir", W / "drill" / "cases",
               "--eval-only", "--result-dir", flat, "--out", W / "regress-out"])
a_p, b_p = W / "eval-out" / "quality_score_batch.json", W / "regress-out" / "eval" / "quality_score_batch.json"
check("两条入口都在根目录写出整批 quality_score_batch.json", a_p.exists() and b_p.exists(), (o1[-300:], o2[-300:]))
a = json.loads(a_p.read_text(encoding="utf-8")) if a_p.exists() else {}
b = json.loads(b_p.read_text(encoding="utf-8")) if b_p.exists() else {}
check("汇总字段(含六维)完全一致", a and b and all(a[k] == b[k] for k in KEYS), (a.get("pass_rate"), b.get("pass_rate")))
check("演练场按设计只有基线用例通过(PASS_WITH_GAPS),其余各类错误不通过",
      a.get("passed") == 1 and a.get("verdicts", {}).get("FAIL", 0) >= 6, a.get("verdicts"))
n = a.get("total_cases") or 1
check("pass_rate = passed ÷ total_cases", a and round(a["passed"] / n, 4) == a["pass_rate"], a.get("pass_rate"))
check("total_score = 逐用例 quality_score 平均(没评出分按 0)",
      a and round(sum(float(c.get("quality_score") or 0) for c in a["cases"]) / n, 1) == a["total_score"], a.get("total_score"))
check("total_score ≈ 六个维度分的平均(算法一致,只差取整)",
      a and abs(sum(d["score"] for d in a["dimensions"].values()) / 6 - a["total_score"]) < 1.0,
      (a.get("total_score"), [d["score"] for d in a.get("dimensions", {}).values()]))
check("演练场里挂在执行环境关的只有 1 个用例:env_failure 通过 8/9",
      a and a["dimensions"]["env_failure"]["passed_cases"] == n - 1, a.get("dimensions", {}).get("env_failure"))
check("regress 终端打印与 index 同一算法", f"通过 {a.get('passed')}/{n}" in o2 and "平台/业务取数" in o2, o2[-400:])
check("regress_summary.json 带同样的 pass_rate / total_score",
      (lambda s: s.get("pass_rate") == a.get("pass_rate") and s.get("total_score") == a.get("total_score"))(
          json.loads((W / "regress-out" / "regress_summary.json").read_text(encoding="utf-8"))))
shutil.rmtree(W, ignore_errors=True)

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
sys.exit(1 if FAILED else 0)
