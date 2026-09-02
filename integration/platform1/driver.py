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
    列口径与 cs-scot-migrate 的表识别一致:路径取 L{n}_Name_CN,层级动态。
    表带标记列(是否修改/is_modified 等)时,未标记行的 zip 不下载(标记契约:
    内容动作只对标记行执行,migrate 同口径)。"""
    header, rows = _read_table(path)
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
        elif ("是否修改" in hn or "是否变更" in hn or "是否优化" in hn or "需要优化" in hn
              or "修改标记" in hn or "is_modified" in low or "need_optimize" in low
              or low in ("modified", "changed", "is_update", "update_flag")):
            mk_col = h
    if not cn_cols or name_col is None:
        raise SystemExit(f"不是平台1 导出表(需要 L*_Name_CN 层级列 + Product_Name 列);"
                         f"实际表头:{header}")
    cn_cols.sort()
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
    ctx = None
    if os.environ.get("PLATFORM1_CB_INSECURE", "").strip().lower() in ("1", "true", "yes"):
        import ssl                       # 内网自签证书联调用:不校验对端证书(线上别开)
        ctx = ssl._create_unverified_context()
        print("[回调] PLATFORM1_CB_INSECURE=1:不校验对端 HTTPS 证书")
    try:
        resp_cm = urllib.request.urlopen(req, timeout=60, context=ctx)
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
    with resp_cm as resp:
        text = resp.read().decode("utf-8", "replace")
        print(f"[回调] {url} → HTTP {resp.status} {text[:300]}")
        # 平台口径:成败在响应 JSON 里(responseCode "0"=成功,"1"=失败),HTTP 200 也可能是
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


# ---------- 迁移执行(捕获输出供决策,同时转发到终端) ----------

def _run_migrate(cmd):
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    print(out)
    return r.returncode, out


def _parse_migrate_result(out: str):
    """从迁移输出解析结果清单:落地/新增/跳过/判据同步/删除/失败(带表行号与原因)。
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
    # optimizeType(平台口径):NONE=问题不在节点,把 skill 引擎代码刷到当前代;
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

    rows = parse_platform_excel(excel)
    print(f"[表] {excel.name}:{len(rows)} 个待迁移节点"
          + (f";optimizeType={otype.upper()}" if otype else ""))
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

    if otype == "none":
        # NONE 单(平台口径):问题不在节点——把 skill 的引擎代码刷到当前代后回传。
        # 引擎的"当前代"取自本机仓库副本:线上跑之前保证联调机器仓库已拉新。
        print("[NONE] 问题不在节点:下载现行 skill → 引擎翻新(--refresh-only)→ 回传。")
        skill_zip = downloader.download_skill(obskey, work, fake_store=fake_store)
        product = unpack_skill(skill_zip, skill_dir)
        print(f"[skill] 已解包,产物目录:{product}")
        rc, _out = _run_migrate([sys.executable, str(MIGRATE),
                                 "--refresh-only", "--product", str(product)])
        if rc != 0:
            print(f"[停] 引擎翻新退出码 {rc}——不回调,工作目录留在 {work} 供排查。")
            sys.exit(rc)
        out_zip = repack(skill_dir, out_dir / f"skill_{version_id[:8]}.zip")
        print(f"[产物] {out_zip}(引擎已刷到当前代,树与绑定未动)")
        if cb_url:
            post_callback(cb_url, version_id, out_zip)
        else:
            print(f"[回调] 未配置 callback_url,只出产物:versionId={version_id}")
        return out_zip

    if not rows and not otype:
        # 空壳表且没带 optimizeType(本地实验/老请求):现行 skill 原样返回,
        # 让平台状态正常闭环(不回调会把版本记录挂死在 OPTIMIZING);
        # 逐字节一致也正是「skill 真下载」实验的判定标尺
        print("空壳表(未带 optimizeType):无迁移可做,现行 skill 原样返回。")
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
    # 同一个 Product_Obs 摊给多个不同 zip 名 = 同一对象被当成多个节点的包,必是上传/导表
    # 错位(联调实测:5 个对象摊给 16 行)——下载迁移只会张冠李戴,下载前就停。
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
    if skill_dir.exists() and any(skill_dir.rglob("tree/migrations.json")):
        # 续跑同一工作目录:上一轮迁移成果(tree_index/调优表/修复副本)就地保留——
        # 重新解包会把 zip 里的旧版 tree_index 盖回去,而台账又让已迁移的包被跳过,账实不符
        product = _locate_product(skill_dir)
        print(f"[skill] 续跑:沿用工作目录里已迁移的产物(不重新解包),产物目录:{product}")
    else:
        skill_zip = downloader.download_skill(obskey, work, fake_store=fake_store)
        product = unpack_skill(skill_zip, skill_dir)
        print(f"[skill] 上一版已解包,产物目录:{product}")

    cmd = [sys.executable, str(MIGRATE), "--package", str(bundle),
           "--product", str(product), "--auto", "--map", str(excel)]
    if otype in ("multi", "all"):
        cmd += ["--map-mode", otype]                      # multi=不动树结构 / all=结构以表为准
    print("[迁移]", " ".join(cmd))

    def _fix_tuning(rc, out):
        # 存量调优表的旧版结构病(如多个顶层节点)卡预导入:备份 .bak 后重建再跑一次。
        # 只有存在可落地的行、迁移进到写入阶段才会触发收编——纯机械问题,机械修
        if rc != 0 and "预导入(收编现有调优表) 失败" in out:
            print("[修表] 存量调优表有旧版结构病 → 加 --rebuild-tuning 重跑"
                  "(病表备份为 .bak,从产物重建)")
            rc, out = _run_migrate(cmd + ["--rebuild-tuning"])
            print(f"[修表] 重跑退出码 {rc}")
        return rc, out

    rc, last_out = _run_migrate(cmd)
    rc, last_out = _fix_tuning(rc, last_out)
    if rc != 0 and with_model and _parse_migrate_result(last_out) is None:
        # 没跑到汇总=环境/输入问题(坏 zip、表读不了、参数错),不存在"失败项"清单,
        # 模型档无从下手——省一轮无头会话,直接走下方硬停
        print("[模型] 迁移没跑到汇总(环境/输入问题,不是节点失败项)——"
              "模型档帮不上,跳过;报错看上方迁移输出")
    elif rc != 0 and with_model:
        # 机械挡吃不下的失败项(语义对号/修管道/补入口)交给模型:起 Claude Code 无头会话
        # 按 cs-scot-migrate 技能处置,完了复跑机械迁移验证——这就是完整的 skill 调用形态
        prompt = (f"用 cs-scot-migrate 技能处理一次批量迁移的失败项,只改文件,不用跑命令。"
                  f"失败清单看这条命令上一轮的输出(可以重跑它看,但验证不归你,驱动会自动复跑):"
                  f"{' '.join(cmd)}。按 SKILL.md 的规矩逐项处置:契约体检 FAIL 的照修法"
                  f"只修产物 scripts/custom/ 里的副本管道,判断逻辑一行不动;缺标准入口的按副本旁"
                  f" *.contract.json 的修法在副本里补 fault_check(data_dir) 薄封装;拿不准算不算"
                  f"改逻辑就跳过并在最后列入返厂清单,绝不赌。三条实测经验:S3 这类文件过滤"
                  f"在同一脚本里常有多处(不同函数各扫一遍目录),要全文搜 .txt 过滤逐处改净;"
                  f"S5 第三方库(如 chardet)属管道,按修法换标准库实现(编码探测退化为"
                  f" utf-8/gbk 依次尝试 + errors='replace');缺入口的脚本补完入口前体检"
                  f"跑不了,清单上只看得见缺入口——补完薄封装后顺手把同一脚本的 S3"
                  f"(.txt/.log 双后缀)与 D3(缺数据判 insufficient_data 而非 false)"
                  f"查一遍修净,免得复跑才暴露、又拖到下一单。"
                  f"完成后汇报改了哪些文件、跳过了哪些。")
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
            timeout = int(os.environ.get("PLATFORM1_MODEL_TIMEOUT", "3600"))
            # 一单一轮:点一次优化只做一轮模型处置,复跑验证后就交付(部分交付
            # 兜底),没修完的下次点优化在交付包基础上续迁——不让平台一单等几轮
            mlog = work / "model_session.log"
            print(f"[模型] 起 Claude Code 无头会话处置失败项(cs-scot-migrate 技能),"
                  f"每一步动作实时打在下面,事件流落 {mlog}")
            _model_session(claude_cmd, mlog, timeout)
            rc, last_out = _run_migrate(cmd)
            print(f"[模型] 处置后复跑机械迁移,退出码 {rc}")
            rc, last_out = _fix_tuning(rc, last_out)
    if rc != 0:
        result = _parse_migrate_result(last_out)
        effective = (result["landed"] + len(result["skipped"])) if result else 0
        if not result or effective == 0:
            print(f"[停] 迁移退出码 {rc},且包里没有任何已生效的迁移内容——"
                  f"不回调、不打包(回传无变化的包只会误导版本记录);"
                  f"工作目录留在 {work} 供排查。")
            sys.exit(rc)
        # 部分交付:已落地的节点先生效,成功/失败清单连原因写进包内,平台可解包查看
        result["versionId"] = version_id
        report = product / "tree" / "migration_report.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[部分交付] 已生效 {effective}/{result['total']}(本次落地 {result['landed']}"
              f" + 此前已迁移 {len(result['skipped'])}),失败 {len(result['failed'])} 个"
              f"——节点清单与原因已写入包内 tree/migration_report.json。"
              f"已修的管道副本也随包带走,下一次点优化在这版基础上续迁。"
              f"注意:回调接口暂无「部分成功」字段,需与平台定口径(见 README 待确认)。")

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
