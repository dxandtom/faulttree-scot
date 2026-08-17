def unZip(fileName):
    if not fileName.endswith(".zip"):
        return False
    if not fileName.endswith(".ZIP"):
        return False
    if not path.exists(fileName):
        return False
    dstDir = fileName.replace(".zip", "")
    dstDir = dstDir.replace(".ZIP", "")
    z = ZipFile(fileName)
    for f in z.filelist:
        z.extract(f, dstDir)
        if f.filename.endswith(".zip") or f.filename.endswith(".ZIP"):
            unZip(dstDir+"/"+f.filename)
    z.close()
    #remove(fileName)

def unRAR(fileName):
    if not fileName.endswith(".rar"):
        return False
    if not fileName.endswith(".RAR"):
        return False
    if not path.exists(fileName):
        return False
    dstDir = fileName.replace(".rar", "")
    dstDir = dstDir.replace(".RAR", "")
    r = RarFile(fileName)
    print(r.namelist())
    for f in r.namelist():
        r.extract(f, dstDir)
        if f.endswith(".rar") or f.endswith(".RAR"):
            unRAR(dstDir+"/"+f)
    #r.close()
    #remove(fileName)

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
            if file.endswith('.zip') or file.endswith('.rar') or file.endswith('.ZIP') or file.endswith('.RAR') \
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

def resolve_filename_conflict(base_path, filename):
    """处理文件名冲突，如果文件已存在则添加序号
    同时检查文件内容是否重复（前5行比较）
    """
    full_path = path.join(base_path, filename)
    if not path.exists(full_path):
        return filename
    
    # 检查是否是重复文件
    try:
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            existing_preview = ''.join(f.readline() for _ in range(5)).strip()
    except:
        existing_preview = ''
    
    name, ext = path.splitext(filename)
    counter = 1
    while path.exists(path.join(base_path, f'{name}_{counter}{ext}')):
        counter += 1
    
    return f'{name}_{counter}{ext}'


def collect_ne_files(neItems):
    """收集网元的待压缩文件列表，返回 (源文件路径, 目标路径, zip内相对路径) 元组列表"""
    file_list = []
    global kpiFileCount
    global logFileCount
    global diagFileCount
    global cmdFileCount
    global cfgFileCount
    global unknownFileCount
    global MopFileCount
    global lsdbFileCount
    
    try:
        for item in neItems:
            fName = path.split(item['fileName'])[-1]
            #跳过以lsdb_开头的文件（这些是LSDB提取的源文件）
            if fName.startswith('lsdb_'):
                continue
            #Rollback和script都放在一个csv文件中，只压缩一个即可
            if item['fileType'] == 'Rollback':
                continue
            
            # 如果 item 中已有 zipPath，优先使用它（避免重复添加）
            if 'zipPath' in item and item['zipPath']:
                dest_path = item['zipPath']
                zip_path = dest_path.replace('/', '\\\\')
                # 检查是否已存在（根据源文件路径去重）
                if not any(f[0] == item['fileName'] for f in file_list):
                    file_list.append((item['fileName'], dest_path, zip_path))
                    
                    # 统计
                    if 'KPI' in item['fileType']:
                        kpiFileCount += 1
                    elif item['fileType'] == 'LOG':
                        logFileCount += 1
                    elif item['fileType'] == 'CONFIG':
                        cfgFileCount += 1
                    elif item['fileType'] == 'CMD':
                        cmdFileCount += 1
                    elif item['fileType'] == 'DIAG':
                        diagFileCount += 1
                    elif item['fileType'] == 'MOP' or item['fileType'] == 'Rollback':
                        MopFileCount += 1
                    elif item['fileType'] == 'LSDB':
                        lsdbFileCount += 1
                    else:
                        unknownFileCount += 1
                continue
            
            if item['fileType'] == 'INFO':
                dest_path = item['neName'].replace('/', '_')+'/'+fName
                # Windows 路径分隔符
                zip_path = dest_path.replace('/', '\\')
                file_list.append((item['fileName'], dest_path, zip_path))
                continue
            dest_name = item['neName'].replace('/', '_')+'/'+item['fileType']+'/'+fName
            # 检查是否已存在
            if not any(f[1] == dest_name for f in file_list):
                name, ext = get_file_prefix(fName)
                dest_path = item['neName'].replace('/', '_')+'/'+item['fileType']+'/'+name+ext.lower()
                # Windows 路径分隔符
                zip_path = dest_path.replace('/', '\\')
                file_list.append((item['fileName'], dest_path, zip_path))
                
                # 统计
                if 'KPI' in item['fileType']:
                    kpiFileCount += 1
                elif item['fileType'] == 'LOG':
                    logFileCount += 1
                elif item['fileType'] == 'CONFIG':
                    cfgFileCount += 1
                elif item['fileType'] == 'CMD':
                    cmdFileCount += 1
                elif item['fileType'] == 'DIAG':
                    diagFileCount += 1
                elif item['fileType'] == 'MOP' or item['fileType'] == 'Rollback':
                    MopFileCount += 1
                elif item['fileType'] == 'LSDB':
                    lsdbFileCount += 1
                else:
                    unknownFileCount += 1
        return file_list
    except Exception as e:
        print('collect_ne_files failed:', neItems)
        print(e)
        return []

