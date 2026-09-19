#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑一遍就懂:不碰网络、不用模型,把平台1 接入的整条链路在本机走一遍,边走边说做了什么。

    python integration/platform1/demo.py            # 结果落在 integration/platform1/_runs/demo/
    python integration/platform1/demo.py --out D:\\tmp\\demo

做的事:①造样例(上一版 skill zip、一个节点交付包 zip、一张一行的平台表)②起一个假的平台回调
接收器 ③写一份请求文件 ④像 server.py 那样起 driver.py 跑一单 ⑤告诉你日志、产物、回调各在哪。
跑完拿 run.log 对照《先看这个.md》第 4 节读,链路就清楚了。"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

NODE = "干扰导致"                                       # 样例树里的一个叶子节点(examples/compression-demo)
SCRIPT = '''\
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
        return {"matched": "undetermined", "evidence": [], "details": {"reason": "没找到数据文件"}}
    return {"matched": "false", "evidence": [], "details": {"reason": "未找到 KW_X"}}
'''


def say(step, msg):
    print(f"\n[{step}] {msg}")


def mk_skill_zip(out: Path):
    """上一版 skill:借仓库自带的 examples/compression-demo(一棵小树 + 两个脚本)拼一个最小产物。"""
    tmp = out.parent / "_skill_v1"
    demo = REPO / "examples" / "compression-demo"
    shutil.copytree(demo / "tree", tmp / "tree")
    shutil.copytree(demo / "scripts", tmp / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    asm_p = REPO / "skills" / "cs-scot-generate" / "scripts" / "assemble_scenario.py"
    spec = importlib.util.spec_from_file_location("asm_demo", asm_p)
    asm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(asm)
    (tmp / "scripts" / "cross_analyzer.py").write_text(asm.CROSS_SCAFFOLD, encoding="utf-8")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(tmp.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(tmp).as_posix())
    shutil.rmtree(tmp)


def mk_node_zip(out: Path, name: str):
    """节点交付包:tester 规范——判断方法_<节点>.md + fault_check_<节点>.py + 测试数据/。"""
    tmp = out.parent / f"_{name}"
    (tmp / "测试数据" / "网元A" / "CMD").mkdir(parents=True)
    (tmp / "测试数据" / "网元A" / "CMD" / "cmd.txt").write_text("blah KW_X\n", encoding="utf-8")
    (tmp / f"判断方法_{name}.md").write_text(
        f"# 判断方法:{name}\n\n## 节点名称\n{name}\n\n## 故障描述\n演练。\n\n"
        "## 判断条件\n1. CMD 中找 KW_X。\n\n### 命中条件\nA\n\n## 输出\n命中输出故障。\n\n"
        "## 数据来源\n| 数据 | 命令 | 目录 |\n|---|---|---|\n| x | display x | CMD/ |\n",
        encoding="utf-8")
    (tmp / f"fault_check_{name}.py").write_text(SCRIPT, encoding="utf-8")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(tmp.rglob("*")):
            if p.is_file():
                zf.write(p, f"{name}/" + p.relative_to(tmp).as_posix())
    shutil.rmtree(tmp)


class FakePlatform(BaseHTTPRequestHandler):
    """假的平台回调接口:收到什么记下来,回 responseCode 0。真平台就是这么一个 POST 接口。"""
    hits = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        FakePlatform.hits.append({"path": self.path, "size": len(body),
                                  "has_versionId": b'name="versionId"' in body,
                                  "has_skillFile": b'name="skillFile"' in body})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"responseCode":"0","data":true}')

    def log_message(self, *a):
        pass


def make_samples(out: Path) -> tuple:
    """①造样例:上一版 skill zip、一个节点交付包、一行的平台表;返回 (货架目录, 表路径)。"""
    store = out / "store"
    store.mkdir(parents=True)

    say("1/5", f"造样例 → {store}")
    mk_skill_zip(store / "skill_v1.zip")
    mk_node_zip(store / f"{NODE}.zip", NODE)
    table = out / "平台表.csv"
    table.write_text("L1_Name_CN,L1_Name_EN,L2_Name_CN,L2_Name_EN,L3_Name_CN,L3_Name_EN,Product_Obs,Product_Name\n"
                     f"接入失败示例,Root,QoS建立失败,Qos,{NODE},Intf,obs://bucket/nodes/{NODE}.zip,{NODE}.zip\n",
                     encoding="utf-8-sig")
    print("   skill_v1.zip   = 上一版 skill(平台里存的那个 zip;真跑时按 prevObjectkey 下载)")
    print(f"   {NODE}.zip = 一个节点的交付包(真跑时按表里的 Product_Obs 下载)")
    print("   平台表.csv     = 平台发来的表,一行:节点路径 + 包的地址 + 包名")
    print("   真跑时下载来自平台;这里用 --fake-store 让 driver 从这个目录按文件名拷,其余逻辑一模一样")
    return store, table


