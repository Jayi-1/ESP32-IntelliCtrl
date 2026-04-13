# wallpaper_mgr.py - 壁纸管理器 (优化版，解决闪屏问题)
# 功能：支持预设纯色、图片壁纸，通过蓝牙切换
# 协议: [AA] [03] [A0/B0] [ID] [00] [CHK]
#   A0 + ID(01-08) = 图片背景
#   B0 + ID(01-06) = 纯色背景 (红/橙/黄/绿/蓝/紫)
import gc
import os
from micropython import const

# ========== 屏幕尺寸常量 ==========
SCR_W = const(172)
SCR_H = const(320)
BG_BUF_SIZE = SCR_W * SCR_H * 2  # 110KB

# ========== 壁纸模式常量 ==========
MODE_SOLID = const(0)  # 纯色背景
MODE_IMAGE = const(1)  # 图片背景

# ========== 预设纯色定义 (RGB565格式) ==========
# 【扩展】支持更多预设颜色
PRESET_COLORS = {
    0x01: (0xF800, "红色"),    # 红色
    0x02: (0xFC00, "橙色"),    # 橙色
    0x03: (0xFFE0, "黄色"),    # 黄色
    0x04: (0x07E0, "绿色"),    # 绿色
    0x05: (0x001F, "蓝色"),    # 蓝色
    0x06: (0xF81F, "紫色"),    # 紫色
    0x07: (0xFFFF, "白色"),    # 白色
    0x08: (0x0000, "黑色"),    # 黑色
    0x09: (0x8410, "深灰"),    # 深灰
    0x0A: (0xC618, "浅灰"),    # 浅灰
    0x0B: (0x07FF, "青色"),    # 青色
    0x0C: (0xF81F, "粉色"),    # 粉色
    0x0D: (0xF0E0, "棕色"),    # 棕色
    0x0E: (0x8000, "暗红"),    # 暗红
    0x0F: (0x051F, "深蓝"),    # 深蓝
    0x10: (0x83E0, "深绿"),    # 深绿
}

# ========== 【修改】动态图片列表：从SD卡扫描 ==========
# SD卡中 xxx_172_320.raw 格式的图片文件
IMAGE_SLOTS = 8  # 最多8个图片槽位（保持兼容）
_sd_image_list = []  # 动态图片列表: [{"name": "xxx_172_320.raw", "index": 0}, ...]
_loaded_images = {}  # 懒加载缓存: {filename: buffer}

# ========== 【核心】静态背景缓冲区 (解决闪屏问题) ==========
# 类似 main.py 原来的 bg_buf，只在壁纸变更时更新
_static_bg_buf = None  # bytearray 静态背景缓冲

# ========== 全局状态 ==========
_current_mode = MODE_SOLID  # 当前壁纸模式
_current_color = 0x0000  # 当前纯色
_current_image_name = ""  # 当前图片文件名
_is_dirty = True  # 标记是否需要重新渲染背景

# 懒加载显示器引用
_tft_instance = None
_settings_mgr = None  # 设置管理器

def _get_settings():
    """延迟导入设置管理器"""
    global _settings_mgr
    if _settings_mgr is None:
        try:
            import settings_mgr
            _settings_mgr = settings_mgr
        except ImportError:
            return None
    return _settings_mgr

# ========== 【新增】SD卡图片扫描 ==========

