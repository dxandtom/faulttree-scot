#!/usr/bin/env python3
"""平台1 联调接入服务——收平台的优化请求,受理即回 success,后台跑 driver,完了回调。

链路口径(字段名与平台接口文档 callDongXuanOptimizeApi 一致):
  平台 POST multipart(versionId + excelFile + prevObjectkey)→ 我方校验入参、落盘、
  起后台任务 → 立刻返回 success(本次调用结束)→ 后台迁移完成 → 回调平台
  POST /workspace/faultTreeNode/optimizeCallback(versionId + skillFile 文件流)。
  平台收到回调后自己上传 OBS 并把版本记录置 COMPLETED。

启动(Windows 本机):
  pip install fastapi uvicorn python-multipart
  回调地址:obs_headers_config.json 当前环境那一节加 "callback_url"(与下载端点同文件同环境),
  或 set PLATFORM1_CALLBACK_URL=<完整回调 URL>(环境变量优先)
  python -m uvicorn server:app --host 0.0.0.0 --port 8080 --app-dir integration/platform1
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, UploadFile

# 请求字段名(平台接口文档 callDongXuanOptimizeApi 的 multipart 字段)
FIELD_EXCEL = "excelFile"        # Excel 二进制文件流(NONE/MULTI/ALL 按选择导出)
FIELD_PREV = "prevObjectkey"     # 上一版 skill 的 OBS 地址;首次优化为 null/缺省
FIELD_VERSION = "versionId"      # 平台预生成的版本 UUID,回调时原样带回
FIELD_OPTYPE = "optimizeType"    # NONE=只刷引擎代码 / MULTI=部分表不动结构 / ALL=结构以表为准

HERE = Path(__file__).resolve().parent
WORK_ROOT = Path(os.environ.get("PLATFORM1_WORK", HERE / "_runs"))
CALLBACK_URL = os.environ.get("PLATFORM1_CALLBACK_URL", "")

app = FastAPI(title="scot-optimize")


def _run_driver(req_file: Path, workdir: Path):
    """后台任务:driver 起独立进程跑(它内部 sys.exit,不能进本进程),日志落 run.log。
    服务模式默认带模型档(没人盯终端,失败项由模型自动处置);PLATFORM1_WITH_MODEL=0 关。"""
    args = [sys.executable, str(HERE / "driver.py"), "--request", str(req_file),
            "--workdir", str(workdir / "work"), "--keep"]
    if os.environ.get("PLATFORM1_WITH_MODEL", "1") != "0":
        args.append("--with-model")
    log = workdir / "run.log"
    with log.open("w", encoding="utf-8") as fh:
        subprocess.run(args, stdout=fh, stderr=subprocess.STDOUT, cwd=str(HERE.parents[1]))


@app.post("/optimize")
async def optimize(background: BackgroundTasks,
                   excel_file: UploadFile = File(..., alias=FIELD_EXCEL),
                   prev_objectkey: str = Form("", alias=FIELD_PREV),
                   version_id: str = Form(..., alias=FIELD_VERSION),
                   optimize_type: str = Form("", alias=FIELD_OPTYPE)):
    run_dir = WORK_ROOT / f"v{version_id[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    excel_path = run_dir / (excel_file.filename or "export.xlsx")
    excel_path.write_bytes(await excel_file.read())
    req = {"excel": str(excel_path), "prevObjectkey": prev_objectkey,
           "versionId": version_id, "optimizeType": optimize_type,
           "callback_url": CALLBACK_URL}
    req_file = run_dir / "request.json"
    req_file.write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    background.add_task(_run_driver, req_file, run_dir)
    return {"result": "success"}
