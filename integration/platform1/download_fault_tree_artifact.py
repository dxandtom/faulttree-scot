#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
故障树产物批量下载脚本(平台1 同事提供;downloader.py 按它接线)
功能:读取Excel文件中的Product_Obs和Product_Name,逐条下载OBS产物
用法:python download_fault_tree_artifact.py <excel_file> [--output_dir <output_dir>] [--env dev|beta]
依赖:pandas、requests、psf(内部库)+ 同目录 obs_headers_config.json(含 token/密钥路径,不进仓库)

与原版的唯一差异(应我方要求改,2026-08-27):平台一次下载有数量限制,download_batch
超过 5 条时按 5 个一批分批下载——每批得到一个合集 zip(文件名加 _partN),都落在同一个
输出目录;最后一批只剩 1 条时按单条方式落原名(服务端单条返回的是文件本身,不是合集包)。
"""

import argparse
import json
import os
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import time
from psf.common.config.psf_config import PsfConfig
from psf.crypto.xms_encryptor.decrypt_service import XMSDecryptService
from psf.crypto.xms_encryptor.encrypt_service import XMSEncryptService

decrypt = XMSDecryptService()
encrypt = XMSEncryptService()
PSF_HAS_INITED = False
PsfConfig.set_config("psf", "server_ssl_client_auth", "none")
PsfConfig.set_config("psf", "ssl_server_cert_switch", "on")

CONFIG_FILE = Path(__file__).parent / "obs_headers_config.json"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "downloads"
BATCH_DOWNLOAD_SIZE = 5   # 平台一次下载有数量限制:超过就按这个数分批

_psf_config_cache = {}


def _load_psf_config():
    """加载PSF配置文件路径到全局缓存"""
    global _psf_config_cache
    if not _psf_config_cache:
        config_path = Path(__file__).parent / "obs_headers_config.json"
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        default_section = config.get('default', {})
        _psf_config_cache['psfRootKeyFile'] = _resolve_path(default_section.get('psfRootKeyFile', ''))
        _psf_config_cache['psfWorkKeyFile'] = _resolve_path(default_section.get('psfWorkKeyFile', ''))
    return _psf_config_cache


def _resolve_path(path_str: str) -> str:
    """解析路径，支持绝对路径和相对路径（相对于脚本所在目录）"""
    if not path_str:
        return path_str
    path = Path(path_str)
    if path.is_absolute():
        return str(path)
    return str(Path(__file__).parent / path_str)


def generate_timestamp_name(prefix: str, extension: str) -> str:
    """生成带时间戳的文件名，如: prefix_2026-08-19-16-15-16.zip"""
    ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    return f"{prefix}_{ts}.{extension}"


class OBSDownloader:
    """OBS下载器"""

    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        self.session = requests.Session()
        self.default_config = self.config.get('default', {})

    def _load_config(self, config_path: str = None) -> dict:
        """加载配置文件"""
        path = Path(config_path) if config_path else CONFIG_FILE
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _get_invoke_token(self, env: str) -> str:
        """获取invokeToken（支持本地文件路径或URL，相对路径相对于脚本目录）"""
        env_config = self.config.get(env, self.config.get('dev'))
        token_source = env_config.get('invokeToken', '')

        if not token_source or token_source.startswith('${'):
            raise ValueError(f"请在配置文件中设置有效的invokeToken (环境: {env})")

        if token_source.startswith('http://') or token_source.startswith('https://'):
            print(f"[获取Token] 从URL获取: {token_source}")
            response = requests.get(token_source, verify=False, timeout=30)
            response.raise_for_status()
            token = response.text.strip()
            print(f"[获取Token] 成功获取Token")
            return token

        resolved_path = _resolve_path(token_source)
        if Path(resolved_path).exists():
            print(f"[获取Token] 从本地文件读取: {resolved_path}")
            with open(resolved_path, 'r', encoding='utf-8') as f:
                token = f.read().strip()
            print(f"[获取Token] 成功读取Token")
            return token
        else:
            print(f"[警告] Token文件不存在: {resolved_path}，尝试作为直接Token使用")
            return token_source

    def _get_headers(self, env: str) -> dict:
        """获取请求头"""
        env_config = self.config.get(env, self.config.get('dev'))
        auth_type = env_config.get('authType', 'invokeToken')

        if auth_type == 'invokeToken':
            token = self._get_invoke_token(env)
            return {"invokeToken": invoke_encode(token)}
        else:
            cookie = env_config.get('cookie', '')
            if not cookie:
                raise ValueError(f"请在配置文件中设置有效的cookie (环境: {env})")
            return {"Cookie": cookie}

    def download_artifacts(self, artifacts: list, output_dir: Path,
                           env: str = 'dev', locale: str = 'zh_CN',
                           custom_file_name: str = None) -> dict:
        """
        下载故障树产物

        Args:
            artifacts: [{"docId": "xxx", "file_name": "xxx.zip"}, ...]
            output_dir: 输出目录
            env: 环境
            locale: 语言标识
            custom_file_name: 自定义文件名（带时间戳）

        Returns:
            下载结果统计 {"success": 0, "failed": 0, "skipped": 0}
        """
        env_config = self.config.get(env, self.config.get('dev'))
        base_url = env_config.get('endpoint')
        url = f"{base_url}?locale={locale}"
        headers = self._get_headers(env)
        headers["Content-Type"] = "application/json"
        timeout = self.default_config.get('timeout', 30)

        results = {"success": 0, "failed": 0, "skipped": 0}

        try:
            print(f"[请求中] 提交下载任务，数量: {len(artifacts)}")
            params = {"docInfo": artifacts}
            print(f"[请求数据] {json.dumps(params, ensure_ascii=False)}")
            response = requests.post(url, headers=headers, json=params, verify=False, timeout=timeout)
            print(f"[响应状态] {response.status_code}")
            response.raise_for_status()

            content_type = response.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                result_data = response.json()
                print(f"[响应] {result_data}")
                return results

            output_dir.mkdir(parents=True, exist_ok=True)

            if custom_file_name:
                file_name = custom_file_name
            else:
                file_name = urllib.parse.unquote(artifacts[0].get('file_name', 'download.zip'))

            file_path = output_dir / file_name

            chunk_sz = 1024 * 1024
            with open(file_path, 'wb') as fb:
                for chunk in response.iter_content(chunk_sz):
                    fb.write(chunk)

            print(f"[成功] -> {file_path}")
            results["success"] = 1

        except requests.exceptions.Timeout:
            print(f"[超时] 请求超时")
            results["failed"] = len(artifacts)
        except requests.exceptions.RequestException as e:
            print(f"[失败] {e}")
            results["failed"] = len(artifacts)

        return results

    def download_batch(self, excel_path: str, output_dir: Path,
                       env: str = 'dev',
                       product_obs_col: str = 'Product_Obs',
                       product_name_col: str = 'Product_Name') -> dict:
        """
        批量下载Excel中的OBS文件
        - 只有一条数据：直接下载，不压缩
        - 多条数据：批量下载，使用带时间戳的压缩包名

        Args:
            excel_path: Excel文件路径
            output_dir: 输出目录
            env: 环境
            product_obs_col: OBS路径列名
            product_name_col: 文件名列名

        Returns:
            下载结果统计 {"success": 0, "failed": 0, "skipped": 0}
        """
        df = pd.read_excel(excel_path)
        excel_basename = Path(excel_path).stem

        results = {"success": 0, "failed": 0, "skipped": 0}

        artifacts = []
        for idx, row in df.iterrows():
            obs_path = str(row.get(product_obs_col, '')).strip()
            file_name = str(row.get(product_name_col, '')).strip()

            if not obs_path or obs_path == 'nan':
                print(f"[跳过] 第{idx + 2}行: OBS路径为空")
                results["skipped"] += 1
                continue

            if not file_name or file_name == 'nan':
                file_name = os.path.basename(obs_path)
                if not file_name:
                    file_name = f"download_{idx}"

            artifacts.append({"docId": obs_path, "file_name": file_name})

        if not artifacts:
            print("[提示] 没有可下载的数据")
            return results

        if len(artifacts) == 1:
            print(f"[下载] 只有1条数据，直接下载")
            batch_result = self.download_artifacts(artifacts, output_dir, env, custom_file_name=None)
            results["success"] = batch_result["success"]
            results["failed"] = batch_result["failed"]
        else:
            batches = [artifacts[i:i + BATCH_DOWNLOAD_SIZE]
                       for i in range(0, len(artifacts), BATCH_DOWNLOAD_SIZE)]
            print(f"[下载] 有{len(artifacts)}条数据，分 {len(batches)} 批下载"
                  f"（每批至多 {BATCH_DOWNLOAD_SIZE} 个）")
            for bi, batch in enumerate(batches, 1):
                print(f"[批次 {bi}/{len(batches)}] {len(batch)} 个")
                if len(batch) == 1:
                    # 服务端单条返回的是文件本身（不是合集包），按单条方式落原名
                    batch_result = self.download_artifacts(batch, output_dir, env,
                                                           custom_file_name=None)
                else:
                    suffix = excel_basename if len(batches) == 1 else f"{excel_basename}_part{bi}"
                    timestamped_name = generate_timestamp_name(suffix, 'zip')
                    batch_result = self.download_artifacts(batch, output_dir, env,
                                                           custom_file_name=timestamped_name)
                results["success"] += batch_result["success"]
                results["failed"] += batch_result["failed"]
                if bi < len(batches):
                    time.sleep(1)

        return results


def invoke_encode(invoke_token):
    data = json.dumps({'time': int(time.time() * 1000), 'client': decode(invoke_token)})
    invoke_token = encode(data)
    return invoke_token


def init():
    psf_config = _load_psf_config()
    root_key_file = psf_config.get('psfRootKeyFile', '')
    work_key_file = psf_config.get('psfWorkKeyFile', '')
    if root_key_file:
        PsfConfig.set_config('psf', 'root_key_file', root_key_file)
    if work_key_file:
        PsfConfig.set_config('psf', 'work_key_file', work_key_file)
    global PSF_HAS_INITED
    PSF_HAS_INITED = True


def encode(data):
    global PSF_HAS_INITED
    if not PSF_HAS_INITED:
        init()
        PSF_HAS_INITED = True
    return encrypt.encrypt(data)


def decode(cipher):
    global PSF_HAS_INITED
    if not PSF_HAS_INITED:
        init()
        PSF_HAS_INITED = True
    return decrypt.decrypt(cipher)


def main():
    parser = argparse.ArgumentParser(
        description='故障树产物批量下载工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python download_fault_tree_artifact.py                                    # 使用配置文件中的defaultExcelPath
  python download_fault_tree_artifact.py fault_tree.xlsx
  python download_fault_tree_artifact.py fault_tree.xlsx -o ./output -e beta
  python download_fault_tree_artifact.py "C:\\Users\\test\\故障树导出.xlsx" -e dev
        """
    )
    parser.add_argument('excel_file', nargs='?', default=None, help='Excel文件路径 (不传则使用配置文件中的defaultExcelPath)')  # excel文件
    parser.add_argument('--output_dir', '-o', default=str(DEFAULT_OUTPUT_DIR),
                        help=f'输出目录 (默认: {DEFAULT_OUTPUT_DIR})')
    parser.add_argument('--env', '-e', default=None, choices=['dev', 'beta'],
                        help='运行环境 (默认: 使用配置文件中的default.env)')
    parser.add_argument('--config', '-c', default=str(CONFIG_FILE),
                        help=f'配置文件路径 (默认: {CONFIG_FILE})')

    args = parser.parse_args()

    if args.excel_file is None:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)
        excel_file = config.get('default', {}).get('defaultExcelPath', '')
        if not excel_file:
            print("[错误] 未提供Excel文件路径，且配置文件中未设置defaultExcelPath")
            print(f"请在配置文件 {args.config} 的 default 节中设置 defaultExcelPath")
            sys.exit(1)
        args.excel_file = excel_file
        print(f"[提示] 未提供Excel文件路径，使用配置文件中的路径: {excel_file}")

    excel_path = Path(args.excel_file)
    if not excel_path.exists():
        print(f"Excel文件不存在: {excel_path}")
        sys.exit(1)

    output_dir = Path(args.output_dir)

    default_env = args.env
    if default_env is None:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)
        default_env = config.get('default', {}).get('env', 'dev')

    print(f"=" * 60)
    print(f"故障树产物批量下载")
    print(f"=" * 60)
    print(f"Excel文件: {excel_path}")
    print(f"输出目录: {output_dir}")
    print(f"环境: {default_env}")
    print(f"配置文件: {args.config}")
    print(f"=" * 60)

    try:
        downloader = OBSDownloader(args.config)
        results = downloader.download_batch(
            str(excel_path),
            output_dir,
            default_env
        )

        print(f"=" * 60)
        print(f"下载完成")
        print(f"成功: {results['success']}")
        print(f"失败: {results['failed']}")
        print(f"跳过: {results['skipped']}")
        print(f"=" * 60)

    except ValueError as e:
        print(f"[配置错误] {e}")
        print(f"请检查配置文件 {args.config} 中的认证信息")
        sys.exit(1)
    except Exception as e:
        print(f"[执行出错] {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
