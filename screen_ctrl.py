# screen_ctrl.py - 屏幕控制器
# 功能：屏幕开关、背光亮度控制、息屏超时管理
import time
import gc

# ========== 延迟导入 ==========
_tft_instance = None
_settings_mgr = None

def _get_tft():
    global _tft_instance
    if _tft_instance is None:
        from st77899 import ST77899
        _tft_instance = ST77899()
    return _tft_instance

def _get_settings():
    global _settings_mgr
    if _settings_mgr is None:
        import settings_mgr
        _settings_mgr = settings_mgr
    return _settings_mgr

# ========== 状态变量 ==========
_is_on = True                     # 屏幕开关状态
_current_brightness = 80           # 当前亮度 5-100
_sleep_timeout = 30              # 息屏超时（秒）
_last_activity_time = 0           # 上次活动时间（用于息屏检测）
_is_sleeping = False              # 是否处于息屏状态

# ========== 初始化 ==========
def init():
    """初始化屏幕控制器"""
    global _is_on, _current_brightness, _sleep_timeout, _last_activity_time

    # 从 Flash 加载设置
    settings = _get_settings()
    settings.init()

    # 读取保存的设置
    _is_on = settings.get_screen_power()
    _current_brightness = settings.get_brightness()
    _sleep_timeout = settings.get_sleep_timeout()
    _last_activity_time = time.ticks_ms()

    # 应用设置到硬件
    tft = _get_tft()

    if _is_on:
        tft.display_on()
        tft.backlight(_current_brightness)
    else:
        tft.display_off()
        tft.backlight(0)

    print(f"[SCREEN] 初始化完成 - 电源:{_is_on}, 亮度:{_current_brightness}%, 息屏:{_sleep_timeout}秒")

def set_tft(tft):
    """设置TFT实例（由main.py调用）"""
    global _tft_instance
    _tft_instance = tft

# ========== 屏幕开关控制 ==========

def power_on():
    """开启屏幕"""
    global _is_on, _is_sleeping
    if not _is_on:
        _is_on = True
        _is_sleeping = False
        tft = _get_tft()
        tft.display_on()
        tft.backlight(_current_brightness)
        _get_settings().set_screen_power(True)
        print("[SCREEN] 已开启")
        return True
    elif _is_sleeping:
        # 从息屏唤醒
        wake_up()
        return True
    return False

def power_off():
    """关闭屏幕"""
    global _is_on, _is_sleeping
    if _is_on:
        _is_on = False
        _is_sleeping = False
        tft = _get_tft()
        tft.display_off()
        tft.backlight(0)
        _get_settings().set_screen_power(False)
        print("[SCREEN] 已关闭")
        return True
    return False

def toggle_power():
    """切换屏幕开关"""
    if _is_on:
        return power_off()
    else:
        return power_on()

def is_on():
    """检查屏幕是否开启"""
    return _is_on

def is_sleeping():
    """检查是否处于息屏状态"""
    return _is_sleeping

# ========== 亮度控制 ==========

def set_brightness(percent):
    """
    设置背光亮度
    percent: 5-100
    """
    global _current_brightness
    percent = max(5, min(100, int(percent)))

    if _current_brightness != percent:
        _current_brightness = percent
        tft = _get_tft()
        if _is_on and not _is_sleeping:
            tft.backlight(percent)
        _get_settings().set_brightness(percent)
        print(f"[SCREEN] 亮度设置为 {percent}%")

    return _current_brightness

def get_brightness():
    """获取当前亮度"""
    return _current_brightness

def brightness_up(step=10):
    """增加亮度"""
    return set_brightness(_current_brightness + step)

def brightness_down(step=10):
    """降低亮度"""
    return set_brightness(_current_brightness - step)

# ========== 息屏控制 ==========

def set_sleep_timeout(seconds):
    """
    设置息屏超时时间
    seconds: 0=从不, 10=10秒, 30=30秒, 60=1分钟, 300=5分钟
    """
    global _sleep_timeout
    seconds = max(0, int(seconds))
    _sleep_timeout = seconds
    _get_settings().set_sleep_timeout(seconds)
    print(f"[SCREEN] 息屏超时设置为 {seconds}秒")
    return _sleep_timeout

