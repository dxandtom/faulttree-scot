#!/usr/bin/env python3
"""平台1 obs 下载封装——工装层,不进技能。

真路已按平台1 的下载脚本(download_fault_tree_artifact.py,同目录)接线:
该脚本不直连 obs,而是把 {"docId": <obs 地址>, "file_name": <落名>} POST 给平台的下载服务
(端点与鉴权在 obs_headers_config.json 里,含 token/psf 密钥路径,**不进仓库**,联调机器上
自己放到本目录)。zip 包和上一版 skill 走同一个接口:docId 分别取表里的 Product_Obs 和
请求里的 prevObjectkey——这就是"下载 skill 能力"的改造,单条调用、custom_file_name 指定
落名,逐条下载(不用它的多条批量:多条时服务端会打成一个带时间戳的大 zip,结构不可控)。

联调机器上需要:pandas、requests、psf(内部库)、obs_headers_config.json(+token/密钥文件)。
环境变量(可选):PLATFORM1_ENV=dev|beta(缺省用配置文件 default.env)、
PLATFORM1_DL_CONFIG=<配置文件路径>(缺省用同目录 obs_headers_config.json)。

fake 模式(联调第一步"纯本地跑通"用):给 --fake-store 目录,下载就变成从该目录按文件名拷贝
——zip 按 Product_Name 取,skill 按 obskey 的文件名部分取。不碰网络、不需要上述依赖。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLATFORM_SCRIPT = HERE / "download_fault_tree_artifact.py"

_PLATFORM_MOD = None


def _fake_fetch(store: Path, filename: str, dst: Path) -> Path:
    src = store / filename
    if not src.exists():
        cands = sorted(p.name for p in store.iterdir()) if store.is_dir() else []
        raise SystemExit(f"[fake-store] 找不到样例文件 {filename}(目录 {store} 里有:{cands})")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def _load_platform_module():
    """加载平台下载脚本(它模块级 import psf/pandas 并初始化加密件,只在联调机器上装得起)。"""
    global _PLATFORM_MOD
    if _PLATFORM_MOD is None:
        if not PLATFORM_SCRIPT.exists():
            raise SystemExit(f"缺平台下载脚本:{PLATFORM_SCRIPT};联调自测请先用 --fake-store")
        spec = importlib.util.spec_from_file_location("p1_download", PLATFORM_SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            raise SystemExit(
                f"平台下载脚本加载失败:{e}\n"
                f"它依赖 pandas/requests/psf(内部库)与同目录 obs_headers_config.json"
                f"(含 token 与密钥路径)——联调机器上装好、放好再跑;自测用 --fake-store。")
        _PLATFORM_MOD = mod
    return _PLATFORM_MOD


def _platform_fetch(kind: str, locator: str, dst: Path) -> Path:
    """真路:单条调平台下载服务。kind='zip' 时 locator=Product_Obs,kind='skill' 时
    locator=prevObjectkey——同一接口,docId 即 obs 地址。文件落 dst。"""
    mod = _load_platform_module()
    cfg = os.environ.get("PLATFORM1_DL_CONFIG", "") or None
    try:
        dl = mod.OBSDownloader(cfg)
    except json.JSONDecodeError as e:
        raise SystemExit(f"obs_headers_config.json 不是合法 JSON({e})——最常见原因:更新 token "
                         "时粘贴带进了真实换行/制表符(JSON 字符串里不允许断行)。打开配置文件"
                         "定位到报错行,把引号里的值并回完整一行再保存重跑。")
    env = os.environ.get("PLATFORM1_ENV", "") or dl.config.get("default", {}).get("env", "dev")
    dst.parent.mkdir(parents=True, exist_ok=True)
    res = dl.download_artifacts([{"docId": locator, "file_name": dst.name}],
                                dst.parent, env, custom_file_name=dst.name)
    if res.get("success") != 1 or not dst.exists():
        raise SystemExit(f"平台下载失败({kind}:{locator}):{res};原因见上方脚本输出"
                         f"(JSON 响应=服务端拒绝,常见是 token 过期或该 obs 路径无权限)")
    return dst


def download_zip(obs_path: str, name: str, dst_dir: Path, fake_store: Path | None = None) -> Path:
    """下载一个交付 zip 包到 dst_dir,落名为 name(不带 .zip 自动补)。"""
    fname = name if name.lower().endswith(".zip") else name + ".zip"
    dst = Path(dst_dir) / fname
    if fake_store is not None:
        return _fake_fetch(Path(fake_store), fname, dst)
    return _platform_fetch("zip", obs_path, dst)


def download_skill(obskey: str, dst_dir: Path, fake_store: Path | None = None) -> Path:
    """按 prevObjectkey(obs 地址)下载上一版 skill 文件(zip)到 dst_dir。"""
    fname = Path(str(obskey)).name or "skill.zip"
    if not fname.lower().endswith(".zip"):
        fname += ".zip"
    dst = Path(dst_dir) / fname
    if fake_store is not None:
        return _fake_fetch(Path(fake_store), fname, dst)
    return _platform_fetch("skill", obskey, dst)
