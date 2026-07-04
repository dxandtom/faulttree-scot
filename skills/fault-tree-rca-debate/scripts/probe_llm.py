#!/usr/bin/env python3
"""LLM 连通性探针(人工直跑,不经引擎):用 vendor/config.yaml 的真实配置发一次最小对话请求。

为什么需要它:引擎若把 LLM 调用失败静默吞掉,跑起来"像成功"(0 辩论、毫秒级完成)。
本脚本绕开引擎,直接回答一个问题:**从本机 Python、用 config 里的地址和密钥,LLM 到底通不通。**
输出全程脱敏:不打印 api_key,地址只显示首段+端口——输出可以放心贴给别人看。

用法:python probe_llm.py [--config <path>](默认 ../vendor/config.yaml)
退出码:0=通(HTTP 200);1=HTTP 非 200(网络通、服务/鉴权有问题);2=网络异常(连不上);3=配置缺失。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _mask(url: str) -> str:
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or "?"
        parts = host.split(".")
        h = parts[0] + ".x.x.x" if len(parts) == 4 and all(p.isdigit() for p in parts) else (host[:3] + "…")
        return f"{u.scheme}://{h}:{u.port or ''}{u.path}"
    except Exception:
        return "<无法解析>"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="用 config.yaml 的配置直测 LLM 连通性(输出脱敏)")
    ap.add_argument("--config", default=str(here.parent / "vendor" / "config.yaml"))
    a = ap.parse_args()

    import yaml
    p = Path(a.config)
    if not p.exists():
        print(f"[3] 找不到配置:{p}")
        return 3
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    llm = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
    base = llm.get("base_url") or cfg.get("llm_base_url") or ""
    key = llm.get("api_key") or cfg.get("llm_api_key") or ""
    model = llm.get("model_name") or llm.get("model") or cfg.get("llm_model_name") or ""
    if not (base and model):
        print(f"[3] 配置缺失:base_url={'有' if base else '无'},model={'有' if model else '无'},api_key={'有' if key else '无'}")
        return 3

    proxies = sorted(k for k in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                                 "http_proxy", "https_proxy", "no_proxy") if os.environ.get(k))
    print(f"目标: {_mask(base)}  model={model}  api_key={'已提供' if key else '空!'}")
    print(f"代理环境变量(只报名字): {proxies or '无'}")

    url = base.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": "ping,只回复 pong"}],
               "max_tokens": 8}
    try:
        import requests
    except ImportError:
        print("[3] 缺 requests(引擎也依赖它):pip install requests")
        return 3
    try:
        import urllib3
        urllib3.disable_warnings()
        r = requests.post(url, json=payload, headers={"Authorization": f"Bearer {key}"},
                          timeout=20, verify=False)
        body = (r.text or "")[:150]
        if key:
            body = body.replace(key, "***")
        print(f"HTTP {r.status_code};响应片段:{body}")
        if r.status_code == 200:
            print("结论:LLM 可达且能出话。若引擎仍 0 辩论/毫秒级完成 → 引擎内部没走到 LLM(找同事核对触发条件/静默兜底)。")
            return 0
        print("结论:网络通,但服务/路径/鉴权有问题(按状态码排查:401/403=鉴权,404=路径,5xx=服务端)。")
        return 1
    except Exception as e:
        msg = str(e)
        if key:
            msg = msg.replace(key, "***")
        print(f"[2] 网络异常:{type(e).__name__}: {msg[:200]}")
        print("结论:从 Python 也连不上——不是引擎问题,是网络/VPN/代理;引擎此前的'成功'是静默降级。")
        return 2


if __name__ == "__main__":
    sys.exit(main())
