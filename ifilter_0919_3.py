
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

def get_diag_info(diagFile):
    if not path.isfile(diagFile):
        return False
    name, ext = get_file_prefix(diagFile)
    temp = diagFile.lower()
    if not temp.endswith('.log') and not temp.endswith('.txt') and not temp.endswith('.cfg') and ext!='':
        return False
    # uFAD datacollector工具采集的信息，剔除此文件
    if 'All Collect Results.txt' in diagFile:
        return False
    # 如下是MOP文件的特征
    if (type=='RFC') and (('mop.txt' in temp) or temp.startswith('mop') or temp.startswith('rollback') or ('rollback.txt' in temp)):
        return False

    diagData = ''
    diagInfo = {}
    toolFlag = False
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
    # Macintosh 字符格式处理
    if '\r\r\n' in diagData[:2000]:
        diagData = diagData.replace('\r\r\n', '\r\n')
        with open(diagFile, 'wb+') as f:
            print('Warning: the format of the file is 0D0D0A')
            f.write(diagData.encode())
    #regExp = search(r'<(.+)>\s*dis\S+\s*diag', diagData, I)
    # filter_str用于标识终端追加的特殊字符，如：13:51:16:227 ||<E8000X16-QUI01-ISP-FW-01>display cpu-usa
    filter_str = ''
    regExp = search(r'(?:\n|^)([\w\d :\.§$\-_\[\]]{0,40}?)(?:HRP_M|HRP_S|HRP_A)?<(.+)>\s*dis\S*\s*diag\S*\s*(?:\n|$)', diagData, I)
    if regExp:
        filter_str = regExp.group(1)
        neName = regExp.group(2)
        fileType = 'CMD'  # dis diag 命令输出归入 CMD
    else:
        # 先用 build_cli_boundaries 检测文件是否包含 CLI 命令分隔符格式
        # 如果包含，说明是标准的诊断日志或命令行文件，应该归类为 CMD
        try:
            cli_boundaries = build_cli_boundaries(diagData)
            if cli_boundaries and len(cli_boundaries) >= 10:
                fileType = 'CMD'
        except:
            pass
        
        # 如果 CLI 边界检测未命中，再用传统方式检测
        if fileType == '':
            regExp = search(r'=============display|\n                 \s*display version', diagData)
            if regExp:
                fileType = 'DIAG'
    
    # ---- LOG 检测：dis logfile / display logfile 命令（RR-JKBTO-01H.log 格式）----
    #   <device>dis logfile log.log | no
    #   <device>dis logfile diag.log | no
    # 这类文件虽然包含 dis/disp 命令，但本质是日志文件，不应归为普通 CMD
    if fileType == '':
        logfile_regExp = search(r'<[^>]+>\s*(?:dis(?:p(?:l(?:a)?)?)?|display)\s+logfile\s+\S+', diagData, I)
        if logfile_regExp:
            fileType = 'LOG'
    
    # 先从配置中获取网元名称，sysname JED1-PE-01
    # 如果获取不到则从命令行用户视图中提取：<JED1-PE-01>dis
    # 如果获取不到则从命令行系统视图中提取：[~JED1-PE-01]dis, 注意，系统视图的中可能包含子视图，可能不准确
    # 如果文件名包含设备名（如 lsdb_AG-BDCKJ-01H.txt），则从文件名中提取

    if neName == '':
        # 尝试从文件名中提取设备名（处理 lsdb_XXX.txt 等格式）
        file_name = path.split(diagFile)[1]
        name_match = search(r'(?:^|_)([A-Z][A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+)(?:_|\.)', file_name, I)
        if name_match:
            neName = name_match.group(1)
        
        if neName == '' and fileType == '':
            fileType = 'CMD'
        
        # 新增：如果文件使用等号分隔符格式，从 display current-configuration 回显中提取 sysname
        # 这种格式没有 <设备名> 提示符，设备名在配置内容中
        if fileType == 'DIAG' and neName == '':
            # 匹配等号分隔符格式的 display current-configuration
            cfg_pattern = compile(r'=+\s*display\s+current-configuration\s*=+\s*(?:---[\s\S]+?---)?', I)
            cfg_match = cfg_pattern.search(diagData)
            if cfg_match:
                # 从匹配位置后开始搜索 sysname（最多搜索50万字符）
                cfg_content = diagData[cfg_match.end():cfg_match.end() + 500000]
                # 匹配 sysname，支持 Unix (\n) 和 Windows (\r\n) 换行符
                regExp = search(r'^\s*sysname\s+(\S+)', cfg_content, M)
                if regExp:
                    neName = regExp.group(1)
                    print(f'Extracted neName "{neName}" from display current-configuration in {path.split(diagFile)[1]}')
        
        #regExp = search(r'(?:\n|^)([\d :\-_]{0,30}?) *sysname\s*(.+)\r', diagData, M)
        # 修改：支持 Unix (\n) 和 Windows (\r\n) 换行符
        regExp = search(r'(?:\n|^)([\w\d :\.§$\-_\[\]]{0,40}? *)sysname\s(\S.+\S)\r?\n?', diagData, M)
        if regExp:
            filter_str = regExp.group(1)
            neName = regExp.group(2)
            #如果是巡检采集的信息，文件名和第一行都是采集命令行
            first_line = diagData.splitlines()[0]
            if (first_line in diagFile or first_line=='display current-configuration' or first_line=='display saved-configuration') and first_line.strip():
                toolFlag = True
                with open(diagFile, 'wb+') as f:
                    temp_data = '<'+neName+'>'+first_line+diagData[len(first_line):]
                    f.write(temp_data.encode())
            # 规避产品命令行中存在display current-configuration存在简写的问题，下个补丁解决后删除--- 20210205
            # temp_data = sub('\s*dis\S*\s+cu\S*', 'display current-configuration', first_line)
            # if temp_data != first_line:
            #     with open(diagFile, 'wb+') as f:
            #         temp_data = temp_data+diagData[len(first_line):]
            #         f.write(temp_data.encode())
        else:
            #regExp = search(r'^<(\S+)>\s*(dis|more|dir)', diagData, M)
            regExp = findall(r'(?:\n|^)([\w\d :\.§$\-_\[\]]{0,40}?)(?:HRP_M|HRP_S|HRP_A)?<(\S+)>\s*(?:dis|more|dir|sys|telnet)', diagData, S|I)
            if regExp:
                #neName = regExp.group(1)
                filter_str = regExp[-1][0]
                # 获取出现频次最大的作为网元名称
                temp_list = [x[1] for x in regExp]
                neName = Counter(temp_list).most_common(1)[0][0]
            else:
                regExp = search(r'(?:\n|^)([\w\d :\.§$\-_\[\]]{0,40}?)(?:HRP_M|HRP_S|HRP_A)?\[~?(\S+?)(?:-aaa|-diagnose|-interface|-ui-vty0)?\]\s*dis', diagData, M|I)
                if regExp:
                    filter_str = regExp.group(1)
                    neName = regExp.group(2)
                else:
                    #如果是巡检采集的信息，文件名和第一行都是采集命令行
                    first_line = diagData.splitlines()[0]
                    #first_line = first_line.split(">")[-1].strip()
                    base_name = path.basename(get_file_prefix(diagFile)[0])
                    #如果文件的第一行包含文件名，或者文件名包含文件的第一行，则认为是工具自动采集的数据
                    #工具采集文件：baq-lvi-s9303-01.interface
                    first_line_string = ''.join(first_line.split(' '))
                    file_name_tag = base_name.split('.')[-1]
                    if (first_line in diagFile
                        or base_name in first_line
                        or ('.version' in diagFile and 'display version' in first_line)
                        or file_name_tag in first_line_string) and first_line.strip():
                        toolFlag = True
                        regExp = search(r'^<((?!ProcId|FEI)\S+)>', diagData, M)
                        if regExp:
                            neName = regExp.group(1)
                            with open(diagFile, 'w+') as f:
                                temp_data = '<'+neName+'>'+first_line+diagData[len(first_line):]
                                f.write(temp_data)
                        else:
                            neName = 'Unknown'
                    else:
                        print('Error:No Display command was found in CMD file:%s' % path.split(diagFile)[1])
                        return False
    filter_str = filter_str.replace('HRP_S', '')
    filter_str = filter_str.replace('HRP_M', '')
    filter_str = filter_str.replace('HRP_A', '')
    filter_str = filter_str.replace('HRP_B', '')
    # 暂时先不过滤特殊字符了，前面追加的字符中可能包含特殊字符
    #filter_str = ''.join(filter(lambda x: ord(x)>=ord(' ') and ord(x)<=ord('~'), filter_str)) #过滤掉特殊字符
    if filter_str and filter_str.strip()!='':
        if 'more' in filter_str.lower():
            diagData = diagData.replace(filter_str, '')
        else:
            print('【filter string】[file]%s[str]%s[ASCII]%s' % (path.split(diagFile)[1], filter_str, '-'.join(list([str(ord(x)) for x in filter_str]))))
            diagData = sub(r'(?:\n|^).{%s}' % len(filter_str), '\n', diagData)
        with open(diagFile, 'wb+') as f:
            f.write(diagData.encode())

    # 优先级最高：从 'HUAWEI xxx uptime is' 行提取 neType
    # 示例：
    #   HUAWEI CE6863-48S6CQ uptime is 9 days, 5 hours, 49 minutes
    #   HUAWEI NetEngine 8000 M14 uptime is 946 days, 14 hours, 23 minutes
    #   HUAWEI MultiserviceEngine 60-X16 uptime is 4533 days, 3 hours, 43 minutes
    huawei_uptime_pattern = compile(r'\bHUAWEI\s+(.*?)\s+Uptime\s+is\b', I)
    huawei_uptime_match = search(huawei_uptime_pattern, diagData)
    if huawei_uptime_match:
        neType = huawei_uptime_match.group(1).strip()

    # 版本提取逻辑始终执行（不依赖 neType 是否为空）
    pattern = compile(r'(?<!")dis\S+\s+ver.{0,400}?oftware.{0,20}?ersion.{0,50}?\s*\(?((?:ATN|NetEngine|AP\S+)?\s*\S+)\s*(V\d{3}R\d{3}C\d{2}\w*)\)?\.?', S|I)
    regExp = search(pattern, diagData)
    if regExp:
        neVersion = regExp.group(2).strip(' )')
        # 仅在旧逻辑未获取到 neType 时才用旧正则的结果覆盖
        if not neType:
            old_neType = regExp.group(1)
            neType = old_neType.strip('(').strip()
    else:
        # 尝试 vrbd 模式提取版本+型号
        pattern = compile(r'(?<!")vrbd.{0,400}?\n(.{0,20}?) Version (V\d{3}R\d{3}C\d{2}\w*).', S|I)
        regExp = search(pattern, diagData)
        if regExp:
            neVersion = regExp.group(2)
            if not neType:
                neType = regExp.group(1).strip()
        else:
            # 尝试从配置注释中提取版本
            regExp = search(r'^!Software Version (V\d{3}R\d{3}C\d{2}\w*)', diagData, M)
            if regExp:
                neVersion = regExp.group(1)

    # 兜底：如果还是没有 neType，尝试从 display device 中提取
    if not neType:
        pattern = compile(r'(?<!")display device.{0,400}?\n(.{0,25}?)\'s Device status', S|I)
        regExp = search(pattern, diagData)
        if regExp:
            neType = regExp.group(1)
            neType = neType.strip()

    pattern = compile(r'display clock.+?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', S)
    regExp = search(pattern, diagData)
    if regExp:
        startTime = regExp.group(1)
    # 规避产品命令行中存在双引号的问题，下个补丁解决后删除--- 20210129
    # if fileType == 'DIAG' and neType.startswith("CE"):
    #     print('>>>>>>>>>>>>>>>>>>>>>>>>>')
    #     diagData = diagData.replace('display forward information slot', 'werdsadfwr display forward information slot')
    #     with open(diagFile, 'wb+') as f:
    #         f.write(bytes(diagData.encode('UTF-8')))
    # 规避防火墙IPSEC debug信息--- 20210428
    if neType.startswith("Eud") and 'IKE/7/IKE_DEBUG' in diagData:
        print('>>>>>>>>>>>>>>>>>>>>>>>>>')
        ike_debug = []
        ike_debug.append('<'+ neName + '>display ikev2 filter remote-address x.x.x.x')
        for line in diagData.splitlines():
            if 'IKE/7/IKE_DEBUG' in line:
                ike_debug.append(line)
        diagData = diagData + '\n' + '\n'.join(ike_debug)
        with open(diagFile, 'wb+') as f:
             f.write(bytes(diagData.encode('UTF-8')))
    # neType 保留原始空格（如 "NetEngine 8000 M8"），不再执行 replace(' ', '')
    
    # 新增：从命令行文件中提取CONFIG、LOG、LSDB相关内容
    # 只有在能够识别到网元名称时才进行提取
    # 返回包含主文件和生成文件的列表
    dataList = []
    if neName and neName not in ['Unknown', 'UNKNOWN', '']:
        try:
            # 全局 CLI 边界缓存预计算（一次性扫描，各 extract 函数查表使用）
            cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
            
            # 提取display current-configuration内容生成CONFIG文件
            cfg_results = extract_current_configuration(diagFile, diagData, neName)
            if cfg_results:
                print(f'Extracted {len(cfg_results)} CONFIG files from {path.split(diagFile)[1]}')
                dataList.extend(cfg_results)
            
            # 提取display logbuffer内容生成LOG文件
            log_results = extract_logbuffer(diagFile, diagData, neName)
            if log_results:
                print(f'Extracted {len(log_results)} LOG files from {path.split(diagFile)[1]}')
                dataList.extend(log_results)
            
            # 提取display trapbuffer内容生成LOG文件
            trap_results = extract_trapbuffer(diagFile, diagData, neName)
            if trap_results:
                print(f'Extracted {len(trap_results)} trapbuffer files from {path.split(diagFile)[1]}')
                dataList.extend(trap_results)
            
            # 提取logfile相关内容生成LOG文件（会从原始内容中删除已提取的块）
            logfile_results, diagData = extract_logfile(diagFile, diagData, neName)
            if logfile_results:
                print(f'Extracted {len(logfile_results)} logfile files from {path.split(diagFile)[1]}')
                dataList.extend(logfile_results)
            
            # 提取more相关内容生成LOG/DBLG文件（会从原始内容中删除已提取的块）
            more_results, diagData = extract_more(diagFile, diagData, neName)
            if more_results:
                print(f'Extracted {len(more_results)} more files from {path.split(diagFile)[1]}')
                dataList.extend(more_results)
            
            # 提取LSDB相关内容生成LSDB文件
            lsdb_results = extract_lsdb(diagFile, diagData, neName)
            if lsdb_results:
                print(f'Extracted {len(lsdb_results)} LSDB files from {path.split(diagFile)[1]}')
                dataList.extend(lsdb_results)
            
            # 从原始数据中删除CONFIG和LOG回显，然后更新文件
            modified_data = remove_cfg_log_from_original(diagData, neName)
            if modified_data != diagData:
                # 如果修改后只剩空白内容，说明整个文件内容都被提取了，直接删除
                if not modified_data.strip():
                    try:
                        remove(diagFile)
                        print(f'Removed empty file: {path.split(diagFile)[1]}')
                    except:
                        pass
                else:
                    with open(diagFile, 'wb+') as f:
                        f.write(modified_data.encode('utf-8'))
            
            # 如果文件名以 lsdb_ 开头，说明这个文件专门用于 LSDB 提取，提取后可以删除
            file_basename = path.split(diagFile)[1]
            if file_basename.startswith('lsdb_'):
                try:
                    remove(diagFile)
                except:
                    pass
        except Exception as e:
            print('Error extracting CONFIG/LOG/LSDB from {}: {}'.format(path.split(diagFile)[1], str(e)))
    
    # 添加主diag文件信息
    diagInfo.update(fileName=diagFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
    dataList.insert(0, diagInfo)
    
    # 清理当前文件的 CLI 边界缓存，防止内存泄漏
    clear_cli_boundary_cache(diagFile)
    
    return dataList

def get_mop_info(fileName):
    if type != 'RFC':
        return False
    if not path.isfile(fileName):
        return False
    name, ext = get_file_prefix(fileName)
    temp = path.split(fileName)[1].lower()
    txt_ret = get_mop_txt(fileName)

    if not ('mop.txt' in temp) and not ('mop.csv' in temp) \
       and not temp.startswith('mop') and not temp.startswith('rollback') \
       and not ('rollback.txt' in temp) and not ('rollback.csv' in temp) and not temp.endswith('.xlsx') \
       and not txt_ret:
        return False
    mopData = ''
    diagInfo = {}
    dataList = []
    neName, fileType, startTime, neVersion, endTime, neType = 'MOP', 'MOP', '', '', '', ''
    #如果是.xlsx文件，则可能是操作脚本文件，需要从中提取脚本信息并转换成txt文件，后续流程会对该文件进行分析，生成csv文件
    if temp.endswith('.xlsx'):
        script, rollback = get_mop_xls(fileName)
        #print('script length:', len(script), len(rollback))
        if len(script) > 0:
            script_file = name+'_mop.txt'
            with open(script_file, 'wb+') as f:
                f.write(script.encode())
            diagInfo.update(fileName=script_file, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
            dataList.append(diagInfo.copy())
        if len(rollback) > 0:
            rollback_file = name+'_rollback.txt'
            with open(rollback_file, 'wb+') as f:
                f.write(rollback.encode())
            diagInfo.update(fileName=rollback_file, neName=neName, fileType='Rollback', neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
            dataList.append(diagInfo.copy())
        if dataList:
            return dataList
        else:
            return False
    if temp.endswith('rollback.txt') or temp.endswith('rollback.csv') or temp.startswith('rollback'):
        fileType = 'Rollback'
    elif 'mop.txt' in temp or 'mop.csv' in temp or temp.startswith('mop'):
        fileType = 'MOP'
    elif txt_ret:
        fileType = txt_ret
    
    diagInfo.update(fileName=fileName, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
    return diagInfo

# 根据文件内容判断txt文件是否为操作脚本或回退脚本：
# 如果不是则返回空字符串
# 如果是操作脚本，则返回'Script'
# 如果是回退脚本，则返回'Rollback'
# 如果文件中既包含操作脚本，也包含回退脚本，则返回'MOP'
def get_mop_txt(fileName):
    if not fileName.endswith('.txt'):
        return False
    data = ''
    display_flag = False
    script_flag = False
    rollback_flag = False
    f_name = path.split(fileName)[1].lower()
    if 'rollback point' in f_name:
        script_flag = True
        return 'MOP'
    if 'rollback' in f_name or 'roll back' in f_name or 'changeback' in f_name:
        #如果文件名中存在rollback关键字，则为回退脚本
        rollback_flag = True
        return 'Rollback'
    elif 'script' in f_name or 'operation' in f_name:
        #否则如果包含script关键字，则为操作脚本
        script_flag = True
        return 'MOP'

    with open(fileName, 'rb') as f:
        data = f.read()
        tempData = detect(data[:5000])
        #print(temp)
        if tempData['encoding'] is not None:
            data = data.decode(tempData['encoding'], errors='ignore')
        else:
            data = data.decode('UTF-8', errors='ignore')
        #data = f.read()[:5000].lower()
    if '>dis' in data or ']dis' in data or 'display ' in data:
        display_flag = True
    if 'rollback' in data or 'roll back' in data or 'changeback' in data and 'rollback point' not in data:
        rollback_flag = True
        data = data.replace('back script', '')
    if ' script' in data:
        script_flag = True

    if not display_flag:
        # 在变更场景，如果存在不包含display命令行的txt文件，只能是脚本，后续如果有误识别，再单独处理
        if script_flag and rollback_flag:
            return 'Rollback'
        elif rollback_flag:
            return 'Rollback'
        elif script_flag:
            return 'MOP'
        else:
            return ''
    else:
        # 如果包含display命令行，可能是采集的设备信息，存在误识别的可能，后续针对具体情况再处理
        if script_flag and rollback_flag:
            return 'Rollback'
        elif rollback_flag:
            return 'Rollback'
        elif script_flag:
            return 'MOP'
        else:
            return ''
def get_device_name_column(columns):

    r = list(filter(lambda x:re.search("^Device *Name$", x.strip(),re.IGNORECASE), columns))

    if len(r) == 1:
        return r[0]

def get_device_df1(df:pd.DataFrame):
    out = {}

    trans_table = str.maketrans("", "", "\",!@[]*/|:<>?")

    for c,series in df.items():
        nename = ''
        tmp = []
        for i in series:
            t = re.search(r"^((?!\*|\?|\[|\]|<|>|\\|/|\"|,|!|@)[ \S]+)\((?:\d+\.\d+\.\d+\.\d+|[\da-fA-F:]+)\)$",str(i).strip())
            if t:
                if tmp and nename:
                    out[nename] = pd.DataFrame(pd.Series(tmp),columns=[nename])

                nename = t.groups()[0]

                tmp= []
                continue

            tmp.append(i)

        if tmp and nename:
            out[nename] = pd.DataFrame(pd.Series(tmp), columns=[nename])

    return out



def get_device_df(item, max_columns = 100):

    device_name_column = get_device_name_column(list(item.columns[:max_columns]))

    if device_name_column is not None:
        item[device_name_column].fillna(method="ffill", inplace=True)
        dfg = item.groupby(device_name_column)
        return dict(list(dfg))
    tmp = get_device_df1(pd.concat([pd.DataFrame([item.columns], columns=item.columns), item]).iloc[:,:100])
    if tmp:
        return tmp
    return {"":item}


def get_mop_xls(fileName):
    #print('Processing xls MOP file:')
    script_list = []
    rollback_list = []
    isTables = fileName.endswith('tables.xlsx')
    if isTables:
        sheettitle={}
        df = pd.read_excel(fileName, sheet_name=None)
        for sheet, item in list(df.items()):
            sheettitle[sheet] = item.columns[0]

        df = pd.read_excel(fileName, sheet_name=None,skiprows=1)
    else:
        # 读取100列，后面判断，如果超过100列，则说明脚本有问题
        df = pd.read_excel(fileName, sheet_name=None, usecols=[x for x in range(100)])
    #print(len(df), fileName)
    for sheet, item in list(df.items()):
        #df_script = df[item].copy()
        #df[item].fillna('', inplace=True)
        #如果sheet页命名中包含rollback，则认为是回退脚本
        #print('#####Sheet:', sheet, sheet.lower())
        if len(item.columns)>=100:
            print('Warning: The column count in file [%s] sheet [%s] exceeds 100!' % (path.split(fileName)[-1], sheet))
        tabletitle = ''
        if isTables:
            tabletitle = sheet.lower() + " " + sheettitle[sheet].lower()
        else:
            tabletitle = sheet.lower()
        #如果sheet页名字中包含如下字段，则认为是回退脚本
        if any([x in tabletitle for x in ['rollback','roll back','changeback','change back', 'recover']]):
            rollback_list_temp = []

            device_dfs = get_device_df(item, 100)
            for i, device_df in device_dfs.items():
                if i:
                    rollback_list_temp += ["####nename: " + i.strip()]
                    script_list_temp += ["####nename: " + i.strip()]

                for index in device_df.columns[:100]:
                    temp = list(device_df[index].dropna())

                    temp = [i for i in temp if isinstance(i, str)]

                    if len(temp)>0:
                        #一个单元格中可能有多条命令，需要进行拆分
                        temp = reduce(lambda x,y:x+y, [i.splitlines() for i in temp])
                    # 如果脚本中存在如下命令rollback configuration to label，说明是回退脚本
                    temp = [str(index)] + temp
                    if is_script(temp) or 'rollback configuration to label' in ' '.join(temp).lower():
                        rollback_list_temp = rollback_list_temp + ["####column_title: " + str(index)] + temp
            if rollback_list_temp:
                rollback_list = rollback_list + ["####sheet_title: " + str(sheet)] + rollback_list_temp
        else:
            script_list_temp = []
            rollback_list_temp = []
            device_dfs = get_device_df(item, 100)
            for i, device_df in device_dfs.items():
                if i:
                    rollback_list_temp += ["####nename: " + i.strip()]
                    script_list_temp += ["####nename: " + i.strip()]
                for index in device_df.columns[:100]:
                    temp = list(device_df[index].dropna())
                    temp = [i for i in temp if isinstance(i, str)]
                    if len(temp)>0:
                        temp = reduce(lambda x,y:x+y, [i.splitlines() for i in temp])
                    temp = [str(index)] + temp
                    if is_script(temp):
                        s = index.lower() + "\n"+ '\n'.join(temp).lower().replace('rollback label', '')
                        # 如果有设置回退标签，说明是操作脚本
                        reg = search(r'commit\s+label\s*\S+', s)
                        if any([x in s for x in ['rollback','roll back','changeback','change back','recover']]) and reg is None:
                            rollback_list_temp = rollback_list_temp + ["####column_title: " + str(index)] + temp
                        else:
                            script_list_temp = script_list_temp + ["####column_title: " + str(index)] + temp


            if script_list_temp:
                script_list = script_list + ["####sheet_title: " + str(sheet)] + script_list_temp
            if rollback_list_temp:
                rollback_list = rollback_list + ["####sheet_title: " + str(sheet)] + rollback_list_temp

    #df_script = map(lambda x:str(x), df_script)
    script_list = [i for i in script_list if i!='' and isinstance(i, str)]
    script = '\n'.join(script_list)
    rollback_list = [i for i in rollback_list if i!='' and isinstance(i, str)]
    rollback = '\n'.join(rollback_list)
    return script, rollback
def pre_check_kpi_dir(file_list):
    for root, dirs, files in file_list:
        root_path = root
        if not root.endswith(path.sep):
            root_path = root + path.sep
        for file in files:
            pre_check_kpi(file, root_path)


def pre_check_kpi(file, root_path):
    if file.endswith('.txt') or file.endswith('.log') or not "." in file:
        is_text, encoding = is_text_file(root_path + file)
        if is_text:
            content = read_file_with_encoding(root_path + file, encoding)
            is_more_file = search(r"(<\S+>)(?:more|display logfile)\s+\S+\s+hex", content, I|M)
            if is_more_file:
                lines = content.splitlines()
                position = is_more_file.group(0).find(is_more_file.group(1))
                content = '\n'.join([line[position:] for line in lines])
                version_type = search(r"Version( ?: ?|.*?\()(.*?)(\(.*\) ?)?(V\d{3,3}R\S+?)(\)| )", content)
                version_type_str = ""
                if version_type:
                    version_type_str = is_more_file.group(1) + version_type.group(0) + "\n"
                # 重新写入文件
                try:
                    with open(root_path + "kpi_" + (file[:file.rfind('.')] if '.' in file else file) + ".txt", 'w') as more_file:
                        more_file.write(version_type_str + content)
                    # 删除文件
                    remove(root_path + file)
                except Exception as e:
                    return

def read_file_with_encoding(file_path, encoding):
    """
    使用指定编码读取文件内容
    """
    try:
        with open(file_path, 'r', encoding=encoding) as f:
            return f.read()
    except Exception as e:
        return ""


def is_text_file(file_path):
    """
    判断文件是否为文本文件，并返回检测到的编码
    """
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read(1024)  # 读取前1024字节用于检测
            result = detect(raw_data)
            encoding = result['encoding']
            confidence = result['confidence']
            if encoding and confidence > 0.5:
                return True, encoding
            else:
                return False, None
    except Exception as e:
        print(f"读取文件时出错: {e}")
        return False, None

kpiFileCount = 0
MopFileCount = 0
logFileCount = 0
diagFileCount = 0
cmdFileCount = 0
cfgFileCount = 0
# ipNiceFileCount = 0
unknownFileCount = 0
lsdbFileCount = 0  # 新增LSDB文件计数器



def compress(file_name):
    fileInfo = []
    if path.isfile(file_name):
        if '.' in file_name:
            file_name = get_file_prefix(file_name)[0]

    if not path.isdir(file_name):
        print("Path is invalid:" + file_name)
        raise Exception("Path is invalid, suspect failed to download data from EDM:", file_name)
        return False
    
    # 创建 formated_result 文件夹而不是 ZIP 文件
    output_folder = file_name + "_result"
    if not path.exists(output_folder):
        makedirs(output_folder)
        
    i = 0
    global kpiFileCount
    global MopFileCount
    global logFileCount
    global diagFileCount
    global cmdFileCount
    global cfgFileCount
    global unknownFileCount
    global lsdbFileCount
    neList = {}
    timeList = {}
    thread_list = []
    excelpath_list = []
    for root, dirs, files in walk(file_name):
        for file in files:
            tempFile = get_coding(file)
            tempRoot = get_coding(root)
            if file.endswith('.zip') or file.endswith('.ZIP') \
                    or file.endswith('.nic') or file.endswith('.NIC') or file.endswith('.docx') or file.endswith('.png') \
                    or file.endswith('.json'):
                continue

            fileName = path.join(tempRoot, tempFile)

            if file.endswith('.xlsx'):
                excelpath_list.append(fileName)

            if path.isdir(fileName):
                continue
            i += 1
            myThread = MyThread(get_file_info, args=(fileName,))
            myThread.start()
            thread_list.append(myThread)
    data_list = []
    for thread in thread_list:
        thread.join()
        data_list.append(thread.get_result())
    for dataInfo in data_list:
        if isinstance(dataInfo, str):
            if 'UnknownFiles' not in list(neList.keys()):
                neList['UnknownFiles'] = []
            neList['UnknownFiles'].append(dataInfo)
        elif isinstance(dataInfo, list):
            for data_item in dataInfo:
                if data_item['neName'] not in list(neList.keys()):
                    neList[data_item['neName']] = []
                neList[data_item['neName']].append(data_item)
                fileInfo.append(data_item)
                if data_item['fileType'] == 'CMD':
                    format(data_item['fileName'])
        elif isinstance(dataInfo, dict):
            if dataInfo['neName'] not in list(neList.keys()):
                neList[dataInfo['neName']] = []
            neList[dataInfo['neName']].append(dataInfo)
            fileInfo.append(dataInfo)
            if dataInfo['fileType'] == 'CMD':
                format(dataInfo['fileName'])
            try:
                if dataInfo['neName'] != 'UNKNOWN' and dataInfo['fileType'] != 'CMD' and dataInfo['fileType'] != 'INFO'\
                        and dataInfo['fileType'] != 'DIAG' and dataInfo['fileType'] != 'MOP':
                    if dataInfo['neName'] not in list(timeList.keys()):
                        timeList[dataInfo['neName']] = {}
                    if dataInfo['fileType'] not in list(timeList[dataInfo['neName']].keys()):
                        timeList[dataInfo['neName']][dataInfo['fileType']] = {}
                    if dataInfo['startTime'] and dataInfo['startTime'] not in list(timeList[dataInfo['neName']][dataInfo['fileType']].keys()):
                        timeList[dataInfo['neName']][dataInfo['fileType']][time2timestamp(dataInfo['startTime'])] = time2timestamp(dataInfo['endTime'])
            except Exception as e:
                print("Error:", dataInfo)
                print(e)
    print('%s format complete' % strftime('%Y-%m-%d %H:%M:%S'))
    print('==========================================')
    version_list = {}
    type_list = {}
    t_list = []
    unknown_list = []
    for key, items in list(neList.items()):
        # 跳过 UnknownFiles 不复制
        if key == 'UnknownFiles':
            continue
        
        # 处理已知网元的文件
        for item in items:
            # 记录版本和类型
            if item['neVersion'] and key not in list(version_list.keys()):
                version_list[key] = item['neVersion']
            if item['neType'] and key not in list(type_list.keys()):
                type_list[key] = item['neType']
            
            # 跳过以 lsdb_ 开头的文件（这些是 LSDB 提取的源文件）
            fName = path.split(item['fileName'])[-1]
            if fName.startswith('lsdb_'):
                continue
            # 跳过已被删除的文件（如整文件被提取后删除的情况）
            if not path.exists(item['fileName']):
                continue
            # 跳过空文件（0字节）
            if path.getsize(item['fileName']) == 0:
                continue
            # Rollback 和 script 都放在一个 csv 文件中，只复制一个即可
            if item['fileType'] == 'Rollback':
                continue
            
            # 按网元和文件类型创建子目录
            output_ft = item['fileType']
            ne_dir = path.join(output_folder, key.replace('/', '_'))
            type_dir = path.join(ne_dir, output_ft)
            if not path.exists(type_dir):
                makedirs(type_dir)
            
            # 处理文件名冲突 + 内容去重（先比大小，再比 MD5 哈希）
            base_name, ext = path.splitext(fName)
            dest_filename = fName
            dest_full_path = path.join(type_dir, dest_filename)
            counter = 1
            is_duplicate = False
            
            while path.exists(dest_full_path):
                # 高效去重：先比文件大小，相同再比哈希
                if are_files_duplicate(item['fileName'], dest_full_path):
                    # 内容完全相同，跳过写入和统计
                    print(f"  [DUPLICATE] Skipped: {fName} (content identical with existing file)")
                    is_duplicate = True
                    break
                
                # 内容不同，添加序号继续查找
                dest_filename = f'{base_name}_{counter}{ext}'
                dest_full_path = path.join(type_dir, dest_filename)
                counter += 1
            
            # 只有非重复文件才复制并统计
            if not is_duplicate:
                from shutil import copy2
                copy2(item['fileName'], dest_full_path)
                
                # 统计
                if 'KPI' in item['fileType']:
                    kpiFileCount += 1
                elif item['fileType'] == 'LOG':
                    logFileCount += 1
                elif item['fileType'] == 'DIAG':
                    diagFileCount += 1
                elif item['fileType'] == 'CONFIG':
                    cfgFileCount += 1
                elif item['fileType'] == 'MOP':
                    MopFileCount += 1
                elif item['fileType'] == 'CMD':
                    cmdFileCount += 1
                elif item['fileType'] == 'LSDB':
                    lsdbFileCount += 1
                else:
                    unknownFileCount += 1
        
        t_item = {}
        t_item['neName'] = key
        if key in list(version_list.keys()):
            t_item['neVersion'] = version_list[key]
        else:
            t_item['neVersion'] = ''
        if key in list(type_list.keys()):
            t_item['neType'] = type_list[key]
        else:
            t_item['neType'] = ''
        t_item['items'] = items
        t_list.append(t_item)

    print("============ File summary ================")
    print(" KPI File Count    :%s" % kpiFileCount)
    print(" LOG File Count    :%s" % logFileCount)
    print(" DIAG File Count   :%s" % diagFileCount)
    print(" CMD File Count    :%s" % (cmdFileCount + diagFileCount))
    print(" CONFIG File Count :%s" % cfgFileCount)
    print(" MOP File Count    :%s" % MopFileCount)
    print(" LSDB File Count   :%s" % lsdbFileCount)
    # print(" Unknown File Count:%s" % unknownFileCount)  # UnknownFiles 已跳过
    print("Total Files Count :%s" % (kpiFileCount + logFileCount + diagFileCount + cfgFileCount + MopFileCount + cmdFileCount + lsdbFileCount + unknownFileCount))
    print("============   NE summary ================")
    if 'UnknownFiles' in list(neList.keys()):
        neList.pop('UnknownFiles')
    for key, value in list(neList.items()):
        neVersion = ''
        neType = ''
        if key in list(version_list.keys()):
            neVersion = version_list[key]
        if key in list(type_list.keys()):
            neType = type_list[key]
        print(" %-20s%-15s%s" % (key, neType, neVersion))
    print("============  Time range  ================")
    for key, value in list(timeList.items()):
        time_range = get_time_range(value)
        if len(time_range) > 0:
            print("%s %s:" % (key, version_list.get(key, '')))
            for time_item in time_range:
                print("  " + time_item)

    print("==========================================")
    
    # 补充缺失的 neVersion 和 neType（从同网元的其他文件获取）
    for item in fileInfo:
        if not item.get('neVersion') or not item.get('neType'):
            ne_name = item.get('neName', '')
            for src_item in fileInfo:
                if src_item.get('neName') == ne_name:
                    if not item.get('neVersion') and src_item.get('neVersion'):
                        item['neVersion'] = src_item['neVersion']
                    if not item.get('neType') and src_item.get('neType'):
                        item['neType'] = src_item['neType']
                    if item.get('neVersion') and item.get('neType'):
                        break
    
    return fileInfo
