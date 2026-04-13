"""
ESP32-IntelliCtrl v1.0.0
MIT License | Copyright (c) 2026 ESP32-IntelliCtrl
Author: jayi
Description: ESP32-S3 智能文件中枢主程序入口
"""
import time
import machine
import gc
import re
from machine import RTC

# 导入字库（关键）
from text import icon_16, namber32_64

# 导入模块
from st77899 import ST77899
from read_sd_capacity import sd_manager

# 双服务器导入
from file_server_sta import LANFileServer      # STA模式：剪贴板专用
from file_server import RobustFileServer       # AP模式：完整文件服务

from config import UI_CFG
import wifi_mgr
import bluetooth_ctrl
import remote_control
import file_browser
import key_scan
import display_ctrl as disp
import time_sync  # 导入时间同步模块

# 菜单模块导入
import menu_def
import display_menu
from remote_control import (
    PAGE_HOME, PAGE_FILES, PAGE_MENU_MAIN, PAGE_MENU_SUB, PAGE_APP
)

# 游戏模块导入
import snake_game
import graph_board

# 壁纸模块导入（【新增】背景切换支持）
import wallpaper_mgr

# 屏幕控制模块导入（【新增】屏幕开关/亮度/息屏）
import screen_ctrl

# 设置持久化模块
import settings_mgr

# ========== 启动模式配置（手动开关）==========
FORCE_AP_ON_BOOT = False  # ← 改成 True 则开机强制进AP，不尝试连WiFi
# ============================================

print("[MAIN] 系统启动...")

# ========== 启动时 WiFi 智能连接（只执行一次）==========
if FORCE_AP_ON_BOOT:
    print("[MAIN] 强制AP模式已启用，跳过STA连接")
    wifi_mgr.switch_ap()
elif not wifi_mgr.connect_smart():
    print("[MAIN] 所有WiFi连接失败，启动 AP 模式等待配网...")
    wifi_mgr.switch_ap()
else:
    status = wifi_mgr.get_status()
    print(f"[MAIN] WiFi已连接！模式: {status['mode_str']}, SSID: {status['ssid']}, IP: {status['ip']}")

# ========== 全局变量 ==========
tft = None
http_server = None      # 当前运行的HTTP服务器实例
server_ip = ""          # 当前服务器IP（用于屏幕显示）
current_server_mode = ""  # 记录当前服务器类型："AP" 或 "STA" 或 ""
files = []
frame_cnt = 0
fps = 0

# 【新增】剪贴板历史（供BLE数据接收使用）
_CLIPBOARD_HISTORY = []
_MAX_CLIPBOARD_ITEMS = 10

# ========== FPS 计数器中断 ==========
def fps_isr(t):
    global fps
    print(f"[FPS] {fps}")
    fps = 0

# 【新增】========== 网络时间同步函数 ==========
def sync_network_time():
    """
    同步网络时间：先尝试从 api.json 获取，失败则使用默认时间
    返回: (success:bool, time_string:str)
    """
    rtc = RTC()
    
    # 检查是否有 api.json 配置
    config = time_sync.load_api_config()
    if not config:
        print("[TimeSync] 未找到 api.json，使用默认时间")
        # 设置默认时间并返回
        default_time = (2026, 3, 20, 4, 12, 0, 0, 0)  # 星期五
        rtc.datetime(default_time)
        return False, "2026-03-20 12:00:00 (默认)"
    
    # 尝试获取网络时间（仅在 WiFi 连接成功时）
    status = wifi_mgr.get_status()
    if not status.get("connected"):
        print("[TimeSync] WiFi 未连接，无法同步网络时间，使用默认时间")
        default_time = (2026, 3, 20, 4, 12, 0, 0, 0)
        rtc.datetime(default_time)
        return False, "WiFi未连接，使用默认时间"
    
    # 获取网络时间
    print("[TimeSync] 正在同步网络时间...")
    try:
        result = time_sync.fetch_time(show_raw=False)
        if result and result.get('timestamp'):
            # 同步成功，time_sync.fetch_time 内部已经设置了 RTC
            dt = rtc.datetime()
            time_str = f"{dt[0]}-{dt[1]:02d}-{dt[2]:02d} {dt[4]:02d}:{dt[5]:02d}:{dt[6]:02d}"
            print(f"[TimeSync] 同步成功: {time_str}")
            return True, time_str
        else:
            # 获取失败，使用默认时间
            print("[TimeSync] 获取失败，使用默认时间")
            default_time = (2026, 3, 20, 4, 12, 0, 0, 0)
            rtc.datetime(default_time)
            return False, "获取失败，使用默认时间"
    except Exception as e:
        print(f"[TimeSync] 同步异常: {e}，使用默认时间")
        default_time = (2026, 3, 20, 4, 12, 0, 0, 0)
        rtc.datetime(default_time)
        return False, f"异常: {e}"

