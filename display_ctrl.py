import math
import time
from config import UI_CFG, CTRL_CFG, GRAPH_CFG
from text import icon_16

# 【优化】预计算的阻尼曲线 - 导出阻尼缓冲区供 file_browser 使用
_damped_buf_pos = None
_damped_buf_neg = None

# 【优化】WiFi模式颜色映射（全局常量）
_WIFI_COLOR_MAP = {
    "STA": UI_CFG.COL_SUCCESS,
    "AP": UI_CFG.COL_WARNING,
    "OFF": UI_CFG.COL_ERROR
}

# 【优化】传输状态常量
_TRANSFER_BAR_W = 162
_TRANSFER_BAR_H = 35
_TRANSFER_PROGRESS_H = 15
_TRANSFER_TEXT_Y = 18

# 【新增】3D立方体动画帧率控制
_CUBE_LAST_UPDATE = 0
_CUBE_FRAME_INTERVAL = 50  # 毫秒间隔（约20fps）

# 【新增】太阳系动画角度
_SOLAR_ANGLE = 0


def init_damped_buffers():
    """初始化阻尼动画缓冲区"""
    global _damped_buf_pos, _damped_buf_neg
    n = 80
    _damped_buf_pos = bytearray(n)
    _damped_buf_neg = bytearray(n)

    alpha = UI_CFG.ANIM_DAMPING
    omega = UI_CFG.ANIM_OMEGA
    amp = 85

    for k in range(n):
        t = k / 20.0
        env = math.exp(-alpha * t)
        y_sig = math.sin(omega * t)

        y_pos = int(amp * env * y_sig)   # 正向振荡
        y_neg = int((-amp) * env * y_sig)  # 负向振荡

        _damped_buf_pos[k] = int(y_pos)
        _damped_buf_neg[k] = int(y_neg)


def show_raw(fname):
    """加载raw图片到内存"""
    try:
        base = fname.rsplit('.', 1)[0]
        parts = base.split('_')
        w, h = int(parts[-2]), int(parts[-1])
        buf = bytearray(w * h * 2)
        with open('/' + fname, 'rb') as f:
            f.readinto(buf)
        return buf
    except Exception as e:
        print(f"[DISP] 加载图片失败 {fname}: {e}")
        return None


def show_ble_status(tft, mv, is_on, icon_font=None):
    """显示蓝牙状态图标"""
    if icon_font is None:
        icon_font = icon_16
    color = UI_CFG.COL_SUCCESS if is_on else UI_CFG.COL_ERROR
    tft.draw_string_mv(mv, 5, 4, "B", icon_font, 16, 16, color=color, bg=UI_CFG.COL_BG, gap=2)


def show_wifi_mode(tft, mv, mode_str, icon_font=None):
    """显示WiFi模式"""
    if icon_font is None:
        icon_font = icon_16
    color = _WIFI_COLOR_MAP.get(mode_str, UI_CFG.COL_TEXT)
    tft.draw_string_mv(mv, 25, 4, "W", icon_font, 16, 16, color=color, bg=UI_CFG.COL_BG, gap=2)
    tft.draw_text_mv(mv, 43, 14, mode_str, color=color, bg=UI_CFG.COL_BG, size=8)


def show_wifi_detail(tft, mv, ip_str, is_connected):
    """显示WiFi详情"""
    if ip_str and ip_str != "0.0.0.0":
        color = UI_CFG.COL_INFO if not is_connected else UI_CFG.COL_SUCCESS
    else:
        color = UI_CFG.COL_ERROR
        ip_str = "--.--.--.--"
    tft.draw_text_mv(mv, 43, 4, ip_str[:12], color=color, bg=UI_CFG.COL_BG, size=8)


def show_transfer_status(tft, mv, status, x=5, y=280):
    """在屏幕底部显示传输进度"""
    if not status or not status.get("active"):
        tft.rect_buf(mv, x, y, _TRANSFER_BAR_W, _TRANSFER_BAR_H, UI_CFG.COL_BG, fill=True)
        return

    # 背景条
    tft.rect_buf(mv, x, y, _TRANSFER_BAR_W, _TRANSFER_BAR_H, UI_CFG.COL_DGRAY, fill=True)

    # 计算进度
    total = status["total"]
    current = status["current"]
    pct = current / total if total > 0 else 0

    # 进度条填充
    bar_w = int(160 * pct)
    if bar_w > 0:
        tft.rect_buf(mv, x+1, y+1, bar_w, _TRANSFER_PROGRESS_H, UI_CFG.COL_SUCCESS, fill=True)

    # 文字信息
    type_str = "↑UP" if status["type"] == "upload" else "↓DL"
    speed_kb = status.get("speed", 0) / 1024
    name = status["filename"][:8] + ".." if len(status["filename"]) > 8 else status["filename"]

    info = f"{type_str} {int(pct*100)}% {speed_kb:.1f}KB/s"
    tft.draw_text_mv(mv, x+2, y+_TRANSFER_TEXT_Y, info, color=UI_CFG.COL_TEXT, bg=UI_CFG.COL_DGRAY, size=8)
    tft.draw_text_mv(mv, x+90, y+_TRANSFER_TEXT_Y, name, color=UI_CFG.COL_LGRAY, bg=UI_CFG.COL_DGRAY, size=8)


