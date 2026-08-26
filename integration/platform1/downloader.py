#!/usr/bin/env python3
"""平台1 obs 下载封装——工装层,不进技能。

两条真路(等平台材料到位后接线)+ 一条自测路(--fake-store,不碰网络):

- download_zip(obs_path, name, dst_dir):按 excel 里的 Product_Obs 地址下载交付 zip 包。
  平台1 给的下载脚本放到本目录(建议名 platform_download.py)后,在 _platform_fetch()
  里按它的真实用法接线——现在是占位,会明确报错提示接线。
- download_skill(obskey, dst_dir):按 objectkey 下载上一版 skill 文件。平台脚本目前
  **只有下 zip 的能力,没有按 obskey 下 skill 的能力**,这是要在平台脚本基础上改造出来的
  (拿到脚本源码后照它的 obs 客户端初始化方式补一个按 key 取对象即可),同样在
  _platform_fetch() 里接线。

fake 模式(联调第一步“纯本地跑通”用):给 --fake-store 目录,下载就变成从该目录按文件名拷贝
——zip 按 Product_Name 取,skill 按 obskey 的文件名部分取。这样不通 obs 也能把整条驱动链
先跑起来。
"""
from __future__ import annotations

import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 平台1 下载脚本放这里(拿到后改名或改这个常量)
PLATFORM_SCRIPT = HERE / "platform_download.py"


def _fake_fetch(store: Path, filename: str, dst: Path) -> Path:
    src = store / filename
    if not src.exists():
        cands = sorted(p.name for p in store.iterdir()) if store.is_dir() else []
        raise SystemExit(f"[fake-store] 找不到样例文件 {filename}(目录 {store} 里有:{cands})")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def _platform_fetch(kind: str, locator: str, dst: Path) -> Path:
    """真路占位:kind='zip' 时 locator 是 Product_Obs 地址,kind='skill' 时是 objectkey。
    拿到平台1 的下载脚本后在这里接线(subprocess 调它,或 import 后调它的函数),
    下载产物落到 dst。"""
    raise SystemExit(
        f"obs 真实下载还没接线(要下 {kind}:{locator})。\n"
        f"把平台1 的下载脚本放到 {PLATFORM_SCRIPT} 并在 downloader._platform_fetch() 里"
        f"按它的用法接上;联调自测请先用 --fake-store。")


def download_zip(obs_path: str, name: str, dst_dir: Path, fake_store: Path | None = None) -> Path:
    """下载一个交付 zip 包到 dst_dir,落名为 name(不带 .zip 自动补)。"""
    fname = name if name.lower().endswith(".zip") else name + ".zip"
    dst = Path(dst_dir) / fname
    if fake_store is not None:
        return _fake_fetch(Path(fake_store), fname, dst)
    return _platform_fetch("zip", obs_path, dst)


def download_skill(obskey: str, dst_dir: Path, fake_store: Path | None = None) -> Path:
    """按 objectkey 下载上一版 skill 文件(zip)到 dst_dir。"""
    fname = Path(str(obskey)).name or "skill.zip"
    if not fname.lower().endswith(".zip"):
        fname += ".zip"
    dst = Path(dst_dir) / fname
    if fake_store is not None:
        return _fake_fetch(Path(fake_store), fname, dst)
    return _platform_fetch("skill", obskey, dst)
