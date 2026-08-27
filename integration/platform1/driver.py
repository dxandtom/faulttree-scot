#!/usr/bin/env python3
"""平台1 联调驱动——把平台的优化请求翻成技能的本地输入,跑完迁移后回调。工装层,不进技能。

链路(字段名与平台1 接口文档 callDongXuanOptimizeApi 对齐):
  平台点优化 → POST 我方接口(multipart:versionId + excelFile + prevObjectkey)
  → 我方受理即回 success(见 server.py)→ 本驱动异步干活:
     ① 解析平台 excel(L*_Name_CN/EN + Product_Obs + Product_Name,层级动态)
     ② 按 Product_Obs 逐条下载节点 zip、按 prevObjectkey 下载上一版 skill(downloader.py)
     ③ 解包 skill、定位产物目录(技能形态认 assets/)
     ④ 调 cs-scot-migrate 对应表批量迁移(表直接喂 --map,技能已认平台表格式)
     ⑤ 重打包新版 skill zip
     ⑥ 回调平台 POST /workspace/faultTreeNode/optimizeCallback
       (multipart:versionId + skillFile 文件流;OBS 上传与版本记录更新由平台做)

三种优化场景(平台按选择导出 excel;prevObjectkey 首次优化为 null):
  MULTI/ALL:表里有技能行 + 有上一版 skill → 本驱动全自动跑(主路)
  表里有行 + 无上一版:生成场景,要走 cs-scot-generate 的模型会话,本驱动不代跑(退出码 4)
  NONE(空壳表,只有表头)+ 有上一版:无迁移可做——现行 skill 原样下载回传,
  让平台状态正常闭环(顺带这就是「skill 下载」的独立验证路)

用法(本地模拟;样例见 request.example.json):
  python integration/platform1/driver.py --request request.json \
      [--workdir 工作目录] [--fake-store 本地样例目录] [--keep]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MIGRATE = REPO / "skills" / "cs-scot-migrate" / "scripts" / "migrate_fault_check.py"

sys.path.insert(0, str(HERE))
import downloader  # noqa: E402

# 回调报文字段名(平台接口文档 /workspace/faultTreeNode/optimizeCallback)
CB_FIELD_ID = "versionId"       # 请求带来的 versionId 原样带回,平台按它匹配版本记录
CB_FIELD_FILE = "skillFile"     # 优化后的 skill zip 文件流(OBS 上传由平台做)


# ---------- ① 平台 excel 解析(只取驱动要的三样:路径/包名/obs 地址) ----------

def _read_table(path: Path):
    """xlsx 走技能自带的零依赖 xlsx_reader;csv 标准库。返回 (header, rows[dict])。"""
    if path.suffix.lower() == ".csv":
        import csv as _csv
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "ignore")
        reader = _csv.DictReader(io.StringIO(text))
        return list(reader.fieldnames or []), [
            {(k or ""): ("" if v is None else str(v)) for k, v in r.items()} for r in reader]
    for cand in (REPO / "skills" / "cs-scot-generate" / "scripts",
                 REPO / "skills" / "cs-scot-migrate" / "engine"):
        if (cand / "xlsx_reader.py").exists() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
    import xlsx_reader
    return xlsx_reader.read_xlsx(path)


def parse_platform_excel(path: Path):
    """平台表 → [(节点路径名字列表, zip 包名, obs 地址)];Product_Name 空的行(结构行)跳过。
    列口径与 cs-scot-migrate 的表识别一致:路径取 L{n}_Name_CN,层级动态。"""
    header, rows = _read_table(path)
    cn_cols, obs_col, name_col = [], None, None
    for h in header:
        hn = str(h).strip()
        low = hn.lower()
        if low.startswith("l") and low.endswith("_name_cn"):
            try:
                cn_cols.append((int(hn[1:hn.index("_")]), h))
            except ValueError:
                pass
        elif low == "product_obs":
            obs_col = h
        elif low == "product_name":
            name_col = h
    if not cn_cols or name_col is None:
        raise SystemExit(f"不是平台1 导出表(需要 L*_Name_CN 层级列 + Product_Name 列);"
                         f"实际表头:{header}")
    cn_cols.sort()
    out = []
    for r in rows:
        name = str(r.get(name_col) or "").strip()
        if not name:
            continue
        names = [v for v in (str(r.get(c) or "").strip() for _l, c in cn_cols) if v]
        obs = str(r.get(obs_col) or "").strip() if obs_col else ""
        out.append((names, name, obs))
    return out


# ---------- ③ skill 解包与产物目录定位 ----------

def unpack_skill(zip_path: Path, dst: Path) -> Path:
    """解包上一版 skill,返回迁移用的产物目录:
    技能形态认 assets/;唯一顶层目录先下探一层;都不是就用解包根。"""
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dst)
    root = dst
    entries = [p for p in root.iterdir() if not p.name.startswith("__MACOSX")]
    if len(entries) == 1 and entries[0].is_dir():
        root = entries[0]
    assets = root / "assets"
    return assets if assets.is_dir() else root


# ---------- ⑤ 重打包 ----------

def repack(unpack_root: Path, out_zip: Path) -> Path:
    """把解包目录按原相对结构打回 zip(产物=新版 skill 文件)。"""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(unpack_root.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(unpack_root).as_posix())
    return out_zip


# ---------- ⑥ 回调(标准库 multipart,零依赖) ----------

def post_callback(url: str, version_id: str, zip_path: Path) -> int:
    boundary = uuid.uuid4().hex
    body = io.BytesIO()

    def part(headers: str, payload: bytes):
        body.write(f"--{boundary}\r\n{headers}\r\n\r\n".encode("utf-8"))
        body.write(payload)
        body.write(b"\r\n")

    part(f'Content-Disposition: form-data; name="{CB_FIELD_ID}"',
         str(version_id).encode("utf-8"))
    part(f'Content-Disposition: form-data; name="{CB_FIELD_FILE}"; '
         f'filename="{zip_path.name}"\r\nContent-Type: application/zip',
         zip_path.read_bytes())
    body.write(f"--{boundary}--\r\n".encode("utf-8"))
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    extra = os.environ.get("PLATFORM1_CB_HEADERS", "")
    if extra:                       # 鉴权口子:回调若要 token,环境变量塞 JSON 请求头
        headers.update(json.loads(extra))   # 例 {"invokeToken": "..."} 或 {"Cookie": "..."}
    req = urllib.request.Request(url, data=body.getvalue(), method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        print(f"[回调] {url} → HTTP {resp.status}")
        return resp.status


# ---------- 模型会话(事件流实时转发,判活一眼可见) ----------

def _model_session(claude_cmd, mlog: Path, timeout: int) -> None:
    """起 Claude Code 无头会话:事件流逐行接收——原始行落 mlog,关键动作翻译成
    人话实时打到终端(带时间戳),看得见它每一步在干什么;超时杀进程按已修部分继续。"""
    import time
    start = time.time()
    try:
        proc = subprocess.Popen(claude_cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError as e:
        print(f"[模型] 会话启动失败({e}),按机械迁移结果处理")
        return
    with mlog.open("w", encoding="utf-8") as fh:
        for line in proc.stdout:
            fh.write(line)
            fh.flush()
            if time.time() - start > timeout:
                proc.kill()
                print(f"[模型] 会话超时({timeout} 秒,PLATFORM1_MODEL_TIMEOUT 可调大),"
                      f"按已修部分继续")
                break
            ts = time.strftime("%H:%M:%S")
            try:
                ev = json.loads(line)
            except ValueError:
                s = line.strip()
                if s:
                    print(f"  [{ts}] {s[:120]}")
                continue
            if ev.get("type") == "assistant":
                for blk in (ev.get("message") or {}).get("content") or []:
                    if blk.get("type") == "tool_use":
                        inp = blk.get("input") or {}
                        brief = str(inp.get("file_path") or inp.get("command")
                                    or inp.get("pattern") or "")[:90]
                        print(f"  [{ts}] {blk.get('name')}  {brief}")
                    elif blk.get("type") == "text" and str(blk.get("text", "")).strip():
                        print(f"  [{ts}] 说:{str(blk['text']).strip()[:110]}")
            elif ev.get("type") == "result":
                print(f"  [{ts}] 会话结束:{str(ev.get('result', ''))[:200]}")
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------- 主流程 ----------

def run(req: dict, workdir: Path | None = None, fake_store: Path | None = None,
        keep: bool = False, with_model: bool = False) -> Path | None:
    excel = Path(req["excel"]).resolve()
    obskey = str(req.get("prevObjectkey") or "").strip()
    version_id = str(req.get("versionId") or "").strip()
    cb_url = str(req.get("callback_url") or "").strip()
    if not excel.exists():
        raise SystemExit(f"excel 不存在:{excel}")
    if not version_id:
        raise SystemExit("request 里缺 versionId(平台预生成的版本 UUID,回调时原样带回)")
    if obskey.lower() in ("null", "none"):                # 平台首次优化传 null
        obskey = ""

    rows = parse_platform_excel(excel)
    print(f"[表] {excel.name}:{len(rows)} 个待迁移节点")
    if not obskey:
        print("prevObjectkey 为空(首次优化)→ 平台侧还没有 skill。从头生成一棵技能"
              "需要专家判据文档等素材,而平台请求里只有 excel(仅节点路径,无判据列)"
              "和 zip 包,凑不齐——首版 skill 应线下用 cs-scot-generate 生成后录入平台,"
              "优化按钮只管之后的迭代。本驱动不处理这种请求。")
        sys.exit(4)

    work = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="p1opt-"))
    work.mkdir(parents=True, exist_ok=True)
    bundle, skill_dir, out_dir = work / "bundle", work / "skill", work / "out"
    bundle.mkdir(exist_ok=True)

    if not rows:
        # NONE 场景(空壳表,只有表头):无节点要迁移——现行 skill 原样返回,
        # 让平台状态正常闭环(不回调会把版本记录挂死在 OPTIMIZING)
        print("NONE 场景(空壳表):无迁移可做,现行 skill 原样返回。")
        skill_zip = downloader.download_skill(obskey, work, fake_store=fake_store)
        out_dir.mkdir(exist_ok=True)
        out_zip = out_dir / f"skill_{version_id[:8]}.zip"
        shutil.copy2(skill_zip, out_zip)
        print(f"[产物] {out_zip}(与现行 skill 逐字节相同)")
        if cb_url:
            post_callback(cb_url, version_id, out_zip)
        else:
            print(f"[回调] 未配置 callback_url,只出产物:versionId={version_id}")
        return out_zip
    for names, name, obs in rows:
        downloader.download_zip(obs, name, bundle, fake_store=fake_store)
        print(f"[下载] {'/'.join(names)} ← {name}")
    skill_zip = downloader.download_skill(obskey, work, fake_store=fake_store)
    product = unpack_skill(skill_zip, skill_dir)
    print(f"[skill] 上一版已解包,产物目录:{product}")

    cmd = [sys.executable, str(MIGRATE), "--package", str(bundle),
           "--product", str(product), "--auto", "--map", str(excel)]
    print("[迁移]", " ".join(cmd))
    rc = subprocess.run(cmd, cwd=str(REPO)).returncode
    if rc != 0 and with_model:
        # 机械挡吃不下的失败项(语义对号/修管道/补入口)交给模型:起 Claude Code 无头会话
        # 按 cs-scot-migrate 技能处置,完了复跑机械迁移验证——这就是完整的 skill 调用形态
        prompt = (f"用 cs-scot-migrate 技能处理一次批量迁移的失败项,只改文件,不用跑命令。"
                  f"失败清单看这条命令上一轮的输出(可以重跑它看,但验证不归你,驱动会自动复跑):"
                  f"{' '.join(cmd)}。按 SKILL.md 的规矩逐项处置:契约体检 FAIL 的照修法"
                  f"只修产物 scripts/custom/ 里的副本管道,判断逻辑一行不动;缺标准入口的按副本旁"
                  f" *.contract.json 的修法在副本里补 fault_check(data_dir) 薄封装;拿不准算不算"
                  f"改逻辑就跳过并在最后列入返厂清单,绝不赌。完成后汇报改了哪些文件、跳过了哪些。")
        # 本地联调默认按用户的日常用法起会话(全权限);平台2 集成时权限策略另定
        claude_cmd = os.environ.get("PLATFORM1_CLAUDE_CMD",
                                    "claude --dangerously-skip-permissions -p").split()
        if "--output-format" not in claude_cmd:
            claude_cmd += ["--output-format", "stream-json", "--verbose"]  # 事件流:每步实时一行
        claude_cmd.append(prompt)
        exe = shutil.which(claude_cmd[0])   # Windows 上 npm 装的是 claude.cmd,须 which 解析
        if exe is None:
            print(f"[模型] 找不到命令「{claude_cmd[0]}」——跳过模型档,按机械迁移结果处理。"
                  f"(检查 Claude Code 是否在 PATH,或设 PLATFORM1_CLAUDE_CMD 指定完整调用)")
        else:
            claude_cmd[0] = exe
            mlog = work / "model_session.log"
            timeout = int(os.environ.get("PLATFORM1_MODEL_TIMEOUT", "3600"))
            print(f"[模型] 起 Claude Code 无头会话处置失败项(cs-scot-migrate 技能),"
                  f"通常 10-30 分钟;它每一步动作会实时打在下面,原始事件流落 {mlog}")
            _model_session(claude_cmd, mlog, timeout)
            rerun = subprocess.run(cmd, cwd=str(REPO), capture_output=True,
                                   text=True, encoding="utf-8", errors="replace")
            print((rerun.stdout or "") + (rerun.stderr or ""))
            rc = rerun.returncode
            print(f"[模型] 处置后复跑机械迁移,退出码 {rc}")
            if rc != 0 and "预导入(收编现有调优表) 失败" in (rerun.stdout or "") + (rerun.stderr or ""):
                # 存量调优表的旧版结构病(如多个顶层节点):备份 .bak 后从产物重建,再跑一次
                print("[修表] 存量调优表有旧版结构病 → 加 --rebuild-tuning 重跑"
                      "(病表备份为 .bak,从产物重建)")
                rc = subprocess.run(cmd + ["--rebuild-tuning"], cwd=str(REPO)).returncode
                print(f"[修表] 重跑退出码 {rc}")
    if rc != 0:
        print(f"[停] 迁移退出码 {rc}(3=有对不上号的行,清单见上方输出)。"
              f"不回调、不打包;工作目录留在 {work} 供排查。")
        sys.exit(rc)

    out_zip = repack(skill_dir, out_dir / f"skill_{version_id[:8]}.zip")
    print(f"[产物] {out_zip}")
    if cb_url:
        post_callback(cb_url, version_id, out_zip)
    else:
        print(f"[回调] 未配置 callback_url,只出产物:version_id={version_id},文件见上行")
    if not keep and workdir is None:
        pass  # 产物在临时目录里,默认保留供人取走;要清理自行删
    return out_zip


def main():
    ap = argparse.ArgumentParser(description="平台1 联调驱动(request.json 模拟平台入参)")
    ap.add_argument("--request", required=True, help="请求 json(样例 request.example.json)")
    ap.add_argument("--workdir", help="工作目录(缺省用临时目录)")
    ap.add_argument("--fake-store", help="本地样例目录:按文件名模拟 obs 下载,不碰网络")
    ap.add_argument("--keep", action="store_true", help="保留工作目录(排查用)")
    ap.add_argument("--with-model", action="store_true",
                    help="机械迁移有失败行时,起 Claude Code 无头会话按 cs-scot-migrate 技能"
                         "处置(语义对号/修管道/补入口),完了复跑机械迁移;命令可用环境变量"
                         " PLATFORM1_CLAUDE_CMD 覆盖(默认 claude --permission-mode acceptEdits -p)")
    a = ap.parse_args()
    req = json.loads(Path(a.request).read_text(encoding="utf-8"))
    run(req, workdir=Path(a.workdir) if a.workdir else None,
        fake_store=Path(a.fake_store) if a.fake_store else None, keep=a.keep,
        with_model=a.with_model)


if __name__ == "__main__":
    main()