def cube_proj_buf(mv, tft, x=86, y=200, side=30, rx=0, ry=0.3, rz=0.2):
    """
    3D立方体投影 - 优化版：添加帧率控制
    """
    global _CUBE_LAST_UPDATE

    # 【优化】帧率控制：限制更新频率
    now = time.ticks_ms()
    if now - _CUBE_LAST_UPDATE < _CUBE_FRAME_INTERVAL:
        return  # 跳过本帧
    _CUBE_LAST_UPDATE = now

    edge_color = UI_CFG.COL_ERROR
    d = 250
    ox, oy = x, y

    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    # 旋转矩阵
    r11, r12, r13 = cz*cy, cz*sy*sx - sz*cx, cz*sy*cx + sz*sx
    r21, r22, r23 = sz*cy, sz*sy*sx + cz*cx, sz*sy*cx - cz*sx
    r31, r32, r33 = -sy, cy*sx, cy*cx

    v2d = []
    for vx, vy, vz in [(-side,-side,-side), (side,-side,-side), (side,side,-side), (-side,side,-side),
                       (-side,-side,side), (side,-side,side), (side,side,side), (-side,side,side)]:
        xr = r11*vx + r12*vy + r13*vz
        yr = r21*vx + r22*vy + r23*vz
        zr = r31*vx + r32*vy + r33*vz + d
        xs = ox + (d * xr) / zr
        ys = oy + (d * yr) / zr
        v2d.append((int(xs), int(ys)))

    edges = [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]
    for i, j in edges:
        tft.line_buf(mv, v2d[i][0], v2d[i][1], v2d[j][0], v2d[j][1], edge_color)


def sphere_proj_buf(mv, tft, x=86, y=160, r=40, rx=0, ry=0.3, rz=0.2):
    """
    3D线框球体投影
    参数:
        mv: memoryview缓冲区
        tft: 显示器实例
        x, y: 球心位置
        r: 球体半径
        rx, ry, rz: 旋转角度
    """
    d = 300  # 透视距离
    ox, oy = x, y
    dens = 10  # 网格密度

    # 旋转矩阵
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    r11 = cz*cy; r12 = cz*sy*sx - sz*cx; r13 = cz*sy*cx + sz*sx
    r21 = sz*cy; r22 = sz*sy*sx + cz*cx; r23 = sz*sy*cx - cz*sx
    r31 = -sy;  r32 = cy*sx;              r33 = cy*cx

    color = UI_CFG.COL_SUCCESS

    # 纬线
    for lat in range(-dens, dens + 1, 2):
        y3 = lat * r / dens
        ry2 = y3 * y3
        if ry2 >= r * r:
            continue
        rad = math.sqrt(r * r - ry2)
        pts = []
        for lon in range(0, 361, 15):
            th = math.radians(lon)
            x0 = rad * math.cos(th)
            z0 = rad * math.sin(th)
            y0 = y3
            xr = r11*x0 + r12*y0 + r13*z0
            yr = r21*x0 + r22*y0 + r23*z0
            zr = r31*x0 + r32*y0 + r33*z0 + d
            xs = int(ox + (d * xr) / zr)
            ys = int(oy + (d * yr) / zr)
            pts.append((xs, ys))
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i+1]
            if 0 < x1 < 172 and 0 < y1 < 320 and 0 < x2 < 172 and 0 < y2 < 320:
                tft.line_buf(mv, x1, y1, x2, y2, color)

    # 经线
    for lon in range(0, 360, 30):
        th = math.radians(lon)
        pts = []
        for lat in range(-dens, dens + 1, 2):
            y3 = lat * r / dens
            ry2 = y3 * y3
            if ry2 >= r * r:
                continue
            rad = math.sqrt(r * r - ry2)
            x0 = rad * math.cos(th)
            z0 = rad * math.sin(th)
            y0 = y3
            xr = r11*x0 + r12*y0 + r13*z0
            yr = r21*x0 + r22*y0 + r23*z0
            zr = r31*x0 + r32*y0 + r33*z0 + d
            xs = int(ox + (d * xr) / zr)
            ys = int(oy + (d * yr) / zr)
            pts.append((xs, ys))
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i+1]
            if 0 < x1 < 172 and 0 < y1 < 320 and 0 < x2 < 172 and 0 < y2 < 320:
                tft.line_buf(mv, x1, y1, x2, y2, color)


