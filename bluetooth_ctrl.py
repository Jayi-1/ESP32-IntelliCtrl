"""
ESP32-IntelliCtrl - BLE蓝牙控制模块
MIT License | Copyright (c) 2026 ESP32-IntelliCtrl
处理Nordic UART Service协议
"""
import bluetooth
import time
import gc
from machine import Timer
from micropython import const
from config import BLE_CFG

# ========== 全局常量（优化：提取避免重复创建）==========
# IRQ 常量
_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)

# 状态通知命令字
CMD_WIFI_STATUS_NOTIFY = const(0x35)

# 通用数据传输协议常量
CMD_DATA_TRANSFER = const(0xDD)
MAX_SESSIONS = const(4)
SESSION_TIMEOUT_MS = const(30000)

# 【优化】遥控指令名称映射（全局常量，避免每次创建字典）
_REMOTE_CMD_NAMES = {
    0x10: "UP",
    0x11: "DOWN",
    0x12: "LEFT",
    0x13: "RIGHT",
    0x14: "OK",
    0x15: "BACK",
}

# 延迟导入状态查询所需的模块（避免循环依赖）
_wifi_mgr = None
_sd_manager = None
_wallpaper_mgr = None
_screen_ctrl = None

# 壁纸控制命令字（与 config.py 保持一致）
_WALLPAPER_IMG_CMD = const(0xA0)  # 图片背景切换
_WALLPAPER_COLOR_CMD = const(0xB0)  # 纯色背景切换

# 壁纸模式常量
_WALLPAPER_MODE_SOLID = const(0)
_WALLPAPER_MODE_IMAGE = const(1)

# 屏幕控制命令字
_SCREEN_POWER_CMD = const(0xC0)     # 屏幕开关
_SCREEN_BRIGHT_CMD = const(0xC1)   # 亮度调节
_SCREEN_SLEEP_CMD = const(0xC3)    # 息屏时间

def _get_wifi_mgr():
    global _wifi_mgr
    if _wifi_mgr is None:
        import wifi_mgr
        _wifi_mgr = wifi_mgr
    return _wifi_mgr

def _get_sd_manager():
    global _sd_manager
    if _sd_manager is None:
        from read_sd_capacity import sd_manager
        _sd_manager = sd_manager
    return _sd_manager

def _get_wallpaper_mgr():
    """延迟导入壁纸管理器"""
    global _wallpaper_mgr
    if _wallpaper_mgr is None:
        try:
            import wallpaper_mgr as wm
            _wallpaper_mgr = wm
        except ImportError as e:
            print(f"[BLE] 壁纸模块导入失败: {e}")
            return None
    return _wallpaper_mgr

def _get_screen_ctrl():
    """延迟导入屏幕控制器"""
    global _screen_ctrl
    if _screen_ctrl is None:
        try:
            import screen_ctrl as sc
            _screen_ctrl = sc
        except ImportError as e:
            print(f"[BLE] 屏幕模块导入失败: {e}")
            return None
    return _screen_ctrl

# 辅助函数：将bytes转为hex字符串（优化：简化实现）
def _hex_str(data):
    """将bytes转换为可读性好的十六进制字符串，如 'BB 05 10 00 00 D0'"""
    if isinstance(data, bytes):
        return ' '.join(f'{b:02X}' for b in data)
    return str(data)

# 【新增】通用数据会话类（内联实现，避免多文件依赖）
class DataSession:
    """BLE通用数据传输会话（自动分包重组）"""
    def __init__(self, session_id):
        self.session_id = session_id
        self.total_pkts = 0           # 总包数（首包确定）
        self.received = 0             # 已收包数
        self.buffer = {}              # 包缓存 {seq: payload_bytes}
        self.last_time = time.ticks_ms()
        self.meta = {}                # 元数据（首包解析）
        self.state = 'IDLE'           # IDLE, RECEIVING, COMPLETE
    
    def update_activity(self):
        self.last_time = time.ticks_ms()
    
    def is_expired(self, timeout=SESSION_TIMEOUT_MS):
        return time.ticks_diff(time.ticks_ms(), self.last_time) > timeout
    
    def add_packet(self, seq, payload):
        """添加数据包，返回是否为新包"""
        if seq in self.buffer:
            return False  # 重复包
        self.buffer[seq] = payload
        self.received += 1
        self.update_activity()
        return True
    
    def is_complete(self):
        """检查是否收齐"""
        if self.total_pkts == 0:
            return False
        return self.received >= self.total_pkts
    
    def assemble(self):
        """重组完整数据（按seq排序）"""
        if not self.is_complete():
            return None
        result = bytearray()
        for i in range(self.total_pkts):
            if i in self.buffer:
                result.extend(self.buffer[i])
            else:
                return None
        return bytes(result)
    
    def clear(self):
        """释放内存"""
        self.buffer.clear()
        self.state = 'IDLE'
        gc.collect()


# ========== 通用数据分包管理器 ==========
class DataAssembler:
    """BLE通用数据接收管理器"""
    def __init__(self):
        self.sessions = {}          # {session_id: DataSession}
        self.timeout_ms = SESSION_TIMEOUT_MS
        self.on_complete = None      # 回调: (session_id, data, meta)
        self.on_progress = None     # 回调: (session_id, percent, info)

    def set_callbacks(self, on_complete=None, on_progress=None, mode='buffer'):
        """注册回调函数"""
        self.on_complete = on_complete
        self.on_progress = on_progress
        self.mode = mode

    def process_packet(self, raw):
        """
        处理原始BLE数据包
        raw格式: [0xDD, DATA_TYPE, SEQ_H, SEQ_L, LEN, ...payload...]
        说明:
          - DATA_TYPE: 0x02=api.json, 0x03=gpio.json
          - SEQ_H/SEQ_L: 序列号（16位，0x0000表示单包）
          - LEN: payload长度
        返回: (handled:bool, info:str)
        """
        if len(raw) < 6 or raw[0] != CMD_DATA_TRANSFER:
            return False, "NOT_DATA_PKT"

        data_type = raw[1]  # 类型字节 (02=api, 03=gpio)
        seq = (raw[2] << 8) | raw[3]  # 序列号（16位）
        payload_len = raw[4]  # payload长度
        payload = raw[5:5 + payload_len] if len(raw) >= 5 + payload_len else raw[5:]

        # 调试日志
        hex_str = ' '.join(f'{b:02X}' for b in raw)
        print(f"[BLE-PKT] RX: {hex_str}")
        print(f"[BLE-PKT] type=0x{data_type:02X}, seq={seq}, len={payload_len}")

        # 使用 session_id = data_type（简化处理）
        session_id = data_type

        # 创建或获取会话
        if session_id not in self.sessions:
            if len(self.sessions) >= MAX_SESSIONS:
                self._cleanup_oldest()
            self.sessions[session_id] = DataSession(session_id)
            print(f"[BLE-DATA] 新建会话 [{session_id}] 类型:0x{data_type:02X}")

        session = self.sessions[session_id]

        # 首包解析：设置元数据
        if seq == 0:
            # 解析类型到 meta
            type_map = {0x02: 'api_config', 0x03: 'gpio_config'}
            new_type = type_map.get(data_type, f'unknown_0x{data_type:02X}')
            session.meta = {'type': new_type}
            print(f"[BLE-DATA] 设置 meta.type = {new_type}")

        # 添加包
        session.add_packet(seq, payload)

        # 进度回调
        if self.on_progress and session.total_pkts > 0:
            pct = (session.received * 100) // session.total_pkts
            self.on_progress(session_id, pct, f"{session.received}/{session.total_pkts}")

        # 检查完成
        if session.is_complete():
            data = session.assemble()
            if data and self.on_complete:
                self.on_complete(session_id, data, session.meta)
            session.clear()
            del self.sessions[session_id]
            return True, "COMPLETE"

        return True, f"OK SEQ={seq}"

    def _cleanup_oldest(self):
        """清理最老的会话"""
        if not self.sessions:
            return
        oldest = min(self.sessions.items(), key=lambda x: x[1].last_time)
        print(f"[BLE-DATA] 清理老会话 [{oldest[0]}]")
        oldest[1].clear()
        del self.sessions[oldest[0]]
        gc.collect()

    def tick(self):
        """主循环调用，清理超时会话"""
        expired = [sid for sid, s in self.sessions.items() if s.is_expired()]
        for sid in expired:
            print(f"[BLE-DATA] 会话 [{sid}] 超时")
            self.sessions[sid].clear()
            del self.sessions[sid]
        if expired:
            gc.collect()


