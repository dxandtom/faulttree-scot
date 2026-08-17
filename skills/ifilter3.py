
def get_extracted_lsdb_info(diagFile):
    """识别 extract_lsdb 生成的 LSDB 文件
    
    文件名格式：设备名_lsdb.txt 或在 LSDB/ 目录下
    例如：AG-BDCKJ-01H_lsdb.txt 或 AG-BDCKJ-01H/LSDB/AG-BDCKJ-01H_lsdb.txt
    """
    if not path.isfile(diagFile):
        return False
    
    file_name = path.split(diagFile)[1]
    
    # 匹配格式：设备名_lsdb.txt（如 AG-BDCKJ-01H_lsdb.txt）
    lsdb_pattern = compile(r'^([A-Z][A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+)_lsdb\.txt$', I)
    match = lsdb_pattern.match(file_name)
    
    if match:
        neName = match.group(1)
        fileType = 'LSDB'
        diagInfo = {
            'fileName': diagFile,
            'neName': neName,
            'fileType': fileType,
            'neVersion': '',
            'startTime': '',
            'endTime': '',
            'neType': ''
        }
        return diagInfo
    
    # 检查是否在 LSDB/ 目录下
    dir_parts = diagFile.replace('\\', '/').split('/')
    if len(dir_parts) >= 2:
        parent_dir = dir_parts[-2]  # LSDB
        if parent_dir.upper() == 'LSDB':
            for part in dir_parts:
                lsdb_match = lsdb_pattern.match(part)
                if lsdb_match:
                    neName = lsdb_match.group(1)
                    fileType = 'LSDB'
                    diagInfo = {
                        'fileName': diagFile,
                        'neName': neName,
                        'fileType': fileType,
                        'neVersion': '',
                        'startTime': '',
                        'endTime': '',
                        'neType': ''
                    }
                    return diagInfo
    
    return False