# ========== 【新增】BLE数据接收处理回调 ==========
def on_ble_data_complete(session_id, data, meta):
    """
    处理BLE重组完成的完整数据
    根据 meta.type 分发到不同处理逻辑
    """
    data_type = meta.get('type', 'raw')
    print(f"[BLE-DATA] 收到类型 {data_type}，大小 {len(data)} 字节")

    # 打印原始数据（十六进制）
    hex_str = ' '.join(f'{b:02X}' for b in data)
    print(f"[BLE-DATA] 原始数据: {hex_str}")

    try:
        if data_type == 'wifi_config':
            # WiFi配置JSON
            import json
            cfg = json.loads(data.decode('utf-8'))
            ssid = cfg.get('ssid')
            pwd = cfg.get('password')
            if ssid:
                print(f"[BLE-WiFi] 添加配置: {ssid}")
                success, index = wifi_mgr.add_wifi(ssid, pwd)
                # 可选：自动连接
                if success and meta.get('auto_connect'):
                    wifi_mgr.connect_by_index(index)

        elif data_type == 'clipboard':
            # 剪贴板文本
            text = data.decode('utf-8', 'ignore')
            _add_to_clipboard(text, f"BLE-{session_id}")
            print(f"[BLE-Clipboard] 新增 {len(text)} 字符")

        elif data_type == 'file':
            # 文件数据（缓冲模式，适合小文件<10KB）
            filename = meta.get('filename', f'ble_{session_id}.bin')
            filepath = f'/sd/{filename}'
            with open(filepath, 'wb') as f:
                f.write(data)
            print(f"[BLE-File] 已保存 {filename} ({len(data)} 字节)")
            # 刷新文件列表
            global files
            list_ok, items, _ = sd_manager.list()
            if list_ok:
                files = [item["name"] for item in items if not item["is_dir"]]

        elif data_type == 'command':
            # 远程命令JSON
            import json
            cmd = json.loads(data.decode('utf-8'))
            # 可扩展：执行特定命令
            print(f"[BLE-CMD] 收到命令: {cmd}")

        elif data_type == 'api_config':
            # API配置保存到 /api.json (用于时间同步等)
            try:
                import json
                text = data.decode('utf-8', 'ignore')
                full_cfg = json.loads(text)
                # 检查是否有嵌套的data字段
                if 'data' in full_cfg:
                    config = full_cfg['data']
                else:
                    config = full_cfg
                filepath = '/api.json'
                with open(filepath, 'w') as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
                print(f"[BLE-API] 保存配置到 {filepath}: {config}")
            except Exception as e:
                print(f"[BLE-API] 保存失败: {e}")

        elif data_type == 'gpio_config':
            # GPIO/引脚配置保存到 /gpio.json
            try:
                import json
                text = data.decode('utf-8', 'ignore')
                full_cfg = json.loads(text)
                file_name = full_cfg.get('file', '')
                # 提取data字段（支持pins_cfg格式）
                if 'data' in full_cfg:
                    cfg_data = full_cfg['data']
                else:
                    cfg_data = full_cfg
                filepath = '/gpio.json'
                with open(filepath, 'w') as f:
                    json.dump(cfg_data, f, ensure_ascii=False, indent=2)
                print(f"[BLE-GPIO] 保存引脚配置到 {filepath}: {cfg_data}")
            except Exception as e:
                print(f"[BLE-GPIO] 保存失败: {e}")

        elif data_type in ('config', 'unknown_0x02', 'unknown_0x03'):
            # 通用配置格式: {"type":"config","file":"xxx","data":{...}}
            try:
                import json
                text = data.decode('utf-8', 'ignore')
                full_cfg = json.loads(text)
                file_name = full_cfg.get('file', '')
                cfg_data = full_cfg.get('data', full_cfg)  # 支持直接data对象或整个JSON

                if file_name == 'ntp_cfg':
                    filepath = '/api.json'
                elif file_name in ('gpio_cfg', 'pins_cfg'):
                    filepath = '/gpio.json'
                else:
                    filepath = f'/{file_name}.json' if file_name else '/config.json'

                with open(filepath, 'w') as f:
                    json.dump(cfg_data, f, ensure_ascii=False, indent=2)
                print(f"[BLE-CFG] 保存配置到 {filepath}")
            except Exception as e:
                print(f"[BLE-CFG] 保存失败: {e}")

    except Exception as e:
        print(f"[BLE-DATA] 处理错误: {e}")