def get_sleep_timeout():
    """获取息屏超时时间"""
    return _sleep_timeout

def sleep_now():
    """立即息屏（但保持背光开启，只关闭显示）"""
    global _is_sleeping
    if _is_on and not _is_sleeping:
        _is_sleeping = True
        tft = _get_tft()
        tft.display_off()
        print("[SCREEN] 已息屏")
        return True
    return False

def wake_up():
    """从息屏唤醒"""
    global _is_sleeping
    if _is_sleeping:
        _is_sleeping = False
        tft = _get_tft()
        tft.display_on()
        tft.backlight(_current_brightness)
        record_activity()  # 重置活动时间
        print("[SCREEN] 已唤醒")
        return True
    return False

def record_activity():
    """记录用户活动时间（蓝牙操作）"""
    global _last_activity_time
    _last_activity_time = time.ticks_ms()

def check_sleep():
    """
    检查是否应该息屏（主循环调用）
    返回: True=已息屏, False=未息屏
    """
    global _is_sleeping

    # 如果息屏超时设为0，则永不息屏
    if _sleep_timeout == 0:
        return False

    # 如果屏幕已关闭，不处理
    if not _is_on:
        return False

    # 如果已息屏，不重复处理
    if _is_sleeping:
        return True

    # 检查超时
    elapsed = time.ticks_diff(time.ticks_ms(), _last_activity_time) // 1000  # 转为秒
    if elapsed >= _sleep_timeout:
        sleep_now()
        return True

    return False

# ========== 状态查询 ==========

def get_status():
    """获取屏幕状态"""
    return {
        "power": _is_on,
        "brightness": _current_brightness,
        "sleep_timeout": _sleep_timeout,
        "sleeping": _is_sleeping,
        "last_activity_sec": time.ticks_diff(time.ticks_ms(), _last_activity_time) // 1000
    }

# ========== 协议处理 ==========

def handle_power_cmd(value):
    """
    处理屏幕开关指令
    value: 0=关闭, 1=开启
    返回: (success:bool, message:str)
    """
    if value == 0:
        if power_off():
            return True, "屏幕已关闭"
        return False, "屏幕已关闭"
    elif value == 1:
        if power_on():
            return True, "屏幕已开启"
        return False, "屏幕已开启"
    return False, f"未知值: {value}"

def handle_brightness_cmd(value):
    """
    处理亮度调节指令
    value: 5-100
    返回: (success:bool, message:str)
    """
    if value < 5 or value > 100:
        return False, f"亮度范围: 5-100"
    brightness = set_brightness(value)
    return True, f"亮度: {brightness}%"

def handle_sleep_cmd(value):
    """
    处理息屏时间指令
    value: 0=从不, 10, 30, 60, 300
    返回: (success:bool, message:str)
    """
    valid_values = [0, 10, 30, 60, 300]
    if value not in valid_values:
        return False, f"无效值，可用: {valid_values}"

    timeout = set_sleep_timeout(value)
    name = "从不" if timeout == 0 else f"{timeout}秒"
    return True, f"息屏时间: {name}"

# 全局实例
screen_ctrl = type('obj', (object,), {
    'init': init,
    'set_tft': set_tft,
    'power_on': power_on,
    'power_off': power_off,
    'toggle_power': toggle_power,
    'is_on': is_on,
    'is_sleeping': is_sleeping,
    'set_brightness': set_brightness,
    'get_brightness': get_brightness,
    'brightness_up': brightness_up,
    'brightness_down': brightness_down,
    'set_sleep_timeout': set_sleep_timeout,
    'get_sleep_timeout': get_sleep_timeout,
    'sleep_now': sleep_now,
    'wake_up': wake_up,
    'record_activity': record_activity,
    'check_sleep': check_sleep,
    'get_status': get_status,
    'handle_power_cmd': handle_power_cmd,
    'handle_brightness_cmd': handle_brightness_cmd,
    'handle_sleep_cmd': handle_sleep_cmd,
})()