def _scan_sd_images():
    """
    扫描SD卡中的 xxx_172_320.raw 图片文件
    返回: 图片文件名列表
    """
    global _sd_image_list
    _sd_image_list = []

    try:
        # 扫描SD卡根目录
        files = os.listdir("/sd")
        for fname in files:
            # 检查是否匹配 xxx_172_320.raw 格式
            if fname.endswith("_172_320.raw") and not fname.startswith('.'):
                _sd_image_list.append({
                    "name": fname,
                    "index": len(_sd_image_list)  # 0-based index
                })
                print(f"[WALLPAPER] 发现图片: {fname}")

        # 也扫描 backgrounds 目录
        try:
            bg_files = os.listdir("/sd/backgrounds")
            for fname in bg_files:
                if fname.endswith("_172_320.raw") and not fname.startswith('.'):
                    # 检查是否已存在（避免重复）
                    if not any(img["name"] == fname for img in _sd_image_list):
                        _sd_image_list.append({
                            "name": fname,
                            "index": len(_sd_image_list),
                            "path": "backgrounds"  # 标记来源目录
                        })
                        print(f"[WALLPAPER] 发现图片(backgrounds): {fname}")
        except:
            pass

        print(f"[WALLPAPER] 共扫描到 {len(_sd_image_list)} 个壁纸图片")
        return _sd_image_list

    except Exception as e:
        print(f"[WALLPAPER] 扫描SD卡图片失败: {e}")
        return []

def get_sd_images():
    """获取SD卡中的图片列表"""
    if not _sd_image_list:
        _scan_sd_images()
    return _sd_image_list

# ========== 初始化 ==========
def init(tft=None, mv=None):
    """初始化壁纸管理器"""
    global _tft_instance, _static_bg_buf, _current_mode, _current_color, _current_image_name

    if tft:
        _tft_instance = tft

    # 【关键】创建静态背景缓冲区
    if _static_bg_buf is None:
        _static_bg_buf = bytearray(BG_BUF_SIZE)

    # 【新增】先扫描SD卡中的图片
    _scan_sd_images()

    # 【修改】从Flash加载保存的壁纸设置
    settings = _get_settings()
    if settings:
        try:
            settings.init()
            _current_mode = settings.get_wallpaper_mode()
            _current_color = settings.get_wallpaper_color()
            saved_img_name = settings.get_wallpaper_image_name()
            # 如果保存的图片文件名存在，使用它
            if saved_img_name and any(img["name"] == saved_img_name for img in _sd_image_list):
                _current_image_name = saved_img_name
            else:
                # 图片不存在，使用第一张
                if _sd_image_list:
                    _current_image_name = _sd_image_list[0]["name"]
                else:
                    _current_image_name = ""
            print(f"[WALLPAPER] 从Flash加载: mode={_current_mode}, color=0x{_current_color:04X}, img={_current_image_name}")
        except Exception as e:
            print(f"[WALLPAPER] 加载设置失败: {e}")
            # 使用默认值
            if _sd_image_list:
                _current_image_name = _sd_image_list[0]["name"]
            else:
                _current_image_name = ""

    # 【修改】不再预加载图片，启动更快，内存更省
    # 图片将在首次切换壁纸时懒加载

    # 渲染初始背景
    _force_render()

    print("[WALLPAPER] 壁纸管理器已初始化（懒加载模式）")
    print(f"[WALLPAPER] 预设纯色: {len(PRESET_COLORS)} 种")
    print(f"[WALLPAPER] SD卡图片: {len(_sd_image_list)} 个")

def _lazy_load_image_by_name(filename):
    """
    【修改】懒加载指定文件名的图片
    参数: filename - 图片文件名，如 "04_172_320.raw"
    返回: 加载成功返回图片buffer，失败返回None
    """
    global _loaded_images

    if not filename:
        return None

    # 检查缓存
    if filename in _loaded_images:
        return _loaded_images[filename]

    try:
        buf = _load_raw_image(filename)
        if buf:
            _loaded_images[filename] = buf  # 缓存
            print(f"[WALLPAPER] 懒加载图片: {filename}")
            return buf
    except Exception as e:
        print(f"[WALLPAPER] 加载 {filename} 失败: {e}")

    return None

def _lazy_load_image(slot):
    """
    【兼容】通过槽位索引懒加载图片（保留兼容）
    参数: slot - 槽位索引 (0-based)
    返回: 加载成功返回图片buffer，失败返回None
    """
    global _sd_image_list

    if slot < 0 or slot >= len(_sd_image_list):
        return None

    filename = _sd_image_list[slot]["name"]
    return _lazy_load_image_by_name(filename)