# 全局实例
data_assembler = DataAssembler()


class BLEController:
    def __init__(self):
        self.ble = bluetooth.BLE()
        self.active = False
        self.char_h = None
        self.ccc_h = None
        self.conn_handles = set()
        self.notify_enabled = False
        self._pending_cmd = None
        
        # 【新增】广播重启标志位（解决断开时立即广播的硬件冲突）
        self._pending_restart_advertising = False
        
        # 状态变更检测
        self._last_wifi_status = None
        self._last_notify_time = 0
        self._notify_interval_ms = 1000  # 最小通知间隔1秒（防刷屏）
        
        # 收发计数器（方便Shell中查看顺序）
        self._rx_count = 0
        self._tx_count = 0
        
        # ========== 新增：Token 配对机制 ==========
        self._pending_tokens = {}  # 存储等待配对的 Token: {token: {'ssid': str, 'timestamp': ms}}
        self._token_timeout_ms = 30000  # Token 有效期 30 秒
        
        self._init_services()
    
    def _init_services(self):
        """初始化GATT服务 - 【修复】改用标准Nordic UART Service，避免Device Information Service的只读限制"""
        self.ble.active(True)
        self.ble.config(bond=True, io=0)
        
        # 【修复】使用标准 Nordic UART Service UUID（解决10007写入权限问题）
        # 原违规配置：SRV_UUID = bluetooth.UUID(0x180A)  # Device Information Service（标准只读服务）
        # 新配置：使用 Nordic UART Service，这是BLE数据通信的行业标准
        SRV_UUID = bluetooth.UUID('6E400001-B5A3-F393-E0A9-E50E24DCCA9E')  # ✅ Nordic UART Service
        
        # 特征值：保持 0xFF01 作为数据通道，或改用标准 Nordic RX 特征值
        # 为保持手机端兼容性，保留 0xFF01，但也可改为 '6E400002-B5A3-F393-E0A9-E50E24DCCA9E'
        CHR_UUID = bluetooth.UUID(0xFF01)  # 保持原特征值UUID，属性仍为 READ|WRITE|NOTIFY
        
        CCC_UUID = bluetooth.UUID(0x2902)  # Client Characteristic Configuration
        
        ((self.char_h, self.ccc_h),) = self.ble.gatts_register_services([(
            SRV_UUID, [(
                CHR_UUID,
                bluetooth.FLAG_READ | bluetooth.FLAG_WRITE | bluetooth.FLAG_NOTIFY,
                ((CCC_UUID, bluetooth.FLAG_READ | bluetooth.FLAG_WRITE),)
            )]
        )])
        
        self.ble.irq(self._irq_handler)
        print("[BLE] GATT服务已注册: Nordic UART Service (6E400001...), 特征值 0xFF01")
    
    def start_advertising(self):
        """开始广播 - 【修复】增加硬件状态检查"""
        # 【新增】确保蓝牙硬件已激活
        if not self.ble.active():
            try:
                self.ble.active(True)
                time.sleep_ms(100)  # 给硬件100ms启动时间
            except OSError as e:
                print(f"[BLE] 硬件激活失败: {e}")
                return False
        
        try:
            adv_data = b'\x02\x01\x02\x07\x09' + BLE_CFG.BLE_NAME.encode()
            self.ble.gap_advertise(100_000, adv_data)
            self.active = True
            # 重置状态缓存
            self._last_wifi_status = None
            print("[BLE] 广播已启动")
            return True
        except OSError as e:
            print(f"[BLE] 广播启动失败: {e}")
            return False
    
    def stop_advertising(self):
        """停止广播 - 【修复】增加异常处理"""
        try:
            self.ble.gap_advertise(None)
            time.sleep_ms(100)
            self.ble.active(False)
            self.active = False
            print("[BLE] 已停止")
            return True
        except OSError as e:
            print(f"[BLE] 停止广播异常: {e}")
            self.active = False  # 强制标记为停止，避免状态不一致
            return False
    
    def _irq_handler(self, event, data):
        """蓝牙事件中断处理 - 【关键修复】断开连接不立即广播"""
        if event == _IRQ_CENTRAL_CONNECT:
            self.conn_handles.add(data[0])
            print(f"[BLE] 设备连接: {data[0]}")
            # 连接成功后立即发送一次当前状态（全量同步）
            self._force_notify_status()
            
        elif event == _IRQ_CENTRAL_DISCONNECT:
            self.conn_handles.discard(data[0])
            print(f"[BLE] 设备断开: {data[0]}")
            # 【关键修复】不要在中断里立即重启广播！设置标志位让主循环处理
            if self.active:
                self._pending_restart_advertising = True
                print("[BLE] 已标记待重启广播")
                
        elif event == _IRQ_GATTS_WRITE:
            self._handle_write(data[1])
    
    def _handle_write(self, handle):
        """处理写入数据"""
        raw = self.ble.gatts_read(handle)
        
        # CCC订阅处理（不打印，避免干扰）
        if handle == self.ccc_h:
            val = int.from_bytes(raw + b'\x00\x00', 'little')
            self.notify_enabled = (val == 0x0001)
            if self.notify_enabled:
                print("[BLE] 手机已开启通知(Notify)")
            return
        
        # 打印接收到的原始数据到Shell
        self._rx_count += 1
        print(f"\n[RX-{self._rx_count:03d}] {_hex_str(raw)} (长度:{len(raw)})")
        
        if len(raw) < 6:
            print(f"[RX-{self._rx_count:03d}] 数据长度不足，丢弃")
            return
        
        prefix = raw[0]
        
        # 【新增】优先检查通用数据传输协议 (0xDD)
        if prefix == CMD_DATA_TRANSFER:
            handled, info = data_assembler.process_packet(raw)
            if handled:
                # 发送ACK确认（可选）
                ack = bytes([CMD_DATA_TRANSFER, raw[1], raw[2], 0x00, 0x01, 0x00])
                self._send_reply(ack)
                print(f"[BLE-DATA] 已处理: {info}")
                return
            else:
                print(f"[BLE-DATA] 处理失败: {info}")
                return
        
        # WiFi控制指令 (0xCC)
        if prefix == BLE_CFG.WIFI_PREFIX and raw[1] == 0x05:
            self._handle_wifi_cmd(raw)
        # 遥控指令 (0xBB)
        elif prefix == BLE_CFG.CMD_PREFIX and raw[1] == 0x05:
            self._handle_remote_cmd(raw)
        # 壁纸控制指令 (0xAA) - 预设背景更换，长度=3
        elif prefix == 0xAA and len(raw) >= 6 and raw[1] == 0x03:
            self._handle_wallpaper_preset(raw)
        # 屏幕控制指令 (0xAA) - 屏幕开关/亮度/息屏，长度=4
        elif prefix == 0xAA and len(raw) >= 6 and raw[1] == 0x04:
            self._handle_screen_cmd(raw)
        else:
            print(f"[RX-{self._rx_count:03d}] 未知指令前缀: 0x{prefix:02X}")
    
    def _handle_wifi_cmd(self, raw):
        """
        处理WiFi指令（兼容旧协议和新协议）
        新协议格式：[CC 05 CMD 00 LEN Payload... CHK]（包尾校验）
        旧协议格式：[CC 05 CMD 00 00 CHK]（第6字节校验，无Payload）
        """
        
        # ========== 新协议检测：raw[3]==0x00 且 len(raw) == raw[4] + 6 ==========
        if len(raw) >= 6 and raw[3] == 0x00 and len(raw) == raw[4] + 6:
            # 新协议：包尾校验
            chk_calc = sum(raw[:-1]) & 0xFF
            chk_recv = raw[-1]
            
            if chk_calc != chk_recv:
                print(f"[RX-{self._rx_count:03d}] 新协议校验失败: 计算0x{chk_calc:02X} != 接收0x{chk_recv:02X}")
                self._send_reply(bytes([0xFD]))
                return
            
            cmd = raw[2]
            payload = raw[5:-1]  # 提取 Payload（去掉前5字节头部和最后1字节校验）
            
            print(f"[RX-{self._rx_count:03d}] 新协议 CMD=0x{cmd:02X}, Payload={_hex_str(payload)}")
            
            # 新协议指令分发
            if cmd == 0x39:  # ADD with Token
                self._handle_add_wifi_token(payload)
            elif cmd == 0x3C:  # SET_PWD with Token
                self._handle_set_pwd_token(payload)
            elif cmd == 0x38:  # GET_LIST
                self._handle_get_list_new(payload)
            elif cmd == 0x3A:  # DEL
                self._handle_del_wifi_new(payload)
            elif cmd == 0x3B:  # CONN_IDX
                self._handle_conn_idx_new(payload)
            else:
                # 其他指令使用原有处理逻辑（保持兼容）
                self._handle_wifi_cmd_legacy(raw, is_new_proto=True)
            return
        
        # ========== 回退到旧协议（第6字节校验）==========
        self._handle_wifi_cmd_legacy(raw, is_new_proto=False)
    
    def _handle_wifi_cmd_legacy(self, raw, is_new_proto=False):
        """旧协议处理（保持原有逻辑不变）"""
        chk = sum(b & 0xFF for b in raw[:5]) & 0xFF
        if chk != raw[5]:
            print(f"[RX-{self._rx_count:03d}] 旧协议校验失败: 计算0x{chk:02X} != 接收0x{raw[5]:02X}")
            self._send_reply(bytes([BLE_CFG.RSP_CHECK_ERR]))
            return
        
        cmd = raw[2]
        cmd_name = {
            BLE_CFG.WIFI_STA_OLD: "STA_OLD",
            BLE_CFG.WIFI_STA_NEW: "STA_NEW", 
            BLE_CFG.WIFI_AP: "AP",
            BLE_CFG.WIFI_OFF: "OFF",
            BLE_CFG.WIFI_QUERY: "QUERY",
            BLE_CFG.WIFI_SCAN: "WIFI_SCAN",
            BLE_CFG.WIFI_GET_LIST: "GET_LIST",
            BLE_CFG.WIFI_ADD: "ADD_WIFI",
            BLE_CFG.WIFI_DEL: "DEL_WIFI",
            BLE_CFG.WIFI_CONN_IDX: "CONN_IDX"
        }.get(cmd, f"UNKNOWN(0x{cmd:02X})")
        
        print(f"[RX-{self._rx_count:03d}] 解析WiFi指令(旧协议): {cmd_name}")
        
        # 指令分发（保持原有映射）
        handlers = {
            BLE_CFG.WIFI_SCAN: self._handle_wifi_scan,
            BLE_CFG.WIFI_GET_LIST: self._handle_get_list,
            BLE_CFG.WIFI_ADD: self._handle_add_wifi,
            BLE_CFG.WIFI_DEL: self._handle_del_wifi,
            BLE_CFG.WIFI_CONN_IDX: self._handle_conn_idx,
        }
        
        if cmd in handlers:
            handlers[cmd](raw)
        elif cmd in [BLE_CFG.WIFI_STA_OLD, BLE_CFG.WIFI_STA_NEW, BLE_CFG.WIFI_AP, BLE_CFG.WIFI_OFF, BLE_CFG.WIFI_QUERY]:
            self._pending_cmd = cmd
            self._send_reply(bytes([BLE_CFG.RSP_OK]))
        else:
            self._send_reply(bytes([BLE_CFG.RSP_UNKNOWN]))
    
    # ==================== 新增：新协议处理函数（支持Token+12字节SSID）====================
    
    def _handle_add_wifi_token(self, payload):
        """
        处理添加WiFi（新协议，带Token，支持12字节SSID）
        Payload: [SSID_LEN(1)] [SSID(N≤12)] [TOKEN(1)]
        注意：省略了PWD_LEN字段，因为ADD命令隐含密码长度为0
        """
        try:
            if len(payload) < 3:  # 至少要有 SSID_LEN(1) + 空SSID + TOKEN(1)
                print("[BLE] ADD数据长度不足")
                self._send_reply(bytes([0xF3]))
                return
            
            ptr = 0
            ssid_len = payload[ptr]
            ptr += 1
            
            # 检查SSID长度合理性（最大12字节，避免溢出）
            if ssid_len > 12 or ssid_len == 0:
                print(f"[BLE] ADD SSID长度无效: {ssid_len}")
                self._send_reply(bytes([0xF3]))
                return
            
            if len(payload) < ptr + ssid_len + 1:  # +1 是 Token
                print(f"[BLE] ADD Payload长度不匹配: 期望{ptr+ssid_len+1}, 实际{len(payload)}")
                self._send_reply(bytes([0xF3]))
                return
            
            ssid = bytes(payload[ptr:ptr+ssid_len]).decode('utf-8', 'ignore').strip('\x00')
            ptr += ssid_len
            
            token = payload[ptr]
            
            if not ssid:
                print("[BLE] ADD SSID为空")
                self._send_reply(bytes([0xF3]))
                return
            
            print(f"[BLE] ADD(新协议): SSID='{ssid}', Token=0x{token:02X}")
            
            # 清理过期Token
            self._clean_expired_tokens()
            
            # 检查Token是否已存在（避免重复）
            if token in self._pending_tokens:
                print(f"[BLE] Token 0x{token:02X} 已存在，覆盖旧值")
            
            # 保存到临时Token表（等待SET_PWD）
            self._pending_tokens[token] = {
                'ssid': ssid,
                'timestamp': time.ticks_ms()
            }
            
            print(f"[BLE] Token 0x{token:02X} 已注册，等待密码设置...")
            
            # 响应成功，返回 [0x00, Token]
            self._send_reply(bytes([0x00, token]))
            
        except Exception as e:
            print(f"[BLE] ADD(新协议)错误: {e}")
            self._send_reply(bytes([0xF2]))
    
    def _handle_set_pwd_token(self, payload):
        """
        处理设置密码（新协议，通过Token配对）
        Payload: [TOKEN(1)] [PWD_LEN(1)] [PWD(N≤13)]
        """
        try:
            if len(payload) < 2:  # 至少要有 TOKEN(1) + PWD_LEN(1)
                print("[BLE] SET_PWD数据长度不足")
                self._send_reply(bytes([0xF4]))
                return
            
            ptr = 0
            token = payload[ptr]
            ptr += 1
            
            # 清理过期Token并检查
            self._clean_expired_tokens()
            
            if token not in self._pending_tokens:
                print(f"[BLE] SET_PWD: Token 0x{token:02X} 不存在或已过期")
                self._send_reply(bytes([0xF4]))  # Token无效
                return
            
            # 获取待设置的SSID
            pending = self._pending_tokens[token]
            ssid = pending['ssid']
            
            pwd_len = payload[ptr]
            ptr += 1
            
            if len(payload) < ptr + pwd_len:
                print(f"[BLE] SET_PWD密码数据不完整: 期望{ptr+pwd_len}, 实际{len(payload)}")
                self._send_reply(bytes([0xF2]))
                return
            
            password = bytes(payload[ptr:ptr+pwd_len]).decode('utf-8', 'ignore') if pwd_len > 0 else ''
            
            print(f"[BLE] SET_PWD: Token=0x{token:02X}, SSID='{ssid}', PWD长度={pwd_len}")
            
            # 实际保存到WiFi列表
            wifi_mgr = _get_wifi_mgr()
            success, index = wifi_mgr.add_wifi(ssid, password)
            
            if success:
                # 从待处理列表移除
                del self._pending_tokens[token]
                print(f"[BLE] WiFi '{ssid}' 已保存，索引={index}")
                self._send_reply(bytes([0x00]))  # 成功
                self._force_notify_status()  # 通知状态变更
            else:
                print("[BLE] 保存WiFi失败")
                self._send_reply(bytes([0xF2]))
                
        except Exception as e:
            print(f"[BLE] SET_PWD(新协议)错误: {e}")
            self._send_reply(bytes([0xF2]))
    
    def _clean_expired_tokens(self):
        """清理过期的Token"""
        now = time.ticks_ms()
        expired = []
        for token, data in self._pending_tokens.items():
            if time.ticks_diff(now, data['timestamp']) > self._token_timeout_ms:
                expired.append(token)
        for token in expired:
            print(f"[BLE] Token 0x{token:02X} 已过期，清理")
            del self._pending_tokens[token]
    
    def _handle_get_list_new(self, payload):
        """获取列表（新协议，保持与旧协议相同格式）"""
        # 直接调用旧协议处理逻辑
        self._handle_get_list(b'')
    
    def _handle_del_wifi_new(self, payload):
        """删除WiFi（新协议）"""
        try:
            if len(payload) < 1:
                self._send_reply(bytes([0xF1]))
                return
            index = payload[0]
            wifi_mgr = _get_wifi_mgr()
            if wifi_mgr.del_wifi(index):
                self._send_reply(bytes([0x00]))
            else:
                self._send_reply(bytes([0xF1]))
        except Exception as e:
            print(f"[BLE] DEL(新协议)错误: {e}")
            self._send_reply(bytes([0xF1]))
    
    def _handle_conn_idx_new(self, payload):
        """连接指定索引（新协议）"""
        try:
            if len(payload) < 1:
                self._send_reply(bytes([0xF1]))
                return
            index = payload[0]
            wifi_mgr = _get_wifi_mgr()
            # 立即响应确认收到指令
            self._send_reply(bytes([0x00]))
            # 异步执行连接
            time.sleep_ms(100)
            success, msg = wifi_mgr.connect_by_index(index)
            self._force_notify_status()
            if success:
                print(f"[BLE] 连接成功: {msg}")
            else:
                print(f"[BLE] 连接失败: {msg}")
        except Exception as e:
            print(f"[BLE] CONN_IDX(新协议)错误: {e}")
            self._send_reply(bytes([0xF1]))
    
    # ==================== 原有功能保持不变 ====================
    
    def _handle_get_list(self, raw):
        """处理获取已保存WiFi列表 (0x38)"""
        try:
            wifi_mgr = _get_wifi_mgr()
            ssid_list = wifi_mgr.get_ssid_list()
            count = min(len(ssid_list), BLE_CFG.MAX_WIFI_SAVED)
            
            print(f"[BLE] 获取WiFi列表，共{count}个")
            
            # 响应格式：0x38 + COUNT + [SSID(10字节定长)]*N
            response = bytearray()
            response.append(BLE_CFG.WIFI_GET_LIST)
            response.append(count)
            
            for i in range(count):
                ssid = ssid_list[i][:10]  # 截断或填充到10字节
                ssid_bytes = ssid.encode('utf-8')
                # 手动填充到10字节（替代 ljust）
                ssid_fixed = ssid_bytes + b'\x00' * (10 - len(ssid_bytes))
                response.extend(ssid_fixed)
            
            self._send_reply(bytes(response))
            print(f"[BLE] WiFi列表已发送")
            
        except Exception as e:
            print(f"[BLE] 获取列表失败: {e}")
            self._send_reply(bytes([BLE_CFG.WIFI_GET_LIST, 0x00]))
    
    def _handle_add_wifi(self, raw):
        """处理添加WiFi (0x39) - 旧协议"""
        try:
            # 数据格式：0xCC 05 39 [SSID_LEN(1)] [SSID(N)] [PWD_LEN(1)] [PWD(M)]
            if len(raw) < 8:
                print("[BLE] 添加WiFi数据长度不足")
                self._send_reply(bytes([BLE_CFG.ERR_WIFI_SSID_EMPTY]))
                return
            
            ptr = 3
            ssid_len = raw[ptr]
            ptr += 1
            
            if len(raw) < ptr + ssid_len + 1:
                print("[BLE] SSID数据不完整")
                self._send_reply(bytes([BLE_CFG.ERR_WIFI_FILE]))
                return
            
            ssid = raw[ptr:ptr+ssid_len].decode('utf-8', 'ignore').strip('\x00')
            ptr += ssid_len
            
            pwd_len = raw[ptr]
            ptr += 1
            
            if len(raw) < ptr + pwd_len:
                print("[BLE] 密码数据不完整")
                self._send_reply(bytes([BLE_CFG.ERR_WIFI_FILE]))
                return
            
            pwd = raw[ptr:ptr+pwd_len].decode('utf-8', 'ignore')
            
            if not ssid:
                self._send_reply(bytes([BLE_CFG.ERR_WIFI_SSID_EMPTY]))
                return
            
            print(f"[BLE] 添加WiFi(旧协议): {ssid}")
            
            # 检查数量限制
            wifi_mgr = _get_wifi_mgr()
            if wifi_mgr.get_wifi_count() >= BLE_CFG.MAX_WIFI_SAVED:
                print("[BLE] WiFi数量已达上限")
                self._send_reply(bytes([BLE_CFG.ERR_WIFI_FILE]))
                return
            
            success, index = wifi_mgr.add_wifi(ssid, pwd)
            
            if success:
                self._send_reply(bytes([BLE_CFG.RSP_OK, index]))
                print(f"[BLE] 添加成功，索引: {index}")
            else:
                self._send_reply(bytes([BLE_CFG.ERR_WIFI_FILE]))
                
        except Exception as e:
            print(f"[BLE] 添加WiFi错误: {e}")
            self._send_reply(bytes([BLE_CFG.ERR_WIFI_FILE]))
    
    def _handle_del_wifi(self, raw):
        """处理删除WiFi (0x3A)"""
        try:
            # 数据格式：0xCC 05 3A [INDEX(1)] [00] [CHK]
            if len(raw) < 7:
                print("[BLE] 删除指令数据不足")
                self._send_reply(bytes([BLE_CFG.ERR_WIFI_INDEX]))
                return
            
            index = raw[3]
            
            print(f"[BLE] 请求删除WiFi索引: {index}")
            
            wifi_mgr = _get_wifi_mgr()
            if index >= wifi_mgr.get_wifi_count():
                self._send_reply(bytes([BLE_CFG.ERR_WIFI_INDEX]))
                return
            
            if wifi_mgr.del_wifi(index):
                self._send_reply(bytes([BLE_CFG.RSP_OK]))
                print(f"[BLE] 删除成功")
            else:
                self._send_reply(bytes([BLE_CFG.ERR_WIFI_FILE]))
                
        except Exception as e:
            print(f"[BLE] 删除WiFi错误: {e}")
            self._send_reply(bytes([BLE_CFG.ERR_WIFI_FILE]))
    
    def _handle_conn_idx(self, raw):
        """处理连接指定索引 (0x3B)"""
        try:
            # 数据格式：0xCC 05 3B [INDEX(1)] [00] [CHK]
            if len(raw) < 7:
                print("[BLE] 连接索引指令数据不足")
                self._send_reply(bytes([BLE_CFG.ERR_WIFI_INDEX]))
                return
            
            index = raw[3]
            
            print(f"[BLE] 请求连接WiFi索引: {index}")
            
            wifi_mgr = _get_wifi_mgr()
            
            # 立即响应确认收到指令
            self._send_reply(bytes([BLE_CFG.RSP_OK]))
            
            # 异步执行连接（避免阻塞蓝牙）
            time.sleep_ms(100)
            success, msg = wifi_mgr.connect_by_index(index)
            
            # 发送状态变更通知
            self._force_notify_status()
            
            if success:
                print(f"[BLE] 连接成功: {msg}")
            else:
                print(f"[BLE] 连接失败: {msg}")
                
        except Exception as e:
            print(f"[BLE] 连接索引错误: {e}")
            self._send_reply(bytes([BLE_CFG.ERR_WIFI_INDEX]))
    
    def _handle_wifi_scan(self, raw):
        """处理WiFi扫描并回传结果"""
        try:
            wifi_mgr = _get_wifi_mgr()
            print("[BLE] 开始扫描WiFi...")
            networks = wifi_mgr.scan_wifi()
            
            # 限制最多返回20个网络，避免数据量过大
            total = min(len(networks), 20)
            print(f"[BLE] 扫描完成，发现{total}个网络，开始回传...")
            
            # 发送头部信息：命令字 + 总数 + 保留字节(补0)
            header = bytes([BLE_CFG.WIFI_SCAN, total, 0x00, 0x00, 0x00, 0x00])
            self._send_reply(header)
            time.sleep_ms(100)  # 给手机处理时间
            
            # 逐个网络发送详情
            for i, net in enumerate(networks[:total]):
                ssid = net['ssid'][:16]  # SSID限制16字节
                ssid_bytes = ssid.encode('utf-8')
                ssid_len = len(ssid_bytes)
                
                # RSSI转换为无符号字节（-100~0 -> 156~256）
                rssi = net['rssi']
                if rssi < 0:
                    rssi = 256 + rssi
                
                secure = 0x01 if net['secure'] else 0x00
                
                # 数据包格式：0x36 + 序号(1) + SSID长度(1) + SSID内容(N) + RSSI(1) + 加密标志(1)
                data = bytes([BLE_CFG.WIFI_SCAN, i, ssid_len]) + ssid_bytes + bytes([rssi & 0xFF, secure])
                self._send_reply(data)
                time.sleep_ms(50)  # 分包间隔，避免丢包
            
            print(f"[BLE] WiFi列表回传完成，共{total}条")
            
        except Exception as e:
            print(f"[BLE] 扫描失败: {e}")
            self._send_reply(bytes([BLE_CFG.WIFI_SCAN, 0xFF, 0x00, 0x00, 0x00, 0x00]))
    
    def _handle_remote_cmd(self, raw):
        """处理遥控指令"""
        from remote_control import handle_input
        chk = sum(b & 0xFF for b in raw[:5]) & 0xFF
        if chk != raw[5]:
            print(f"[RX-{self._rx_count:03d}] 校验失败")
            self._send_reply(bytes([0xFE]))
            return

        cmd = raw[2]
        cmd_name = _REMOTE_CMD_NAMES.get(cmd, f"KEY_0x{cmd:02X}")
        print(f"[RX-{self._rx_count:03d}] 解析遥控指令: {cmd_name}")

        result = handle_input(cmd)
        print(f"[RX-{self._rx_count:03d}] 遥控处理结果: {_hex_str(result)}")
        self._send_reply(result)

    def _handle_wallpaper_preset(self, raw):
        """
        处理预设壁纸切换指令
        协议格式: [AA] [03] [CMD] [ID] [00] [CHK]
            CMD = 0xA0: 图片背景, ID = 01-08
            CMD = 0xB0: 纯色背景, ID = 01-06
        """
        try:
            # 解析协议
            length = raw[1]
            cmd = raw[2]
            target_id = raw[3]
            reserved = raw[4]

            # 校验长度
            if length != 0x03:
                print(f"[BLE] 壁纸指令长度错误: 0x{length:02X}")
                self._send_reply(bytes([0xFE]))  # 长度错误
                return

            # 校验保留字节
            if reserved != 0x00:
                print(f"[BLE] 壁纸指令保留位错误: 0x{reserved:02X}")
                self._send_reply(bytes([0xFE]))
                return

            # 校验校验和 (前5字节之和)
            chk_calc = sum(raw[:5]) & 0xFF
            chk_recv = raw[5]
            if chk_calc != chk_recv:
                print(f"[BLE] 壁纸指令校验失败: 计算0x{chk_calc:02X} != 接收0x{chk_recv:02X}")
                self._send_reply(bytes([0xFD]))  # 校验错误
                return

            # 获取壁纸管理器
            wp_mgr = _get_wallpaper_mgr()
            if wp_mgr is None:
                print("[BLE] 壁纸管理器未初始化")
                self._send_reply(bytes([0xFF]))
                return

            success = False
            info = ""

            if cmd == _WALLPAPER_IMG_CMD:
                # 图片背景切换
                # ID范围: 01-08
                if target_id < 0x01 or target_id > 0x08:
                    print(f"[BLE] 无效图片ID: {target_id}")
                    self._send_reply(bytes([0xF0]))  # 参数错误
                    return

                print(f"[BLE] 请求切换图片背景 #{target_id}")
                success = wp_mgr.set_image_by_id(target_id)

                if success:
                    info = f"图片 #{target_id}"
                    # 触发壁纸刷新（如果有回调）
                    self._notify_wallpaper_change(_WALLPAPER_MODE_IMAGE, target_id)
                else:
                    info = f"图片 #{target_id} 不存在"
                    print(f"[BLE] {info}")
                    self._send_reply(bytes([0xF1]))  # 资源不存在
                    return

            elif cmd == _WALLPAPER_COLOR_CMD:
                # 纯色背景切换
                # ID范围: 01-16 (支持更多预设颜色)
                if target_id < 0x01 or target_id > 0x10:
                    print(f"[BLE] 无效颜色ID: {target_id}")
                    self._send_reply(bytes([0xF0]))  # 参数错误
                    return

                print(f"[BLE] 请求切换纯色背景 #{target_id}")
                success = wp_mgr.set_color_by_id(target_id)

                if success:
                    color_names = {
                        1: "红色", 2: "橙色", 3: "黄色", 4: "绿色",
                        5: "蓝色", 6: "紫色", 7: "白色", 8: "黑色",
                        9: "深灰", 10: "浅灰", 11: "青色", 12: "粉色",
                        13: "棕色", 14: "暗红", 15: "深蓝", 16: "深绿"
                    }
                    info = color_names.get(target_id, f"颜色#{target_id}")
                    # 触发壁纸刷新（如果有回调）
                    self._notify_wallpaper_change(_WALLPAPER_MODE_SOLID, target_id)
                else:
                    info = f"颜色 #{target_id} 设置失败"
                    print(f"[BLE] {info}")
                    self._send_reply(bytes([0xF1]))
                    return
            else:
                print(f"[BLE] 未知壁纸指令: 0x{cmd:02X}")
                self._send_reply(bytes([0xFE]))  # 未知命令
                return

            # 成功响应: [ACK] [CMD] [ID] [00]
            response = bytes([0x00, cmd, target_id, 0x00])
            self._send_reply(response)
            print(f"[BLE] 壁纸切换成功: {info}")

        except Exception as e:
            print(f"[BLE] 壁纸处理异常: {e}")
            self._send_reply(bytes([0xFF]))  # 通用错误

    def _notify_wallpaper_change(self, mode, target_id):
        """
        通知壁纸变更（用于触发UI刷新回调）
        这里预留回调机制，实际刷新由 main.py 处理
        """
        try:
            # 打印变更信息
            if mode == _WALLPAPER_MODE_IMAGE:
                print(f"[BLE] 壁纸已切换为图片 #{target_id}，UI将自动刷新")
            else:
                color_names = {
                    1: "红色", 2: "橙色", 3: "黄色", 4: "绿色",
                    5: "蓝色", 6: "紫色", 7: "白色", 8: "黑色",
                    9: "深灰", 10: "浅灰", 11: "青色", 12: "粉色",
                    13: "棕色", 14: "暗红", 15: "深蓝", 16: "深绿"
                }
                color_name = color_names.get(target_id, f"颜色#{target_id}")
                print(f"[BLE] 壁纸已切换为 {color_name}，UI将自动刷新")
        except Exception as e:
            print(f"[BLE] 壁纸通知失败: {e}")

    def _handle_screen_cmd(self, raw):
        """
        处理屏幕控制指令
        协议格式: [AA] [04] [CMD] [VAL] [00] [CHK]
            CMD = 0xC0: 屏幕开关 (VAL: 0=关, 1=开)
            CMD = 0xC1: 亮度调节 (VAL: 5-100)
            CMD = 0xC3: 息屏时间 (VAL: 0/10/30/60/300)
        """
        try:
            # 解析协议
            length = raw[1]
            cmd = raw[2]
            value = raw[3]
            reserved = raw[4]

            # 校验长度
            if length != 0x04:
                print(f"[BLE] 屏幕指令长度错误: 0x{length:02X}")
                self._send_reply(bytes([0xFE]))
                return

            # 校验保留字节
            if reserved != 0x00:
                print(f"[BLE] 屏幕指令保留位错误: 0x{reserved:02X}")
                self._send_reply(bytes([0xFE]))
                return

            # 校验校验和 (前5字节之和)
            chk_calc = sum(raw[:5]) & 0xFF
            chk_recv = raw[5]
            if chk_calc != chk_recv:
                print(f"[BLE] 屏幕指令校验失败: 计算0x{chk_calc:02X} != 接收0x{chk_recv:02X}")
                self._send_reply(bytes([0xFD]))
                return

            # 记录活动（用于息屏超时检测）
            screen_ctrl = _get_screen_ctrl()
            if screen_ctrl:
                screen_ctrl.record_activity()

            # 获取屏幕控制器
            if screen_ctrl is None:
                print("[BLE] 屏幕控制器未初始化")
                self._send_reply(bytes([0xFF]))
                return

            success = False
            info = ""

            if cmd == _SCREEN_POWER_CMD:
                # 屏幕开关
                success, info = screen_ctrl.handle_power_cmd(value)
                print(f"[BLE] 屏幕开关: {info}")

            elif cmd == _SCREEN_BRIGHT_CMD:
                # 亮度调节
                success, info = screen_ctrl.handle_brightness_cmd(value)
                print(f"[BLE] 亮度调节: {info}")

            elif cmd == _SCREEN_SLEEP_CMD:
                # 息屏时间
                success, info = screen_ctrl.handle_sleep_cmd(value)
                print(f"[BLE] 息屏时间: {info}")

            else:
                print(f"[BLE] 未知屏幕指令: 0x{cmd:02X}")
                self._send_reply(bytes([0xFE]))
                return

            # 响应
            if success:
                # 成功响应: [00] [CMD] [VAL] [00]
                response = bytes([0x00, cmd, value, 0x00])
                self._send_reply(response)
            else:
                # 失败响应: [错误码]
                self._send_reply(bytes([0xF0]))

        except Exception as e:
            print(f"[BLE] 屏幕处理异常: {e}")
            self._send_reply(bytes([0xFF]))
    
    def process_wifi_switch(self):
        """处理待执行的WiFi命令（主循环调用） - 【新增】处理广播重启"""
        # 【新增】处理待重启广播（延迟500ms避免硬件冲突）
        if self._pending_restart_advertising:
            self._pending_restart_advertising = False
            if self.active:
                print("[BLE] 延迟重启广播...")
                time.sleep_ms(500)  # 关键：给硬件500ms恢复时间
                try:
                    self.start_advertising()
                except OSError as e:
                    print(f"[BLE] 重启广播失败: {e}")
            return  # 本轮不处理其他命令，避免冲突
        
        if self._pending_cmd is None:
            # 无命令时检查状态变更（被动检测）
            self.check_wifi_status_change()
            return
        
        cmd = self._pending_cmd
        self._pending_cmd = None
        
        wifi_mgr = _get_wifi_mgr()
        sd_mgr = _get_sd_manager()
        
        try:
            if cmd == BLE_CFG.WIFI_STA_OLD:
                print(f"[BLE] 执行: 切换到STA模式...")
                #ok = wifi_mgr.switch_sta(use_new=False)
                ok = wifi_mgr.connect_smart()
                status = wifi_mgr.get_status()
                reply = bytes([
                    BLE_CFG.RSP_OK if ok else BLE_CFG.RSP_FAIL,
                    0x01 if status["connected"] else 0x00,
                    status["rssi"] & 0xFF
                ])
                self._send_reply(reply)
                self._force_notify_status()
            
            elif cmd == BLE_CFG.WIFI_STA_NEW:
                print(f"[BLE] 执行: STA_NEW暂不支持")
                self._send_reply(bytes([BLE_CFG.RSP_UNKNOWN]))
            
            elif cmd == BLE_CFG.WIFI_AP:
                print(f"[BLE] 执行: 切换到AP模式...")
                ok = wifi_mgr.switch_ap()
                self._send_reply(bytes([BLE_CFG.RSP_OK if ok else BLE_CFG.RSP_FAIL]))
                if ok:
                    self._force_notify_status()
            
            elif cmd == BLE_CFG.WIFI_OFF:
                print(f"[BLE] 执行: 关闭WiFi...")
                wifi_mgr.shutdown()
                self._send_reply(bytes([BLE_CFG.RSP_OK]))
                self._force_notify_status()
            
            elif cmd == BLE_CFG.WIFI_QUERY:
                print(f"[BLE] 执行: 查询状态...")
                self._send_status_full(wifi_mgr.get_status(), sd_mgr, is_query=True)
            
            else:
                print(f"[BLE] 执行: 未知指令0x{cmd:02X}")
                self._send_reply(bytes([BLE_CFG.RSP_UNKNOWN]))
                
        except Exception as e:
            print(f"[BLE] 处理错误: {e}")
            self._send_reply(bytes([0xFF]))
    
    def check_wifi_status_change(self):
        """检查WiFi状态是否发生变化，如有变化则主动通知"""
        now = time.ticks_ms()
        
        if time.ticks_diff(now, self._last_notify_time) < self._notify_interval_ms:
            return
        
        if not self.conn_handles or not self.notify_enabled:
            return
        
        try:
            wifi_mgr = _get_wifi_mgr()
            current_status = wifi_mgr.get_status()
            
            if self._last_wifi_status is None:
                self._last_wifi_status = current_status.copy()
                return
            
            changed = False
            
            if current_status["mode"] != self._last_wifi_status.get("mode"):
                changed = True
                print(f"[BLE] 模式变更: {self._last_wifi_status.get('mode')} -> {current_status['mode']}")
            
            elif current_status["connected"] != self._last_wifi_status.get("connected"):
                changed = True
                print(f"[BLE] 连接状态变更: {current_status['connected']}")
            
            elif current_status["ip"] != self._last_wifi_status.get("ip"):
                changed = True
                print(f"[BLE] IP变更: {current_status['ip']}")
            
            elif abs(current_status["rssi"] - self._last_wifi_status.get("rssi", 0)) > 10:
                changed = True
                print(f"[BLE] 信号变更: {current_status['rssi']}dBm")
            
            if changed:
                self._notify_status_change(current_status)
                self._last_wifi_status = current_status.copy()
                self._last_notify_time = now
                
        except Exception as e:
            print(f"[BLE] 状态检查错误: {e}")
    
    def _force_notify_status(self):
        """强制发送当前状态"""
        if not self.conn_handles or not self.notify_enabled:
            return

        try:
            wifi_mgr = _get_wifi_mgr()
            status = wifi_mgr.get_status()
            self._notify_status_change(status)
            self._last_wifi_status = status.copy()
            self._last_notify_time = time.ticks_ms()
        except Exception as e:
            print(f"[BLE] 强制通知失败: {e}")

    def _build_status_base(self, status):
        """
        【优化】构建状态包的基础字节（IP、SSID、文件数等）
        返回: tuple (mode_byte, conn_byte, ip0-3, ssid0-3, file_count_h, file_count_l, sd_status, page_byte)
        失败返回: None
        """
        try:
            sd_mgr = _get_sd_manager()

            mode_byte = status["mode"]
            conn_byte = 0x02 if status["connected"] else (0x01 if mode_byte != 0 else 0x00)

            # IP解析
            ip_parts = [0, 0, 0, 0]
            if status["ip"] and status["ip"] != "0.0.0.0":
                try:
                    ip_parts = [int(x) for x in status["ip"].split(".")]
                except:
                    pass

            # SSID处理（手动填充到4字节）
            ssid = status["ssid"]
            if isinstance(ssid, bytes):
                ssid_b = ssid[:4]
            else:
                ssid_b = ssid.encode('utf-8')[:4]
            ssid_bytes = ssid_b + b'\x00' * (4 - len(ssid_b))

            # 文件数
            try:
                ok, items, _ = sd_mgr.list()
                file_count = len([i for i in items if not i["is_dir"]]) if ok else 0
            except:
                file_count = 0

            sd_status = 0x01 if sd_mgr.mounted else 0x00

            from remote_control import get_page
            page_byte = 0x01 if get_page() == "files" else 0x00

            return (mode_byte, conn_byte,
                    ip_parts[0], ip_parts[1], ip_parts[2], ip_parts[3],
                    ssid_bytes[0], ssid_bytes[1], ssid_bytes[2], ssid_bytes[3],
                    (file_count >> 8) & 0xFF, file_count & 0xFF,
                    sd_status, page_byte)

        except Exception as e:
            print(f"[BLE] 状态基础构建失败: {e}")
            return None
    
    def _notify_status_change(self, status):
        """发送状态变更通知（0x35命令字）"""
        try:
            # 【优化】使用公共函数构建状态字节
            base = self._build_status_base(status)
            if base is None:
                return

            # 构建通知数据（命令字 0x35 + 基本状态）
            rssi = status.get("rssi", 0)
            if rssi < 0:
                rssi = 256 + rssi

            current_idx = status.get("current_index", -1)
            if current_idx < 0 or current_idx > 254:
                current_idx = 0xFF

            data = bytes([
                0x35,  # 命令字
                base[0],  # mode_byte
                base[1],  # conn_byte
                base[2], base[3], base[4], base[5],  # IP
                base[6], base[7], base[8], base[9],  # SSID
                base[10], base[11],  # file_count
                base[12],  # sd_status
                base[13],  # page_byte
                current_idx,
                rssi & 0xFF
            ])

            self._send_reply(data)
            print(f"[BLE] 状态已通知: {status['mode_str']}/{status['ip']}")

        except Exception as e:
            print(f"[BLE] 通知构建失败: {e}")

    def _send_status_full(self, status, sd_mgr, is_query=False):
        """响应0x34查询"""
        try:
            # 【优化】使用公共函数构建状态字节
            base = self._build_status_base(status)
            if base is None:
                return

            # 当前连接索引
            current_idx = status.get("current_index", -1)
            if current_idx < 0:
                current_idx = 0xFF

            reply = bytes([
                base[0], base[1],  # mode_byte, conn_byte
                base[2], base[3], base[4], base[5],  # IP
                base[6], base[7], base[8], base[9],  # SSID
                base[10], base[11],  # file_count
                base[12], base[13],  # sd_status, page_byte
                current_idx,
                0x00
            ])
            self._send_reply(reply)

            if is_query:
                self._last_wifi_status = status.copy()
        except Exception as e:
            print(f"[BLE] 查询响应失败: {e}")
    
    def _send_reply(self, data):
        """发送数据到所有连接的设备（带Shell打印）"""
        if not self.conn_handles:
            print("[TX] 无连接，数据未发送")
            return
        
        self._tx_count += 1
        print(f"[TX-{self._tx_count:03d}] {_hex_str(data)} (长度:{len(data)})")
        
        for handle in self.conn_handles:
            for i in range(0, len(data), 20):
                chunk = data[i:i+20]
                self.ble.gatts_notify(handle, self.char_h, chunk)
                time.sleep_ms(5)