# 输入网元的一种类型数据的时间序列，输出该数据覆盖的时间范围
def time_check(time_list):
    if not isinstance(time_list, dict):
        return False
    if len(time_list) == 0:
        return False
    time_list = sorted(time_list.items())
    time_range = []
    start_time = 0
    end_time = 0
    for s_time, e_time in time_list:
        if start_time == 0:
            start_time = s_time
            end_time = e_time
            continue
        if end_time == 'now':
            break
        if e_time == 'now':
            end_time = e_time
            break
        if s_time - end_time > 3600:
            time_range.append(strftime("%Y-%m-%d %H:%M:%S", gmtime(start_time)) + ' ~ ' + strftime("%Y-%m-%d %H:%M:%S", gmtime(end_time)))
            start_time = s_time
            end_time = e_time
        else:
            end_time = e_time
    if end_time != 'now' and end_time:
        end_time = strftime("%Y-%m-%d %H:%M:%S", gmtime(end_time))
    time_range.append(strftime("%Y-%m-%d %H:%M:%S", gmtime(start_time)) + ' ~ ' + str(end_time))
    return time_range
def save_csv(fileName, dataList):
    """保存xlsx文件，按neName和fileType降序排序
       生成 xlsx 文件（包含两个 sheet）：
       - 解析数据详情：原始数据
       - 解析数据汇总：设备及文件类型汇总
    """
    xlsxName = ''
    if not dataList:
        print('dataList is null')
        return False
    if not fileName:
        print('fileName is null')
        return False

    if path.isdir(fileName):
        # 对于文件夹输出，xlsx 文件创建在文件夹内部
        xlsxName = path.join(fileName, 'formated_result_summary.xlsx')

    if path.isfile(fileName):
        if '.' in fileName:
            xlsxName = fileName.replace(fileName.split('.')[-1], '') + 'xlsx'
        else:
            xlsxName = fileName + '.xlsx'
    
    # 获取 zip 文件名
    zip_basename = path.basename(fileName) if fileName.endswith('.zip') else path.basename(fileName)
    
    # 过滤掉 fileType 为空的行
    filtered_data = [item for item in dataList if item.get('fileType')]
    
    # 按 neName 降序，相同则按 fileType 降序排序
    filtered_data.sort(key=lambda x: (x.get('neName', ''), x.get('fileType', '')), reverse=True)
    
    # 构建 zipPath 查找表：dest_name -> zipPath
    from os import path as os_path
    zip_path_lookup = {}
    for item in filtered_data:
        if 'zipPath' in item and item['zipPath']:
            fName = os_path.split(item.get('fileName', ''))[-1]
            dest_name = item.get('neName', '').replace('/', '_') + '/' + item.get('fileType', '') + '/' + fName
            if dest_name not in zip_path_lookup:
                zip_path_lookup[dest_name] = item['zipPath']
    
    # 准备详情数据（删除 startTime 和 endTime）
    detail_data = []
    for item in filtered_data:
        if 'neName' in list(item.keys()) and 'fileType' in list(item.keys()) and 'neVersion' in list(item.keys()) \
                and 'fileName' in list(item.keys()) and 'neType' in list(item.keys()):
            # 格式化 relativePath
            fName = os_path.split(item.get('fileName', ''))[-1]
            output_type = item.get('fileType', '')
            relative_path = item.get('neName', '').replace('/', '_') + '/' + output_type + '/' + fName
            
            detail_data.append({
                'neName': item['neName'],
                'fileType': output_type,
                'neVersion': item['neVersion'],
                'zipFile': zip_basename,
                'relativePath': relative_path,
                'neType': item['neType']
            })
    
    # 如果 openpyxl 可用，生成 xlsx 文件
    if HAS_OPENPYXL:
        wb = Workbook()
        
        # Sheet 1: 解析数据详情
        ws1 = wb.active
        ws1.title = "解析数据详情"
        
        # 写入表头（删除 startTime 和 endTime）
        detail_header = ['neName', 'fileType', 'neVersion', 'zipFile', 'relativePath', 'neType']
        ws1.append(detail_header)
        
        # 写入数据
        for row in detail_data:
            ws1.append([row['neName'], row['fileType'], row['neVersion'], 
                       row['zipFile'], row['relativePath'], row['neType']])
        
        # Sheet 2: 解析数据汇总
        ws2 = wb.create_sheet(title="解析数据汇总")
        
        # 汇总逻辑：统计每个设备的文件类型
        ne_summary = {}
        for item in filtered_data:
            ne_name = item.get('neName', 'UNKNOWN')
            file_type = item.get('fileType', '')
            ne_version = item.get('neVersion', '')
            ne_type = item.get('neType', '')
            
            if ne_name not in ne_summary:
                ne_summary[ne_name] = {
                    'neVersion': ne_version,
                    'neType': ne_type,
                    'fileTypes': set()
                }
            # 保留最新的版本和类型信息
            if ne_version and ne_version != 'UNKNOWN':
                ne_summary[ne_name]['neVersion'] = ne_version
            if ne_type and ne_type != 'UNKNOWN':
                ne_summary[ne_name]['neType'] = ne_type
            if file_type:
                ne_summary[ne_name]['fileTypes'].add(file_type)
        
        # 确定所有文件类型列（只保留常用的文件类型）
        all_file_types = ['CONFIG', 'CMD', 'LOG', 'DIAG', 'KPI', 'LSDB']
        
        # 按 neName 降序排序
        sorted_ne_names = sorted(ne_summary.keys(), reverse=True)
        
        # 写入表头（添加 neType 列）
        summary_header = ['neName', 'neType', 'neVersion'] + all_file_types
        ws2.append(summary_header)
        
        # 写入汇总数据
        summary_data = []
        for ne_name in sorted_ne_names:
            info = ne_summary[ne_name]
            row = [ne_name, info.get('neType', ''), info['neVersion']]
            for ft in all_file_types:
                if ft in info['fileTypes']:
                    row.append('OK')
                else:
                    row.append('NOK')
            ws2.append(row)
            
            # 收集 JSON 数据
            summary_data.append({
                'neName': ne_name,
                'neType': info.get('neType', ''),
                'neVersion': info['neVersion'],
                **{ft: 'OK' if ft in info['fileTypes'] else 'NOK' for ft in all_file_types}
            })
        
        # 调整列宽
        for ws in [ws1, ws2]:
            for column in ws.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                ws.column_dimensions[column_letter].width = adjusted_width
        
        # 保存 xlsx 文件
        wb.save(xlsxName)
        print(f"Summary saved to {xlsxName}")
        
        # 生成 JSON 文件（解析数据汇总）
        import json
        json_name = xlsxName.replace('.xlsx', '.json')
        with open(json_name, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)
        # JSON 移动和打印由主程序处理
    
    return xlsxName
