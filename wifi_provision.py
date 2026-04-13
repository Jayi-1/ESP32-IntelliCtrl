import network, time, machine
# from micropython import const
import gc

gc.collect()
_STA = network.WLAN(network.STA_IF)
# _MAX_SSID = const(31)
# _MAX_PWD = const(63)

# 配置文件名（与 wifi_mgr.py 保持一致）
WIFI_CONFIG_FILE = "wifi.json"
LAST_WIFI_FILE = "last_wifi.json"

# ---------- 配置管理工具（支持多 WiFi 列表）----------

def _load_profiles():
    """加载所有保存的 WiFi 配置（兼容单条旧格式和列表新格式）"""
    import json, os
    try:
        if WIFI_CONFIG_FILE in os.listdir():
            with open(WIFI_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 兼容旧版单条格式
                if isinstance(data, dict):
                    return [data]
                elif isinstance(data, list):
                    return data
                else:
                    return []
        return []
    except (OSError, ValueError) as e:
        print(f'[Provision] 加载配置失败: {e}')
        return []

def _save_or_update_profile(ssid: str, pwd: str):
    """
    保存或更新 WiFi 配置（追加模式，不覆盖已有）
    返回: (success: bool, index: int) index 为该配置在列表中的位置
    """
    import json, os
    
    if not ssid:
        return False, -1
    
    # 加载现有配置
    profiles = _load_profiles()
    
    # 检查是否已存在，存在则更新密码
    existing_index = -1
    for i, profile in enumerate(profiles):
        if profile.get('ssid') == ssid:
            profiles[i]['password'] = pwd
            existing_index = i
            break
    
    # 不存在则追加
    if existing_index == -1:
        profiles.append({'ssid': ssid, 'password': pwd})
        existing_index = len(profiles) - 1
    
    # 保存回文件
    try:
        with open(WIFI_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(profiles, f, ensure_ascii=False)
        print(f'[Provision] 已保存 WiFi [{existing_index}] {ssid}，共 {len(profiles)} 个配置')
        return True, existing_index
    except Exception as e:
        print(f'[Provision] 保存失败: {e}')
        return False, -1

def _load_last_index():
    """读取上次成功连接的 WiFi 索引"""
    import json, os
    try:
        if LAST_WIFI_FILE in os.listdir():
            with open(LAST_WIFI_FILE, 'r') as f:
                data = json.load(f)
                return data.get('last_index', 0)
    except Exception as e:
        print(f'[Provision] 读取上次索引失败: {e}')
    return 0

def _save_last_index(index: int):
    """保存当前成功连接的 WiFi 索引"""
    import json
    try:
        with open(LAST_WIFI_FILE, 'w') as f:
            json.dump({'last_index': index}, f)
    except Exception as e:
        print(f'[Provision] 保存索引失败: {e}')

# ---------- 连接工具 ----------

def _connect(ssid: str, pwd: str, timeout: int = None) -> bool:
    """连接指定 WiFi，成功返回 True"""
    if not ssid:
        return False
    
    if timeout is None:
        timeout = WIFI_PROV_CFG.CONNECT_TIMEOUT
    
    _STA.active(True)
    if _STA.isconnected():
        _STA.disconnect()
        time.sleep_ms(100)
    
    print(f'[Provision] 正在连接 {ssid}...')
    _STA.connect(ssid, pwd)
    
    for i in range(timeout):
        if _STA.isconnected():
            return True
        time.sleep(1)
    
    _STA.active(False)
    return False

def _try_connect_specific(index: int, timeout: int = 10) -> tuple:
    """
    尝试连接指定索引的 WiFi
    返回: (success: bool, ssid: str, ip: str)
    """
    profiles = _load_profiles()
    if index < 0 or index >= len(profiles):
        return False, "", ""
    
    profile = profiles[index]
    ssid = profile.get('ssid', '')
    pwd = profile.get('password', '')
    
    if not ssid:
        return False, "", ""
    
    if _connect(ssid, pwd, timeout):
        ip = _STA.ifconfig()[0]
        return True, ssid, ip
    return False, "", ""

def _try_connect_all(start_from: int = 0, timeout_per_wifi: int = 10) -> tuple:
    """
    智能轮询连接：从指定索引开始，失败后遍历所有
    返回: (success: bool, index: int, ssid: str, ip: str)
    """
    profiles = _load_profiles()
    if not profiles:
        print('[Provision] 没有保存的 WiFi 配置')
        return False, -1, "", ""
    
    total = len(profiles)
    
    # 从 start_from 开始遍历（循环遍历，尝试所有）
    for offset in range(total):
        idx = (start_from + offset) % total
        profile = profiles[idx]
        ssid = profile.get('ssid', '')
        pwd = profile.get('password', '')
        
        if not ssid:
            continue
        
        print(f'[Provision] 尝试 [{idx}] {ssid}...')
        
        if _connect(ssid, pwd, timeout_per_wifi):
            ip = _STA.ifconfig()[0]
            print(f'[Provision] 成功连接 [{idx}] {ssid}')
            return True, idx, ssid, ip
        else:
            print(f'[Provision] [{idx}] {ssid} 失败')
    
    return False, -1, "", ""

def connect_smart_after_provision(new_index: int = None) -> tuple:
    """
    配网后的智能连接策略：
    1. 如果提供了 new_index（刚配网的新 WiFi），先尝试新 WiFi
    2. 如果新 WiFi 连不上，尝试上次成功的 WiFi（快速连接）
    3. 如果上次成功的也连不上，从 0 开始轮询所有
    
    返回: (success: bool, index: int, ssid: str, ip: str)
    """
    # 第 1 步：优先尝试刚配网的新 WiFi（如果提供且有效）
    if new_index is not None:
        print(f'[Provision] 优先尝试刚配置的 WiFi [{new_index}]...')
        success, ssid, ip = _try_connect_specific(new_index)
        if success:
            _save_last_index(new_index)
            print(f'[Provision] 新 WiFi 连接成功，已记住索引 [{new_index}]')
            return True, new_index, ssid, ip
        print('[Provision] 新 WiFi 不可用')
    
    # 第 2 步：尝试上次成功的 WiFi（快速连接）
    last_index = _load_last_index()
    profiles = _load_profiles()
    
    if profiles and 0 <= last_index < len(profiles):
        # 确保不是刚才已经试过的新 WiFi
        if new_index != last_index:
            print(f'[Provision] 尝试上次成功的 WiFi [{last_index}]...')
            success, ssid, ip = _try_connect_specific(last_index)
            if success:
                print(f'[Provision] 上次成功的 WiFi 可用，IP: {ip}')
                return True, last_index, ssid, ip
            print('[Provision] 上次成功的 WiFi 已不可用')
    
    # 第 3 步：从 0 开始轮询所有（除了刚才试过的）
    print('[Provision] 开始轮询所有 WiFi...')
    skip_index = new_index if new_index is not None else -1
    total = len(profiles)
    
    for idx in range(total):
        if idx == skip_index or idx == last_index:
            continue  # 跳过已经试过的
        
        profile = profiles[idx]
        ssid = profile.get('ssid', '')
        pwd = profile.get('password', '')
        
        if not ssid:
            continue
        
        print(f'[Provision] 尝试 [{idx}] {ssid}...')
        if _connect(ssid, pwd, 10):
            ip = _STA.ifconfig()[0]
            _save_last_index(idx)  # 记住新的成功索引
            print(f'[Provision] 轮询成功，连接 [{idx}] {ssid}，已记住索引')
            return True, idx, ssid, ip
    
    return False, -1, "", ""

# ---------- 配网入口 ----------

def provision_bin(data: bytes) -> str:
    print('>>> 原始数据:', data.hex())

    # 1. 只有 AA 头 & 最小长度 6
    if not (len(data) >= 6 and data[0] == 0xAA):
        print('>>> 头错误')
        return 'FORMAT_ERR'

    # 2. 长度字段在第 1 字节（不含 AA）
    tot_len = data[1]
    if len(data) != tot_len + 2:  # AA(1) + 数据(tot_len) + 校验(1)
        print('>>> 长度错误')
        return 'FORMAT_ERR'

    # 3. 命令字在第 2 字节
    if data[2] != WIFI_PROV_CFG.PROV_TAG:
        print('>>> 命令字错误')
        return 'FORMAT_ERR'

    print('>>> 校验通过，开始提取 SSID/PWD')
    
    # 4. 提取 SSID / 密码（双 0x00 结束）
    try:
        off = 3
        print('>>> off =', off)

        # 手动找第一个 0x00（SSID 结束）
        ssid_end = -1
        for i in range(off, len(data)):
            if data[i] == 0x00:
                ssid_end = i
                break
        if ssid_end == -1:
            raise ValueError
        print('>>> ssid_end =', ssid_end)

        ssid_bytes = data[off:ssid_end]
        print('>>> ssid_bytes =', ssid_bytes)

        try:
            ssid = ssid_bytes.decode('utf-8')
            print('>>> ssid =', ssid)
        except Exception as e:
            print('>>> SSID decode 异常：', e)
            return 'FORMAT_ERR'

        off = ssid_end + 1
        print('>>> off(密码) =', off)

        # 手动找第二个 0x00（密码结束）
        pwd_end = -1
        for i in range(off, len(data)):
            if data[i] == 0x00:
                pwd_end = i
                break
        if pwd_end == -1:
            raise ValueError
        print('>>> pwd_end =', pwd_end)

        pwd_bytes = data[off:pwd_end]
        print('>>> pwd_bytes =', pwd_bytes)

        try:
            pwd = pwd_bytes.decode('utf-8')
            print('>>> pwd =', pwd)
        except Exception as e:
            print('>>> PWD decode 异常：', e)
            return 'FORMAT_ERR'

        print('>>> 提取完成，SSID=', ssid, 'PWD=', pwd)
    except ValueError:
        return 'FORMAT_ERR'

    # 5. 保存配置（追加模式，支持多 WiFi）
    success, new_index = _save_or_update_profile(ssid, pwd)
    if not success:
        return 'SAVE_ERR'

    print(f'>>> 配置已保存到索引 [{new_index}]，开始智能连接...')
    gc.collect()
    print('free :', gc.mem_free())

    # 6. 智能连接：先尝试新 WiFi，失败再尝试上次成功的，再失败则轮询所有
    conn_success, conn_idx, conn_ssid, ip = connect_smart_after_provision(new_index)
    
    if conn_success:
        print(f'>>> 联网成功！索引 [{conn_idx}]，SSID: {conn_ssid}，IP: {ip}')
        gc.collect()
        print('free :', gc.mem_free())
        return 'OK'
    
    return 'CONNECT_FAIL'

# ---------- 兼容旧版的快捷函数 ----------
# 为了兼容可能直接调用这些函数的旧代码

def try_connect_all(start_from: int = 0, timeout: int = 10):
    """供外部调用的轮询连接接口"""
    return _try_connect_all(start_from, timeout)

def get_saved_count():
    """获取保存的 WiFi 数量"""
    return len(_load_profiles())