def solar_system_buf(mv, tft, x=86, y=160, rx=0, ry=0.0, rz=0.0):
    """
    太阳系模型投影（太阳系不旋转，只有行星公转，视角可调）
    参数:
        mv: memoryview缓冲区
        tft: 显示器实例
        x, y: 太阳中心位置
        rx, ry, rz: 视角旋转角度（用于调整观察角度）
    """
    global _SOLAR_ANGLE

    # 行星公转角度（自动递增，太阳系本身不旋转）
    _SOLAR_ANGLE += 0.02
    orbit_angle = _SOLAR_ANGLE

    # 视角旋转矩阵（用于投影，不影响行星轨道）
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    # 投影矩阵（只影响视觉效果）
    r11 = cz*cy; r12 = cz*sy*sx - sz*cx; r13 = cz*sy*cx + sz*sx
    r21 = sz*cy; r22 = sz*sy*sx + cz*cx; r23 = sz*sy*cx - cz*sx
    r31 = -sy;  r32 = cy*sx;              r33 = cy*cx

    ox, oy = x, y
    d = 200  # 透视距离

    # 绘制太阳（中心黄色圆）
    tft.circle_buf(mv, ox, oy, 12, UI_CFG.COL_WARNING, fill=True)

    # 行星数据：(轨道半径, 行星半径, 颜色, 公转速度因子)
    planets = [
        (20, 2, UI_CFG.COL_LGRAY, 4.15),    # 水星
        (32, 3, UI_CFG.COL_INFO, 1.62),      # 金星
        (46, 3, UI_CFG.COL_SUCCESS, 1.0),    # 地球
        (60, 2, UI_CFG.COL_ERROR, 0.53),     # 火星
        (80, 6, 0xFD20, 0.084),              # 木星
        (100, 5, 0xD4A0, 0.034),             # 土星
    ]

    # 绘制每个行星
    for orbit_r, planet_r, planet_color, speed_factor in planets:
        # 计算行星公转位置（太阳系不旋转，只改变公转相位）
        angle = orbit_angle * speed_factor
        px = orbit_r * math.cos(angle)  # 在XY平面公转
        py = orbit_r * math.sin(angle)
        pz = 0  # 轨道在Z=0平面

        # 应用视角投影（只改变观察角度，不改变轨道本身）
        xr = r11*px + r12*py + r13*pz
        yr = r21*px + r22*py + r23*pz
        zr = r31*px + r32*py + r33*pz + d
        xs = int(ox + (d * xr) / zr)
        ys = int(oy + (d * yr) / zr)

        # 绘制轨道线（椭圆，应用视角变换）
        orbit_pts = []
        for i in range(0, 361, 15):
            a = math.radians(i)
            ox_pt = orbit_r * math.cos(a)
            oy_pt = orbit_r * math.sin(a)
            oz_pt = 0
            xr = r11*ox_pt + r12*oy_pt + r13*oz_pt
            yr = r21*ox_pt + r22*oy_pt + r23*oz_pt
            zr = r31*ox_pt + r32*oy_pt + r33*oz_pt + d
            sx_pt = int(ox + (d * xr) / zr)
            sy_pt = int(oy + (d * yr) / zr)
            orbit_pts.append((sx_pt, sy_pt))

        # 绘制轨道
        for i in range(len(orbit_pts) - 1):
            x1, y1 = orbit_pts[i]
            x2, y2 = orbit_pts[i+1]
            if 0 < x1 < 172 and 0 < y1 < 320 and 0 < x2 < 172 and 0 < y2 < 320:
                tft.line_buf(mv, x1, y1, x2, y2, UI_CFG.COL_DGRAY)

        # 绘制行星（如果可见）
        if 0 < xs < 172 and 0 < ys < 320:
            tft.circle_buf(mv, xs, ys, planet_r, planet_color, fill=True)

            # 土星加光环
            if planet_r >= 5:
                ring_r = planet_r + 3
                for dx in range(-ring_r, ring_r + 1, 2):
                    dy = int(math.sqrt(ring_r * ring_r - dx * dx) * 0.4)
                    if 0 <= xs + dx < 172 and 0 <= ys + dy < 320:
                        tft.pixel_buf(mv, ys + dy, xs + dx, 0xD4A0)
                    if 0 <= xs + dx < 172 and 0 <= ys - dy < 320:
                        tft.pixel_buf(mv, ys - dy, xs + dx, 0xD4A0)


# 初始化阻尼缓冲区
init_damped_buffers()