def on_ble_data_progress(session_id, percent, info):
    """数据接收进度（可显示在屏幕上）"""
    print(f"[BLE-Progress] 会话[{session_id}]: {percent}% ({info})")

def on_ble_data_stream(session_id, seq, data, is_last, meta):
    """
    流式数据处理（适合大文件，边收边写SD卡）
    不会占用大量内存，收到一包写一包
    """
    data_type = meta.get('type', 'raw')

    # 根据类型决定保存的文件名
    if data_type == 'api_config':
        filename = 'api.json'
    elif data_type == 'gpio_config':
        filename = 'gpio.json'
    else:
        filename = meta.get('filename', f'stream_{session_id}.bin')

    filepath = f'/sd/{filename}'

    if seq == 0:
        # 首包：创建文件
        print(f"[BLE-Stream] 开始接收 {filename} (类型:{data_type})")
        # 可选：如果文件存在先删除
        import os
        try:
            os.remove(filepath)
        except:
            pass

    # 追加写入
    try:
        with open(filepath, 'ab') as f:  # 注意：'ab'追加模式
            f.write(data)
    except Exception as e:
        print(f"[BLE-Stream] 写入失败: {e}")

    if is_last:
        print(f"[BLE-Stream] 流式完成 [{session_id}]: 已保存 {filepath}")
        # 刷新文件列表
        global files
        list_ok, items, _ = sd_manager.list()
        if list_ok:
            files = [item["name"] for item in items if not item["is_dir"]]

def _save_config_file(data, filename, tag='[BLE-CFG]'):
    """通用配置保存函数"""
    try:
        import json
        import re
        # 尝试直接解析JSON
        try:
            config = json.loads(data.decode('utf-8'))
        except:
            # 提取有效JSON部分
            text = data.decode('utf-8', 'ignore')
            match = re.search(r'\{[^}]+\}', text) or re.search(r'\[[^\]]+\]', text)
            if match:
                config = json.loads(match.group())
            else:
                raise ValueError("无法解析为JSON")
        filepath = f'/{filename}'
        with open(filepath, 'w') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"{tag} 保存配置到 {filepath}")
    except Exception as e:
        print(f"{tag} 保存失败: {e}")

def _add_to_clipboard(text, device="BLE"):
    """添加文本到剪贴板历史"""
    global _CLIPBOARD_HISTORY
    if not text:
        return
    item = {
        "text": text[:10240],  # 限制10KB
        "device": device[:20],
        "time": time.ticks_ms(),
        "size": len(text)
    }
    _CLIPBOARD_HISTORY.insert(0, item)
    while len(_CLIPBOARD_HISTORY) > _MAX_CLIPBOARD_ITEMS:
        _CLIPBOARD_HISTORY.pop()

# ========== 服务器管理函数 ==========
def create_server_for_mode(mode_str):
    """
    根据WiFi模式创建对应的服务器实例
    mode_str: "AP" 或 "STA"
    返回: (server_instance, ip_address)
    """
    global current_server_mode
    
    if mode_str == "AP":
        print("[服务器] 创建AP模式服务器（完整文件服务）...")
        server = RobustFileServer(sd_manager, port=80)
        if server.start():
            current_server_mode = "AP"
            return server, "192.168.4.1"
        else:
            print("[服务器] AP服务器启动失败")
            return None, ""
            
    elif mode_str == "STA":
        print("[服务器] 创建STA模式服务器（仅剪贴板）...")
        server = LANFileServer(sd_manager, port=80, clipboard_only=True)
        success, ip = server.start_sta()
        if success:
            current_server_mode = "STA"
            print(f"[剪贴板] 同网段设备访问 http://{ip} 可同步剪贴板")
            return server, ip
        else:
            print("[服务器] STA服务器启动失败")
            return None, ""
    
    return None, ""