def _unload_all_images():
    """
    【新增】卸载所有已加载的图片（释放内存）
    """
    global _loaded_images
    _loaded_images.clear()
    gc.collect()
    print("[WALLPAPER] 已卸载所有图片缓存")

def _unload_image_by_name(filename):
    """
    【新增】卸载指定图片缓存
    """
    global _loaded_images
    if filename in _loaded_images:
        del _loaded_images[filename]
        gc.collect()
        print(f"[WALLPAPER] 卸载图片: {filename}")

def _load_raw_image(fname):
    """加载raw格式图片（从SD卡）"""
    # 先尝试SD卡根目录
    try:
        base = fname.rsplit('.', 1)[0]
        parts = base.split('_')
        w, h = int(parts[-2]), int(parts[-1])
        buf = bytearray(w * h * 2)
        with open('/sd/' + fname, 'rb') as f:
            f.readinto(buf)
        return buf
    except:
        pass

    # 再尝试 backgrounds 目录
    try:
        base = fname.rsplit('.', 1)[0]
        parts = base.split('_')
        w, h = int(parts[-2]), int(parts[-1])
        buf = bytearray(w * h * 2)
        with open('/sd/backgrounds/' + fname, 'rb') as f:
            f.readinto(buf)
        return buf
    except Exception as e:
        print(f"[WALLPAPER] 加载失败 {fname}: {e}")
        return None

# ========== 【核心】内部渲染函数 (更新静态缓冲区) ==========
def _force_render():
    """
    【关键修复】强制重新渲染背景到静态缓冲区
    只有壁纸变更时才调用此函数
    """
    global _is_dirty, _static_bg_buf

    if _static_bg_buf is None:
        _static_bg_buf = bytearray(BG_BUF_SIZE)

    tft = _tft_instance

    if _current_mode == MODE_SOLID:
        # 纯色：填充矩形到静态缓冲区
        if tft:
            tft.rect_buf(_static_bg_buf, 0, 0, SCR_W, SCR_H, _current_color, fill=True)
        else:
            # 无显示器时，直接填充字节
            color_hi = (_current_color >> 8) & 0xFF
            color_lo = _current_color & 0xFF
            for i in range(0, BG_BUF_SIZE, 2):
                _static_bg_buf[i] = color_lo
                _static_bg_buf[i+1] = color_hi
    else:
        # 图片：复制图片数据到静态缓冲区
        if _current_image_name:
            img_buf = _lazy_load_image_by_name(_current_image_name)
            if img_buf:
                # 直接复制图片数据（避免调用 blit_buf 造成闪屏）
                mv_buf = memoryview(_static_bg_buf)
                mv_buf[:len(img_buf)] = img_buf
            else:
                # 图片无效，回退到纯色
                _render_solid_to_buf(_static_bg_buf, _current_color)
        else:
            # 无图片，回退到纯色
            _render_solid_to_buf(_static_bg_buf, _current_color)

    _is_dirty = False
    print(f"[WALLPAPER] 背景已渲染: {_current_image_name or '纯色'}")

def _render_solid_to_buf(buf, color):
    """将纯色渲染到指定缓冲区"""
    color_hi = (color >> 8) & 0xFF
    color_lo = color & 0xFF
    for i in range(0, BG_BUF_SIZE, 2):
        buf[i] = color_lo
        buf[i+1] = color_hi

# ========== 对外 API ==========

def set_mv(mv):
    """设置内存视图缓冲区（保留兼容性）"""
    pass  # 不再需要，render_to_mv 直接复制

def set_image_buffer(slot, buffer):
    """
    设置指定槽位的图片
    参数:
        slot: 槽位索引 (0-7)
        buffer: RGB565格式图片数据
    返回:
        True=成功, False=失败
    """
    global _loaded_images
    if slot < 0 or slot >= IMAGE_SLOTS:
        print(f"[WALLPAPER] 槽位无效: {slot}")
        return False
    _loaded_images[slot] = buffer
    print(f"[WALLPAPER] 设置图片槽位 #{slot+1}")
    return True