class BLEDataAssembler:
    """
    通用BLE数据分包重组器
    自动处理：多会话管理、超时清理、去重、重组、流式保存到/api.json
    """
    def __init__(self):
        self.sessions = {}
        self.on_complete = None
        self.on_progress = None
        self.on_stream = None
        self.mode = 'buffer'
        self._last_print_progress = {}
    
    def set_callbacks(self, on_complete=None, on_progress=None, on_stream=None, mode='buffer'):
        self.on_complete = on_complete
        self.on_progress = on_progress
        self.on_stream = on_stream
        self.mode = mode
    
    def _stream_write_to_file(self, session_id, seq, data, is_last, total_pkts):
        """流式写入文件：SEQ=0覆盖创建，SEQ>0追加"""
        try:
            # 根据 session_id（数据类型）决定文件名
            if session_id == 2:
                filename = 'api.json'
            elif session_id == 3:
                filename = 'gpio.json'
            else:
                filename = f'config_{session_id}.json'
            filepath = f'/{filename}'

            mode = 'wb' if seq == 0 else 'ab'
            with open(filepath, mode) as f:
                f.write(data)

            if total_pkts > 0:
                progress = ((seq + 1) * 100) // total_pkts
                last_print = self._last_print_progress.get(session_id, -10)

                if progress - last_print >= 10 or seq == 0 or is_last:
                    if is_last:
                        print(f"[BLE-DATA] 流式完成 [{session_id}]: {progress}%，已保存 {filepath}")
                    else:
                        print(f"[BLE-DATA] 流式写入 [{session_id}]: 包#{seq} ({len(data)}字节)")
                    self._last_print_progress[session_id] = progress

            if is_last and session_id in self._last_print_progress:
                del self._last_print_progress[session_id]

        except Exception as e:
            print(f"[BLE-DATA] 流式写入失败 [{session_id}]: {e}")
    
    def process_packet(self, raw_data):
        """处理通用数据包（流式保存版）"""
        if len(raw_data) < 5:
            return False, "TOO_SHORT"
        
        cmd = raw_data[0]
        if cmd != CMD_DATA_TRANSFER:
            return False, "NOT_DATA_CMD"
        
        session_id = raw_data[1]
        seq = raw_data[2]
        payload_len = (raw_data[3] << 8) | raw_data[4]
        payload = raw_data[5:5+payload_len]
        
        if session_id not in self.sessions:
            if len(self.sessions) >= MAX_SESSIONS:
                self._cleanup_oldest()
            self.sessions[session_id] = DataSession(session_id)
            print(f"[BLE-DATA] 新建会话 [{session_id}]")
        
        session = self.sessions[session_id]
        session.state = 'RECEIVING'
        
        is_first = (seq == 0)
        is_last = (seq == 255)
        
        if is_first and len(payload) >= 2:
            session.total_pkts = payload[0]
            meta_len = payload[1]
            if meta_len > 0 and len(payload) >= 2 + meta_len:
                try:
                    import json
                    session.meta = json.loads(payload[2:2+meta_len])
                except:
                    session.meta = {'raw': payload[2:2+meta_len]}
            data_start = 2 + meta_len
            actual_payload = payload[data_start:]
        else:
            actual_payload = payload
            if is_last and len(actual_payload) > 0:
                session.total_pkts = actual_payload[0]
                actual_payload = actual_payload[1:]
        
        is_new = session.add_packet(seq, actual_payload)
        if not is_new:
            return True, f"SEQ_{seq}_DUPLICATE"
        
        total_pkts = session.total_pkts if session.total_pkts > 0 else 0
        
        if self.mode == 'stream' and self.on_stream:
            self.on_stream(session_id, seq, actual_payload, is_last, session.meta)
        else:
            self._stream_write_to_file(session_id, seq, actual_payload, is_last, total_pkts)
        
        if is_last or (total_pkts > 0 and session.received >= total_pkts):
            session.clear()
            if session_id in self.sessions:
                del self.sessions[session_id]
            return True, "COMPLETE_STREAM"
        
        if total_pkts > 0 and self.on_progress:
            progress = (session.received * 100) // total_pkts
            self.on_progress(session_id, progress, f"{session.received}/{total_pkts}")
        
        return True, f"SEQ_{seq}_STREAMED"
    
    def tick(self):
        """清理超时会话"""
        expired = []
        for sid, session in self.sessions.items():
            if session.is_expired():
                expired.append(sid)
        
        for sid in expired:
            print(f"[BLE-DATA] 会话 [{sid}] 超时清理")
            self.sessions[sid].clear()
            del self.sessions[sid]
            if sid in self._last_print_progress:
                del self._last_print_progress[sid]
        
        if expired:
            gc.collect()
    
    def _cleanup_oldest(self):
        """清理最老的会话"""
        if not self.sessions:
            return
        oldest_id = min(self.sessions.keys(), key=lambda k: self.sessions[k].last_time)
        print(f"[BLE-DATA] 会话数超限，清理 [{oldest_id}]")
        self.sessions[oldest_id].clear()
        del self.sessions[oldest_id]
        if oldest_id in self._last_print_progress:
            del self._last_print_progress[oldest_id]
        gc.collect()

    def reset(self):
        """重置所有会话"""
        for session in self.sessions.values():
            session.clear()
        self.sessions.clear()
        self._last_print_progress.clear()
        gc.collect()


# 全局实例
ble_ctrl = BLEController()
data_assembler = BLEDataAssembler()

# 便捷函数
def ble_on():
    ble_ctrl.start_advertising()

def ble_off():
    ble_ctrl.stop_advertising()

def process_wifi_switch(): 
    """主循环调用：处理命令 + 检查状态变更"""
    ble_ctrl.process_wifi_switch()

def check_wifi_status():
    """主循环定期调用（如每2秒），用于检测非命令触发的状态变化"""
    ble_ctrl.check_wifi_status_change()

def get_status():
    return {
        "active": ble_ctrl.active,
        "connected": len(ble_ctrl.conn_handles) > 0,
        "notify": ble_ctrl.notify_enabled
    }

def get_data_assembler():
    """获取数据组装器实例以设置回调"""
    return data_assembler

def tick_data_manager():
    """数据管理器tick（主循环调用）"""
    data_assembler.tick()