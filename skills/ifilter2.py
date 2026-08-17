            preview_lines = [f.readline() for _ in range(lines)]
        return ''.join(line.strip() for line in preview_lines if line.strip())
    except:
        return ''

def find_ne_info_from_related_files(ne_name, file_info_list):
    """从同一网元的其他文件中获取 neVersion 和 neType"""
    ne_version = ''
    ne_type = ''
    
    for item in file_info_list:
        if item.get('neName') == ne_name:
            if not ne_version and item.get('neVersion'):
                ne_version = item['neVersion']
            if not ne_type and item.get('neType'):
                ne_type = item['neType']
            if ne_version and ne_type:
                break
    
    return ne_version, ne_type


def is_script(data):
    temp = []
    if isinstance(data, str):
        # 配置文件不能判断为脚本
        temp = data.lower()
        if '#\nsysname ' in temp and '\n#\nreturn' in temp:
            return False
        if 'current_config' in temp or 'current config' in temp or 'current-config' in temp:
            return False
        # existing configuration 表示已有配置，不能判断为脚本
        if 'existing configuration' in temp:
            return False
        temp = temp.splitlines()
    elif isinstance(data, list):
        # 配置文件不能判断为脚本
        temp_data = '\n'.join(data).lower()
        if '#\nsysname ' in temp_data and '\n#\nreturn' in temp_data:
            return False
        if 'current_config' in temp_data or 'current config' in temp_data or 'current-config' in temp_data:
            return False
        # existing configuration 表示已有配置，不能判断为脚本
        if 'existing configuration' in temp_data:
            return False
        temp = data
    else:
        return False
    if not temp:
        return False
    # 如果字符串中包含rollback 或 roll back关键字，则认为是回退脚本，暂不处理
    #if 'rollback' in ' '.join(temp).lower() or 'roll back' in ' '.join(temp).lower():
    #    return False
    def f(x):
        if len(x)>0:
            x = x.lower()
            if x.startswith('display') or x.startswith('##') or x.startswith('*') or x.startswith('ping'):
                return ''
            elif x == '#':
                return 'return'
            elif x.startswith('undo '):
                return x.split()[1].lower()
            else:
                return x.split()[0].lower()
        else:
            return ''
    temp = list([x for x in [f(x.strip()) for x in temp] if len(x)>0])
    if len(temp) == 0:
        return False

    match_percent = float(len([x for x in temp if x in FIRST_KEYS]))/len(temp)
    #valid_percent = len(set(temp))/len(temp)
    #print(set(temp)&set(KEY_WORDS_LOWER))
    #print(temp)
    #percent = (0.6-valid_percent)*0.5 + match_percent*0.5
    #print('percent:%s', percent)
    if match_percent > 0.4:
        #print('This is a script! valid_percent:%s match_percent:%s' % (valid_percent, match_percent))
        return True
    else:
        #print('This is not a script! valid_percent:%s match_percent:%s' % (valid_percent, match_percent))
        return False
def get_str(dir):
    buffer = open(dir, mode='r')
    data = buffer.readlines()
    buffer.close()
    return data
def save_file(fileName, data):
    fileDir, name = path.split(fileName)
    name, ext = path.splitext(name)
    name = path.join(fileDir, name+'_test'+ext)
    with open(name, 'w') as f:
        f.write(data)
#def remove_control_chars(s):
# control_chars = ''.join(map(unichr, range(0,32) + range(127,160)))
# control_char_re = compile('[%s]' % re.escape(control_chars))
# return control_char_re.sub('', s)

def filter_esc1(s):
    if chr(27) not in s:
        return s
    ePos = s.find(chr(27))
    sPos = 1
    del_num = 0
    f_str = s[:ePos]
    s_str = s[ePos+1:]
    obj = match(r'\[(\d+)D', s_str)
    if obj:
        del_num = int(obj.group(1))
        sPos = len(obj.group(1)) + 2
    if len(f_str) >= del_num:
        f_str = f_str[:len(f_str)-del_num]
    else:
        f_str = ''
    s_str = s_str[sPos:]
    return filter_esc1(f_str+s_str)
def filter_esc(s):
    if chr(27) not in s:
        return s

    item_list = s.split(chr(27))
    temp = ''
    for item in item_list:
        obj = match(r'\[(\d+)D', item)
        if obj:
            del_num = int(obj.group(1))
            if len(temp) >= int(del_num):
                temp = temp[:len(temp)-del_num] + str(item[len(obj.group(1))+2:])
            else:
                temp = str(item[len(obj.group(1))+2:])
        else:
            temp = temp + item
    return temp
def filter_bs(s):
    if chr(8) not in s:
        return s
    while chr(8) in s:
        pos = s.find(chr(8))
        sPos = 0
        ePos = 0
        if len(s)-1 > pos > 0:
            s = s[:pos]+s[pos+1:]
        elif pos == 0:
            s = s[pos+1:]
        elif pos == len(s)-1:
            s = s[:pos]
    return s
def filter_more(s):
    return s.replace('  ---- More ----                ', '')