def start_fake_platform() -> str:
    """②起假的平台回调接收器(只收一次),返回回调地址。"""
    say("2/5", "起一个假的平台回调接收器(真平台的 optimizeCallback 接口就是这样一个 POST)")
    srv = HTTPServer(("127.0.0.1", 0), FakePlatform)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    cb_url = f"http://127.0.0.1:{srv.server_address[1]}/agent/workspace/faultTreeNode/optimizeCallback"
    print(f"   {cb_url}")
    return cb_url


def write_request(out: Path, table: Path, cb_url: str) -> Path:
    """③写请求文件(平台四个字段 + 本地实验的回调地址),返回文件路径。"""
    say("3/5", "写请求文件(平台 POST /optimize 发来的四个字段,server.py 收到后也是先存成这样一份)")
    req = {"versionId": "demo0001-0000-0000-0000-000000000001",
           "excel": str(table), "prevObjectkey": "obs://bucket/skills/skill_v1.zip",
           "optimizeType": "MULTI", "callback_url": cb_url}
    req_p = out / "request.json"
    req_p.write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   {req_p}")
    return req_p


def run_driver_once(req_p: Path, work: Path, store: Path):
    """④像 server.py 那样起 driver.py 子进程跑一单,原样转发输出并落 run.log;返回 CompletedProcess。"""
    say("4/5", "跑一单:像 server.py 那样起 driver.py 子进程(下面是 driver 的原样输出)")
    cmd = [sys.executable, str(HERE / "driver.py"), "--request", str(req_p),
           "--workdir", str(work), "--fake-store", str(store)]
    print("   " + " ".join(cmd))
    env = dict(os.environ)
    for k in ("PLATFORM1_CALLBACK_URL", "PLATFORM1_DL_CONFIG", "PLATFORM1_ENV",
              "PLATFORM1_CB_AUTH", "PLATFORM1_CB_HEADERS", "PLATFORM1_CB_ON_FAIL"):
        env.pop(k, None)
    env["PLATFORM1_CB_AUTH"] = "none"                    # 假接收器不要鉴权
    env["PYTHONIOENCODING"] = "utf-8"
    print("   " + "-" * 70)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(REPO), env=env, timeout=900)
    log = (p.stdout or "") + (p.stderr or "")
    for line in log.splitlines():
        print("   | " + line)
    print("   " + "-" * 70)
    (work / "run.log").parent.mkdir(parents=True, exist_ok=True)
    (work / "run.log").write_text(log, encoding="utf-8")
    return p


def show_ledger(outz: Path) -> None:
    """从新版 skill 包里读迁移台账 tree/migrations.json,点名这一单装进去的节点。"""
    with zipfile.ZipFile(outz) as zf:
        n = next((x for x in zf.namelist() if x.endswith("tree/migrations.json")), None)
        if n:
            mig = json.loads(zf.read(n))
            hit = [f"{k}「{v.get('package_node')}」← {v.get('script')}" for k, v in mig.items()
                   if v.get("package_node") == NODE]
            print(f"   包里 tree/migrations.json(迁移台账)记了这一单装进去的节点:{'; '.join(hit) or list(mig)[:3]}")


def show_results(p, work: Path) -> bool:
    """⑤告诉你日志、产物、回调各在哪;返回这一单是否走通。"""
    say("5/5", "看结果")
    outz = next(iter((work / "out").glob("skill_*.zip")), None) if (work / "out").is_dir() else None
    print(f"   退出码 {p.returncode}(0 = 这一单成功)")
    print(f"   日志   {work / 'run.log'}(server 模式下每单在 _runs/v<版本号前8位>/run.log)")
    print(f"   下载的包  {work / 'bundle'}")
    print(f"   解开的上一版 skill  {work / 'skill'}")
    print(f"   新版 skill  {outz if outz else '(没有出包,看上面日志里的 [停] 行)'}")
    if FakePlatform.hits:
        h = FakePlatform.hits[0]
        print(f"   回调  假平台收到 1 次 POST {h['path']},multipart 里带 versionId={h['has_versionId']}"
              f" skillFile={h['has_skillFile']},共 {h['size']} 字节")
    else:
        print("   回调  假平台没收到(看日志里 [回调] 行)")
    if outz:
        show_ledger(outz)
    return p.returncode == 0 and outz is not None and bool(FakePlatform.hits)


def main() -> int:
    ap = argparse.ArgumentParser(description="平台1 接入链路本机演示(不碰网络、不用模型)")
    ap.add_argument("--out", help="演示目录(缺省 integration/platform1/_runs/demo,每次清空重来)")
    a = ap.parse_args()
    out = Path(a.out).resolve() if a.out else HERE / "_runs" / "demo"
    if out.exists():
        shutil.rmtree(out)
    store, table = make_samples(out)
    cb_url = start_fake_platform()
    req_p = write_request(out, table, cb_url)
    work = out / "run"
    p = run_driver_once(req_p, work, store)
    ok = show_results(p, work)
    print("\n" + ("演示成功:表 → 下载 → 迁移 → 打包 → 回调,整条链路走通。"
                  "接下来拿 run.log 对照《先看这个.md》第 4 节逐行看。" if ok
                  else "演示没走通,把上面的输出整段发给交接人。"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
