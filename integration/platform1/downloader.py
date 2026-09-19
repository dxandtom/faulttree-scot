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
import zipfile
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
    if not zipfile.is_zipfile(dst):
        # 服务端拒绝时也回 HTTP 200,报错体(JSON/HTML)会被当文件存盘——当场验明,
        # 别等到迁移解包才炸;坏件改名 .bad 留证,不占正名(重跑会重新下载)
        head = dst.read_bytes()[:200]
        bad = dst.with_name(dst.name + ".bad")
        dst.replace(bad)
        raise SystemExit(f"下载到的不是 zip({kind}:{locator}):响应体开头 {head!r}——"
                         f"HTTP 200 但内容是报错体,说明服务端拒了这条 docId"
                         f"(路径不存在/无权限/token 过期都可能);坏件留在 {bad},"
                         "把这段输出甩给平台侧查该 docId")
    return dst


def dl_config():
    """只读下载配置 obs_headers_config.json(PLATFORM1_DL_CONFIG 可指别处),不加载平台脚本
    (不需要 psf)。返回 (配置 dict 或 {}, 当前环境名):环境取 PLATFORM1_ENV,没有就取
    default.env,再没有就 dev。文件不在=({}, env);不是合法 JSON → SystemExit 并给出易懂的说明。"""
    cfg_p = Path(os.environ.get("PLATFORM1_DL_CONFIG", "") or (HERE / "obs_headers_config.json"))
    cfg = {}
    if cfg_p.exists():
        try:
            cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{cfg_p} 不是合法 JSON({e})——常见是 token 粘贴带了换行,或多余逗号")
    env = (os.environ.get("PLATFORM1_ENV", "").strip()
           or str((cfg.get("default") or {}).get("env") or "").strip() or "dev")
    return cfg, env


def default_env() -> str:
    return dl_config()[1]


def auth_headers():
    """复用平台下载脚本那套鉴权(obs_headers_config.json 的 authType/invokeToken,token 走它的
    加密封装),给回调用——回调接口挂在同一个网关(webot)时鉴权方式一致。
    返回 (请求头 dict, 下载端点主机名);配置/依赖不在时抛 SystemExit(调用方决定要不要吞)。"""
    from urllib.parse import urlparse
    mod = _load_platform_module()
    cfg = os.environ.get("PLATFORM1_DL_CONFIG", "") or None
    try:
        dl = mod.OBSDownloader(cfg)
    except json.JSONDecodeError as e:
        raise SystemExit(f"obs_headers_config.json 不是合法 JSON({e})")
    env = os.environ.get("PLATFORM1_ENV", "") or dl.config.get("default", {}).get("env", "dev")
    env_cfg = dl.config.get(env, dl.config.get("dev")) or {}
    host = urlparse(str(env_cfg.get("endpoint") or "")).hostname or ""
    return dl._get_headers(env), host


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