def set_solid_color(color_rgb565):
    """
    设置纯色壁纸
    参数:
        color_rgb565: 16位RGB565颜色值
    """
    global _current_mode, _current_color, _is_dirty
    if _current_mode != MODE_SOLID or _current_color != color_rgb565:
        _current_mode = MODE_SOLID
        _current_color = color_rgb565
        _is_dirty = True
        # 【新增】保存到Flash
        settings = _get_settings()
        if settings:
            settings.save_wallpaper(MODE_SOLID, color_rgb565)
        print(f"[WALLPAPER] 设置纯色: 0x{color_rgb565:04X}")

def set_color_by_id(color_id):
    """
    通过预设ID设置纯色
    参数:
        color_id: 颜色ID (1-16)
    返回:
        True=成功, False=失败
    """
    color_id = color_id & 0xFF
    if color_id in PRESET_COLORS:
        color, name = PRESET_COLORS[color_id]
        set_solid_color(color)
        # 【新增】保存到Flash
        settings = _get_settings()
        if settings:
            settings.set_wallpaper_mode(MODE_SOLID)
        print(f"[WALLPAPER] 设置颜色 #{color_id}: {name}")
        return True
    else:
        print(f"[WALLPAPER] 无效颜色ID: {color_id}")
        return False

def set_image_by_id(image_id):
    """
    【修改】通过SD卡图片列表索引设置图片背景
    参数:
        image_id: 图片ID (1-based，1=第一张，2=第二张...)
    返回:
        True=成功, False=失败
    """
    global _current_mode, _current_image_name, _is_dirty

    # 转换为0-based索引
    idx = (image_id - 1) & 0xFF

    # 检查图片是否存在
    if idx < 0 or idx >= len(_sd_image_list):
        print(f"[WALLPAPER] 无效图片ID: {image_id}，可用图片: {len(_sd_image_list)}")
        return False

    target_name = _sd_image_list[idx]["name"]

    # 切换壁纸
    if _current_mode != MODE_IMAGE or _current_image_name != target_name:
        # 卸载旧图片释放内存
        if _current_mode == MODE_IMAGE and _current_image_name:
            _unload_image_by_name(_current_image_name)

        _current_mode = MODE_IMAGE
        _current_image_name = target_name
        _is_dirty = True
        # 【修改】保存文件名到Flash
        settings = _get_settings()
        if settings:
            settings.save_wallpaper_image_name(target_name)
        print(f"[WALLPAPER] 切换到图片 #{image_id}: {target_name}")

    return True

def set_image_by_filename(filename):
    """
    【新增】通过文件名直接设置图片背景
    参数:
        filename: 图片文件名，如 "my_wallpaper_172_320.raw"
    返回:
        True=成功, False=失败
    """
    global _current_mode, _current_image_name, _is_dirty

    # 检查文件是否在列表中
    if not any(img["name"] == filename for img in _sd_image_list):
        print(f"[WALLPAPER] 文件不存在: {filename}")
        return False

    # 切换壁纸
    if _current_mode != MODE_IMAGE or _current_image_name != filename:
        # 卸载旧图片释放内存
        if _current_mode == MODE_IMAGE and _current_image_name:
            _unload_image_by_name(_current_image_name)

        _current_mode = MODE_IMAGE
        _current_image_name = filename
        _is_dirty = True
        # 保存文件名到Flash
        settings = _get_settings()
        if settings:
            settings.save_wallpaper_image_name(filename)
        print(f"[WALLPAPER] 切换到图片: {filename}")

    return True

def _unload_image(slot):
    """
    【兼容】卸载指定槽位的图片缓存
    """
    global _sd_image_list, _loaded_images
    if slot < 0 or slot >= len(_sd_image_list):
        return
    filename = _sd_image_list[slot]["name"]
    _unload_image_by_name(filename)