# 如果cmd文件中包含多个网元的数据，则进行拆分，对于ABABA这种场景，无法分割
def split_cmd_file1(file_data):
    if not isinstance(file_data, str):
        print('file data is not str:%s' % type(file_data))
        return False
    # 获取数据中的所有系统名称
    name_list = findall(r'<(\S+)>\s*(?:dis|more|dir|sys|telnet)', file_data, S|I)
    if not name_list:
        name_list = findall(r'^<(\S+)>', file_data, S)
        if not name_list:
            print('ne name is null')
            return file_data
    # 获取网元列表（去重）
    ne_list = []
    #name_list = set(name_list)
    file_obj = {}
    for item in name_list:
        if item in ne_list:
            continue
        ne_list.append(item)
        file_obj[item] = []
    # 如果只有一个网元，则不需要拆分
    if len(ne_list) == 1:
        return file_data
    file_data = file_data.splitlines()

    i = 0
    for item in file_data:
        if i == len(ne_list)-1:
            file_obj[ne_list[-1]].append(item)
            continue
        if ne_list[i+1] in item and (item[0]=='<' or item[0]=='['):
            i += 1
            file_obj[ne_list[i]].append(item)
        else:
            file_obj[ne_list[i]].append(item)
    return file_obj
def split_cmd_file(file_data, file_name):
    if not isinstance(file_data, str):
        print('file data is not str:%s' % type(file_data))
        return False
    # 获取数据中的所有系统名称
    name_list = findall(r'^<(\S+)>\s*(?:dis|more|dir|sys|telnet)', file_data, M)
    if not name_list:
        name_list = findall(r'^<(\S+)>', file_data, M)
        if not name_list:
            print('warning: ne name is null in CMD file: %s' % file_name)
            return file_data
    # 获取网元列表（去重）
    ne_list = []
    #name_list = set(name_list)
    for item in name_list:
        if item in ne_list:
            continue
        ne_list.append(item)
    # 如果只有一个网元，则不需要拆分
    if len(ne_list) == 1:
        return file_data

    file_obj = {}
    file_data = file_data.splitlines()
    i = 0
    neName = ''
    temp_data = []
    for item in file_data:
        # 命令行字段应该以'<'或'['开头，防火墙前的操作前可能有hrp，也需要考虑
        if not item.startswith('<') and not item.startswith('['):
            if not neName:
                temp_data.append(item)
            else:
                if neName not in list(file_obj.keys()):
                    file_obj[neName] = []
                file_obj[neName].append(item)
            continue

        res = search(r'^<(.+)>', item)
        if not res:
            res = search(r'^\[~?(\S+?)(?:-aaa|-diagnose|-interface|-ui-vty0)?\]', item)
        if res:
            # 只有在列表中的才有效，否则认为是命令行回显中的内容
            if res.group(1) in ne_list:
                if not neName:
                    temp_data.append(item)
                    neName = res.group(1)
                    file_obj[neName] = temp_data
                    continue
                if res.group(1) == neName:
                    file_obj[neName].append(item)
                    continue
                if res.group(1) != neName and neName:
                    neName = res.group(1)
                    if neName not in list(file_obj.keys()):
                        file_obj[neName] = []
                    file_obj[neName].append(item)
                    continue
        if neName:
            file_obj[neName].append(item)
        else:
            temp_data.append(item)
    return file_obj
# 循环遍历处理特殊字符
def format(fileName):
    if not path.isfile(fileName):
        return False
    change_flag = False
    with open(fileName, 'rb+') as f:
        fBuff = f.read()
        temp = detect(fBuff[:5000])
        #print(temp)
        if temp['encoding'] is not None:
            fBuff = fBuff.decode(temp['encoding'], errors='ignore').splitlines()
        else:
            fBuff = fBuff.decode('UTF-8', errors='ignore').splitlines()
        data = []

        for item in fBuff:
            temp = item
            if chr(27) in item:
                change_flag = True
                temp = filter_esc(temp)
            if chr(8) in item:
                change_flag = True
                temp = filter_bs(temp)
            if '---- More ----' in item:
                change_flag = True
                temp = filter_more(temp)
            #temp = filter_esc(item)
            #temp = filter_bs(temp)
            data.append(temp)
        if change_flag:
            f.seek(0)
            f.truncate()
            f.write(bytes(('\n'.join(data)).encode('UTF-8')))
    return True

def filter_logbuffer(fileName):
    if not path.isfile(fileName):
        return False
    with open(fileName, 'rb+') as f:
        fBuff = f.read()
        temp = detect(fBuff[:5000])
        #print(temp)
        if temp['encoding'] is not None:
            fBuff = fBuff.decode(temp['encoding'], errors='ignore')
        else:
            fBuff = fBuff.decode('UTF-8', errors='ignore')

        fBuff = fBuff.replace('display logbuffer', 'temp display logbuffer')
        f.seek(0)
        f.truncate()
        f.write(bytes(fBuff.encode('UTF-8')))
    return True

# 递归方式处理特殊字符
def format1(fileName):
    if not path.isfile(fileName):
        return False
    with open(fileName, 'r+') as f:
        fBuff = f.readlines()
        data = []
        for item in fBuff:
            data.append(filter_esc1(item))
        f.truncate()
        f.seek(0)
        f.write(''.join(data))
    return True