def get_coding(strData):
    #该方法在py3下已废弃
    if isinstance(strData, str):
        return strData
    coding = sys.getdefaultencoding()
    if not isinstance(strData, str):
        sys.setdefaultencoding(detect(strData)['encoding'])
    try:
        fileName = strData.encode(detect(strData)['encoding']).decode('utf-8')
    except:
        fileName = strData.encode(detect(strData)['encoding']).decode("gbk")
    sys.setdefaultencoding(coding)
    return fileName
#main, fileName = sys.argv[:2]
# Accept input file path from command line argv, default to empty

# 检查并安装依赖
def check_and_install_deps():
    deps = ['chardet', 'olefile', 'oletools', 'pandas', 'rarfile']
    import importlib
    for dep in deps:
        try:
            importlib.import_module(dep)
        except ImportError:
            print(f'Installing {dep}...')
            import subprocess
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', dep, '-q'])

check_and_install_deps()

# 收集所有输入文件/目录
input_paths = sys.argv[1:] if len(sys.argv) > 1 else []
if not input_paths:
    input_paths = []

type = 'SR'
if '--rfc' in input_paths:
    input_paths.remove('--rfc')
    type = 'RFC'

clear_cache_flag = False
if '--clear-cache' in input_paths:
    input_paths.remove('--clear-cache')
    clear_cache_flag = True
    print('[CACHE] --clear-cache flag detected, will clear CLI boundary cache')