def get_current_image():
    """获取当前图片缓冲区（懒加载）"""
    global _current_image_name
    if _current_mode == MODE_IMAGE and _current_image_name:
        return _lazy_load_image_by_name(_current_image_name)
    return None

def get_status():
    """获取壁纸状态信息"""
    global _current_image_name, _loaded_images, _sd_image_list
    if _current_mode == MODE_SOLID:
        color_name = _get_color_name(_current_color)
    else:
        color_name = "N/A"
    return {
        "mode": _current_mode,
        "color": _current_color,
        "color_name": color_name,
        "image_name": _current_image_name,
        "image_count": len(_sd_image_list),  # SD卡中的图片数
        "loaded_count": len(_loaded_images),  # 已加载的图片数
        "is_dirty": _is_dirty
    }

def _get_color_name(color):
    """获取颜色名称"""
    for cid, (c, name) in PRESET_COLORS.items():
        if c == color:
            return name
    return "自定义"

# ========== 【关键】渲染接口 (解决闪屏) ==========

def is_dirty():
    """检查背景是否需要更新"""
    return _is_dirty

def render_to_mv(mv):
    """
    【优化版】将静态背景复制到 memoryview
    调用方式: mv[:] = wallpaper_mgr.get_bg_buf()
    或直接: wallpaper_mgr.render_to_mv(draw_mv)
    """
    global _static_bg_buf, _is_dirty

    # 检查是否需要重新渲染
    if _is_dirty:
        _force_render()

    # 复制静态背景到目标缓冲区
    if _static_bg_buf and len(_static_bg_buf) >= len(mv):
        mv[:len(_static_bg_buf)] = _static_bg_buf
    elif _static_bg_buf:
        mv[:] = _static_bg_buf[:len(mv)]

def get_bg_buf():
    """获取静态背景缓冲区（供直接复制使用）"""
    global _static_bg_buf, _is_dirty

    if _is_dirty:
        _force_render()

    return _static_bg_buf

def render(mv, tft):
    """
    【兼容旧接口】渲染当前壁纸
    注意：此函数每帧调用会造成闪屏，请使用 render_to_mv() 替代
    保留此接口仅为兼容性
    """
    # 如果需要更新，先渲染到静态缓冲区
    if _is_dirty:
        _force_render()

    # 复制到目标缓冲区
    if _static_bg_buf:
        mv[:len(_static_bg_buf)] = _static_bg_buf

def clear():
    """清空所有壁纸数据"""
    global _loaded_images, _current_mode, _current_image_name, _static_bg_buf
    _loaded_images.clear()
    _current_mode = MODE_SOLID
    _current_image_name = ""
    _static_bg_buf = bytearray(BG_BUF_SIZE)
    _is_dirty = True
    gc.collect()
    print("[WALLPAPER] 已清空壁纸数据")

def rescan_sd_images():
    """【新增】重新扫描SD卡中的图片"""
    global _loaded_images, _sd_image_list
    # 先卸载所有缓存
    _loaded_images.clear()
    gc.collect()
    # 重新扫描
    _scan_sd_images()
    return len(_sd_image_list)

# ========== 便捷函数 ==========
def set_color(color):
    """快捷函数：设置纯色"""
    set_solid_color(color)

def need_refresh():
    """检查是否需要刷新（兼容旧接口）"""
    return is_dirty()

# 全局实例 (保持向后兼容)
wallpaper_mgr = type('obj', (object,), {
    'set_solid_color': set_solid_color,
    'set_color_by_id': set_color_by_id,
    'set_image_by_id': set_image_by_id,
    'set_image_by_filename': set_image_by_filename,
    'set_image_buffer': set_image_buffer,
    'get_current': get_current_image,
    'get_status': get_status,
    'get_sd_images': get_sd_images,
    'rescan_sd_images': rescan_sd_images,
    'render': render,
    'render_to_mv': render_to_mv,
    'get_bg_buf': get_bg_buf,
    'is_dirty': is_dirty,
    'need_refresh': need_refresh,
    'clear': clear,
    'set_mv': set_mv
})()
