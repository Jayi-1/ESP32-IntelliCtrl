"""
通用BLE数据分包管理器
自动处理分包接收、重组、超时清理
适用于：文件传输、JSON配置、固件更新等大/小数据传输
"""
import time
import gc
from micropython import const

# 协议常量
_DATA_PREFIX = const(0xDD)      # 通用数据传输命令字（与0xBB遥控、0xCC WiFi区分）
_MAX_SESSIONS = const(4)        # 最大并发会话数（防内存耗尽）
_MAX_PKT_SIZE = const(20)       # BLE默认MTU 20字节（含头部）
_HDR_SIZE = const(5)            # 头部5字节
_PAYLOAD_MAX = const(_MAX_PKT_SIZE - _HDR_SIZE)  # 15字节载荷

# 会话状态
_STATE_IDLE = const(0)
_STATE_RECEIVING = const(1)
_STATE_COMPLETE = const(2)

class DataSession:
    """单个数据传输会话"""
    __slots__ = ('session_id', 'total_pkts', 'received', 'buffer', 
                 'state', 'last_time', 'cmd_type', 'meta')
    
    def __init__(self, session_id, cmd_type=0):
        self.session_id = session_id
        self.cmd_type = cmd_type      # 业务类型（如'FILE', 'CONFIG', 'CLIP'）
        self.total_pkts = 0           # 总包数（由首包或后续包确定）
        self.received = 0           # 已收包数
        self.buffer = {}            # 包缓存 {seq: bytes}
        self.state = _STATE_IDLE
        self.last_time = time.ticks_ms()
        self.meta = {}              # 元数据（如文件名、总大小等，首包可传）
    
    def update_activity(self):
        self.last_time = time.ticks_ms()
    
    def is_expired(self, timeout_ms=30000):
        return time.ticks_diff(time.ticks_ms(), self.last_time) > timeout_ms
    
    def add_packet(self, seq, data):
        """添加数据包，返回是否为新包"""
        if seq in self.buffer:
            return False  # 重复包
        self.buffer[seq] = data
        self.received += 1
        self.update_activity()
        return True
    
    def get_progress(self):
        if self.total_pkts == 0:
            return 0
        return (self.received * 100) // self.total_pkts
    
    def is_complete(self):
        """检查是否收齐所有包"""
        if self.total_pkts == 0:
            return False
        return self.received >= self.total_pkts
    
    def assemble(self):
        """重组完整数据（假设包序号连续从0开始）"""
        if not self.is_complete():
            return None
        # 按序号排序重组
        result = bytearray()
        for i in range(self.total_pkts):
            if i in self.buffer:
                result.extend(self.buffer[i])
            else:
                return None  # 缺包（理论上不会，因为is_complete已检查）
        return bytes(result)
    
    def clear(self):
        """释放内存"""
        self.buffer.clear()
        self.state = _STATE_IDLE
        gc.collect()