def check_and_update_server():
    """
    检查当前WiFi模式，必要时切换服务器类型
    返回: (server_instance, current_ip)
    """
    global http_server, server_ip, current_server_mode
    
    status = wifi_mgr.get_status()
    mode_str = status["mode_str"]
    
    # 情况1：当前为OFF模式，应关闭服务器
    if mode_str == "OFF":
        if http_server is not None:
            print("[服务器] WiFi已关闭，停止HTTP服务...")
            http_server.stop()
            http_server = None
            server_ip = ""
            current_server_mode = ""
            gc.collect()
        return None, ""
    
    # 情况2：模式匹配，无需更换
    if http_server is not None and current_server_mode == mode_str:
        return http_server, server_ip
    
    # 情况3：模式不匹配或首次启动，需要重建服务器
    if http_server is not None:
        print(f"[服务器] 模式变更 {current_server_mode} -> {mode_str}，重启服务...")
        http_server.stop()
        http_server = None
        server_ip = ""
        gc.collect()
        time.sleep_ms(100)
    
    new_server, new_ip = create_server_for_mode(mode_str)
    if new_server:
        server_ip = new_ip
        http_server = new_server
        if mode_str == "STA":
            print(f"[提示] 剪贴板服务就绪: http://{server_ip}")
        else:
            print(f"[提示] 文件服务就绪: http://{server_ip}")
    
    return http_server, server_ip

# ========== 初始化 ==========
def init():
    global tft, files

    print("\n[MAIN] 系统初始化...")

    # 1. 初始化显示
    tft = ST77899()
    print("[MAIN] 显示初始化完成")

    # 【新增】2. 初始化屏幕控制器（要在显示之后）
    screen_ctrl.init()
    screen_ctrl.set_tft(tft)

    # 3. 初始化SD卡
    ok, _, msg = sd_manager.init()
    if ok:
        list_ok, items, _ = sd_manager.list()
        if list_ok and items:
            files = [item["name"] for item in items if not item["is_dir"]]
            print(f"[MAIN] SD卡就绪，文件数: {len(files)}")
        else:
            files = []
    else:
        print(f"[MAIN] SD卡错误: {msg}")
        files = []

    # 4. 启动蓝牙
    bluetooth_ctrl.ble_on()

    # 【新增】5. 初始化BLE数据组装器并设置回调
    assembler = bluetooth_ctrl.get_data_assembler()
    # 选择模式：'buffer'(缓冲，收齐回调) 或 'stream'(流式，逐包回调)
    # 小数据(配置/剪贴板)用buffer，大数据(文件)用stream
    assembler.set_callbacks(
        on_complete=on_ble_data_complete,
        on_progress=on_ble_data_progress,
        mode='buffer'  # 默认缓冲模式，文件传输时临时切stream见on_ble_data_complete处理
    )
    print("[MAIN] BLE数据分包管理器就绪")

    # 6. 启动按键扫描
    key_scan.start()

    # 【修改】7. 同步网络时间（替换原有的硬编码时间）
    # 先给 RTC 一个默认值，确保屏幕不显示错误时间
    rtc = RTC()
    rtc.datetime((2026, 1, 1, 3, 0, 0, 0, 0))  # 临时默认值
    # 如果 WiFi 已连接，尝试获取网络时间
    status = wifi_mgr.get_status()
    if status.get("connected"):
        success, time_str = sync_network_time()
        if success:
            print(f"[MAIN] 网络时间同步成功: {time_str}")
        else:
            print(f"[MAIN] 使用默认时间: {time_str}")
    else:
        print("[MAIN] WiFi 未连接，跳过时间同步，使用默认时间")
    
    # 7. 加载资源
    disp.init_damped_buffers()
    
    print("[MAIN] 初始化完成，进入主循环\n")
    gc.collect()