def get_kpi_info(kpiFile):
    temp = kpiFile.lower()
    if not temp.endswith(".dat") and not temp.endswith(".slog") and "kpi_" not in temp or 'logfilebuf' in kpiFile:
        return False
    endTime = ''
    regExp = search(r'(\d{4}\-\d{2}\-\d{2})\.(\d{2}\-\d{2}\-\d{2})', path.split(kpiFile)[1])
    if regExp:
        endTime = regExp.group(1) + ' ' + regExp.group(2).replace('-', ':')
    else:
        endTime = 'now'
    kpiInfo = {}
    kpiInfo.update(fileName='', neName='', fileType='', neVersion='', startTime='', endTime='', neType='')
    neName, fileType, startTime, neVersion,neType = 'UNKNOWN', 'KPI', '', '', ''

    with open(kpiFile, 'rb') as f:
        fBuf = f.read(1000)
        # license文件剔除
        if 'LicenseSerialNo' in str(fBuf):
            return False
        if len(fBuf) < 200:
            print("Unrecognized KPI format(data length<1000):", kpiFile, kpiFile)
            return False
        last_slash_index = temp.rfind('/')
        if last_slash_index == -1:
            last_slash_index = temp.rfind('\\')
        file_name = temp[last_slash_index + 1:]
        if file_name.endswith(".txt"):
            if file_name.startswith("kpi_"):
                version_type = search(r"<(\S+)>Version( ?: ?|.*?\()(.*?)(\(.*\) ?)?(V\d{3,3}R\S+?)(\)| )", str(fBuf))
                more_file = search(r"<(\S+)>(?:more|display logfile)\s+\S+\s+hex", str(fBuf))
                if more_file:
                    neName = more_file.group(1)
                neType = ''
                if version_type:
                    neName = version_type.group(1)
                    neType = version_type.group(3)
                    neVersion = version_type.group(5)
                kpiInfo.update(fileName=kpiFile, neName=neName, fileType='KPI', neVersion=neVersion,
                               startTime='2000-01-01 00:00:00', endTime=endTime, neType=neType)
                return kpiInfo
            else:
                return False

        # 如果是ATN的KPI格式 *.slog
        if kpiFile.endswith(".slog"):
            temp = detect(fBuf)
            if temp['encoding'] is not None:
                fBuf = fBuf.decode(temp['encoding'], errors='ignore')
            else:
                fBuf = fBuf.decode('UTF-8', errors='ignore')
            regExp = search(r'Sysname : (\S+)', fBuf)
            if regExp:
                neName = regExp.group(1)
            regExp = search(r'statistics at (\d{4}\-\d{2}\-\d{2} \d{2}:\d{2}:\d{2})', fBuf)
            if regExp:
                startTime = regExp.group(1)
            regExp = search(r'Version : (\S+)', fBuf)
            if regExp:
                neVersion = regExp.group(1)
            if neName == 'HUAWEI':
                neName = 'UNKNOWN'
            kpiInfo.update(fileName=kpiFile, neName=neName, fileType='KPI', neVersion=neVersion, startTime=startTime, endTime=endTime, neType='ATN950B')
            return kpiInfo

        # 如果是IPNICE数据（已禁用）
        # if 'IPNICE' in str(fBuf):
        #     data = str(fBuf)
        #     regExp = search(r'\<DevName\>(.+)\<\/DevName\>', data)
        #     if regExp:
        #         neName = regExp.group(1)
        #     regExp = search(r'<CheckStartTime>(.+)</CheckStartTime>', data)
        #     if regExp:
        #         startTime = regExp.group(1)
        #     regExp = search(r'<Version>(.+)</Version>', data)
        #     if regExp:
        #         neVersion = regExp.group(1)
        #     kpiInfo.update(fileName=kpiFile, neName=neName, fileType='IPNICE', neVersion=neVersion, startTime=startTime, endTime=endTime)
        #     return kpiInfo
        # 老KPI格式
        offset = 0
        if "KPIL.FMT" in str(fBuf[offset:offset+8]):
            frame_fhead = fBuf[8:24]
            FMT_IP1,FMT_IP2,FMT_IP3,FMT_IP4,FMT_YEAR,FMT_MONTH,FMT_DAY,FMT_H,FMT_M,FMT_S,FMT_R,FMT_LEN = unpack("!BBBBHBBBBBBI", frame_fhead)
            FMT_IP = str(FMT_IP1) + "." + str(FMT_IP2) + "." + str(FMT_IP3) + "." + str(FMT_IP4)
            startTime = str(FMT_YEAR)+"-"+str(FMT_MONTH)+"-"+str(FMT_DAY)+' '+str(FMT_H)+':'+str(FMT_M)+':'+str(FMT_S)
            neName = fBuf[24:24+FMT_LEN].decode()
            fileType = 'KPI'
            if neName == 'HUAWEI':
                neName = 'UNKNOWN'
            kpiInfo.update(fileName=kpiFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime)
            return kpiInfo
        
        # 新格式：检查是否包含 \x0c 后跟设备名称的模式（AG-BDCKJ-01H KPI\lpustat.dat 格式）
        # 这种格式在 \x0c 后面跟设备名称，以 \x00 结尾
        if b'\x0c' in fBuf[:100]:
            pos = fBuf[:100].index(b'\x0c')
            # 读取 \x0c 后的数据直到 \x00
            remaining = fBuf[pos+1:pos+50]
            null_pos = remaining.find(b'\x00')
            if null_pos > 0 and null_pos < 50:
                potential_name = remaining[:null_pos].decode('utf-8', errors='ignore')
                # 验证是否是有效的设备名称（包含字母数字，长度合理）
                if potential_name and len(potential_name) >= 3 and len(potential_name) <= 32:
                    if search(r'^[A-Za-z0-9\-_]+$', potential_name):
                        neName = potential_name
                        # 尝试继续读取版本信息（\x04 后跟版本号）
                        rest_data = fBuf[pos+1+null_pos:]
                        if b'\x04' in rest_data[:50]:
                            ver_pos = rest_data.index(b'\x04')
                            ver_remaining = rest_data[ver_pos+1:ver_pos+50]
                            ver_null = ver_remaining.find(b'\x00')
                            if ver_null > 0 and ver_null < 50:
                                potential_ver = ver_remaining[:ver_null].decode('utf-8', errors='ignore')
                                if potential_ver.startswith('V') and 'R' in potential_ver:
                                    neVersion = potential_ver
                        # 尝试读取设备类型（\x05 后跟设备类型）
                        if b'\x05' in rest_data[:50]:
                            type_pos = rest_data.index(b'\x05')
                            type_remaining = rest_data[type_pos+1:type_pos+50]
                            type_null = type_remaining.find(b'\x00')
                            if type_null > 0 and type_null < 50:
                                neType = type_remaining[:type_null].decode('utf-8', errors='ignore').strip()
                        if neName == 'HUAWEI' or neName == 'Quidway':
                            neName = 'UNKNOWN'
                        kpiInfo.update(fileName=kpiFile, neName=neName, fileType='KPI', neVersion=neVersion, 
                                       startTime=startTime, endTime=endTime, neType=neType)
                        return kpiInfo
        
        # 新KPI格式
        file_flag, name_len = unpack("!LH", fBuf[0:6])
        if file_flag == 84215045:
            offset = 4
            length, = unpack("!H", fBuf[offset:offset+2])
            if not isinstance(length, int):
                print("Unrecognized KPI format(not int):", length, kpiFile)
                return False
            if length < 8:
                print("Unrecognized KPI format(header length < 8):", length, kpiFile)
                return False

            header_len = length + 12 + 4
            offset = 12
            while(offset < header_len):
                type, = unpack("!H", fBuf[offset:offset+2])
                if type == 41120:
                    break
                if type > 10:
                    print("Unrecognized KPI format(type > 10):", type, offset, kpiFile)
                    return False
                offset += 2
                data_len, = unpack("!H", fBuf[offset:offset+2])
                if not isinstance(data_len, int):
                    print("Unrecognized KPI format(not digit):", data_len, kpiFile)
                    return False
                if data_len > 1000:
                    print("Unrecognized KPI format(length exceed 1000):", data_len, kpiFile)
                    return False
                offset += 2
                data = fBuf[offset:offset+data_len]
                offset += data_len
                if type == 0:
                    neName = data.decode()
                    #print 'neName', neName
                elif type == 1:
                    continue
                elif type == 2:
                    fileType = data.decode()
                    #print 'file_type', file_type
                elif type == 3:
                    Y, M, D, H, MM, S, R = unpack("!HBBBBBB", data)
                    startTime = str(Y)+'-'+str(M)+'-'+str(D)+' '+str(H)+':'+str(MM)+':'+str(S)
                    #print 'start_time', startTime
                elif type == 4:
                    neVersion = data.decode()
                    #print 'neVersion', neVersion
            if neName == 'HUAWEI':
                neName = 'UNKNOWN'
            kpiInfo.update(fileName=kpiFile, neName=neName, fileType='KPI', neVersion=neVersion, startTime=startTime, endTime=endTime)
            return kpiInfo

        # 最老的KPI格式，没有设备名称，只有数据区，读取开始记录的时间
        offset = 0
        head_size = 10
        frame_head = fBuf[offset:offset+head_size]
        frame_date_stamp, frame_chassis, frame_slot, frame_module_id, frame_len = unpack("!LBBHH", frame_head)
        date_arr = gmtime(frame_date_stamp)
        startTime = strftime("%Y-%m-%d %H:%M:%S", date_arr)
        kpiInfo.update(fileName=kpiFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime)
        return kpiInfo