class BLEDataManager:
    """
    BLE通用数据接收管理器
    用法：
        1. 在bluetooth_ctrl._handle_write中调用process_raw()
        2. 注册on_complete回调处理完整数据
        3. 在主循环调用tick()清理超时会话
    """
    
    def __init__(self, timeout_ms=30000, mode='buffer'):
        """
        mode: 'buffer'=缓存模式（收齐回调）, 'stream'=流模式（逐包回调）
        """
        self.sessions = {}          # {session_id: DataSession}
        self.timeout_ms = timeout_ms
        self.mode = mode
        self.on_complete = None     # 完整数据回调: func(session_id, data, meta)
        self.on_progress = None     # 进度回调: func(session_id, progress_pct, received/total)
        self.on_stream = None       # 流模式回调: func(session_id, seq, data, is_last)
        self._max_sessions = _MAX_SESSIONS
    
    def register_callbacks(self, on_complete=None, on_progress=None, on_stream=None):
        """注册回调函数"""
        self.on_complete = on_complete
        self.on_progress = on_progress
        self.on_stream = on_stream
    
    def process_raw(self, raw_data):
        """
        处理原始BLE数据（在bluetooth_ctrl._handle_write中调用）
        raw_data: bytes（完整蓝牙包，包含0xDD头部）
        返回: (handled:bool, info:str)
        """
        if len(raw_data) < _HDR_SIZE or raw_data[0] != _DATA_PREFIX:
            return False, "NOT_DATA_PKT"
        
        # 解析头部
        cmd = raw_data[0]
        session_id = raw_data[1]
        seq = raw_data[2]           # 0=首包, 1-254=数据, 255=结束标记（可选）
        payload_len = (raw_data[3] << 8) | raw_data[4]
        
        # 提取载荷（考虑实际长度可能小于声明长度，取最小值）
        actual_payload = raw_data[_HDR_SIZE:_HDR_SIZE + payload_len]
        
        # 会话管理
        if session_id not in self.sessions:
            if len(self.sessions) >= self._max_sessions:
                # 清理最老的会话
                self._cleanup_oldest()
            self.sessions[session_id] = DataSession(session_id, cmd_type=cmd)
            print(f"[BLE-DATA] 新建会话 [{session_id}]")
        
        session = self.sessions[session_id]
        
        # 首包特殊处理（可包含元数据如文件名、总包数等）
        if seq == 0:
            # 首包前4字节可定义为：[TOTAL_PKTS, META_LEN, META...]
            if len(actual_payload) >= 2:
                session.total_pkts = actual_payload[0]
                meta_len = actual_payload[1]
                if meta_len > 0 and len(actual_payload) >= 2 + meta_len:
                    try:
                        import json
                        session.meta = json.loads(actual_payload[2:2+meta_len])
                    except:
                        session.meta = {'raw_meta': actual_payload[2:2+meta_len]}
                # 剩余为首包数据
                data_start = 2 + meta_len
                payload = actual_payload[data_start:]
            else:
                payload = actual_payload
                session.total_pkts = 0  # 未知，等待后续包或结束标记
        else:
            payload = actual_payload
        
        # 更新总包数（如果首包没传，从其他包推断或直到收到结束标记）
        if seq == 255:  # 结束标记包，包含总包数
            session.total_pkts = payload[0] if payload else session.received
        
        # 添加包到会话
        is_new = session.add_packet(seq, payload)
        
        # 流模式：立即回调
        if self.mode == 'stream' and self.on_stream and is_new:
            is_last = (seq == 255) or (session.total_pkts > 0 and session.received >= session.total_pkts)
            self.on_stream(session_id, seq, payload, is_last, session.meta)
        
        # 进度回调
        if self.on_progress and is_new:
            progress = session.get_progress()
            self.on_progress(session_id, progress, f"{session.received}/{session.total_pkts}")
        
        # 检查完成（缓存模式）
        if self.mode == 'buffer' and session.is_complete():
            complete_data = session.assemble()
            if complete_data and self.on_complete:
                self.on_complete(session_id, complete_data, session.meta)
            session.clear()
            del self.sessions[session_id]
            return True, "COMPLETE"
        
        return True, f"OK SEQ={seq}"
    
    def _cleanup_oldest(self):
        """清理最老的超时会话"""
        oldest_id = None
        oldest_time = time.ticks_ms()
        
        for sid, session in self.sessions.items():
            if time.ticks_diff(time.ticks_ms(), session.last_time) > 0:
                if session.last_time < oldest_time:
                    oldest_time = session.last_time
                    oldest_id = sid
        
        if oldest_id is not None:
            print(f"[BLE-DATA] 清理老会话 [{oldest_id}]")
            self.sessions[oldest_id].clear()
            del self.sessions[oldest_id]
            gc.collect()
    
    def tick(self):
        """主循环调用，清理超时会话"""
        now = time.ticks_ms()
        expired = []
        for sid, session in self.sessions.items():
            if session.is_expired(self.timeout_ms):
                expired.append(sid)
        
        for sid in expired:
            print(f"[BLE-DATA] 会话 [{sid}] 超时清理")
            self.sessions[sid].clear()
            del self.sessions[sid]
        
        if expired:
            gc.collect()
    
    def force_cleanup(self, session_id=None):
        """强制清理指定或所有会话"""
        if session_id is not None:
            if session_id in self.sessions:
                self.sessions[session_id].clear()
                del self.sessions[session_id]
        else:
            for session in self.sessions.values():
                session.clear()
            self.sessions.clear()
        gc.collect()

# ========== 便捷函数与示例用法 ==========

def create_file_receiver(sd_manager, callback):
    """
    创建文件接收器（缓冲模式示例）
    接收完成后自动写入SD卡
    """
    def on_complete(session_id, data, meta):
        filename = meta.get('filename', f'RECV_{session_id}.bin')
        try:
            filepath = f'/sd/{filename}'
            with open(filepath, 'wb') as f:
                f.write(data)
            print(f"[FILE-RECV] 会话[{session_id}] 已保存 {filename} ({len(data)}bytes)")
            if callback:
                callback(True, filename, len(data))
        except Exception as e:
            print(f"[FILE-RECV] 保存失败: {e}")
            if callback:
                callback(False, str(e), 0)
    
    def on_progress(sid, pct, detail):
        print(f"[FILE-RECV] 会话[{sid}] 进度: {pct}% ({detail})")
    
    manager = BLEDataManager(mode='buffer')
    manager.register_callbacks(on_complete=on_complete, on_progress=on_progress)
    return manager

def create_stream_receiver(sd_manager, callback):
    """
    创建流式接收器（适合大文件，边收边写）
    """
    current_files = {}  # {session_id: file_handle}
    
    def on_stream(session_id, seq, data, is_last, meta):
        if seq == 0:  # 首包，打开文件
            filename = meta.get('filename', f'STREAM_{session_id}.bin')
            filepath = f'/sd/{filename}'
            try:
                current_files[session_id] = open(filepath, 'wb')
                print(f"[STREAM] 开始接收 [{session_id}] -> {filename}")
            except Exception as e:
                print(f"[STREAM] 打开文件失败: {e}")
                return
        
        # 写入数据
        if session_id in current_files:
            current_files[session_id].write(data)
            if seq % 10 == 0:  # 每10包同步一次
                import os
                if hasattr(os, 'sync'):
                    os.sync()
        
        # 结束关闭
        if is_last:
            if session_id in current_files:
                current_files[session_id].close()
                del current_files[session_id]
                print(f"[STREAM] 完成 [{session_id}]")
                if callback:
                    callback(True, session_id)
    
    manager = BLEDataManager(mode='stream')
    manager.register_callbacks(on_stream=on_stream)
    return manager

# 全局实例（供集成）
data_manager = None

def init_manager(mode='buffer', on_complete=None, on_progress=None):
    """初始化全局数据管理器"""
    global data_manager
    data_manager = BLEDataManager(mode=mode)
    data_manager.register_callbacks(on_complete, on_progress)
    return data_manager

def process_ble_data(raw_data):
    """供bluetooth_ctrl调用的接口"""
    global data_manager
    if data_manager:
        return data_manager.process_raw(raw_data)
    return False, "NOT_INIT"