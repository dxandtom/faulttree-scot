#!/usr/bin/env python3
"""平台1 联调驱动——把平台的优化请求转换成技能的本地输入,跑完迁移后回调平台。属于联调工具层,不进入技能。

处理链路(字段名与平台1 接口文档 callDongXuanOptimizeApi 一致):
  平台点击优化 → POST 我方接口(multipart:versionId + excelFile + prevObjectkey)
  → 我方受理后立即返回 success(见 server.py)→ 本驱动在后台异步处理:
     ① 解析平台 excel(L*_Name_CN/EN + Product_Obs + Product_Name,层级数动态)
     ② 按 Product_Obs 逐条下载节点 zip、按 prevObjectkey 下载上一版 skill(downloader.py)
     ③ 解包 skill、定位产物目录(技能形态取 assets/)
     ④ 调用 cs-scot-migrate 按对应表批量迁移(表直接作为 --map 参数传入,技能已能识别平台表格式)
     ⑤ 重新打包新版 skill zip
     ⑥ 回调平台 POST /workspace/faultTreeNode/optimizeCallback
       (multipart:versionId + skillFile 文件流;OBS 上传与版本记录更新由平台完成)

三种优化场景(平台按用户选择导出 excel;首次优化时 prevObjectkey 为 null):
  MULTI/ALL:表里有技能行 + 有上一版 skill → 本驱动全自动处理(主要流程)
  表里有行 + 无上一版:属于生成场景,要走 cs-scot-generate 的模型会话,本驱动不代为处理(退出码 4)
  NONE(只有表头的空表)+ 有上一版:无迁移可做——现行 skill 原样下载后回传,
  让平台的版本状态正常完结(这同时也是「skill 下载」功能的独立验证路径)

用法(本地模拟;样例见 request.example.json):
  python integration/platform1/driver.py --request request.json \
      [--workdir 工作目录] [--fake-store 本地样例目录] [--keep]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _skill_roots():
    """技能可能安装的位置(按顺序查找):环境变量 SCOT_SKILLS_ROOT(平台安装技能的目录)→ 本仓库 skills/
    → 用户级 ~/.claude/skills。每个根目录下既识别独立技能 cs-scot-migrate/,也识别发行包
    cs-scot-generate-optimize/(迁移与生成两个技能内嵌为子目录 migrate/、generate/,平台2 只部署这一个包)。"""
    roots = []
    env = os.environ.get("SCOT_SKILLS_ROOT", "").strip()
    if env:
        roots += [Path(x) for x in env.split(os.pathsep) if x.strip()]
    roots += [REPO / "skills", Path.home() / ".claude" / "skills"]
    return roots


def _find_skill_file(rel_in_migrate: str, rel_in_generate: str | None = None) -> Path | None:
    """在各技能根目录下查找 migrate/generate 的某个文件:独立技能形态与发行包内嵌形态都识别。"""
    for root in _skill_roots():
        cands = [root / "cs-scot-migrate" / rel_in_migrate,
                 root / "cs-scot-generate-optimize" / "migrate" / rel_in_migrate]
        if rel_in_generate:
            cands += [root / "cs-scot-generate" / rel_in_generate,
                      root / "cs-scot-generate-optimize" / "generate" / rel_in_generate]
        for c in cands:
            if c.exists():
                return c
    return None


def _find_migrate() -> Path:
    """迁移脚本定位:SCOT_MIGRATE_SCRIPT 显式指定 → 各技能根目录下的独立技能 / 发行包内嵌形态。
    平台2 只安装 cs-scot-generate-optimize 时,命中的是 <包>/migrate/scripts/migrate_fault_check.py
    ——机械迁移(不用模型的脚本迁移)直接调用这个脚本,不经过统一入口 cs-scot-generate-optimize;
    统一入口只在模型处置环节启动会话时参与(按表的形态路由到优化流程)。"""
    env = os.environ.get("SCOT_MIGRATE_SCRIPT", "").strip()
    if env:
        return Path(env)
    hit = _find_skill_file("scripts/migrate_fault_check.py")
    return hit if hit else REPO / "skills" / "cs-scot-migrate" / "scripts" / "migrate_fault_check.py"


def _skill_name_for_model() -> str:
    """模型处置环节(--with-model 启动的模型会话)使用哪个技能:安装了独立 cs-scot-migrate 就用它;
    只安装了发行包就用统一入口 cs-scot-generate-optimize(统一入口按输入形态自动路由:
    对应表=有层级列∧有 zip 列 → 优化流程)。SCOT_MODEL_SKILL 可显式指定。"""
    env = os.environ.get("SCOT_MODEL_SKILL", "").strip()
    if env:
        return env
    for root in _skill_roots():
        if (root / "cs-scot-migrate" / "SKILL.md").exists():
            return "cs-scot-migrate"
        if (root / "cs-scot-generate-optimize" / "SKILL.md").exists():
            return "cs-scot-generate-optimize"
    return "cs-scot-migrate"


MIGRATE = _find_migrate()

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
    xr = _find_skill_file("engine/xlsx_reader.py", "scripts/xlsx_reader.py")
    if xr is not None and str(xr.parent) not in sys.path:
        sys.path.insert(0, str(xr.parent))
    import xlsx_reader
    return xlsx_reader.read_xlsx(path)


def _is_mark_header(hn: str, low: str) -> bool:
    """表头是不是"是否修改/is_modified"这类标记列(hn=去空白原名,low=其小写)。"""
    cn_keys = ("是否修改", "是否变更", "是否优化", "需要优化", "修改标记")
    low_keys = ("is_modified", "need_optimize")
    exact = ("modified", "changed", "is_update", "update_flag")
    return (any(k in hn for k in cn_keys) or any(k in low for k in low_keys)
            or low in exact)


def _classify_platform_header(header):
    """按列名识别平台表的四类列:返回 (层级列 [(层号, 列名)], obs 列, 包名列, 标记列);同类多列后者覆盖。"""
    cn_cols, obs_col, name_col, mk_col = [], None, None, None
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
        elif _is_mark_header(hn, low):
            mk_col = h
    return cn_cols, obs_col, name_col, mk_col


def _select_platform_rows(rows, cn_cols, obs_col, name_col, mk_col):
    """逐行取 (路径名字列表, 包名, obs):包名空的结构行跳过,有标记列时未标记行也跳过。"""
    out = []
    for r in rows:
        name = str(r.get(name_col) or "").strip()
        if not name:
            continue
        if mk_col is not None and str(r.get(mk_col) or "").strip().lower() not in (
                "y", "yes", "1", "true", "是", "要", "需要", "修改"):
            continue                                     # 未标记行:zip 不下载,migrate 那边也不迁
        names = [v for v in (str(r.get(c) or "").strip() for _l, c in cn_cols) if v]
        obs = str(r.get(obs_col) or "").strip() if obs_col else ""
        out.append((names, name, obs))
    return out


def parse_platform_excel(path: Path):
    """平台表 → [(节点路径名字列表, zip 包名, obs 地址)];Product_Name 空的行(结构行)跳过。
    列的识别规则与 cs-scot-migrate 一致:路径取 L{n}_Name_CN,层级动态。
    表带标记列(是否修改/is_modified 等)时,未标记行的 zip 不下载(标记规则:
    内容动作只对标记行执行,与 migrate 同一规则)。"""
    header, rows = _read_table(path)
    cn_cols, obs_col, name_col, mk_col = _classify_platform_header(header)
    if not cn_cols or name_col is None:
        raise SystemExit(f"不是平台1 导出表(需要 L*_Name_CN 层级列 + Product_Name 列);"
                         f"实际表头:{header}")
    cn_cols.sort()
    return _select_platform_rows(rows, cn_cols, obs_col, name_col, mk_col)


# ---------- ③ skill 解包与产物目录定位 ----------

def _locate_product(dst: Path) -> Path:
    """解包目录里定位迁移用的产物目录:技能形态认 assets/;唯一顶层目录先下探一层;
    都不是就用解包根。"""
    root = dst
    entries = [p for p in root.iterdir() if not p.name.startswith("__MACOSX")]
    if len(entries) == 1 and entries[0].is_dir():
        root = entries[0]
    assets = root / "assets"
    return assets if assets.is_dir() else root


def unpack_skill(zip_path: Path, dst: Path) -> Path:
    """解包上一版 skill,返回迁移用的产物目录。"""
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dst)
    return _locate_product(dst)


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

def _multipart_body(version_id: str, zip_path: Path):
    """拼回调报文(versionId 文本域 + skillFile 文件域),返回 (请求头 dict, 报文字节)。"""
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
    return headers, body.getvalue()


def _callback_auth(url: str, headers: dict) -> bool:
    """按 PLATFORM1_CB_HEADERS / PLATFORM1_CB_AUTH 往请求头里加鉴权;返回是否复用了下载脚本的鉴权。"""
    reused = False
    extra = os.environ.get("PLATFORM1_CB_HEADERS", "")
    mode = os.environ.get("PLATFORM1_CB_AUTH", "auto").strip().lower() or "auto"
    if extra:                       # 显式鉴权头优先:环境变量塞 JSON 请求头
        headers.update(json.loads(extra))   # 例 {"invokeToken": "..."} 或 {"Cookie": "..."}
        print("[回调] 鉴权:PLATFORM1_CB_HEADERS 指定的请求头")
    elif mode != "none":
        # 回调接口与下载服务同一个网关(webot)时鉴权方式一致:复用下载脚本的 invokeToken
        # (token 文件 + 它的加密封装,配置就在 obs_headers_config.json)。auto=主机相同才复用;
        # platform=强制复用;none=不带
        from urllib.parse import urlparse
        try:
            auth, dl_host = downloader.auth_headers()
        except SystemExit as e:
            auth, dl_host = None, ""
            if mode == "platform":
                raise SystemExit(f"[回调] PLATFORM1_CB_AUTH=platform 但拿不到下载脚本的鉴权:{e}")
        cb_host = urlparse(url).hostname or ""
        if auth and (mode == "platform" or (dl_host and cb_host.lower() == dl_host.lower())):
            headers.update(auth)
            reused = True
            print(f"[回调] 鉴权:复用下载脚本的 {'/'.join(auth)}(回调与下载服务同主机 {cb_host})")
        elif auth and mode == "auto":
            print(f"[回调] 鉴权:不带(回调主机 {cb_host} ≠ 下载服务 {dl_host};要强制复用设 PLATFORM1_CB_AUTH=platform)")
    return reused


def _callback_ssl_context(reused: bool):
    """复用下载脚本鉴权或设了 PLATFORM1_CB_INSECURE 时返回不校验证书的 SSL 上下文,否则 None。"""
    ctx = None
    if reused or os.environ.get("PLATFORM1_CB_INSECURE", "").strip().lower() in ("1", "true", "yes"):
        # 复用下载脚本鉴权时同它一样不校验证书(该脚本对这个网关一直 verify=False)
        import ssl                       # 内网自签证书联调用:不校验对端证书(线上别开)
        ctx = ssl._create_unverified_context()
        if not reused:
            print("[回调] PLATFORM1_CB_INSECURE=1:不校验对端 HTTPS 证书")
    return ctx


def _callback_open(req, url: str, ctx):
    """发出回调请求;HTTP 错误码 / 连不上分别翻译成带排查提示的 SystemExit。返回响应(上下文管理器)。"""
    try:
        return urllib.request.urlopen(req, timeout=60, context=ctx)
    except urllib.error.HTTPError as e:
        snippet = e.read().decode("utf-8", "replace")[:200]
        hint = {404: "——路径或主机不对(找平台要完整回调 URL:协议+主机+端口+路径)",
                401: "——要鉴权:环境变量 PLATFORM1_CB_HEADERS 塞 JSON 请求头",
                403: "——要鉴权:环境变量 PLATFORM1_CB_HEADERS 塞 JSON 请求头"}.get(e.code, "")
        raise SystemExit(f"[回调失败] HTTP {e.code} {url} {snippet}{hint};产物已在本地 out/,修好重跑即重发")
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))
        if "CERTIFICATE_VERIFY_FAILED" in reason:
            hint = "——对端 HTTPS 证书本机不信任(内网自签):联调可设 PLATFORM1_CB_INSECURE=1 跳过校验"
        elif "getaddrinfo" in reason or "Name or service not known" in reason or "resolve" in reason.lower():
            hint = "——主机名解析不了:本机 nslookup 该域名,不通就找平台要 IP 加 hosts,或确认要不要挂 VPN/代理"
        else:
            hint = "——网络不通:确认主机/端口/防火墙"
        raise SystemExit(f"[回调失败] 连不上 {url}:{reason}{hint};产物已在本地 out/,修好重跑即重发")


def _callback_result(resp_cm, url: str) -> int:
    """读回调响应:打印状态与正文,按响应 JSON 的 responseCode 判业务成败;返回 HTTP 状态码。"""
    with resp_cm as resp:
        text = resp.read().decode("utf-8", "replace")
        print(f"[回调] {url} → HTTP {resp.status} {text[:300]}")
        # 平台约定:成败在响应 JSON 里(responseCode "0"=成功,"1"=失败),HTTP 200 也可能是
        # 业务失败——认这个字段;响应不是这格式(本地假平台/旧对端)才退回只看 HTTP 状态
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict) and "responseCode" in data and str(data["responseCode"]) != "0":
            raise SystemExit(f"[回调失败] 平台返回 responseCode={data['responseCode']}"
                             f" message={data.get('message', '')}——产物已在本地 out/ 目录,"
                             "排查后重跑同一条命令即可重发(续跑不重迁,直接打包回调)")
        return resp.status


def post_callback(url: str, version_id: str, zip_path: Path) -> int:
    headers, body = _multipart_body(version_id, zip_path)
    reused = _callback_auth(url, headers)
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    ctx = _callback_ssl_context(reused)
    resp_cm = _callback_open(req, url, ctx)
    return _callback_result(resp_cm, url)


def _resolve_callback_url(req: dict) -> str:
    """回调地址是**部署配置**,不是每单请求的字段(平台真请求里没有它)。取值顺序:
    request.json 的 callback_url(本地实验显式给)→ 环境变量 PLATFORM1_CALLBACK_URL
    → 下载配置 obs_headers_config.json 当前环境那一节的 callback_url(与下载端点、鉴权
    同一个文件、同一个 default.env 切环境——回调和下载走同一个网关)。都没有=只出产物不回调。"""
    u = str(req.get("callback_url") or "").strip()
    if u:
        print(f"[回调地址] request 指定:{u}")
        return u
    u = os.environ.get("PLATFORM1_CALLBACK_URL", "").strip()
    if u:
        print(f"[回调地址] 环境变量 PLATFORM1_CALLBACK_URL:{u}")
        return u
    cfg, env = downloader.dl_config()
    if not cfg:
        return ""
    u = str(((cfg.get(env) or {}).get("callback_url")) or "").strip()
    if u:
        print(f"[回调地址] obs_headers_config.json [{env}].callback_url:{u}")
    else:
        print(f"[回调地址] obs_headers_config.json 环境「{env}」那一节没配 callback_url → 只出产物不回调")
    return u


# ---------- 迁移执行(捕获输出供决策,同时转发到终端) ----------

def _run_migrate(cmd):
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    print(out)
    return r.returncode, out


def _parse_migrate_result(out: str):
    """从迁移输出解析结果清单:生效/新增/跳过/判据同步/删除/失败(带表行号与原因)。
    解析不到返回 None。"""
    m = re.search(r"落地 (\d+)/(\d+) 行", out)
    if not m:
        return None
    ok = [{"node_id": a, "name": b} for a, b in re.findall(r"✓ (n\d+)「([^」]+)」", out)]
    added = [{"node_id": a, "name": b} for a, b in re.findall(r"\+ (n\d+)「([^」]+)」 新增", out)]
    skipped = [{"node_id": a, "name": b}
               for a, b in re.findall(r"= (n\d+)「([^」]+)」 未变化", out)]
    jd_synced = [{"node_id": a, "name": b}
                 for a, b in re.findall(r"~ (n\d+)「([^」]+)」 判据已按对应表更新", out)]
    sol_synced = [{"node_id": a, "name": b}
                  for a, b in re.findall(r"≈ (n\d+)「([^」]+)」 解决方案已按对应表更新", out)]
    deleted = [{"node_id": a, "name": b}
               for a, b in re.findall(r"✂ (n\d+)「([^」]+)」 已删除", out)]
    failed = [{"name": a, "excel_row": int(b), "reason": c.strip()[:300]}
              for a, b, c in re.findall(r"✗ 「([^」]+)」\(表第(\d+)行[^)]*\):(.*)", out)]
    r = {"total": int(m.group(2)), "landed": int(m.group(1)),
         "ok": ok, "skipped": skipped, "failed": failed}
    for k, v in (("added", added), ("jd_synced", jd_synced),
                 ("sol_synced", sol_synced), ("deleted", deleted)):
        if v:
            r[k] = v
    return r


# ---------- 模型会话(事件流实时转发,判活一眼可见) ----------

def _model_session(claude_cmd, mlog: Path, timeout: int) -> None:
    """起 Claude Code 无头会话:事件流逐行接收——原始行落 mlog,关键动作翻译成
    易懂的文字实时打到终端(带时间戳),看得见它每一步在干什么;超时杀进程按已修部分继续。"""
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

def _attach_report(src_zip: Path, out_zip: Path, report: dict) -> Path:
    """原样复制上一版 skill zip,只多塞一个 tree/migration_report.json(放在 tree_index 所在目录旁)。
    失败回传用:不带任何半成品改动,平台拿到的就是"上一版 + 一份失败报告"。"""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src_zip) as zin:
        names = zin.namelist()
        ti = next((n for n in names if n.endswith(".tree_index.json")), None)
        rep_name = (ti.rsplit("/", 1)[0] + "/migration_report.json") if ti and "/" in ti \
            else "tree/migration_report.json"
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zout:
            for n in names:
                if n != rep_name:
                    zout.writestr(zin.getinfo(n), zin.read(n))
            zout.writestr(rep_name, json.dumps(report, ensure_ascii=False, indent=1))
    return out_zip


def _on_fail(e: SystemExit, ctx: dict) -> None:
    """本单失败也回调(用户拍板:版本挂在"优化中"比回传一个没变的包更糟):按平台格式回传
    **未变化的上一版 skill** + 包内 tree/migration_report.json(status=failed, reason),让版本
    记录闭环;平台"失败/部分成功"字段定了再改为按字段告知。PLATFORM1_CB_ON_FAIL=0 关。
    拿不到上一版(首次优化 / skill 也下不来)或回调本身失败的,只能停,不硬凑。"""
    cb_url, vid = ctx.get("cb_url"), ctx.get("version_id") or ""
    if not cb_url or ctx.get("cb_done") or os.environ.get("PLATFORM1_CB_ON_FAIL", "1").strip() == "0":
        return
    reason = str(ctx.get("reason") or (e.code if not isinstance(e.code, int) else f"退出码 {e.code}"))
    if reason.startswith("[回调"):
        return                                           # 回调本身失败:不再套一层回调
    if not ctx.get("obskey") or not ctx.get("work"):
        print("[失败回传] 没有上一版 skill 可回传(首次优化)——版本记录需平台侧处理")
        return
    skill_zip = ctx.get("skill_zip")
    if skill_zip is None:
        try:
            skill_zip = downloader.download_skill(ctx["obskey"], ctx["work"], fake_store=ctx.get("fake_store"))
        except SystemExit as e2:
            print(f"[失败回传] 上一版 skill 也拿不到({e2.code}),无法回传")
            return
    report = {"versionId": vid, "status": "failed", "total": 0, "landed": 0, "ok": [], "skipped": [],
              "failed": [], "reason": reason[:3000],
              "note": "本单优化失败,回传的是未变化的上一版 skill;原因见 reason"}
    out_zip = _attach_report(skill_zip, ctx["out_dir"] / f"skill_{vid[:8]}.zip", report)
    print(f"[失败回传] 本单失败:{reason[:200]}\n  按平台格式回传未变化的上一版 skill + 包内"
          f" tree/migration_report.json(status=failed),让版本记录闭环;PLATFORM1_CB_ON_FAIL=0 可关")
    try:
        post_callback(cb_url, vid, out_zip)
    except SystemExit as e3:
        print(f"[失败回传] 回调也失败:{e3.code}")


def run(req: dict, workdir: Path | None = None, fake_store: Path | None = None,
        keep: bool = False, with_model: bool = False) -> Path | None:
    ctx: dict = {"fake_store": fake_store}
    try:
        return _run(req, workdir, fake_store, keep, with_model, ctx)
    except SystemExit as e:
        _on_fail(e, ctx)
        raise


def _check_request(req: dict, ctx: dict):
    """校验请求入参并解析回调地址,返回 (excel 路径, obskey, versionId, 回调地址, optimizeType 小写)。"""
    excel = Path(req["excel"]).resolve()
    obskey = str(req.get("prevObjectkey") or "").strip()
    version_id = str(req.get("versionId") or "").strip()
    cb_url = _resolve_callback_url(req)
    ctx.update(cb_url=cb_url, version_id=version_id)
    # optimizeType(平台约定):NONE=问题不在节点,把 skill 引擎代码刷到当前代;
    # MULTI=部分表,只动列出的节点、不动树结构;ALL=全量表,判据与树结构都以表为准。
    # 缺省(本地实验/老请求)= 老行为:空表原样回传,有行走迁移且不带 --map-mode。
    otype = str(req.get("optimizeType") or "").strip().lower()
    if otype and otype not in ("none", "multi", "all"):
        raise SystemExit(f"optimizeType 不认识:「{req.get('optimizeType')}」(认 NONE/MULTI/ALL)")
    if not excel.exists():
        raise SystemExit(f"excel 不存在:{excel}")
    if not version_id:
        raise SystemExit("request 里缺 versionId(平台预生成的版本 UUID,回调时原样带回)")
    if obskey.lower() in ("null", "none"):                # 平台首次优化传 null
        obskey = ""
    ctx["obskey"] = obskey
    return excel, obskey, version_id, cb_url, otype


def _parse_rows(excel: Path, otype: str, obskey: str):
    """解析平台表并报行数;首次优化(没有上一版 skill)不归本驱动处理,退出码 4。"""
    rows = parse_platform_excel(excel)
    print(f"[表] {excel.name}:{len(rows)} 个待迁移节点"
          + (f";optimizeType={otype.upper()}" if otype else ""))
    if not obskey:
        print("prevObjectkey 为空(首次优化)→ 平台侧还没有 skill。从头生成一棵技能"
              "需要专家判据文档等素材,而平台请求里只有 excel(仅节点路径,无判据列)"
              "和 zip 包,凑不齐——首版 skill 应线下用 cs-scot-generate 生成后录入平台,"
              "优化按钮只管之后的迭代。本驱动不处理这种请求。")
        sys.exit(4)
    return rows


def _prepare_workdir(workdir: Path | None, ctx: dict):
    """建工作目录(缺省临时目录),返回 (work, bundle, skill_dir, out_dir)。"""
    work = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="p1opt-"))
    work.mkdir(parents=True, exist_ok=True)
    bundle, skill_dir, out_dir = work / "bundle", work / "skill", work / "out"
    bundle.mkdir(exist_ok=True)
    ctx.update(work=work, skill_dir=skill_dir, out_dir=out_dir)
    return work, bundle, skill_dir, out_dir


def _callback_or_note(cb_url: str, version_id: str, out_zip: Path, ctx: dict, note: str) -> None:
    """有回调地址就回调(先记 cb_done,回调本身失败不再套失败回传);没有就只提示产物在哪。"""
    if cb_url:
        ctx["cb_done"] = True
        post_callback(cb_url, version_id, out_zip)
    else:
        print(note)


def _run_none(obskey: str, work: Path, skill_dir: Path, out_dir: Path, version_id: str,
              cb_url: str, fake_store: Path | None, ctx: dict) -> Path:
    """NONE 单:下载现行 skill → 引擎翻新(--refresh-only)→ 重打包 → 回传。"""
    # NONE 单(平台约定):问题不在节点——把 skill 的引擎代码刷到当前代后回传。
    # 引擎的"当前代"取自本机仓库副本:线上跑之前保证联调机器仓库已拉新。
    print("[NONE] 问题不在节点:下载现行 skill → 引擎翻新(--refresh-only)→ 回传。")
    skill_zip = downloader.download_skill(obskey, work, fake_store=fake_store)
    ctx["skill_zip"] = skill_zip
    product = unpack_skill(skill_zip, skill_dir)
    print(f"[skill] 已解包,产物目录:{product}")
    rc, _out = _run_migrate([sys.executable, str(MIGRATE),
                             "--refresh-only", "--product", str(product)])
    if rc != 0:
        ctx["reason"] = f"[停] 引擎翻新(--refresh-only)退出码 {rc}"
        print(ctx["reason"] + f"——工作目录留在 {work} 供排查。")
        sys.exit(rc)
    out_zip = repack(skill_dir, out_dir / f"skill_{version_id[:8]}.zip")
    print(f"[产物] {out_zip}(引擎已刷到当前代,树与绑定未动)")
    _callback_or_note(cb_url, version_id, out_zip, ctx,
                      f"[回调] 未配置 callback_url,只出产物:versionId={version_id}")
    return out_zip


def _run_empty(obskey: str, work: Path, out_dir: Path, version_id: str, cb_url: str,
               fake_store: Path | None, ctx: dict) -> Path:
    """空表且没带 optimizeType:现行 skill 原样下载后回传。"""
    # 空表且没带 optimizeType(本地实验/老请求):现行 skill 原样返回,
    # 让平台状态正常闭环(不回调会把版本记录挂死在 OPTIMIZING);
    # 逐字节一致也正是「skill 真下载」实验的判定标尺
    print("空表(未带 optimizeType):无迁移可做,现行 skill 原样返回。")
    skill_zip = downloader.download_skill(obskey, work, fake_store=fake_store)
    ctx["skill_zip"] = skill_zip
    out_dir.mkdir(exist_ok=True)
    out_zip = out_dir / f"skill_{version_id[:8]}.zip"
    shutil.copy2(skill_zip, out_zip)
    print(f"[产物] {out_zip}(与现行 skill 逐字节相同)")
    _callback_or_note(cb_url, version_id, out_zip, ctx,
                      f"[回调] 未配置 callback_url,只出产物:versionId={version_id}")
    return out_zip


def _check_obs_tangle(rows) -> None:
    """同一个 Product_Obs 指向多个不同 zip 名 = 上传/导表错位,下载前就停。"""
    # 同一个 Product_Obs 摊给多个不同 zip 名 = 同一对象被当成多个节点的包,必是上传/导表
    # 错位(联调实测:5 个对象摊给 16 行)——下载迁移只会把包对到错误的节点,下载前就停。
    # 同名 zip 被多行复用(如 dscp/8021p 共用一个包)是合法惯例,不在此列
    by_obs: dict[str, set[str]] = {}
    for _names, _name, _obs in rows:
        _fn = _name if _name.lower().endswith(".zip") else _name + ".zip"
        by_obs.setdefault(_obs, set()).add(_fn)
    tangle = {o: sorted(fs) for o, fs in by_obs.items() if len(fs) > 1}
    if tangle:
        lines = "\n".join(f"  {o} → {'、'.join(fs)}" for o, fs in sorted(tangle.items()))
        raise SystemExit("[停] 表里同一个 Product_Obs 指向多个不同的 zip 名——同一个对象"
                         "不可能同时是多个节点的包,是上传/导表错位:\n" + lines +
                         "\n请平台侧给每个节点上传自己的包、重新导出表后再跑")


def _download_bundle(rows, bundle: Path, fake_store: Path | None) -> None:
    """按表逐条下载节点 zip 到 bundle/:已在的合法 zip 跳过,坏件删掉重下。"""
    for names, name, obs in rows:
        fname = name if name.lower().endswith(".zip") else name + ".zip"
        have = bundle / fname
        if have.exists():
            if zipfile.is_zipfile(have):
                print(f"[下载] {'/'.join(names)} ← {fname}(已在,跳过)")
                continue
            # 上次跑留下的坏件(报错体被当文件存盘)——占着正名会被"已在"跳过,删掉重下
            print(f"[下载] {fname} 已在但不是合法 zip(上次的坏件)→ 删除重下")
            have.unlink()
        downloader.download_zip(obs, name, bundle, fake_store=fake_store)
        print(f"[下载] {'/'.join(names)} ← {name}")


def _prepare_product(skill_dir: Path, obskey: str, work: Path, fake_store: Path | None,
                     ctx: dict) -> Path:
    """定位迁移用的产物目录:续跑沿用工作目录里已迁移的产物,否则下载上一版 skill 解包。"""
    if skill_dir.exists() and any(skill_dir.rglob("tree/migrations.json")):
        # 续跑同一工作目录:上一轮迁移成果(tree_index/调优表/修复副本)就地保留——
        # 重新解包会把 zip 里的旧版 tree_index 盖回去,而台账又让已迁移的包被跳过,账实不符
        product = _locate_product(skill_dir)
        print(f"[skill] 续跑:沿用工作目录里已迁移的产物(不重新解包),产物目录:{product}")
    else:
        skill_zip = downloader.download_skill(obskey, work, fake_store=fake_store)
        ctx["skill_zip"] = skill_zip
        product = unpack_skill(skill_zip, skill_dir)
        print(f"[skill] 上一版已解包,产物目录:{product}")
    return product


def _migrate_cmd(bundle: Path, product: Path, excel: Path, otype: str) -> list:
    """拼机械迁移命令行(MULTI/ALL 带 --map-mode)并打印。"""
    cmd = [sys.executable, str(MIGRATE), "--package", str(bundle),
           "--product", str(product), "--auto", "--map", str(excel)]
    if otype in ("multi", "all"):
        cmd += ["--map-mode", otype]                      # multi=不动树结构 / all=结构以表为准
    print("[迁移]", " ".join(cmd))
    return cmd


def _fix_tuning(cmd: list, rc: int, out: str):
    """存量调优表的旧版结构病卡预导入:加 --rebuild-tuning 重跑一次,返回 (rc, out)。"""
    # 存量调优表的旧版结构病(如多个顶层节点)卡预导入:备份 .bak 后重建再跑一次。
    # 只有存在可生效的行、迁移进到写入阶段才会触发预导入——纯机械问题,机械修
    if rc != 0 and "预导入(收编现有调优表) 失败" in out:
        print("[修表] 存量调优表有旧版结构病 → 加 --rebuild-tuning 重跑"
              "(病表备份为 .bak,从产物重建)")
        rc, out = _run_migrate(cmd + ["--rebuild-tuning"])
        print(f"[修表] 重跑退出码 {rc}")
    return rc, out


def _model_prompt(skill: str, cmd: list) -> str:
    """模型会话的提示词:按 cs-scot-migrate 技能处置失败项,只改文件不跑命令。"""
    route = ("(统一入口按输入形态路由:这是对应表批量迁移,走优化线的 migrate 手册)"
             if skill == "cs-scot-generate-optimize" else "")
    return (f"用 {skill} 技能{route}处理一次批量迁移的失败项,只改文件,不用跑命令。"
            f"失败清单看这条命令上一轮的输出(可以重跑它看,但验证不归你,驱动会自动复跑):"
            f"{' '.join(cmd)}。按 SKILL.md 的规则逐项处置:格式检查 FAIL 的照修法"
            f"只修产物 scripts/custom/ 里的副本管道,判断逻辑一行不动;缺标准入口的按副本旁"
            f" *.contract.json 的修法在副本里补 fault_check(data_dir) 薄封装;拿不准算不算"
            f"改逻辑就跳过并在最后列入退回清单,绝不猜。三条实测经验:S3 这类文件过滤"
            f"在同一脚本里常有多处(不同函数各扫一遍目录),要全文搜 .txt 过滤逐处改净;"
            f"S5 第三方库(如 chardet)属管道,按修法换标准库实现(编码探测退化为"
            f" utf-8/gbk 依次尝试 + errors='replace');缺入口的脚本先补完入口再检查"
            f"跑不了,清单上只看得见缺入口——补完薄封装后顺手把同一脚本的 S3"
            f"(.txt/.log 双后缀)与 D3(缺数据判 insufficient_data 而非 false)"
            f"查一遍修净,免得复跑才暴露、又拖到下一单。"
            f"完成后汇报改了哪些文件、跳过了哪些。")


def _model_pass(cmd: list, rc: int, last_out: str, work: Path):
    """机械迁移失败后的模型档:起无头会话处置失败项,再复跑机械迁移验证;返回 (rc, last_out)。"""
    if _parse_migrate_result(last_out) is None:
        # 没跑到汇总=环境/输入问题(坏 zip、表读不了、参数错),不存在"失败项"清单,
        # 模型档无从下手——省一轮无头会话,直接走下方的停止并报错
        print("[模型] 迁移没跑到汇总(环境/输入问题,不是节点失败项)——"
              "模型档帮不上,跳过;报错看上方迁移输出")
        return rc, last_out
    # 机械档处理不了的失败项(语义匹配/修输入输出格式/补入口)交给模型:起 Claude Code 无头会话
    # 按 cs-scot-migrate 技能处置,完了复跑机械迁移验证——这就是完整的 skill 调用形态
    skill = _skill_name_for_model()
    prompt = _model_prompt(skill, cmd)
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
        return rc, last_out
    claude_cmd[0] = exe
    timeout = int(os.environ.get("PLATFORM1_MODEL_TIMEOUT", "3600"))
    # 一单一轮:点一次优化只做一轮模型处置,复跑验证后就交付(部分交付
    # 作为保底),没修完的下次点优化在交付包基础上续迁——不让平台一单等几轮
    mlog = work / "model_session.log"
    print(f"[模型] 起 Claude Code 无头会话处置失败项({skill} 技能),"
          f"每一步动作实时打在下面,事件流落 {mlog}")
    _model_session(claude_cmd, mlog, timeout)
    rc, last_out = _run_migrate(cmd)
    print(f"[模型] 处置后复跑机械迁移,退出码 {rc}")
    return _fix_tuning(cmd, rc, last_out)


def _settle_failures(rc: int, last_out: str, work: Path, product: Path, version_id: str,
                     ctx: dict) -> None:
    """迁移退出码非 0 时定夺:一行没生效就停;否则部分交付,清单写进包内 migration_report.json。"""
    result = _parse_migrate_result(last_out)
    effective = (result["landed"] + len(result["skipped"])) if result else 0
    if not result or effective == 0:
        tail = "\n".join(l for l in last_out.splitlines() if l.strip())[-1500:]
        ctx["reason"] = (f"[停] 迁移退出码 {rc},包里没有任何已生效的迁移内容(一行都没落地)。"
                         f"迁移输出末尾:\n{tail}")
        print(f"[停] 迁移退出码 {rc},且包里没有任何已生效的迁移内容——不打包新版;"
              f"工作目录留在 {work} 供排查。")
        sys.exit(rc)
    # 部分交付:已迁入的节点先生效,成功/失败清单连原因写进包内,平台可解包查看
    result["versionId"] = version_id
    report = product / "tree" / "migration_report.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[部分交付] 已生效 {effective}/{result['total']}(本次落地 {result['landed']}"
          f" + 此前已迁移 {len(result['skipped'])}),失败 {len(result['failed'])} 个"
          f"——节点清单与原因已写入包内 tree/migration_report.json。"
          f"已修的管道副本也随包带走,下一次点优化在这版基础上续迁。"
          f"注意:回调接口暂无「部分成功」字段,需与平台约定(见 README 待确认)。")


def _run(req: dict, workdir: Path | None, fake_store: Path | None,
         keep: bool, with_model: bool, ctx: dict) -> Path | None:
    excel, obskey, version_id, cb_url, otype = _check_request(req, ctx)
    rows = _parse_rows(excel, otype, obskey)
    work, bundle, skill_dir, out_dir = _prepare_workdir(workdir, ctx)

    if otype == "none":
        return _run_none(obskey, work, skill_dir, out_dir, version_id, cb_url, fake_store, ctx)

    if not rows and not otype:
        return _run_empty(obskey, work, out_dir, version_id, cb_url, fake_store, ctx)
    _check_obs_tangle(rows)
    _download_bundle(rows, bundle, fake_store)
    product = _prepare_product(skill_dir, obskey, work, fake_store, ctx)

    cmd = _migrate_cmd(bundle, product, excel, otype)
    rc, last_out = _run_migrate(cmd)
    rc, last_out = _fix_tuning(cmd, rc, last_out)
    if rc != 0 and with_model:
        rc, last_out = _model_pass(cmd, rc, last_out, work)
    if rc != 0:
        _settle_failures(rc, last_out, work, product, version_id, ctx)

    out_zip = repack(skill_dir, out_dir / f"skill_{version_id[:8]}.zip")
    print(f"[产物] {out_zip}")
    _callback_or_note(cb_url, version_id, out_zip, ctx,
                      f"[回调] 未配置 callback_url,只出产物:version_id={version_id},文件见上行")
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
                         "处置(语义匹配/修输入输出格式/补入口),完了复跑机械迁移;命令可用环境变量"
                         " PLATFORM1_CLAUDE_CMD 覆盖(默认 claude --permission-mode acceptEdits -p)")
    a = ap.parse_args()
    req = json.loads(Path(a.request).read_text(encoding="utf-8"))
    run(req, workdir=Path(a.workdir) if a.workdir else None,
        fake_store=Path(a.fake_store) if a.fake_store else None, keep=a.keep,
        with_model=a.with_model)


if __name__ == "__main__":
    main()
