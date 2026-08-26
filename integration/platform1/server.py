#!/usr/bin/env python3
"""平台1 联调接入服务——收平台的优化请求,受理即回 success,后台跑 driver,完了回调。

链路口径:平台调这个接口 → 我方校验入参、落盘、起后台任务 → 立刻返回 success(本次调用结束)
→ 后台迁移完成 → 调平台 update 回调(version_id + skill 产物文件)。

启动(Windows 本机):
  pip install fastapi uvicorn
  set PLATFORM1_CALLBACK_URL=http://平台1地址/其update接口   (联调前平台给;不配则只打印产物)
  python -m uvicorn server:app --host 0.0.0.0 --port 8080 --app-dir integration/platform1

【字段名占位】平台的请求样例到了,照实改下面三个常量即可(集中一处):
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, UploadFile

# 请求字段名——等平台样例定稿后照实改
FIELD_EXCEL = "excel_file"       # excel 构造的文件流
FIELD_OBSKEY = "skill_obskey"    # 上一版 skill 的 objectkey(空 = 无 skill,生成场景)
FIELD_VERSION = "version_id"     # fault_tree_skill_version 表的 id

HERE = Path(__file__).resolve().parent
WORK_ROOT = Path(os.environ.get("PLATFORM1_WORK", HERE / "_runs"))
CALLBACK_URL = os.environ.get("PLATFORM1_CALLBACK_URL", "")

app = FastAPI(title="scot-optimize")


def _run_driver(req_file: Path, workdir: Path):
    """后台任务:driver 起独立进程跑(它内部 sys.exit,不能进本进程),日志落 run.log。"""
    log = workdir / "run.log"
    with log.open("w", encoding="utf-8") as fh:
        subprocess.run(
            [sys.executable, str(HERE / "driver.py"), "--request", str(req_file),
             "--workdir", str(workdir / "work"), "--keep"],
            stdout=fh, stderr=subprocess.STDOUT, cwd=str(HERE.parents[1]))


@app.post("/optimize")
async def optimize(background: BackgroundTasks,
                   excel_file: UploadFile = File(..., alias=FIELD_EXCEL),
                   skill_obskey: str = Form("", alias=FIELD_OBSKEY),
                   version_id: str = Form(..., alias=FIELD_VERSION)):
    run_dir = WORK_ROOT / f"v{version_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    excel_path = run_dir / (excel_file.filename or "export.xlsx")
    excel_path.write_bytes(await excel_file.read())
    req = {"excel": str(excel_path), "skill_obskey": skill_obskey,
           "version_id": version_id, "callback_url": CALLBACK_URL}
    req_file = run_dir / "request.json"
    req_file.write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    background.add_task(_run_driver, req_file, run_dir)
    return {"result": "success"}