#获取info.txt文件中的网元和版本信息
def get_neinfo_file(diagFile):
    if not path.isfile(diagFile):
        return False
    if path.split(diagFile)[-1] != 'info.txt':
        return False
    diagData = ''
    diagInfo = {}
    neName, fileType, startTime, neVersion, endTime, neType = '', 'INFO', '', '', '', ''
    #print diagFile
    # UTF-16 BOM detection: 优先于 charset_normalizer 检测，避免误判
    encoding_to_use = None
    raw_bytes = None
    with open(diagFile, 'rb') as f:
        raw_bytes = f.read()
    bom = raw_bytes[:4]
    if bom[:2] == b'\xff\xfe':
        encoding_to_use = 'utf-16-le'
        print(f'  [UTF16-DETECT] BOM LE detected for {path.basename(diagFile)}, size={len(raw_bytes)}')
    elif bom[:2] == b'\xfe\xff':
        encoding_to_use = 'utf-16-be'
        print(f'  [UTF16-DETECT] BOM BE detected for {path.basename(diagFile)}, size={len(raw_bytes)}')
    if encoding_to_use is None:
        detected = detect(raw_bytes[:5000])
        if detected['encoding'] is not None and detected['confidence'] >= 0.7:
            enc = detected['encoding'].lower()
            if 'utf-16' in enc:
                encoding_to_use = 'utf-16'
            else:
                encoding_to_use = detected['encoding']
        else:
            encoding_to_use = 'utf-8'
    diagData = raw_bytes.decode(encoding_to_use, errors='ignore')
    # Verify: check if 'display current-configuration' appears in first 10KB
    if 'AG-BDMJK' in diagFile and 'diag.log' in diagFile.lower():
        first_check = diagData[:10000]
        has_cc = 'display current-configuration' in first_check
        print(f'  [UTF16-DETECT] File={path.basename(diagFile)}, enc={encoding_to_use}, size={len(raw_bytes)}, has_cc_in_first_10k={has_cc}, first_30={repr(diagData[:30])}')
    if not diagData:
        return False
    regExp = search(r'ne.name:(\S+)', diagData)
    if regExp:
        neName = regExp.group(1)
        fileType = 'INFO'
    else:
        return False

    regExp = search(r'ne.version:(\S+)', diagData)
    if regExp:
        neVersion = regExp.group(1)

    regExp = search(r'ne.type:(.+)', diagData)
    if regExp:
        neType = regExp.group(1).strip()

    diagInfo.update(fileName=diagFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
    return diagInfo

def get_cfg_info(diagFile):
    if not path.isfile(diagFile):
        return False
    name, ext = get_file_prefix(diagFile)
    temp = diagFile.lower()
    if not temp.endswith('.cfg'):
        return False
    diagData = ''
    diagInfo = {}
    neName, fileType, startTime, neVersion, endTime, neType = '', '', '', '', '', ''
    #print diagFile
    # UTF-16 BOM detection: 优先于 charset_normalizer 检测，避免误判
    encoding_to_use = None
    raw_bytes = None
    with open(diagFile, 'rb') as f:
        raw_bytes = f.read()
    bom = raw_bytes[:4]
    if bom[:2] == b'\xff\xfe':
        encoding_to_use = 'utf-16-le'
        print(f'  [UTF16-DETECT] BOM LE detected for {path.basename(diagFile)}, size={len(raw_bytes)}')
    elif bom[:2] == b'\xfe\xff':
        encoding_to_use = 'utf-16-be'
        print(f'  [UTF16-DETECT] BOM BE detected for {path.basename(diagFile)}, size={len(raw_bytes)}')
    if encoding_to_use is None:
        detected = detect(raw_bytes[:5000])
        if detected['encoding'] is not None and detected['confidence'] >= 0.7:
            enc = detected['encoding'].lower()
            if 'utf-16' in enc:
                encoding_to_use = 'utf-16'
            else:
                encoding_to_use = detected['encoding']
        else:
            encoding_to_use = 'utf-8'
    diagData = raw_bytes.decode(encoding_to_use, errors='ignore')
    # Verify: check if 'display current-configuration' appears in first 10KB
    if 'AG-BDMJK' in diagFile and 'diag.log' in diagFile.lower():
        first_check = diagData[:10000]
        has_cc = 'display current-configuration' in first_check
        print(f'  [UTF16-DETECT] File={path.basename(diagFile)}, enc={encoding_to_use}, size={len(raw_bytes)}, has_cc_in_first_10k={has_cc}, first_30={repr(diagData[:30])}')
    if not diagData:
        return False
    regExp = search(r'sysname\s*(\S+)', diagData)
    if regExp:
        neName = regExp.group(1)
        fileType = 'CONFIG'
    else:
        return False
    #regExp = search(r'V\d{3}R\d{3}C\d{2}\S+', diagData)
    regExp = search(r'.ersion.+?(V\d{3}R\d{3}C\d{2}\S+)\)?\.?', diagData, M)
    if regExp:
        neVersion = regExp.group(1)
        neVersion = neVersion.strip(' )')

    diagInfo.update(fileName=diagFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
    return diagInfo
def get_file_prefix(fileName):
    name, ext = path.splitext(fileName)
    #后缀名合法性校验：如果后缀名大于5或全是数字或包含数字的话并且不包含pcapng，则认为是不可识别的后缀名，算作文件名的一部分
    if (len(ext) > 5 or ext.strip('.').isdigit() or len(''.join(filter(str.isdigit, str(ext))))>1) and ('pcapng' not in ext):
        name = name+ext
        ext = ''
    return (name, ext)

# 辅助函数：去除所有空行，并统一换行符
def remove_blank_lines(content):
    """去除所有空行，统一换行符为\n"""
    if not content:
        return content
    # 统一换行符为 \n
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    lines = content.split('\n')
    result = [line for line in lines if len(line.strip()) > 0]
    return '\n'.join(result)

# 辅助函数：从原始数据中删除display current-configuration和display logbuffer的回显
def remove_cfg_log_from_original(content, neName):
    """从原始数据中删除CONFIG和LOG回显，返回修改后的数据"""
    if not content:
        return content
    
    # 统一换行符
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    
    result = content
    
    # 删除 display current-configuration 的回显
    # 格式1：等号分隔符格式（兼容命令头行前有空格的情况）
    cfg_pattern1 = compile(r'\n\s*=+\s*display\s+current-configuration.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = cfg_pattern1.sub('', result)
    
    # 格式2：提示符格式
    cfg_pattern2 = compile(r'<{}[^>]*>\s*display\s+current-configuration\s*\n.*?(?=\n<{}[^>]*>\s*(?:dis|quit|return|sys)|\Z)'.format(re.escape(neName), re.escape(neName)), S)
    result = cfg_pattern2.sub('', result)
    
    # 格式3：HUAWEI 分隔符 + 提示符格式（nicfiledata 标准 CLI 格式）
    cfg_pattern3 = compile(rf'<{re.escape(neName)}[^>]*>\s*\n\s*=+######HUAWEI#####=+\s*\n\s*display\s+current-configuration\s*\n.*?(?=\n<{re.escape(neName)}[^>]*>\s*\n|\n\[{re.escape(neName)}\]\s*\n|\Z)', S)
    result = cfg_pattern3.sub('', result)
    
    # 带方括号设备名变体 [~设备名]
    cfg_pattern3b = compile(rf'\[~?{re.escape(neName)}[^]]*\]\s*\n\s*=+######HUAWEI#####=+\s*\n\s*display\s+current-configuration\s*\n.*?(?=\n<[^>]*>\s*\n|\n\[[^\]]*\]\s*\n|\Z)', S)
    result = cfg_pattern3b.sub('', result)
    
    # 删除 display logbuffer 的回显
    # 格式1：等号分隔符格式（兼容命令头行前有空格的情况）
    log_pattern1 = compile(r'\n\s*=+\s*display\s+logbuffer.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = log_pattern1.sub('', result)
    
    # 格式2：提示符格式
    log_pattern2 = compile(r'<{}[^>]*>\s*display\s+logbuffer\s*\n.*?(?=\n<{}[^>]*>\s*(?:dis|quit|return|sys)|\Z)'.format(re.escape(neName), re.escape(neName)), S)
    result = log_pattern2.sub('', result)
    
    # 格式3：HUAWEI 分隔符 + 提示符格式（nicfiledata 标准 CLI 格式）
    log_pattern3 = compile(rf'<{re.escape(neName)}[^>]*>\s*\n\s*=+######HUAWEI#####=+\s*\n\s*display\s+logbuffer\s*\n.*?(?=\n<{re.escape(neName)}[^>]*>\s*\n|\n\[{re.escape(neName)}\]\s*\n|\Z)', S)
    result = log_pattern3.sub('', result)
    
    # 带方括号设备名变体 [~设备名]
    log_pattern3b = compile(rf'\[~?{re.escape(neName)}[^]]*\]\s*\n\s*=+######HUAWEI#####=+\s*\n\s*display\s+logbuffer\s*\n.*?(?=\n<[^>]*>\s*\n|\n\[[^\]]*\]\s*\n|\Z)', S)
    result = log_pattern3b.sub('', result)
    
    # 删除 display trapbuffer 的回显
    # 格式1：等号分隔符格式
    trap_pattern1 = compile(r'\n\s*=+\s*display\s+trapbuffer.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = trap_pattern1.sub('', result)
    
    trap_pattern2 = compile(r'<{}[^>]*>\s*display\s+trapbuffer\s*\n.*?(?=\n<{}[^>]*>\s*(?:dis|quit|return|sys)|\Z)'.format(re.escape(neName), re.escape(neName)), S)
    result = trap_pattern2.sub('', result)
    
    # 删除 dis* logfile 的回显
    logfile_pattern1 = compile(r'\n\s*=+\s*dis\S+\s+logfile.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = logfile_pattern1.sub('', result)
    
    logfile_pattern2 = compile(r'<{}[^>]*>\s*dis\S+\s+logfile.*?\s*\n.*?(?=\n<{}[^>]*>\s*(?:dis|quit|return|sys)|\Z)'.format(re.escape(neName), re.escape(neName)), S)
    result = logfile_pattern2.sub('', result)
    
    # 删除 more.* 的回显
    more_pattern1 = compile(r'\n\s*=+\s*more\s+.+?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = more_pattern1.sub('', result)
    
    more_pattern2 = compile(r'<{}[^>]*>\s*more\s+.*?\n.*?(?=\n<{}[^>]*>\s*(?:dis|quit|return|sys)|\Z)'.format(re.escape(neName), re.escape(neName)), S)
    result = more_pattern2.sub('', result)
    
    # 新增：删除 LSDB 相关命令的回显（支持 display 缩写和管道参数）
    dis = r'dis(?:p(?:l(?:a(?:y)?)?)?)?'
    
    # display isis lsdb verbose
    isis_lsdb_pattern1 = compile(rf'\n\s*=+\s*{dis}\s+isis\s+lsdb\s+verbose.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = isis_lsdb_pattern1.sub('', result)
    
    # 提示符格式：<设备名>disp isis lsdb verbose | no
    isis_lsdb_pattern2 = compile(rf'<{re.escape(neName)}[^>]*>\s*{dis}\s+isis\s+lsdb\s+verbose.*?\n.*?(?=\n<{re.escape(neName)}[^>]*>\s*(?:dis|quit|return|sys)|\Z)', S)
    result = isis_lsdb_pattern2.sub('', result)
    
    # 方括号格式：[设备名]disp isis lsdb verbose
    isis_lsdb_pattern3 = compile(rf'\[{re.escape(neName)}\]\s*{dis}\s+isis\s+lsdb\s+verbose.*?\n.*?(?=\n\[{re.escape(neName)}\]\s*(?:dis|quit|return|sys)|\Z)', S)
    result = isis_lsdb_pattern3.sub('', result)
    
    # display ospf lsdb router
    ospf_router_pattern1 = compile(rf'\n\s*=+\s*{dis}\s+ospf\s+lsdb\s+router.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = ospf_router_pattern1.sub('', result)
    
    ospf_router_pattern2 = compile(rf'<{re.escape(neName)}[^>]*>\s*{dis}\s+ospf\s+lsdb\s+router.*?\n.*?(?=\n<{re.escape(neName)}[^>]*>\s*(?:dis|quit|return|sys)|\Z)', S)
    result = ospf_router_pattern2.sub('', result)
    
    # 方括号格式：[设备名]disp ospf lsdb router
    ospf_router_pattern3 = compile(rf'\[{re.escape(neName)}\]\s*{dis}\s+ospf\s+lsdb\s+router.*?\n.*?(?=\n\[{re.escape(neName)}\]\s*(?:dis|quit|return|sys)|\Z)', S)
    result = ospf_router_pattern3.sub('', result)
    
    # display ospf lsdb network
    ospf_network_pattern1 = compile(rf'\n\s*=+\s*{dis}\s+ospf\s+lsdb\s+network.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = ospf_network_pattern1.sub('', result)
    
    ospf_network_pattern2 = compile(rf'<{re.escape(neName)}[^>]*>\s*{dis}\s+ospf\s+lsdb\s+network.*?\n.*?(?=\n<{re.escape(neName)}[^>]*>\s*(?:dis|quit|return|sys)|\Z)', S)
    result = ospf_network_pattern2.sub('', result)
    
    # 方括号格式：[设备名]disp ospf lsdb network
    ospf_network_pattern3 = compile(rf'\[{re.escape(neName)}\]\s*{dis}\s+ospf\s+lsdb\s+network.*?\n.*?(?=\n\[{re.escape(neName)}\]\s*(?:dis|quit|return|sys)|\Z)', S)
    result = ospf_network_pattern3.sub('', result)
    
    # 清理连续的空行
    result = compile(r'\n{3,}').sub('\n\n', result)
    
    return result

# 从命令行输出中提取display current-configuration内容并生成配置文件
def extract_current_configuration(diagFile, diagData, neName):
    try:
        diagData = diagData.replace('\r\n', '\n').replace('\r', '\n')
        
        # 从全局缓存中查找 display current-configuration 边界
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        cfg_matches = find_command_boundaries(cli_boundaries, 'display current-configuration')
        
        # 过滤1：排除带管道过滤的命令
        cfg_matches = [
            (s, e, cmd) for s, e, cmd in cfg_matches
            if not re.search(r'current-configuration\s*\|', diagData[s:e], I)
        ]
        
        # 过滤2：只保留纯 display current-configuration 命令（无额外参数）
        # 排除 display current-configuration configuration xxx / interface 等
        cfg_matches = [
            (s, e, cmd) for s, e, cmd in cfg_matches
            if cmd.lower().strip() == 'display current-configuration'
        ]
        
        if not cfg_matches:
            # 打印实际边界命令（最多前3个），排查 display current-configuration 为何未匹配
            for b in cli_boundaries[:3]:
                print(f'  DEBUG BCmd [{neName}]: {repr(b[2][:80])}')
            return []
        
        results = []
        
        for idx, (cfg_start, cfg_end, cmd_text) in enumerate(cfg_matches):
            start_pos = cfg_end
            
            # 从缓存中查找下一个命令边界（二分查找 O(log n)）
            next_pos = find_next_boundary(cli_boundaries, cfg_end)
            if next_pos is not None:
                end_pos = next_pos
            else:
                # 最后一个命令，读合理上限
                end_pos = start_pos + min(1000000, len(diagData) - start_pos)
            
            cfg_content = strip_cli_separator(diagData[start_pos:end_pos])
            
            # 过滤掉太短的内容
            if len(cfg_content) > 1000:  # 配置至少1KB
                # 创建目录（先删除可能存在的同名文件）
                config_dir = path.join(path.dirname(diagFile), 'CONFIG')
                try:
                    if path.exists(config_dir):
                        if not path.isdir(config_dir):
                            remove(config_dir)  # 删除同名文件
                            makedirs(config_dir)
                    else:
                        makedirs(config_dir)
                except FileExistsError:
                    # 多线程竞争时，目录可能已被其他线程创建
                    pass
                
                # 生成最终写入内容，用于去重对比
                cmd_title = f'<{neName}>' + ' ' + 'display current-configuration'
                final_content = cmd_title + '\n' + remove_blank_lines(cfg_content)
                
                # 内容去重：与同目录已有的 CONFIG 文件逐个对比，完全一致则跳过
                skip_write = False
                try:
                    existing_files = [f for f in listdir(config_dir) if f.startswith(f'{neName}_cfg')]
                except Exception:
                    existing_files = []
                for existing_file in existing_files:
                    existing_path = path.join(config_dir, existing_file)
                    if not path.exists(existing_path):
                        continue
                    try:
                        # 读取已有文件的原始二进制，统一换行符后比对
                        with open(existing_path, 'rb') as ef:
                            existing_bytes = ef.read()
                        existing_normalized = existing_bytes.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                        new_normalized = final_content.encode('utf-8').replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                        if existing_normalized == new_normalized:
                            skip_write = True
                            break
                    except Exception:
                        continue
                if skip_write:
                    continue
                
                # 如果文件名已存在，添加序号
                cfg_file_name = f'{neName}_cfg.txt'
                cfg_file_path = path.join(config_dir, cfg_file_name)
                counter = 1
                while path.exists(cfg_file_path):
                    cfg_file_path = path.join(config_dir, f'{neName}_cfg_{counter}.txt')
                    counter += 1
                
                # 写入文件
                with open(cfg_file_path, 'w', encoding='utf-8') as f:
                    f.write(final_content)
                
                # 及时释放大字符串
                del cfg_content
                
                # 计算 zipPath
                zip_path = f'{neName}\\CONFIG\\{path.basename(cfg_file_path)}'
                
                cfg_info = {
                    'fileName': cfg_file_path,
                    'neName': neName,
                    'fileType': 'CONFIG',
                    'neVersion': '',
                    'startTime': '',
                    'endTime': '',
                    'neType': '',
                    'zipPath': zip_path
                }
                results.append(cfg_info)
        
        return results
        
    except Exception as e:
        print('extract_current_configuration error:', e)
        return []

# 从命令行输出中提取display logbuffer内容并生成日志文件
def extract_logbuffer(diagFile, diagData, neName):
    try:
        diagData = diagData.replace('\r\n', '\n').replace('\r', '\n')
        
        # 从全局缓存中查找 display logbuffer 边界
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        lb_matches = find_command_boundaries(cli_boundaries, 'display logbuffer')
        
        if not lb_matches:
            return []
        
        results = []
        
        for idx, (lb_start, lb_end, cmd_text) in enumerate(lb_matches):
            start_pos = lb_end
            
            # 从缓存中查找下一个命令边界（二分查找 O(log n)）
            next_pos = find_next_boundary(cli_boundaries, lb_end)
            if next_pos is not None:
                end_pos = next_pos
            else:
                end_pos = start_pos + min(1000000, len(diagData) - start_pos)
            
            log_content = strip_cli_separator(diagData[start_pos:end_pos])
            
            # 过滤掉太短的内容
            if len(log_content) > 500:
                # 创建目录（先删除可能存在的同名文件）
                log_dir = path.join(path.dirname(diagFile), 'LOG')
                try:
                    if path.exists(log_dir):
                        if not path.isdir(log_dir):
                            remove(log_dir)  # 删除同名文件
                            makedirs(log_dir)
                    else:
                        makedirs(log_dir)
                except FileExistsError:
                    # 多线程竞争时，目录可能已被其他线程创建
                    pass
                
                # 生成文件名
                log_file_name = f'{neName}_logbuffer.txt'
                log_file_path = path.join(log_dir, log_file_name)
                
                # 检查是否重复文件（前5行比较）
                if path.exists(log_file_path):
                    existing_preview = get_content_preview(log_file_path)
                    new_preview = remove_blank_lines(log_content)[:500]  # 取前500字符
                    if existing_preview and existing_preview[:200] == new_preview[:200]:
                        # 内容相同，跳过
                        del log_content
                        continue
                    # 内容不同，添加序号
                    counter = 1
                    while path.exists(log_file_path):
                        log_file_path = path.join(log_dir, f'{neName}_logbuffer_{counter}.txt')
                        counter += 1
                
                # 写入文件（去除空行，在最前面加上设备名）
                cmd_title = f'<{neName}>' + ' ' + 'display logbuffer'
                final_content = cmd_title + '\n' + remove_blank_lines(log_content)
                with open(log_file_path, 'w', encoding='utf-8') as f:
                    f.write(final_content)
                
                # 及时释放
                del log_content
                
                # 计算 zipPath
                zip_path = f'{neName}\\LOG\\{path.basename(log_file_path)}'
                
                log_info = {
                    'fileName': log_file_path,
                    'neName': neName,
                    'fileType': 'LOG',
                    'neVersion': '',
                    'startTime': '',
                    'endTime': '',
                    'neType': '',
                    'zipPath': zip_path
                }
                results.append(log_info)
        
        return results
        
    except Exception as e:
        print('extract_logbuffer error:', e)
        return []

# 从命令行输出中提取 display trapbuffer 内容并生成日志文件
def extract_trapbuffer(diagFile, diagData, neName):
    try:
        # 统一换行符格式，与 build_cli_boundaries 保持一致
        diagData = diagData.replace('\r\n', '\n').replace('\r', '\n')
        
        # 从全局缓存中查找 display trapbuffer 边界
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        trap_matches = find_command_boundaries(cli_boundaries, 'display trapbuffer')
        
        if not trap_matches:
            return []
        
        results = []
        all_content_parts = []
        
        for trap_start, trap_end, cmd_text in trap_matches:
            start_pos = trap_end
            
            # 从缓存中查找下一个命令边界
            next_pos = find_next_boundary(cli_boundaries, trap_end)
            if next_pos is not None:
                end_pos = next_pos
            else:
                end_pos = start_pos + min(500000, len(diagData) - start_pos)
            
            content = strip_cli_separator(diagData[start_pos:end_pos])
            
            # 过滤掉太短的内容
            if len(content) > 100:
                all_content_parts.append(content)
        
        if all_content_parts:
            # 创建目录
            log_dir = path.join(path.dirname(diagFile), 'LOG')
            try:
                if path.exists(log_dir):
                    if not path.isdir(log_dir):
                        remove(log_dir)
                        makedirs(log_dir)
                else:
                    makedirs(log_dir)
            except FileExistsError:
                # 多线程竞争时，目录可能已被其他线程创建
                pass
            
            # 生成文件名
            log_file_name = f'{neName}_trapbuffer.txt'
            log_file_path = path.join(log_dir, log_file_name)
            
            # 如果已存在，添加序号
            counter = 1
            while path.exists(log_file_path):
                log_file_path = path.join(log_dir, f'{neName}_trapbuffer_{counter}.txt')
                counter += 1
            
            # 写入文件（合并所有匹配内容）
            cmd_title = f'<{neName}>' + ' ' + 'display trapbuffer'
            final_content = cmd_title + '\n' + '\n'.join([remove_blank_lines(c) for c in all_content_parts])
            with open(log_file_path, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            # 计算 zipPath（相对于 zip 内的路径）
            zip_path = f'{neName}\\LOG\\{path.basename(log_file_path)}'
            
            log_info = {
                'fileName': log_file_path,
                'neName': neName,
                'fileType': 'LOG',
                'neVersion': '',
                'startTime': '',
                'endTime': '',
                'neType': '',
                'zipPath': zip_path
            }
            results.append(log_info)
        
        return results
        
    except Exception as e:
        print('extract_trapbuffer error:', e)
        return []

# 从命令行输出中提取 logfile 相关命令内容并生成日志文件
def extract_logfile(diagFile, diagData, neName):
    """从命令行输出中提取 logfile 相关命令内容并生成日志文件
    
    返回: (results列表, 修改后的diagData)
    """
    try:
        results = []
        diagData = diagData.replace('\r\n', '\n').replace('\r', '\n').replace('\x00\n', '\n').replace('\x00\r', '\n')
        
        # 从全局缓存中查找 logfile 相关命令
        # 命令已归一化：dis logfile → display logfile
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        logfile_matches = find_command_boundaries(cli_boundaries, 'display logfile')
        
        # 过滤：只保留包含 neName 的匹配（命令行提示符中有设备名，或附近日志行包含设备名）
        # 扩大前缀窗口到 200 字符，确保跨行 SSH 断开日志中的设备名也被检测到
        filtered_matches = []
        for start_pos, end_pos, cmd_text in logfile_matches:
            # 检查命令前 200 字符范围内是否有设备名提示符
            prefix_region = diagData[max(0, start_pos - 200):start_pos]
            if neName in prefix_region:
                filtered_matches.append((start_pos, end_pos, cmd_text))
        
        if not filtered_matches:
            return ([], diagData)
        
        log_parts = []
        diag_parts = []
        blocks_to_remove = []
        
        for start_pos, end_pos, cmd_text in filtered_matches:
            # 提取命令行
            cmd_line = cmd_text.strip()
            
            # 从缓存查找下一个边界（确保在当前命令之后）
            next_pos = find_next_boundary_after(cli_boundaries, end_pos, start_pos)
            
            # 检查 next_pos 是否指向真正的命令行（而不是日志内容）
            # 如果 next_pos 指向的内容不是以 <device> 或 [~device] 开头，则忽略它
            # 注意：[XXX-INF] 格式是华为设备日志的标识，不是命令行
            if next_pos is not None:
                # next_pos 可能指向行首的 \n 之后，需要找到该行的起始位置
                if diagData[next_pos] == '\n':
                    next_line_start = next_pos + 1
                else:
                    next_line_start = next_pos
                # 找到该行的结束位置
                next_line_end = diagData.find('\n', next_line_start)
                if next_line_end == -1:
                    next_line_end = len(diagData)
                next_line = diagData[next_line_start:next_line_end]
                
                # 判断是否为有效的命令行提示符：
                # 1. 以 <device> 开头（如 <RR-JKBTO-01H>）
                # 2. 以 [~device] 开头（如 [~NE8000]、[~SRG1-LE-02]）
                # 3. 不接受以 [XXX 开头且包含 -INF] 格式的日志行（如 [LOG-INF]、[DBG-INF]）
                stripped_line = next_line.strip()
                is_device_prompt = (stripped_line.startswith('<') and '>' in stripped_line[:50] and 
                                   stripped_line[1:2].isalpha())  # 设备名必须以字母开头
                # [~device] 格式的提示符，必须包含 ~ 符号
                is_sysview_prompt = (stripped_line.startswith('[') and '~' in stripped_line[:15])
                
                if not (is_device_prompt or is_sysview_prompt):
                    # next_pos 不是真正的命令行，使用文件末尾
                    next_pos = None
                # 只接受真正的命令行提示符格式（<device> 或 [~device]）
                if not (next_line.strip().startswith('<') and '>' in next_line[:50]) and \
                   not (next_line.strip().startswith('[') and '~' in next_line[:10]):
                    # next_pos 不是真正的命令行，使用文件末尾
                    next_pos = None
            
            if next_pos is not None:
                output_end_pos = next_pos
            else:
                output_end_pos = len(diagData)
            
            output_content = diagData[end_pos:output_end_pos].strip()
            full_content = cmd_line + '\n' + output_content
            blocks_to_remove.append((start_pos, output_end_pos))
            
            if len(full_content) > 10:
                # 从命令行提取文件名
                filename_from_cmd = None
                fn_match = compile(r'logfile\s+(\S+?)(?:\s*\||\s*$)', I).search(cmd_line)
                if fn_match:
                    filename_from_cmd = fn_match.group(1).strip().rstrip('|').strip()
                
                is_diag = False
                if filename_from_cmd and 'diag' in filename_from_cmd.lower():
                    is_diag = True
                elif not filename_from_cmd and ('diag.log' in output_content or compile(r'diag\S+\.zip').search(output_content)):
                    is_diag = True
                
                if is_diag:
                    diag_parts.append(full_content)
                else:
                    log_parts.append(full_content)
        
        # 从原始数据中删除已提取的块（倒序删除，保持位置正确）
        modified_data = diagData
        for start_pos, end_pos in reversed(blocks_to_remove):
            modified_data = modified_data[:start_pos] + modified_data[end_pos:]
        
        # 创建目录（LOG 和 DIAG 均写入 temp_dir/LOG/，由 copy2 按 fileType 路由到正确位置）
        log_dir = path.join(path.dirname(diagFile), 'LOG')
        try:
            if path.exists(log_dir):
                if not path.isdir(log_dir):
                    remove(log_dir)
                    makedirs(log_dir)
            else:
                makedirs(log_dir)
        except FileExistsError:
            pass
        
        # 写入 log 文件（LOG 类型 → 目标目录 LOG）
        if log_parts:
            log_file_name = f'{neName}_loglog.txt'
            log_file_path = path.join(log_dir, log_file_name)
            
            # 如果已存在，添加序号
            counter = 1
            while path.exists(log_file_path):
                log_file_path = path.join(log_dir, f'{neName}_loglog_{counter}.txt')
                counter += 1
            
            # 写入文件（每个匹配块之间用分隔符隔开），添加设备名前缀
            log_parts_with_prefix = []
            for p in log_parts:
                # 如果内容第一行已有 <neName> 前缀则保留
                lines = p.split('\n', 1)
                if lines and not lines[0].strip().startswith('<'):
                    log_parts_with_prefix.append('<' + neName + '> ' + lines[0] + ('\n' + lines[1] if len(lines) > 1 else ''))
                else:
                    log_parts_with_prefix.append(p)
            final_content = '\n\n=== SEPARATOR ===\n\n'.join(log_parts_with_prefix)
            with open(log_file_path, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            # 计算 zipPath（相对于 zip 内的路径）
            zip_path = f'{neName}\\LOG\\{path.basename(log_file_path)}'
            
            log_info = {
                'fileName': log_file_path,
                'neName': neName,
                'fileType': 'LOG',
                'neVersion': '',
                'startTime': '',
                'endTime': '',
                'neType': '',
                'zipPath': zip_path
            }
            results.append(log_info)
        
        # 写入 diag 文件 → 写入 DIAG/ 目录，fileType='DIAG'
        if diag_parts:
            diag_dir = path.join(path.dirname(diagFile), 'DIAG')
            try:
                if path.exists(diag_dir):
                    if not path.isdir(diag_dir):
                        remove(diag_dir)
                        makedirs(diag_dir)
                else:
                    makedirs(diag_dir)
            except FileExistsError:
                pass
            
            diag_file_name = f'{neName}_diaglog.txt'
            diag_file_path = path.join(diag_dir, diag_file_name)
            
            # 如果已存在，添加序号
            counter = 1
            while path.exists(diag_file_path):
                diag_file_path = path.join(log_dir, f'{neName}_diaglog_{counter}.txt')
                counter += 1
            
            # 写入文件，添加设备名前缀
            diag_parts_with_prefix = []
            for p in diag_parts:
                lines = p.split('\n', 1)
                if lines and not lines[0].strip().startswith('<'):
                    diag_parts_with_prefix.append('<' + neName + '> ' + lines[0] + ('\n' + lines[1] if len(lines) > 1 else ''))
                else:
                    diag_parts_with_prefix.append(p)
            final_content = '\n\n=== SEPARATOR ===\n\n'.join(diag_parts_with_prefix)
            with open(diag_file_path, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            # 计算 zipPath（目标是 DIAG 目录）（相对于 zip 内的路径）
            diag_zip_path = f'{neName}\\DIAG\\{path.basename(diag_file_path)}'  # 目标 DIAG 目录
            
            diag_info = {
                'fileName': diag_file_path,
                'neName': neName,
                'fileType': 'DIAG',
                'neVersion': '',
                'startTime': '',
                'endTime': '',
                'neType': '',
                'zipPath': zip_path
            }
            results.append(diag_info)
        
        return (results, modified_data)
        
    except Exception as e:
        print('extract_logfile error:', e)
        return ([], diagData)

# 从命令行输出中提取 more 相关命令内容并生成日志文件
def extract_more(diagFile, diagData, neName):
    """从命令行输出中提取 more 相关命令内容并生成日志文件
    
    返回: (results列表, 修改后的diagData)
    """
    try:
        results = []
        diagData = diagData.replace('\r\n', '\n').replace('\r', '\n').replace('\x00\n', '\n').replace('\x00\r', '\n')
        
        # 从全局缓存中查找 more 相关命令
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        # 精确匹配 more 命令：more filename（必须以 more 开头）
        # 不匹配 display more xxx、display diag | no-more 等含 more 但非 more 命令的情况
        more_matches = [
            b for b in cli_boundaries
            if compile(r'^(?:<[^>]+>|\[[^\]]+\])?\s*more\s+[\w\-.\:\\:]+$', I).search(b[2])
        ]
        
        if not more_matches:
            return ([], diagData)
        
        log_parts = []
        diag_parts = []
        blocks_to_remove = []
        
        for start_pos, end_pos, cmd_text in more_matches:
            cmd_line = cmd_text.strip()
            # 提取文件名：严格匹配 more xxx
            fn_match = re.match(r'^(?:<[^>]+>|\[[^\]]+\])?\s*more\s+([\w\-.\\:]+)$', cmd_line, re.I)
            if fn_match:
                filename_in_cmd = fn_match.group(1).strip()
            else:
                filename_in_cmd = ''
            
            # 查找下一个命令边界
            next_pos = find_next_boundary(cli_boundaries, end_pos)
            if next_pos is not None:
                output_end_pos = next_pos
            else:
                output_end_pos = end_pos + min(500000, len(diagData) - end_pos)
            
            content = diagData[end_pos:output_end_pos].strip()
            
            if len(content) > 100:
                cmd_title = f'<{neName}> ' + cmd_line
                full_content = cmd_title + '\n' + content
                blocks_to_remove.append((start_pos, output_end_pos))
                
                # 分类逻辑：文件名包含 diag → DIAG，否则 → LOG
                # 支持: log.log, diag.log, loglog_xx.log, diaglog_xx.log 等
                fn_lower = filename_in_cmd.lower()
                is_diag = 'diag' in fn_lower
                
                if is_diag:
                    diag_parts.append(full_content)
                else:
                    log_parts.append(full_content)
        
        # 从原始数据中删除已提取的块（倒序删除，保持位置正确）
        modified_data = diagData
        for start_pos, end_pos in reversed(blocks_to_remove):
            modified_data = modified_data[:start_pos] + modified_data[end_pos:]
        
        # 创建目录
        base_dir = path.join(path.dirname(diagFile))
        
        # 写入 log 文件到 LOG 目录
        if log_parts:
            log_dir = path.join(base_dir, 'LOG')
            try:
                if path.exists(log_dir):
                    if not path.isdir(log_dir):
                        remove(log_dir)
                        makedirs(log_dir)
                else:
                    makedirs(log_dir)
            except FileExistsError:
                pass
            
            log_file_name = f'{neName}_loglog.txt'
            log_file_path = path.join(log_dir, log_file_name)
            
            counter = 1
            while path.exists(log_file_path):
                log_file_path = path.join(log_dir, f'{neName}_loglog_{counter}.txt')
                counter += 1
            
            final_content = '\n\n=== SEPARATOR ===\n\n'.join(log_parts)
            with open(log_file_path, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            zip_path = f'{neName}\\LOG\\{path.basename(log_file_path)}'
            
            log_info = {
                'fileName': log_file_path,
                'neName': neName,
                'fileType': 'LOG',
                'neVersion': '',
                'startTime': '',
                'endTime': '',
                'neType': '',
                'zipPath': zip_path
            }
            results.append(log_info)
        
        # 写入 diag 文件到 DIAG 目录
        if diag_parts:
            diag_dir = path.join(base_dir, 'DIAG')
            try:
                if path.exists(diag_dir):
                    if not path.isdir(diag_dir):
                        remove(diag_dir)
                        makedirs(diag_dir)
                else:
                    makedirs(diag_dir)
            except FileExistsError:
                pass
            
            diag_file_name = f'{neName}_diaglog.txt'
            diag_file_path = path.join(diag_dir, diag_file_name)
            
            counter = 1
            while path.exists(diag_file_path):
                diag_file_path = path.join(diag_dir, f'{neName}_diaglog_{counter}.txt')
                counter += 1
            
            final_content = '\n\n=== SEPARATOR ===\n\n'.join(diag_parts)
            with open(diag_file_path, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            zip_path = f'{neName}\\DIAG\\{path.basename(diag_file_path)}'
            
            diag_info = {
                'fileName': diag_file_path,
                'neName': neName,
                'fileType': 'DIAG',
                'neVersion': '',
                'startTime': '',
                'endTime': '',
                'neType': '',
                'zipPath': zip_path
            }
            results.append(diag_info)
        
        return (results, modified_data)
        
    except Exception as e:
        print('extract_more error:', e)
        return ([], diagData)

# 从命令行输出中提取LSDB相关内容并生成文件
def extract_lsdb(diagFile, diagData, neName):
    try:
        # 从全局缓存中查找 LSDB 相关命令
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        # 严格匹配这三个 LSDB 命令行（支持 dis*/display 缩写，支持 | no / | no more 后缀）
        lsdb_exact_commands = ['display isis lsdb verbose', 'display ospf lsdb router', 'display ospf lsdb network']
        lsdb_matches = []
        for exact_cmd in lsdb_exact_commands:
            # 匹配命令：精确匹配 + 可选的后缀 | no 或 | no more
            for start_pos, end_pos, cmd_text in cli_boundaries:
                cmd_lower = cmd_text.lower().strip()
                # 检查命令是否以目标命令开头，后面可跟 | no 或 | no more
                if (cmd_lower == exact_cmd or 
                    cmd_lower.startswith(exact_cmd + ' | no') or
                    cmd_lower.startswith(exact_cmd + ' | no more')):
                    lsdb_matches.append((start_pos, end_pos, cmd_text))
        
        if not lsdb_matches:
            return []
        
        results = []
        combined_content = []
        matched_commands = []
        
        for lsdb_start, lsdb_end, cmd_text in lsdb_matches:
            start_pos = lsdb_end
            next_pos = find_next_boundary(cli_boundaries, lsdb_end)
            if next_pos is not None:
                end_pos = next_pos
            else:
                end_pos = start_pos + min(500000, len(diagData) - start_pos)
            
            lsdb_content = diagData[start_pos:end_pos].strip()
            if len(lsdb_content) > 500:
                combined_content.append(lsdb_content)
                combined_content.append('')
                matched_commands.append(cmd_text)
        
        if not combined_content:
            return []
        
        # 创建LSDB目录（先删除可能存在的同名文件）
        lsdb_dir = path.join(path.dirname(diagFile), 'LSDB')
        try:
            if path.exists(lsdb_dir) and not path.isdir(lsdb_dir):
                remove(lsdb_dir)  # 删除文件
            if not path.exists(lsdb_dir):
                makedirs(lsdb_dir)
        except FileExistsError:
            # 多线程竞争时，目录可能已被其他线程创建
            pass
        
        # 生成文件名
        lsdb_file_name = f'{neName}_lsdb.txt'
        lsdb_file_path = path.join(lsdb_dir, lsdb_file_name)
        
        # 如果已存在，添加序号
        counter = 1
        while path.exists(lsdb_file_path):
            lsdb_file_path = path.join(lsdb_dir, f'{neName}_lsdb_{counter}.txt')
            counter += 1
        
        # 写入文件（去除空行，在最前面加上设备名和实际匹配的命令）
        # 生成命令标题（使用实际匹配到的命令，用空格分隔）
        cmd_title = f'<{neName}>' + ' ' + ' '.join(matched_commands)
        final_content = cmd_title + '\n' + remove_blank_lines('\n'.join(combined_content))
        with open(lsdb_file_path, 'w', encoding='utf-8') as f:
            f.write(final_content)
        
        # 及时释放
        del combined_content
        
        # 计算 zipPath
        zip_path = f'{neName}\\LSDB\\{path.basename(lsdb_file_path)}'
        
        lsdb_info = {
            'fileName': lsdb_file_path,
            'neName': neName,
            'fileType': 'LSDB',
            'neVersion': '',
            'startTime': '',
            'endTime': '',
            'neType': '',
            'zipPath': zip_path
        }
        
        results.append(lsdb_info)
        return results
        
    except Exception as e:
        print('extract_lsdb error:', e)
        return []