def get_log_info(logFile):
    if not path.isfile(logFile):
        return False
    temp = logFile.lower()
    if not temp.endswith('.log') and not temp.endswith('.txt') and not temp.endswith('.dblg'):
        return False
    # uFAD datacollector工具采集的信息，剔除此文件
    if 'All Collect Results.txt' in logFile:
        return False
    logData = ''
    logInfo = {}
    neName, fileType, startTime, neVersion, endTime, neType = 'UNKNOWN', '', '', '', '', ''
    #防火墙日志名称2019-05-20.19-02-27.log
    timeObj = search(r'(\d{4})[\-]*(\d{2})[\-]*(\d{2})\.?(\d{2})[\-]*(\d{2})[\-]*(\d{2})', path.split(logFile)[1])
    if timeObj:
        endTime = timeObj.group(1)+'-'+timeObj.group(2)+'-'+timeObj.group(3)+' '+timeObj.group(4)+':'+timeObj.group(5)+':'+timeObj.group(6)
    else:
        endTime = 'now'

    with open(logFile, 'rb') as f:
        raw_bytes = f.read()
        # UTF-16 BOM detection: 优先于 charset_normalizer，避免误判
        bom = raw_bytes[:4]
        if bom[:2] == b'\xff\xfe':
            encoding_to_use = 'utf-16-le'
        elif bom[:2] == b'\xfe\xff':
            encoding_to_use = 'utf-16-be'
        else:
            detected = detect(raw_bytes[:5000])
            if detected['encoding'] is not None and detected['confidence'] >= 0.7:
                enc = detected['encoding'].lower()
                encoding_to_use = 'utf-16' if 'utf-16' in enc else detected['encoding']
            else:
                encoding_to_use = 'utf-8'
        logData = raw_bytes.decode(encoding_to_use, errors='ignore')

    if not logData:
        return False
    dir, fileName = path.split(temp)
    
    # 识别 nicfiledata.txt 多设备合并格式：<device> + =HUAWEI= + command
    # 这种格式包含 <device> 或 [~device] 格式的设备名
    # 提取第一个设备名作为默认网元名称，后续处理逻辑会使用
    nicfile_default_neName = None
    if '=######HUAWEI#####=' in logData[:5000]:
        nicfile_pat = search(r'^(?:<([^>]+)>|\[~?([^\]]+)\])', logData, M)
        if nicfile_pat:
            nicfile_default_neName = nicfile_pat.group(1) or nicfile_pat.group(2)
    
    if fileName.endswith('.dblg'):
        if 'This logfile is generated' not in logData[:5000]:
            return False
        # .dblg 文件是 diagnostic log，归入 DIAG 目录
        fileType = 'DIAG'
        if '\x00' in logData[:5000]:
            temp = split('[\x00\x01]+', logData[:5000])
            # 有些情况下没有记录网元名称，需要特殊判断
            #if '####' in temp[1] or '\xFE\xFF' in temp[1]:
            if '####' in temp[1]:
                neName = 'UNKNOWN'
            else:
                try:
                    neName = temp[1]
                except:
                    neName = 'UNKNOWN'
        if neName=='HUAWEI' or neName=='Quidway':
            neName = 'UNKNOWN'
        logInfo.update(fileName=logFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
        return logInfo
    elif '=######HUAWEI#####=' in logData[:5000]:
        # nicfiledata.txt 多设备合并格式
        fileType = 'CMD'
        # 跳过标准日志格式检查，直接进入网元名称识别
        # （因为 nicfiledata.txt 不是标准日志格式）
        if nicfile_default_neName:
            neName = nicfile_default_neName
        # 尝试从 display version 输出中提取设备信息
        version_pat = search(r'HUAWEI\s+(\S+)\s+uptime', logData[:10000])
        if version_pat:
            neType = version_pat.group(1)
        # 尝试从 display version 输出中提取版本信息
        ver_pat = search(r'Version\s+(\S+)', logData[:10000])
        if ver_pat:
            neVersion = ver_pat.group(1)
        # 跳到提取逻辑
        dataList = []
        if neName and neName not in ['Unknown', 'UNKNOWN', '']:
            try:
                cfg_results = extract_current_configuration(logFile, logData, neName)
                if cfg_results:
                    print(f'Extracted {len(cfg_results)} CONFIG files from {path.split(logFile)[1]}')
                    dataList.extend(cfg_results)
                log_results = extract_logbuffer(logFile, logData, neName)
                if log_results:
                    print(f'Extracted {len(log_results)} LOG files from {path.split(logFile)[1]}')
                    dataList.extend(log_results)
                trap_results = extract_trapbuffer(logFile, logData, neName)
                if trap_results:
                    print(f'Extracted {len(trap_results)} trapbuffer files from {path.split(logFile)[1]}')
                    dataList.extend(trap_results)
                logfile_results, logData = extract_logfile(logFile, logData, neName)
                if logfile_results:
                    print(f'Extracted {len(logfile_results)} logfile files from {path.split(logFile)[1]}')
                    dataList.extend(logfile_results)
                more_results, logData = extract_more(logFile, logData, neName)
                if more_results:
                    print(f'Extracted {len(more_results)} more files from {path.split(logFile)[1]}')
                    dataList.extend(more_results)
                lsdb_results = extract_lsdb(logFile, logData, neName)
                if lsdb_results:
                    print(f'Extracted {len(lsdb_results)} LSDB files from {path.split(logFile)[1]}')
                    dataList.extend(lsdb_results)
                modified_data = remove_cfg_log_from_original(logData, neName)
                if modified_data != logData:
                    if not modified_data.strip():
                        try:
                            remove(logFile)
                        except:
                            pass
                    else:
                        with open(logFile, 'wb+') as f:
                            f.write(modified_data.encode('utf-8'))
                file_basename = path.split(logFile)[1]
                if file_basename.startswith('lsdb_'):
                    try:
                        remove(logFile)
                    except:
                        pass
            except Exception as e:
                print('Error extracting CONFIG/LOG/LSDB from {}: {}'.format(path.split(logFile)[1], str(e)))
        logInfo.update(fileName=logFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
        dataList.insert(0, logInfo)
        return dataList
    elif 'This logfile is generated' in logData[:5000]:
        if 'diag' in fileName:
            fileType = 'DIAG'
        else:
            fileType = 'LOG'
    elif search(r'\n%?(.+? \d+:\d+:\d+).*? (\S+) (?:%%\d\d)?\w+/\d/\w+.*?:.*$', logData[-5000:].strip()) \
            and 'IKE_DEBUG' not in logData[-5000:].strip():
        if 'diag' in fileName:
            fileType = 'DIAG'
        else:
            fileType = 'LOG'
    else:
        #不识别的文件返回False
        return False
    # 按日志格式进行规则匹配，获取文件尾部日志，根据最后一条日志提取网元名称和时间
    #Nov 15 2019 17:11:24+08:00 NTJERTR1 %%01DEVM/1/hwEntityInvalid_clear(l)
    regExp = search(r'[\s\S]*\n%?([\w -]{0,30}? \d+:\d+:\d+)\S*\s*(?:DST|GMT|UTC)*\s*((?!HUAWEI).{0,80}?) (?:%%\d\d)?\w+/\d/\w+.{0,50}?:[\S\s]*?$', logData[-5000:].strip())
    if regExp:
        neName = regExp.group(2)
        endTime = get_time(regExp.group(1).split('%')[-1])
    else:
        # 如果尾部日志没有匹配到，则对全量日志进行匹配
        regExp = search(r'\n%?([\w -]{0,30}? \d+:\d+:\d+)\S*\s*(?:DST|GMT|UTC)*\s*((?!HUAWEI).{0,80}?) (?:%%\d\d)?\w+/\d/\w+.{0,50}?:', logData)
        if regExp:
            neName = regExp.group(2)
            startTime = get_time(regExp.group(1).split('%')[-1])
    if not neName:
        # 防火墙中可能会记录的是诊断命令回显信息：
        ################################################################
        #The following log information been wrote automatically
        #by logfile-timer per 5 minute(s)
        #DateTime Stamp: 2020-02-16 02:11:35.050
        # HRP_M[DH_PB02_PS_E8000_X8_FW1-diagnose]
        regExp = search(r'\[(\S+)\-diagnose\]', logData)
        if regExp:
            neName = regExp.group(1)
    if not startTime:
        regExp = search(r'%?([\w -]{0,20}? \d+:\d+:\d+).{0,100}?\S+/\d/\S+', logData)
        if regExp:
            startTime = get_time(regExp.group(1).split('%')[-1])
        else:
            regExp = search(r'DateTime Stamp: (\S+\s+\d+:\d+:\d+)', logData)
            if regExp:
                startTime = get_time(regExp.group(1))
    if 'HUAWEI' in neName or 'Quidway' in neName:
        neName = 'UNKNOWN'
    
    # 如果标准日志格式没有识别到网元名称，使用 nicfiledata.txt 格式中识别的设备名
    if neName == 'UNKNOWN' and nicfile_default_neName:
        neName = nicfile_default_neName
    
    # 新增：从命令行文件中提取CONFIG、LOG、LSDB相关内容
    # 只有在能够识别到网元名称时才进行提取
    # 返回包含主文件和生成文件的列表
    dataList = []
    if neName and neName not in ['Unknown', 'UNKNOWN', '']:
        try:
            # 提取display current-configuration内容生成CONFIG文件
            cfg_results = extract_current_configuration(logFile, logData, neName)
            if cfg_results:
                print(f'Extracted {len(cfg_results)} CONFIG files from {path.split(logFile)[1]}')
                dataList.extend(cfg_results)
            
            # 提取display logbuffer内容生成LOG文件
            log_results = extract_logbuffer(logFile, logData, neName)
            if log_results:
                print(f'Extracted {len(log_results)} LOG files from {path.split(logFile)[1]}')
                dataList.extend(log_results)
            
            # 提取display trapbuffer内容生成LOG文件
            trap_results = extract_trapbuffer(logFile, logData, neName)
            if trap_results:
                print(f'Extracted {len(trap_results)} trapbuffer files from {path.split(logFile)[1]}')
                dataList.extend(trap_results)
            
            # 提取logfile相关内容生成LOG文件（会从原始内容中删除已提取的块）
            logfile_results, logData = extract_logfile(logFile, logData, neName)
            if logfile_results:
                print(f'Extracted {len(logfile_results)} logfile files from {path.split(logFile)[1]}')
                dataList.extend(logfile_results)
            
            # 提取more相关内容生成LOG/DBLG文件（会从原始内容中删除已提取的块）
            more_results, logData = extract_more(logFile, logData, neName)
            if more_results:
                print(f'Extracted {len(more_results)} more files from {path.split(logFile)[1]}')
                dataList.extend(more_results)
            
            # 提取LSDB相关内容生成LSDB文件
            lsdb_results = extract_lsdb(logFile, logData, neName)
            if lsdb_results:
                print(f'Extracted {len(lsdb_results)} LSDB files from {path.split(logFile)[1]}')
                dataList.extend(lsdb_results)
            
            # 从原始数据中删除CONFIG和LOG回显，然后更新文件
            modified_data = remove_cfg_log_from_original(logData, neName)
            if modified_data != logData:
                # 如果修改后只剩空白内容，说明整个文件内容都被提取了，直接删除
                if not modified_data.strip():
                    try:
                        remove(logFile)
                        print(f'Removed empty file: {path.split(logFile)[1]}')
                    except:
                        pass
                else:
                    with open(logFile, 'wb+') as f:
                        f.write(modified_data.encode('utf-8'))
            
            # 如果文件名以 lsdb_ 开头，说明这个文件专门用于 LSDB 提取，提取后可以删除
            file_basename = path.split(logFile)[1]
            if file_basename.startswith('lsdb_'):
                try:
                    remove(logFile)
                except:
                    pass
        except Exception as e:
            print('Error extracting CONFIG/LOG/LSDB from {}: {}'.format(path.split(logFile)[1], str(e)))
    
    # 添加主log文件信息
    logInfo.update(fileName=logFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
    dataList.insert(0, logInfo)
    
    return dataList

# 将时间转换成秒
def time2timestamp(strTime):
    if strTime.isalpha() or not strTime:
        return strTime
    #if len(strTime) != len('1970-01-01 00:00:00'):
    #    #print("para format error: " + strTime)
    #    return strTime
    oriTime = dd.strptime("1970-01-01 00:00:00", '%Y-%m-%d %H:%M:%S')
    tempTime = ''
    time_diff = ''
    if '/' in strTime:
        tempTime = dd.strptime(strTime, '%Y/%m/%d %H:%M:%S')
    elif '-' in strTime:
        tempTime = dd.strptime(strTime, '%Y-%m-%d %H:%M:%S')
    time_diff = tempTime - oriTime
    return time_diff.total_seconds()

# 格式化时间字符串1）2019-9-14 18:22:52，Nov 27 2019 15:10:35
def get_time_range(timeList):
    try:
        # timeList 格式: {fileType: {startTimestamp: endTimestamp}}
        time_range = []
        for fileType, timeDict in list(timeList.items()):
            if len(timeDict) == 0:
                continue
            # 获取最小时间戳和最大时间戳
            timestamps = sorted(timeDict.keys())
            start_time = timestamp2time(min(timestamps))
            end_time = timestamp2time(max(timeDict.values()))
            time_range.append('%s: %s - %s' % (fileType, start_time, end_time))
        return time_range
    except Exception as e:
        traceback.print_exc()
        return []


def get_time(strTime):
    strTime = strTime.strip()
    strTime = strTime.replace('  ', ' ')
    with c:
        try:
            if ':' not in strTime:
                print('Time format is wrong:', strTime)
                return strTime
            elif '-' in strTime and strTime[:4].isdigit() and strTime[5:8].isalpha():
                #2018-Dec-05 10:20:30
                return strftime('%Y-%m-%d %H:%M:%S', strptime(strTime, '%Y-%b-%d %H:%M:%S'))
            elif '-' in strTime and strTime[:4].isdigit():
                #2019-9-14 18:22:52
                return strftime('%Y-%m-%d %H:%M:%S', strptime(strTime, '%Y-%m-%d %H:%M:%S'))
            elif '-' not in strTime and strTime[:3].isalpha():
                #Nov 27 2019 15:10:35
                return strftime('%Y-%m-%d %H:%M:%S', strptime(strTime, '%b %d %Y %H:%M:%S'))
        except Exception as e:
            print("Error: get time error:", strTime)
            print(e)
    #如果时间格式不对，则返回空字符串
    print('Unknown time format:', strTime)
    return ''

def get_file_info(diagFile):
    try:
        data_info = get_kpi_info(diagFile)
        if data_info:
            return data_info
        data_info = get_log_info(diagFile)
        if data_info:
            return data_info
        data_info = get_cfg_info(diagFile)
        if data_info:
            return data_info
        # 新增：识别通过 extract_current_configuration 生成的 CONFIG 文件
        # 文件名格式：设备名_cfg.txt 或在 CONFIG/ 目录下
        data_info = get_extracted_config_info(diagFile)
        if data_info:
            return data_info
        # 新增：识别通过 extract_lsdb 生成的 LSDB 文件
        # 文件名格式：设备名_lsdb.txt 或在 LSDB/ 目录下
        data_info = get_extracted_lsdb_info(diagFile)
        if data_info:
            return data_info
        try:
            data_info = get_diag_info(diagFile)
        except Exception as e:
            print('get_diag_info Error:', e)
            print('file name:', diagFile)
            data_info = False
        if data_info:
            return data_info
        data_info = get_mop_info(diagFile)
        if data_info:
            return data_info
        data_info = get_neinfo_file(diagFile)
        if data_info:
            return data_info
        # 新增兜底：从 SNMP Trap 风格日志中提取设备名
        data_info = get_snmp_trap_log_info(diagFile)
        if data_info:
            return data_info
        return diagFile
    except Exception as e:
        print('get_file_info Error:', diagFile)
        print(e)
        return False


def get_snmp_trap_log_info(logFile):
    """兜底识别：从 SNMP Trap 风格日志文件中提取设备名并归类为 LOG 文件

    支持两种时间戳格式：
    格式 1: Jul 23 2020 10:13:15-05:00 Switch01 %%01MSTP/4/PORT_STATE_FORWARDING(l):...
    格式 2: 2024-11-21 02:40:57.382+07:00 AG-BDCKJ-01H %%01CPUDEFEND/7/CPCARMODIFY(D):...

    如果匹配成功，设备名从日志中间提取，返回 LOG 类型文件信息。
    """
    if not path.isfile(logFile):
        return False

    temp = logFile.lower()
    if not temp.endswith('.log') and not temp.endswith('.txt') and not temp.endswith('.dblg'):
        return False

    # uFAD datacollector 工具采集的信息，剔除此文件
    if 'All Collect Results.txt' in logFile:
        return False

    try:
        with open(logFile, 'rb') as f:
            logData = f.read()
            tempData = detect(logData[:5000])
            if tempData['encoding'] is not None:
                logData = logData.decode(tempData['encoding'], errors='ignore')
            else:
                logData = logData.decode('UTF-8', errors='ignore')

        if not logData or len(logData.strip()) == 0:
            return False

        # 格式 1: Jul 23 2020 10:13:15-05:00 DeviceName %%01...
        # 格式 2: 2024-11-21 02:40:57.382+07:00 DeviceName %%01...
        pattern_snmp = compile(
            r'(?:'
            r'[A-Z][a-z]{2}\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}'
            r'|'
            r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2}'
            r')\s+(\S+)\s+%%\d\d',
            I
        )
        match = pattern_snmp.search(logData)
        if not match:
            return False

        neName = match.group(1)
        # 过滤无效设备名
        if neName in ('HUAWEI', 'Quidway', 'UNKNOWN', 'Unknown', ''):
            return False

        # 提取结束时间（取最后一条日志的时间）
        all_matches = list(pattern_snmp.finditer(logData))
        last_match = all_matches[-1]
        time_str = logData[last_match.start():last_match.start() + 50]
        endTime = 'now'
        # 尝试提取时间
        timeObj = search(r'(\d{4})[\-]*(\d{2})[\-]*(\d{2})\.?(\d{2})[\-]*(\d{2})[\-]*(\d{2})', logFile)
        if timeObj:
            endTime = timeObj.group(1) + '-' + timeObj.group(2) + '-' + timeObj.group(3) + ' ' + \
                      timeObj.group(4) + ':' + timeObj.group(5) + ':' + timeObj.group(6)

        logInfo = {
            'fileName': logFile,
            'neName': neName,
            'fileType': 'LOG',
            'neVersion': '',
            'startTime': '',
            'endTime': endTime,
            'neType': ''
        }
        return logInfo

    except Exception as e:
        print(f'get_snmp_trap_log_info Error: {logFile}, {e}')
        return False

def get_extracted_config_info(diagFile):
    """识别 extract_current_configuration 生成的 CONFIG 文件
    
    文件名格式：设备名_cfg.txt 或在 CONFIG/ 目录下
    例如：AG-BDCKJ-01H_cfg.txt 或 AG-BDCKJ-01H/CONFIG/AG-BDCKJ-01H_cfg.txt
    """
    if not path.isfile(diagFile):
        return False
    
    file_name = path.split(diagFile)[1]
    
    # 匹配格式：设备名_cfg.txt（如 AG-BDCKJ-01H_cfg.txt）
    # 设备名格式：大写字母开头，包含 -，由字母数字组成
    config_pattern = compile(r'^([A-Z][A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+)_cfg\.txt$', I)
    match = config_pattern.match(file_name)
    
    if match:
        neName = match.group(1)
        fileType = 'CONFIG'
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
    
    # 检查是否在 CONFIG/ 目录下
    # 目录结构应为：.../设备名/CONFIG/设备名_cfg.txt
    dir_parts = diagFile.replace('\\', '/').split('/')
    if len(dir_parts) >= 2:
        parent_dir = dir_parts[-2]  # CONFIG
        if parent_dir.upper() == 'CONFIG':
            # 从上级目录或文件名获取设备名
            grand_parent = dir_parts[-3] if len(dir_parts) >= 3 else ''
            # 设备名也可能直接编码在文件名中
            for part in dir_parts:
                config_match = config_pattern.match(part)
                if config_match:
                    neName = config_match.group(1)
                    fileType = 'CONFIG'
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