if clear_cache_flag:
    _CLI_BOUNDARY_CACHE.clear()
    print('[CACHE] CLI boundary cache cleared')

print(type)
print('APP release time: 2021-01-22')

if len(input_paths) == 0:
    print('Usage: python ifilter.py <file1.zip> [file2.zip] [...] [--rfc] [--clear-cache]')
    print('       python ifilter.py <dir1> [dir2] [...] [--rfc] [--clear-cache]')
    exit(1)

print('%s Start' % strftime('%Y-%m-%d %H:%M:%S'))

import shutil

for input_path in input_paths:
    # 处理每个输入路径：将所有文件合并后统一输出
    output_timestamp = strftime('%Y%m%d_%H%M%S')
    
    # 确定根目录
    root_dir = path.dirname(input_path) if path.isfile(input_path) else input_path
    if not path.dirname(input_path) and not path.isdir(input_path):
        root_dir = '.'
    
    # 清理已有的输出结果（递归删除根目录下所有 formated_result 和 _result 相关文件夹和文件）
    # 包括：formated_result/、_result/、formated_result.json、formated_result_summary.xlsx 等
    old_result_dir = path.join(root_dir, 'formated_result')
    old_result_dir_alt = path.join(root_dir, root_dir.split('\\')[-1] + '_result') if path.isdir(root_dir) else ''
    old_json_file = path.join(root_dir, 'formated_result.json')
    old_xlsx_file = path.join(root_dir, 'formated_result_summary.xlsx')
    
    # 清理根目录的输出物
    if path.exists(old_result_dir):
        print(f'Removing existing formated_result directory: {old_result_dir}')
        shutil.rmtree(old_result_dir, ignore_errors=True)
    if old_result_dir_alt and path.exists(old_result_dir_alt) and old_result_dir_alt != old_result_dir:
        print(f'Removing existing _result directory: {old_result_dir_alt}')
        shutil.rmtree(old_result_dir_alt, ignore_errors=True)
    if path.exists(old_json_file):
        print(f'Removing existing formated_result.json...')
        remove(old_json_file)
    if path.exists(old_xlsx_file):
        print(f'Removing existing formated_result_summary.xlsx...')
        remove(old_xlsx_file)
    
    # 递归删除所有子目录下的 formated_result、_merged_temp_* 和 _result 文件夹以及所有相关输出文件
    result_patterns_files = {'formated_result.json', 'formated_result_summary.xlsx', 'formated_result.txt', 'formated_result.log'}
    for r, dirs, files in walk(root_dir):
        # 删除匹配的输出文件
        for f in files:
            if f in result_patterns_files:
                try:
                    remove(path.join(r, f))
                    print(f'Removing nested output file: {path.join(r, f)}')
                except:
                    pass
        # 删除匹配的输出目录（formated_result/、*_result/、_merged_temp_*/）
        for d in dirs[:]:
            if d == 'formated_result' or d.endswith('_result') or d.startswith('_merged_temp_'):
                target_dir = path.join(r, d)
                try:
                    shutil.rmtree(target_dir, ignore_errors=True)
                    print(f'Removing nested output dir: {target_dir}')
                except:
                    pass
    
    # 收集所有文件（递归遍历，跳过已有的输出目录和临时目录）
    archive_files = []
    direct_files = []
    
    for r, dirs, files in walk(root_dir):
        # 跳过输出目录和临时目录
        dirs[:] = [d for d in dirs if d != 'formated_result' and not d.startswith('_merged_temp_')]
        
        for f in files:
            fp = path.join(r, f)
            if f.lower().endswith(('.zip', '.rar')):
                archive_files.append(fp)
            else:
                direct_files.append(fp)
    
    if len(archive_files) + len(direct_files) == 0:
        print(f'No valid files found in {root_dir}.')
        continue
    
    print(f'Found {len(archive_files) + len(direct_files)} file(s) in {path.basename(root_dir)}')
    if archive_files:
        print(f'  - {len(archive_files)} archive(s) to extract')
    if direct_files:
        print(f'  - {len(direct_files)} direct file(s)')
    
    # 创建合并输出的临时目录
    merged_temp_dir = path.join(root_dir, f'_merged_temp_{output_timestamp}')
    if not path.exists(merged_temp_dir):
        makedirs(merged_temp_dir)
    
    # 处理压缩包（解压）
    all_dataList = []
    for i, f in enumerate(archive_files):
        print(f'Extracting archive [{i+1}/{len(archive_files)}]: {path.basename(f)}')
        
        if f.lower().endswith('.rar'):
            try:
                from rarfile import RarFile
                with RarFile(f) as r:
                    r.extractall(merged_temp_dir)
            except Exception as e:
                print(f'  Error extracting rar file: {e}')
        else:
            try:
                with ZipFile(f) as z:
                    z.extractall(merged_temp_dir)
            except Exception as e:
                print(f'  Error extracting zip file: {e}')
    
    # 递归解压所有嵌套的 zip 文件
    def extract_all_nested_zips(directory):
        from rarfile import RarFile
        processed_files = set()
        max_iterations = 50
        iteration = 0
        valid_patterns = ['diag', 'log', 'diaglog', 'logfile', 'kpi']
        
        while iteration < max_iterations:
            iteration += 1
            found_new = False
            
            for r, dirs, files in walk(directory):
                path_lower = r.lower()
                is_valid_path = any(p in path_lower for p in valid_patterns)
                
                for file in files:
                    file_path = path.join(r, file)
                    file_lower = file.lower()
                    
                    if not file_lower.endswith('.zip'):
                        continue
                        
                    if not is_valid_path and not any(p in file_lower for p in valid_patterns):
                        continue
                    
                    if file_path in processed_files:
                        continue
                        
                    processed_files.add(file_path)
                    
                    try:
                        if path.getsize(file_path) == 0:
                            continue
                            
                        print(f'  Extracting nested archive: {path.basename(file_path)}')
                        found_new = True
                        
                        with ZipFile(file_path) as z:
                            z.extractall(r)
                        
                        try:
                            remove(file_path)
                        except:
                            pass
                    except Exception as e:
                        print(f'  Error extracting {file_path}: {e}')
            
            if not found_new:
                break
        
        print(f'  Nested archive extraction completed ({len(processed_files)} archives processed)')
    
    extract_all_nested_zips(merged_temp_dir)
    
    # 处理普通文件（直接复制到临时目录）
    if direct_files:
        print(f'Copying {len(direct_files)} direct files...')
        from shutil import copy2
        for i, f in enumerate(direct_files):
            try:
                dest = path.join(merged_temp_dir, path.basename(f))
                counter = 1
                while path.exists(dest):
                    name, ext = path.splitext(path.basename(f))
                    dest = path.join(merged_temp_dir, f'{name}_{counter}{ext}')
                    counter += 1
                copy2(f, dest)
            except Exception as e:
                print(f'  Error copying {f}: {e}')
    
    # KPI 检查
    print('Pre-checking KPI files...')
    pre_check_kpi_dir(walk(merged_temp_dir))
    
    # 压缩处理
    print('Compressing data...')
    print('%s Compressing files' % strftime('%Y-%m-%d %H:%M:%S'))
    all_dataList = compress(merged_temp_dir)
    
    print('==========================================')
    
    # 输出结果
    if all_dataList:
        result_temp_dir = merged_temp_dir + "_result"
        if path.exists(result_temp_dir):
            final_result_dir = path.join(root_dir, 'formated_result')
            
            if path.exists(final_result_dir):
                shutil.rmtree(final_result_dir, ignore_errors=True)
            
            shutil.move(result_temp_dir, final_result_dir)
            print(f'\nMerged output saved to: {final_result_dir}')
            
            # 保存汇总 xlsx
            print('%s Saving to xlsx file' % strftime('%Y-%m-%d %H:%M:%S'))
            xlsx_file = save_csv(final_result_dir, all_dataList)
            
            # JSON 文件
            json_file = xlsx_file.replace('.xlsx', '.json')
            json_final_path = path.join(root_dir, 'formated_result.json')
            if path.exists(json_file):
                if path.exists(json_final_path):
                    remove(json_final_path)
                shutil.move(json_file, json_final_path)
                print(f'JSON saved to: {json_final_path}')
            else:
                print(f'Warning: JSON file not found at {json_file}')
        else:
            print('Warning: Expected output folder not found')
    else:
        print('No data to save.')
    
    # 清理临时目录前，确认所有工作线程已结束，避免后台线程仍在写入该目录导致文件丢失
    for t in threading.enumerate():
        if isinstance(t, MyThread) and t.is_alive():
            t.join()
    # 清理临时目录
    if path.exists(merged_temp_dir):
        shutil.rmtree(merged_temp_dir, ignore_errors=True)

print('%s Finished' % strftime('%Y-%m-%d %H:%M:%S'))