def main():
    global fps, frame_cnt, http_server, server_ip, tft

    # 【修复】不再重复初始化，复用 init() 的结果
    # 确保 init() 已执行（正常启动流程）

    buf_size = 172 * 320 * 2
    try:
        back_buf = bytearray(buf_size)
        front_buf = bytearray(buf_size)
    except MemoryError:
        back_buf = bytearray(buf_size)
        front_buf = back_buf
        print("[内存] 警告：使用单缓冲模式")

    draw_mv = memoryview(back_buf)

    # 加载背景
    bg_buf = disp.show_raw('04_172_320.raw')
    if bg_buf is None:
        bg_buf = bytearray(buf_size)
        for i in range(0, buf_size, 2):
            bg_buf[i] = 0x10
            bg_buf[i+1] = 0x42

    # 【新增】初始化壁纸管理器
    wallpaper_mgr.init(tft=tft, mv=draw_mv)
    # 设置默认壁纸（黑色背景）
    wallpaper_mgr.set_solid_color(0x0000)
    
    # 主循环
    while True:
        fps += 1
        frame_cnt += 1

        # 1. 绘制背景（【优化】解决闪屏问题）
        # 方案：使用静态背景缓冲区，只在壁纸变更时更新
        # 主循环只做内存复制，避免每帧重绘
        bg_buf = wallpaper_mgr.get_bg_buf()
        if bg_buf:
            draw_mv[:] = bg_buf  # 直接复制，无闪屏
        else:
            # 回退：使用旧方案
            wallpaper_mgr.render(draw_mv, tft)

        # 2. WiFi与HTTP服务器管理
        status = wifi_mgr.get_status()
        current_mode = status["mode_str"]

        if current_mode in ["AP", "STA"]:
            if frame_cnt % 10 == 0 or http_server is None:
                http_server, server_ip = check_and_update_server()

            if http_server:
                http_server.tick()
        else:
            if http_server:
                http_server.stop()
                http_server = None
                server_ip = ""
                gc.collect()

        # 3. 页面路由
        current_page = remote_control.get_page()

        if current_page == PAGE_HOME:
            # 【修改】时间显示：使用 RTC 当前时间（已被网络时间同步或默认时间）
            dt = RTC().datetime()
            # dt 格式: (year, month, day, weekday, hour, minute, second, microsecond)
            time_str = f"{dt[4]:02d}:{dt[5]:02d}"
            tft.draw_string_mv(draw_mv, 2, 20, time_str, namber32_64, 32, 64, color=0xF81F, bg=0x0000, gap=1)

            # 立方体动画
            #disp.cube_proj_buf(draw_mv, tft, x=86, y=200, side=30,rx=frame_cnt*0.02, ry=frame_cnt*0.01, rz=frame_cnt*0.03)
            # 球体动画（取消注释启用）
            #disp.sphere_proj_buf(draw_mv, tft, x=86, y=160, r=40,rx=frame_cnt*0.02, ry=frame_cnt*0.01, rz=frame_cnt*0.03)

            # 太阳系动画（取消注释启用）
            disp.solar_system_buf(draw_mv, tft, x=86, y=210,rx=frame_cnt*0.001, ry=frame_cnt*0.005, rz=frame_cnt*0.002)
            # 显示当前IP
            if server_ip:
                ip_text = f"IP:{server_ip}"
                color = 0xFFE0 if current_mode == "AP" else 0x07E0
                tft.draw_string_mv(draw_mv, 2, 280, ip_text, icon_16, 16, 16, color=color, bg=0x0000)
                # mode_text = "AP" if current_mode == "AP" else "STA"
                # tft.draw_string_mv(draw_mv, 140, 280, mode_text, icon_16, 16, 16, color=color, bg=0x0000)

            # 提示：按OK进入菜单
            tft.draw_text_mv(draw_mv, 20, 290, "OK->Menu", color=UI_CFG.COL_LGRAY, size=8)

        elif current_page == PAGE_MENU_MAIN:
            # 主菜单渲染
            menu_items = menu_def.get_main_menu()
            display_menu.render_main_menu(draw_mv, tft, menu_items)
            # 同步选中索引
            remote_control.update_main_index(display_menu.get_selected_index())

        elif current_page == PAGE_MENU_SUB:
            # Nano子菜单渲染
            menu_state = remote_control.get_menu_state()
            parent_id = menu_state.current_parent
            sub_items = menu_def.get_sub_menu(parent_id)
            # 获取父菜单项的label用于标题
            parent_label = ""
            main_items = menu_def.get_main_menu()
            for item in main_items:
                if item["id"] == parent_id:
                    parent_label = item["label"]
                    break
            display_menu.render_sub_menu(draw_mv, tft, sub_items, parent_label)
            # 同步选中索引
            remote_control.update_sub_index(display_menu.get_selected_index())

        elif current_page == PAGE_APP:
            # 应用页面：根据 app_id 显示对应应用
            menu_state = remote_control.get_menu_state()
            app_id = menu_state.app_id

            if app_id == "game_snake":
                # 贪吃蛇游戏
                game = snake_game.get_snake_game()

                # 先更新游戏逻辑
                was_playing = game.is_playing()
                if was_playing:
                    game.update()

                # 只在以下情况重绘：
                # 1. 游戏中且有更新（基于时间控制）
                # 2. 游戏状态改变时（菜单/结束）
                # 3. 每隔3帧自动刷新一次（防止残留）
                should_redraw = False
                if game.need_redraw or not was_playing or (frame_cnt % 3 == 0):
                    should_redraw = True
                    game.need_redraw = False

                if should_redraw:
                    game.draw(draw_mv, tft)

            elif app_id == "game_graph":
                # 函数图像画板
                board = graph_board.get_graph_board()
                board.handle_input()
                board.draw(draw_mv, tft)
            else:
                # 其他应用（占位显示）
                parent_id = menu_state.current_parent
                tft.draw_text_mv(draw_mv, 10, 50, f"App:{app_id}", color=UI_CFG.COL_SUCCESS, bg=UI_CFG.COL_BG, size=16)
                tft.draw_text_mv(draw_mv, 10, 70, f"From:{parent_id}", color=UI_CFG.COL_INFO, bg=UI_CFG.COL_BG, size=16)
                tft.draw_text_mv(draw_mv, 10, 100, "Arrow:Control", color=UI_CFG.COL_LGRAY, bg=UI_CFG.COL_BG, size=16)
                tft.draw_text_mv(draw_mv, 10, 120, "BACK:Return", color=UI_CFG.COL_LGRAY, bg=UI_CFG.COL_BG, size=16)

        elif current_page == PAGE_FILES:
            file_browser.render_page(draw_mv, tft, files)

            # 显示传输状态
            if http_server:
                if hasattr(http_server, 'get_transfer_status'):
                    xfer = http_server.get_transfer_status()
                    if xfer and xfer.get("active"):
                        disp.show_transfer_status(tft, draw_mv, xfer)

                # 显示剪贴板状态
                if current_mode == "STA" and hasattr(http_server, 'get_clipboard_status'):
                    clip = http_server.get_clipboard_status()
                    if clip and clip.get("text"):
                        preview = clip["text"][:15]
                        # 可在此显示剪贴板预览

        # 4. 状态栏
        ble_stat = bluetooth_ctrl.get_status()
        disp.show_ble_status(tft, draw_mv, ble_stat["active"], icon_16)
        disp.show_wifi_mode(tft, draw_mv, current_mode, icon_16)
        disp.show_wifi_detail(tft, draw_mv, status["ip"], status["connected"])

        # 5. 刷新显示
        tft.blit_buffer(back_buf, 0, 0, 172, 320)

        # 6. 交换缓冲
        if front_buf is not back_buf:
            back_buf, front_buf = front_buf, back_buf
            draw_mv = memoryview(back_buf)

        # 7. 处理蓝牙指令（原有）
        bluetooth_ctrl.process_wifi_switch()

        # 【新增】8. 息屏检测（无蓝牙操作后息屏）
        screen_ctrl.check_sleep()

        # 【新增】9. 数据管理器超时清理（每60帧约1秒）
        if frame_cnt % 60 == 0:
            bluetooth_ctrl.tick_data_manager()

        # 【新增】可选：每小时自动重新同步时间（每216000帧约1小时）
        # if frame_cnt % 216000 == 0 and status.get("connected"):
        #     sync_network_time()

        # 9. 内存回收（每30帧）
        if frame_cnt % 30 == 0:
            gc.collect()

# 启动入口
if __name__ == "__main__":
    try:
        init()  # 先执行初始化（包含时间同步）
        main()
    except Exception as e:
        print(f"[FATAL] 主循环异常: {e}")
        import sys
        sys.print_exception(e)   