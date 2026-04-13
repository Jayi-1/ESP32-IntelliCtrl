"""
ESP32-IntelliCtrl - WiFi管理模块
MIT License | Copyright (c) 2026 ESP32-IntelliCtrl
处理WiFi连接、配网和AP模式
"""
import network
import time
import gc
import json
import os
from micropython import const
from config import WIFI_CFG, SYS_CFG

# 模式常量（保留在 mgr 中，与蓝牙协议常量区分开）
MODE_OFF = const(0)
MODE_STA = const(1)
MODE_AP = const(2)

# WiFi 配置文件名
WIFI_CONFIG_FILE = "wifi.json"
LAST_WIFI_FILE = "last_wifi.json"  # 新增：记录上次成功的索引

class WiFiManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._sta = network.WLAN(network.STA_IF)
        self._ap = network.WLAN(network.AP_IF)
        self._current_mode = MODE_OFF
        self._current_ssid = ""
        self._current_index = -1  # 当前连接的 WiFi 索引（-1表示无）
        self._rssi = 0
        self._config_cache = None  # 配置缓存
        self._initialized = True
    
    def _hard_reset(self):
        """彻底关闭WiFi，释放射频资源"""
        try:
            if self._sta.active():
                self._sta.config(reconnects=0)
                if self._sta.isconnected():
                    self._sta.disconnect()
                time.sleep_ms(200)
                self._sta.active(False)
            
            if self._ap.active():
                self._ap.active(False)
            
            time.sleep_ms(500)
            gc.collect()
            return True
        except Exception as e:
            print(f"[WiFi] 重置错误: {e}")
            return False

    # ==================== 新增：上次成功索引的持久化 ====================
    
    def _load_last_index(self):
        """从文件读取上次成功连接的 WiFi 索引"""
        try:
            if LAST_WIFI_FILE in os.listdir():
                with open(LAST_WIFI_FILE, 'r') as f:
                    data = json.load(f)
                    return data.get('last_index', 0)
        except Exception as e:
            print(f"[WiFi] 读取上次索引失败: {e}")
        return 0
    
    def _save_last_index(self, index):
        """保存当前成功连接的 WiFi 索引到文件"""
        try:
            with open(LAST_WIFI_FILE, 'w') as f:
                json.dump({'last_index': index}, f)
        except Exception as e:
            print(f"[WiFi] 保存索引失败: {e}")
    
    # ==================== 配置管理（列表操作）====================
    
    def _load_config(self):
        """
        加载 WiFi 配置列表（自动兼容旧版单条格式）
        返回: [{'ssid': str, 'password': str}, ...]
        """
        if self._config_cache is not None:
            return self._config_cache
            
        try:
            if WIFI_CONFIG_FILE in os.listdir():
                with open(WIFI_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 兼容旧版：如果是字典（单条），转为列表
                    if isinstance(data, dict):
                        data = [data]
                    elif not isinstance(data, list):
                        data = []
                    self._config_cache = data
            else:
                self._config_cache = []
        except Exception as e:
            print(f"[WiFi] 加载配置失败: {e}")
            self._config_cache = []
        
        return self._config_cache
    
    def _save_config(self, configs):
        """保存配置列表到文件"""
        try:
            with open(WIFI_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(configs, f)
            self._config_cache = configs  # 更新缓存
            return True
        except Exception as e:
            print(f"[WiFi] 保存配置失败: {e}")
            return False
    
    def reload_config(self):
        """强制刷新配置缓存（供外部调用）"""
        self._config_cache = None
        gc.collect()
        return self._load_config()
    
    def get_ssid_list(self):
        """
        获取已保存的 SSID 列表（仅名称，供蓝牙传输）
        返回: ['ssid1', 'ssid2', ...]
        """
        configs = self._load_config()
        return [item.get("ssid", "") for item in configs if item.get("ssid")]
    
    def get_wifi_count(self):
        """获取保存的 WiFi 数量"""
        return len(self._load_config())
    
    def add_wifi(self, ssid, password):
        """
        添加/更新 WiFi 配置（不连接）
        返回: (success:bool, index:int) index为新添加或更新的位置
        """
        if not ssid:
            return False, -1
            
        configs = self._load_config()
        
        # 检查是否已存在，存在则更新
        for i, cfg in enumerate(configs):
            if cfg.get("ssid") == ssid:
                configs[i]["password"] = password
                if self._save_config(configs):
                    return True, i
                return False, -1
        
        # 不存在则追加
        configs.append({"ssid": ssid, "password": password})
        if self._save_config(configs):
            return True, len(configs) - 1
        return False, -1
    
    def del_wifi(self, index):
        """
        删除指定索引的 WiFi
        如果删除的是当前连接的，会先断开
        返回: success:bool
        """
        configs = self._load_config()
        
        if index < 0 or index >= len(configs):
            return False
        
        deleted_ssid = configs[index].get("ssid", "")
        
        # 如果删除的是当前连接的，断开连接
        if self._current_mode == MODE_STA and self._current_index == index:
            self.turn_off()
        
        configs.pop(index)
        
        # 如果删除的是当前连接的索引，清空状态
        if self._current_index == index:
            self._current_index = -1
            self._current_ssid = ""
        # 如果删除的索引小于当前索引，当前索引前移
        elif self._current_index > index:
            self._current_index -= 1
            
        return self._save_config(configs)
    
    # ==================== 连接控制 ====================
    
    def connect_by_index(self, index, timeout_sec=None):
        """
        根据索引连接已保存的 WiFi（供蓝牙指令调用）
        index: 0-based 索引
        timeout_sec: 超时时间（秒），默认使用配置
        返回: (success:bool, message:str)
        """
        configs = self._load_config()
        
        if index < 0 or index >= len(configs):
            return False, "索引超出范围"
        
        cfg = configs[index]
        ssid = cfg.get("ssid", "")
        pwd = cfg.get("password", "")
        
        if not ssid:
            return False, "SSID为空"
        
        # 执行连接
        result = self._do_connect_sta(ssid, pwd, timeout_sec)
        
        if result[0]:
            self._current_index = index
            self._current_ssid = ssid
            self._save_last_index(index)  # 新增：记住这次成功的索引
        
        return result
    
    def connect_new_wifi(self, ssid, password):
        """
        连接新 WiFi（带保存）
        供 switch_sta(use_new=True) 和外部调用
        返回: (success:bool, message:str)
        """
        if not ssid:
            return False, "SSID不能为空"
        
        # 先尝试连接
        result = self._do_connect_sta(ssid, password)
        
        if result[0]:
            # 连接成功，保存到列表（自动去重/更新）
            success, index = self.add_wifi(ssid, password)
            if success:
                self._current_index = index
                self._save_last_index(index)  # 记住索引
            self._current_ssid = ssid
        
        return result
    
    def _do_connect_sta(self, ssid, pwd, timeout_sec=None):
        """底层 STA 连接实现"""
        timeout = (timeout_sec or getattr(WIFI_CFG, 'STA_TIMEOUT_S', 10)) * 2
        
        for attempt in range(getattr(WIFI_CFG, 'STA_RETRY', 3)):
            try:
                self._hard_reset()
                time.sleep_ms(200)
                
                self._sta.active(True)
                time.sleep_ms(800)
                
                print(f"[WiFi] 连接 {ssid}... (尝试 {attempt+1})")
                self._sta.connect(ssid, pwd)
                
                for i in range(timeout):
                    if self._sta.isconnected():
                        self._current_mode = MODE_STA
                        self._update_rssi()
                        ip = self._sta.ifconfig()[0]
                        print(f"[WiFi] 已连接: {ip}")
                        gc.collect()
                        return True, ip
                    time.sleep_ms(500)
                
                self._sta.disconnect()
                time.sleep_ms(500)
                
            except Exception as e:
                print(f"[WiFi] STA错误: {e}")
        
        return False, "连接超时"
    
    # ==================== 新增：智能连接（记住上次） ====================
    
    def connect_smart(self, timeout_per_wifi=10):
        """
        智能连接策略：
        1. 先尝试上次成功连接的 WiFi（快速连接）
        2. 如果失败，从索引 0 开始轮询所有 WiFi
        3. 成功后记住新的索引
        
        返回: (success:bool, connected_index:int) 
        """
        configs = self._load_config()
        if not configs:
            print("[WiFi] 没有保存的 WiFi 配置")
            return False, -1
        
        total = len(configs)
        last_index = self._load_last_index()
        
        # 确保上次索引在有效范围内
        if last_index >= total:
            last_index = 0
        
        print(f"[WiFi] 智能连接：优先尝试上次成功的索引 [{last_index}]...")
        
        # 第 1 步：先尝试上次成功的
        if 0 <= last_index < total:
            cfg = configs[last_index]
            ssid = cfg.get("ssid", "")
            pwd = cfg.get("password", "")
            
            if ssid and self._do_connect_sta(ssid, pwd, timeout_per_wifi)[0]:
                self._current_index = last_index
                self._current_ssid = ssid
                print(f"[WiFi] 上次成功的 WiFi [{last_index}] {ssid} 连接成功")
                return True, last_index
            else:
                print(f"[WiFi] 上次成功的 WiFi 已不可用")
        
        # 第 2 步：失败则从 0 开始轮询（跳过刚才试过的 last_index）
        print(f"[WiFi] 开始轮询所有 {total} 个 WiFi...")
        for idx in range(total):
            if idx == last_index:  # 跳过刚才已经试过的
                continue
                
            cfg = configs[idx]
            ssid = cfg.get("ssid", "")
            pwd = cfg.get("password", "")
            
            if not ssid:
                continue
                
            print(f"[WiFi] 尝试 [{idx}] {ssid}...")
            
            if self._do_connect_sta(ssid, pwd, timeout_per_wifi)[0]:
                self._current_index = idx
                self._current_ssid = ssid
                self._save_last_index(idx)  # 记住新的成功索引
                print(f"[WiFi] 成功连接到 [{idx}] {ssid}，已记住此索引")
                return True, idx
            else:
                print(f"[WiFi] [{idx}] {ssid} 失败，继续...")
        
        print("[WiFi] 所有 WiFi 均连接失败")
        return False, -1
    
    def switch_ap(self) -> bool:
        """启动AP模式（供蓝牙指令 WIFI_AP 调用）"""
        if self._current_mode == MODE_AP and self._ap.active():
            return True
        
        try:
            self._hard_reset()
            time.sleep_ms(200)
            
            self._ap.active(True)
            time.sleep_ms(100)
            
            self._ap.config(
                essid=WIFI_CFG.AP_SSID,
                password=WIFI_CFG.AP_PASS,
                authmode=network.AUTH_WPA_WPA2_PSK,
                channel=WIFI_CFG.AP_CHAN,
                max_clients=4
            )
            
            timeout = 50
            while not self._ap.active() and timeout > 0:
                time.sleep_ms(100)
                timeout -= 1
            
            if self._ap.active():
                self._current_mode = MODE_AP
                self._current_ssid = WIFI_CFG.AP_SSID
                self._current_index = -1  # AP模式无索引
                print(f"[WiFi] AP就绪: {self._ap.ifconfig()[0]}")
                return True
            return False
            
        except Exception as e:
            print(f"[WiFi] AP启动失败: {e}")
            return False
    
    def switch_sta(self, use_new=False, config=None, start_index=0) -> bool:
        """
        切换STA模式，支持多WiFi自动轮询
        use_new=True: 使用 config={'ssid': 'xxx', 'password': 'yyy'} 连接新WiFi
        use_new=False: 从 start_index 开始轮询所有已保存的WiFi，直到有一个连接成功
        start_index: 轮询起始索引（默认0，即从头开始）
        返回: bool（是否有任何一个WiFi连接成功）
        """
        if use_new and config:
            return self.connect_new_wifi(config['ssid'], config['password'])[0]
        
        # 自动轮询所有已保存的 WiFi，从 start_index 开始
        configs = self._load_config()
        if not configs:
            print("[WiFi] 没有保存的 WiFi 配置")
            return False
        
        total = len(configs)
        for offset in range(total):
            idx = (start_index + offset) % total  # 循环遍历，支持从任意位置开始
            cfg = configs[idx]
            ssid = cfg.get("ssid", "")
            pwd = cfg.get("password", "")
            
            if not ssid:
                continue
                
            print(f"[WiFi] 尝试连接 [{idx}] {ssid}...")
            result = self._do_connect_sta(ssid, pwd)
            
            if result[0]:
                self._current_index = idx
                self._current_ssid = ssid
                self._save_last_index(idx)  # 记住成功的索引
                print(f"[WiFi] 成功连接到 [{idx}] {ssid}")
                return True
            else:
                print(f"[WiFi] [{idx}] {ssid} 连接失败，尝试下一个...")
        
        print("[WiFi] 所有 WiFi 均连接失败")
        return False
    
    def turn_off(self):
        """关闭WiFi"""
        self._hard_reset()
        self._current_mode = MODE_OFF
        self._current_ssid = ""
        self._current_index = -1
        self._rssi = 0
    
    shutdown = turn_off  # 别名
    
    # ==================== 状态查询 ====================
    
    def get_status(self):
        """获取当前WiFi详细状态（供蓝牙状态查询调用）"""
        self._update_rssi()
        
        status = {
            "mode": self._current_mode,
            "mode_str": ["OFF", "STA", "AP"][self._current_mode],
            "connected": False,
            "ssid": "",
            "ip": "0.0.0.0",
            "rssi": 0,
            "current_index": self._current_index,  # 新增：当前连接索引（-1表示无）
            "saved_count": self.get_wifi_count()   # 新增：已保存数量
        }
        
        if self._current_mode == MODE_STA and self._sta.isconnected():
            status["connected"] = True
            status["ssid"] = self._current_ssid
            status["ip"] = self._sta.ifconfig()[0]
            status["rssi"] = self._rssi
        elif self._current_mode == MODE_AP and self._ap.active():
            status["connected"] = True
            status["ssid"] = self._current_ssid
            status["ip"] = self._ap.ifconfig()[0]
        
        return status
    
    def get_current_ssid(self):
        """获取当前SSID"""
        return self._current_ssid
    
    def _update_rssi(self):
        """更新信号强度（仅STA模式有效）"""
        if self._sta.isconnected():
            try:
                self._rssi = self._sta.status('rssi')
            except:
                self._rssi = 0

# 创建全局实例
wifi_mgr = WiFiManager()


def init(default_ap=True):
    """初始化WiFi并启动默认模式"""
    return wifi_mgr.switch_ap() if default_ap else wifi_mgr.switch_sta()


def connect(ssid, pwd):
    """连接指定WiFi并保存配置（对外接口）"""
    return wifi_mgr.connect_new_wifi(ssid, pwd)


def switch_ap():
    """切换到AP热点模式"""
    return wifi_mgr.switch_ap()


def switch_sta(use_new=False, config=None, start_index=0):
    """
    切换到STA模式（普通轮询模式，如需智能模式请用 connect_smart()）
    """
    return wifi_mgr.switch_sta(use_new, config, start_index)


def shutdown():
    """完全关闭WiFi射频模块"""
    wifi_mgr.shutdown()


# ==================== 新增：供外部调用的智能接口 ====================

def connect_smart():
    """
    智能连接：优先尝试上次成功的 WiFi，失败再轮询所有
    这是开机时推荐的调用方式
    """
    return wifi_mgr.connect_smart()

def connect_by_index(index):
    """通过索引连接（供蓝牙指令）"""
    return wifi_mgr.connect_by_index(index)

def add_wifi(ssid, pwd):
    """添加WiFi到列表（供蓝牙指令）"""
    return wifi_mgr.add_wifi(ssid, pwd)

def del_wifi(index):
    """删除指定索引WiFi（供蓝牙指令）"""
    return wifi_mgr.del_wifi(index)

def get_ssid_list():
    """获取SSID列表（供蓝牙指令）"""
    return wifi_mgr.get_ssid_list()

def reload_wifi_config():
    """刷新配置缓存（供蓝牙指令，在json被修改后调用）"""
    return wifi_mgr.reload_config()


def get_status():
    """获取当前WiFi详细状态（供蓝牙状态查询调用）"""
    return wifi_mgr.get_status()

def get_wifi_count():
    """获取保存的WiFi数量"""
    return wifi_mgr.get_wifi_count()