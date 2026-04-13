# settings_mgr.py - 设置持久化管理器
# 功能：将设备设置保存到 Flash，避免重启丢失
import json
import gc

# ========== 默认设置 ==========
DEFAULT_SETTINGS = {
    # 屏幕设置
    "screen": {
        "power": True,           # 屏幕开关
        "brightness": 80,        # 亮度 5-100
        "sleep_timeout": 30,     # 息屏超时时间（秒），0=从不
    },
    # 壁纸设置
    "wallpaper": {
        "mode": 0,              # 0=纯色, 1=图片
        "color": 0x0000,         # 当前纯色
        "image_index": 0,        # 当前图片索引
    },
    # WiFi设置（由 wifi_mgr 管理，这里只做备份）
    "wifi_backup": {
        "last_ssid": "",
    }
}

# ========== 全局变量 ==========
_settings = None
_settings_file = "/settings.json"  # 保存在 Flash

# ========== 初始化 ==========
def init():
    """加载设置（从Flash读取）"""
    global _settings
    _settings = _load_settings()
    print(f"[SETTINGS] 已加载 {len(_settings)} 项设置")
    return _settings

def _load_settings():
    """从Flash加载设置"""
    try:
        with open(_settings_file, 'r') as f:
            loaded = json.loads(f.read())
            # 合并默认设置，确保所有键存在
            return _merge_defaults(loaded)
    except Exception as e:
        print(f"[SETTINGS] 加载失败，使用默认: {e}")
        return DEFAULT_SETTINGS.copy()

def _merge_defaults(loaded):
    """合并默认设置"""
    result = DEFAULT_SETTINGS.copy()
    for section, values in loaded.items():
        if section in result and isinstance(result[section], dict):
            result[section].update(values)
        else:
            result[section] = values
    return result

def save():
    """保存设置到Flash"""
    global _settings
    try:
        with open(_settings_file, 'w') as f:
            f.write(json.dumps(_settings))
        print("[SETTINGS] 已保存")
        return True
    except Exception as e:
        print(f"[SETTINGS] 保存失败: {e}")
        return False

# ========== 屏幕设置 API ==========

def get_screen_power():
    """获取屏幕开关状态"""
    return _settings.get("screen", {}).get("power", True)

def set_screen_power(power):
    """设置屏幕开关状态"""
    if "screen" not in _settings:
        _settings["screen"] = {}
    _settings["screen"]["power"] = bool(power)
    save()

def get_brightness():
    """获取亮度 (5-100)"""
    return _settings.get("screen", {}).get("brightness", 80)

def set_brightness(brightness):
    """设置亮度 (5-100)"""
    brightness = max(5, min(100, int(brightness)))
    if "screen" not in _settings:
        _settings["screen"] = {}
    _settings["screen"]["brightness"] = brightness
    save()

def get_sleep_timeout():
    """获取息屏超时时间（秒）"""
    return _settings.get("screen", {}).get("sleep_timeout", 30)

def set_sleep_timeout(seconds):
    """设置息屏超时时间（秒），0=从不"""
    seconds = max(0, int(seconds))
    if "screen" not in _settings:
        _settings["screen"] = {}
    _settings["screen"]["sleep_timeout"] = seconds
    save()

# ========== 壁纸设置 API ==========

def get_wallpaper_mode():
    """获取壁纸模式 (0=纯色, 1=图片)"""
    return _settings.get("wallpaper", {}).get("mode", 0)

def set_wallpaper_mode(mode):
    """设置壁纸模式"""
    if "wallpaper" not in _settings:
        _settings["wallpaper"] = {}
    _settings["wallpaper"]["mode"] = mode
    save()

def get_wallpaper_color():
    """获取壁纸纯色"""
    return _settings.get("wallpaper", {}).get("color", 0x0000)

def set_wallpaper_color(color):
    """设置壁纸纯色"""
    if "wallpaper" not in _settings:
        _settings["wallpaper"] = {}
    _settings["wallpaper"]["color"] = color
    save()

def get_wallpaper_image_index():
    """获取壁纸图片索引（兼容旧接口）"""
    return _settings.get("wallpaper", {}).get("image_index", 0)

def set_wallpaper_image_index(index):
    """设置壁纸图片索引（兼容旧接口）"""
    if "wallpaper" not in _settings:
        _settings["wallpaper"] = {}
    _settings["wallpaper"]["image_index"] = index
    save()

def get_wallpaper_image_name():
    """【新增】获取壁纸图片文件名"""
    return _settings.get("wallpaper", {}).get("image_name", "")

def save_wallpaper_image_name(filename):
    """【新增】保存壁纸图片文件名"""
    if "wallpaper" not in _settings:
        _settings["wallpaper"] = {}
    _settings["wallpaper"]["image_name"] = filename
    _settings["wallpaper"]["mode"] = 1  # 自动切换到图片模式
    save()

def save_wallpaper(mode, color_or_index):
    """保存壁纸设置"""
    if "wallpaper" not in _settings:
        _settings["wallpaper"] = {}
    _settings["wallpaper"]["mode"] = mode
    if mode == 0:
        _settings["wallpaper"]["color"] = color_or_index
    else:
        # 旧接口使用索引，新版本使用文件名
        if isinstance(color_or_index, str):
            _settings["wallpaper"]["image_name"] = color_or_index
        else:
            _settings["wallpaper"]["image_index"] = color_or_index
    save()

# ========== 工具函数 ==========

def get_all():
    """获取所有设置"""
    return _settings

def update_section(section, values):
    """更新整个section"""
    global _settings
    if section not in _settings:
        _settings[section] = {}
    _settings[section].update(values)
    save()

def reset():
    """重置所有设置到默认值"""
    global _settings
    _settings = DEFAULT_SETTINGS.copy()
    save()
    print("[SETTINGS] 已重置为默认值")

# 全局实例
settings_mgr = type('obj', (object,), {
    'init': init,
    'save': save,
    'get_screen_power': get_screen_power,
    'set_screen_power': set_screen_power,
    'get_brightness': get_brightness,
    'set_brightness': set_brightness,
    'get_sleep_timeout': get_sleep_timeout,
    'set_sleep_timeout': set_sleep_timeout,
    'get_wallpaper_mode': get_wallpaper_mode,
    'set_wallpaper_mode': set_wallpaper_mode,
    'get_wallpaper_color': get_wallpaper_color,
    'set_wallpaper_color': set_wallpaper_color,
    'get_wallpaper_image_index': get_wallpaper_image_index,
    'set_wallpaper_image_index': set_wallpaper_image_index,
    'get_wallpaper_image_name': get_wallpaper_image_name,
    'save_wallpaper_image_name': save_wallpaper_image_name,
    'save_wallpaper': save_wallpaper,
    'get_all': get_all,
    'update_section': update_section,
    'reset': reset,
})()
