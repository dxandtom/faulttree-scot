#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台1 联调驱动端到端测试(fake 货架,不碰网络;标准库,直接 python 跑)。
最小产物 = tree/*.tree_index.json + 空 scripts/(引擎由迁移自动补全);节点交付包按 tester 规范造。
覆盖:成功路(下载→迁移→打包→回调)/ 续跑 / 失败也回传(表错位、一行没生效)/ 关闭失败回传 /
首次优化不回传 / 回调地址取值顺序 / responseCode 判成败。
用法:python integration/platform1/tests/test_platform1_e2e.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
P1 = HERE.parent
REPO = P1.parents[1]
DRIVER = P1 / "driver.py"
sys.path.insert(0, str(P1))

n_pass = n_fail = 0


def check(name, cond, detail=""):
    global n_pass, n_fail
    if cond:
        n_pass += 1
        print(f"  ok  {name}")
    else:
        n_fail += 1
        print(f"  FAIL {name} {detail}")


GOOD_SCRIPT = '''\
import os

def fault_check(data_dir):
    hits, n_files = [], 0
    for root, _dirs, files in os.walk(data_dir):
        for f in files:
            if f.lower().endswith((".txt", ".log")):
                n_files += 1
                for line in open(os.path.join(root, f), encoding="utf-8", errors="replace"):
                    if "KW_X" in line:
                        hits.append(line.strip())
    if hits:
        return {"matched": "true",
                "evidence": [{"type": "ne", "id": "x", "matched": "true",
                              "data": {"source_files": [], "matched_items": hits[:5]}}],
                "details": {}}
    if n_files == 0:
        return {"matched": "undetermined", "evidence": [],
                "details": {"reason": "没找到任何 .txt/.log 数据文件"}}
    return {"matched": "false", "evidence": [], "details": {"reason": "未找到 KW_X"}}
'''


def mk_product(d: Path):
    """最小可迁移产物:借仓库自带的 examples/compression-demo(tree_index + 两个 analyzer)
    + generate 的 cross_analyzer 脚本框架(S 绑定写在这里)。树:接入失败示例 → QoS建立失败(n2)
    → 干扰导致(n3)/ 弱覆盖(n4)/ License受限(n5)。"""
    demo = REPO / "examples" / "compression-demo"
    shutil.copytree(demo / "tree", d / "tree")
    shutil.copytree(demo / "scripts", d / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    import importlib.util
    asm_p = REPO / "skills" / "cs-scot-generate" / "scripts" / "assemble_scenario.py"
    spec = importlib.util.spec_from_file_location("asm_fixture", asm_p)
    asm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(asm)
    (d / "scripts" / "cross_analyzer.py").write_text(asm.CROSS_SCAFFOLD, encoding="utf-8")


def mk_pkg(store: Path, name: str, script=GOOD_SCRIPT):
    pkg = store / f"_{name}"
    (pkg / "测试数据" / "网元A" / "CMD").mkdir(parents=True)
    (pkg / "测试数据" / "网元A" / "CMD" / "cmd.txt").write_text("blah KW_X\n", encoding="utf-8")
    (pkg / f"判断方法_{name}.md").write_text(
        f"# 判断方法:{name}\n\n## 节点名称\n{name}\n\n## 故障描述\n演练。\n\n"
        "## 判断条件\n1. CMD 中找 KW_X。\n\n### 命中条件\nA\n\n## 输出\n命中输出故障。\n\n"
        "## 数据来源\n| 数据 | 命令 | 目录 |\n|---|---|---|\n| x | display x | CMD/ |\n",
        encoding="utf-8")
    (pkg / f"fault_check_{name}.py").write_text(script, encoding="utf-8")
    out = store / f"{name}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(pkg.rglob("*")):
            if p.is_file():
                zf.write(p, f"{name}/" + p.relative_to(pkg).as_posix())
    shutil.rmtree(pkg)
    return out


def zip_dir(src: Path, out: Path):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(src).as_posix())


class CB(BaseHTTPRequestHandler):
    hits = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        CB.hits.append({"path": self.path, "body": body})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"responseCode":"0","data":true}')

    def log_message(self, *a):
        pass


def serve_cb():
    s = HTTPServer(("127.0.0.1", 0), CB)
    threading.Thread(target=s.handle_request, daemon=True).start()
    return f"http://127.0.0.1:{s.server_address[1]}/agent/workspace/faultTreeNode/optimizeCallback"


def run_driver(req: dict, workdir: Path, store: Path, env_extra=None):
    rq = workdir.parent / f"{workdir.name}.request.json"
    rq.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ)
    for k in ("PLATFORM1_CALLBACK_URL", "PLATFORM1_DL_CONFIG", "PLATFORM1_ENV", "PLATFORM1_CB_ON_FAIL",
              "PLATFORM1_CB_AUTH", "PLATFORM1_CB_HEADERS"):
        env.pop(k, None)
    env["PLATFORM1_CB_AUTH"] = "none"
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, str(DRIVER), "--request", str(rq), "--workdir", str(workdir),
                        "--fake-store", str(store)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900, cwd=str(REPO), env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def report_in(zip_path: Path):
    with zipfile.ZipFile(zip_path) as zf:
        n = next((x for x in zf.namelist() if x.endswith("tree/migration_report.json")), None)
        return json.loads(zf.read(n)) if n else None


WORK = Path(tempfile.mkdtemp(prefix="p1e2e-"))
HDR = "L1_Name_CN,L1_Name_EN,L2_Name_CN,L2_Name_EN,L3_Name_CN,L3_Name_EN,Product_Obs,Product_Name\n"
prod = WORK / "prod"
mk_product(prod)
store = WORK / "store"
store.mkdir()
mk_pkg(store, "干扰导致")
mk_pkg(store, "弱覆盖", script="def diagnose(d):\n    return {'matched': 'false'}\n")   # 只识别 .txt 之外还缺 .log/缺数据判 false:契约检查过不了
zip_dir(prod, store / "skill_v1.zip")
PREV = "obs://bucket/skills/skill_v1.zip"

print("\n== ① 成功路:下载→迁移→打包→回调 ==")
xl = WORK / "ok.csv"
xl.write_text(HDR + "接入失败示例,Root,QoS建立失败,Qos,干扰导致,Intf,obs://b/甲.zip,干扰导致.zip\n", encoding="utf-8-sig")
CB.hits.clear()
rc, out = run_driver({"excel": str(xl), "prevObjectkey": PREV, "versionId": "ok000001-x",
                      "callback_url": serve_cb()}, WORK / "run_ok", store)
check("退出码 0", rc == 0, out[-800:])
check("迁移落地 ✓ n3", "✓ n3「干扰导致」" in out, out[-600:])
outz = WORK / "run_ok" / "out" / "skill_ok000001.zip"
check("产物出包", outz.exists())
check("回调一发,带 versionId 与 skillFile", len(CB.hits) == 1 and b"ok000001" in CB.hits[0]["body"]
      and b'name="skillFile"' in CB.hits[0]["body"], repr(len(CB.hits)))
check("成功包里没有失败报告", report_in(outz) is None)

print("\n== ② 续跑同一目录:不重下不重迁,照出包 ==")
CB.hits.clear()
rc2, out2 = run_driver({"excel": str(xl), "prevObjectkey": PREV, "versionId": "ok000001-x",
                        "callback_url": serve_cb()}, WORK / "run_ok", store)
check("续跑退出码 0 且跳过", rc2 == 0 and "续跑" in out2 and "(已在,跳过)" in out2 and "未变化,跳过" in out2, out2[-600:])

print("\n== ③ 失败也回传:表错位(同一 obs 摊多个 zip 名)→ 回传未变化上一版 + 失败报告 ==")
xt = WORK / "tangle.csv"
xt.write_text(HDR + "接入失败示例,Root,QoS建立失败,Qos,干扰导致,Intf,obs://b/one.zip,干扰导致.zip\n"
              "接入失败示例,Root,QoS建立失败,Qos,弱覆盖,Weak,obs://b/one.zip,弱覆盖.zip\n", encoding="utf-8-sig")
CB.hits.clear()
rc3, out3 = run_driver({"excel": str(xt), "prevObjectkey": PREV, "versionId": "tg000001-x",
                        "callback_url": serve_cb()}, WORK / "run_tg", store)
check("驱动仍以失败退出", rc3 != 0 and "错位" in out3, out3[-500:])
check("打印失败回传", "[失败回传]" in out3, out3[-500:])
check("回调发出", len(CB.hits) == 1 and b"tg000001" in CB.hits[0]["body"], repr(len(CB.hits)))
oz3 = WORK / "run_tg" / "out" / "skill_tg000001.zip"
rep3 = report_in(oz3) if oz3.exists() else None
check("回传包=上一版 + status=failed 报告(带原因)", rep3 is not None and rep3["status"] == "failed"
      and "错位" in rep3["reason"] and rep3["landed"] == 0, repr(rep3)[:300])
with zipfile.ZipFile(oz3) as zf, zipfile.ZipFile(store / "skill_v1.zip") as z0:
    same = all(zf.read(n) == z0.read(n) for n in z0.namelist())
check("除报告外内容与上一版逐字节一致", same)

print("\n== ④ 失败也回传:一行都没落地(格式检查不过)→ 回传未变化上一版 ==")
xb = WORK / "bad.csv"
xb.write_text(HDR + "接入失败示例,Root,QoS建立失败,Qos,弱覆盖,Weak,obs://b/乙.zip,弱覆盖.zip\n", encoding="utf-8-sig")
CB.hits.clear()
rc4, out4 = run_driver({"excel": str(xb), "prevObjectkey": PREV, "versionId": "bd000001-x",
                        "callback_url": serve_cb()}, WORK / "run_bd", store)
check("一行没落地:失败退出", rc4 != 0 and "没有任何已生效" in out4, out4[-600:])
rep4 = report_in(WORK / "run_bd" / "out" / "skill_bd000001.zip") if (WORK / "run_bd" / "out" / "skill_bd000001.zip").exists() else None
check("回传 + 报告带迁移输出末尾", len(CB.hits) == 1 and rep4 and rep4["status"] == "failed"
      and "落地 0/1" in rep4["reason"], repr(rep4)[:300] if rep4 else "no report")

print("\n== ⑤ PLATFORM1_CB_ON_FAIL=0:失败不回传(老行为) ==")
CB.hits.clear()
rc5, out5 = run_driver({"excel": str(xt), "prevObjectkey": PREV, "versionId": "nf000001-x",
                        "callback_url": serve_cb()}, WORK / "run_nf", store, {"PLATFORM1_CB_ON_FAIL": "0"})
check("失败退出且无回调", rc5 != 0 and not CB.hits and "[失败回传]" not in out5)

print("\n== ⑥ 首次优化(prevObjectkey=null):退出码 4,没东西可回传 ==")
CB.hits.clear()
rc6, out6 = run_driver({"excel": str(xl), "prevObjectkey": "null", "versionId": "fs000001-x",
                        "callback_url": serve_cb()}, WORK / "run_fs", store)
check("退出码 4 且不回调", rc6 == 4 and not CB.hits, out6[-300:])

print("\n== ⑦ 回调地址取值:request → 环境变量 → obs 配置[env].callback_url ==")
cfg = WORK / "obs_headers_config.json"
cfg.write_text(json.dumps({"default": {"env": "beta"},
                           "beta": {"endpoint": "https://gw/agent/x.do", "callback_url": serve_cb()},
                           "dev": {"endpoint": "https://dev/agent/x.do"}}), encoding="utf-8")
CB.hits.clear()
rc7, out7 = run_driver({"excel": str(xl), "prevObjectkey": PREV, "versionId": "cf000001-x"},
                       WORK / "run_cf", store, {"PLATFORM1_DL_CONFIG": str(cfg)})
check("配置文件里的回调地址被采用", rc7 == 0 and "[beta].callback_url" in out7 and len(CB.hits) == 1, out7[:400])
CB.hits.clear()
rc8, out8 = run_driver({"excel": str(xl), "prevObjectkey": PREV, "versionId": "cf000002-x"},
                       WORK / "run_cf2", store, {"PLATFORM1_DL_CONFIG": str(cfg), "PLATFORM1_ENV": "dev"})
check("切到没配的环境 → 只出产物不回调", rc8 == 0 and "没配 callback_url" in out8 and not CB.hits, out8[:400])

print("\n== ⑧ responseCode 非 0 判失败 ==")


class CBFail(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"responseCode":"40040003","message":"Current status does not allow callback"}')

    def log_message(self, *a):
        pass


s8 = HTTPServer(("127.0.0.1", 0), CBFail)
threading.Thread(target=s8.handle_request, daemon=True).start()
rc9, out9 = run_driver({"excel": str(xl), "prevObjectkey": PREV, "versionId": "rc000001-x",
                        "callback_url": f"http://127.0.0.1:{s8.server_address[1]}/cb"}, WORK / "run_rc", store)
check("业务码非 0 → 失败退出、带 message、不再套一层失败回传",
      rc9 != 0 and "40040003" in out9 and "does not allow" in out9 and "[失败回传]" not in out9, out9[-500:])

print("\n== ⑨ 平台2 形态:只装发行包 cs-scot-generate-optimize 时,迁移脚本与模型技能名自动定位 ==")
import importlib.util
spec = importlib.util.spec_from_file_location("p1driver_mod", DRIVER)
fake_root = WORK / "installed_skills"
pkg = fake_root / "cs-scot-generate-optimize"
(pkg / "migrate" / "scripts").mkdir(parents=True)
(pkg / "migrate" / "scripts" / "migrate_fault_check.py").write_text("# 发行包内嵌的迁移脚本(占位)\n", encoding="utf-8")
(pkg / "generate" / "scripts").mkdir(parents=True)
(pkg / "SKILL.md").write_text("# 前门\n", encoding="utf-8")
os.environ["SCOT_SKILLS_ROOT"] = str(fake_root)
drv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drv)
check("SCOT_SKILLS_ROOT 指向只装发行包的目录 → 迁移脚本定位到 <包>/migrate/scripts",
      drv.MIGRATE == pkg / "migrate" / "scripts" / "migrate_fault_check.py", str(drv.MIGRATE))
check("模型档技能名 → cs-scot-generate-optimize(统一入口按表形态路由到优化线)",
      drv._skill_name_for_model() == "cs-scot-generate-optimize")
os.environ["SCOT_MIGRATE_SCRIPT"] = str(WORK / "x.py")
check("SCOT_MIGRATE_SCRIPT 显式指定优先", drv._find_migrate() == WORK / "x.py")
os.environ.pop("SCOT_MIGRATE_SCRIPT")
os.environ.pop("SCOT_SKILLS_ROOT")
drv2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drv2)
check("仓库形态(缺省)→ 本仓库 skills/cs-scot-migrate 的脚本",
      drv2.MIGRATE == REPO / "skills" / "cs-scot-migrate" / "scripts" / "migrate_fault_check.py", str(drv2.MIGRATE))
check("仓库形态模型档点名 cs-scot-migrate", drv2._skill_name_for_model() == "cs-scot-migrate")

print(f"\n通过 {n_pass} / {n_pass + n_fail}" + ("  【有失败!】" if n_fail else "  全绿"))
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(1 if n_fail else 0)
