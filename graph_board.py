"""
graph_board.py - 函数图像可视化器
通过方向键选择函数、调整参数，实时显示图像变化
"""
import math
import time
from config import UI_CFG, CTRL_CFG, MENU_CFG, GRAPH_CFG
from remote_control import get_ctrl_state

# ========== 屏幕参数 ==========
SCREEN_W = GRAPH_CFG.SCR_W
SCREEN_H = GRAPH_CFG.SCR_H
CENTER_X = SCREEN_W // 2
CENTER_Y = SCREEN_H // 2

# ========== 颜色 ==========
COL_AXIS = UI_CFG.COL_DGRAY      # 坐标轴颜色
COL_CURVE = UI_CFG.COL_SUCCESS  # 曲线颜色
COL_PARAM = UI_CFG.COL_HIGHLIGHT # 参数高亮
COL_TEXT = UI_CFG.COL_TEXT
COL_BG = UI_CFG.COL_BG


class GraphFunction:
    """单个函数类"""

    def __init__(self, name, draw_func, params, param_ranges):
        """
        name: 函数名称
        draw_func: 绘制函数 (mv, tft, params) -> None
        params: 当前参数字典
        param_ranges: 参数范围 {(参数名): (最小值, 最大值, 步进, 精度)}
        """
        self.name = name
        self.draw_func = draw_func
        self.params = params.copy()
        self.param_ranges = param_ranges
        self.selected_param = 0  # 当前选中的参数索引

    def get_param_names(self):
        return list(self.params.keys())

    def adjust_param(self, delta):
        """调整当前选中的参数"""
        names = self.get_param_names()
        if not names:
            return

        param_name = names[self.selected_param]
        pmin, pmax, step, precision = self.param_ranges[param_name]
        new_val = self.params[param_name] + delta * step
        new_val = max(pmin, min(pmax, new_val))
        # 四舍五入到指定精度
        if precision > 0:
            new_val = round(new_val / step) * step
        else:
            new_val = int(new_val)
        self.params[param_name] = new_val

    def select_next_param(self):
        """选择下一个参数"""
        names = self.get_param_names()
        if names:
            self.selected_param = (self.selected_param + 1) % len(names)

    def select_prev_param(self):
        """选择上一个参数"""
        names = self.get_param_names()
        if names:
            self.selected_param = (self.selected_param - 1 + len(names)) % len(names)


class GraphBoard:
    """画板主类"""

    def __init__(self):
        self.state = "SELECT"  # SELECT=选择函数, VIEW=查看函数
        self.selected_func_idx = 0
        self.prev_x = 0
        self.prev_y = 0
        self.anim_frame = 0  # 用于动画效果

        # 定义可用的函数
        self._init_functions()

    def _init_functions(self):
        """初始化所有函数"""
        self.functions = [
            # 1. 阻尼正弦波
            GraphFunction(
                name="Damped Sin",
                draw_func=self._draw_damped_sin,
                params={"alpha": 0.8, "omega": 4.0, "amp": 70},
                param_ranges={
                    "alpha": (0.1, 2.0, 0.1, 1),
                    "omega": (1.0, 10.0, 0.5, 1),
                    "amp": (20, 150, 5, 0)
                }
            ),
            # 2. 二阶系统响应
            GraphFunction(
                name="2nd Order",
                draw_func=self._draw_2nd_order,
                params={"wn": 5.0, "zeta": 0.3},
                param_ranges={
                    "wn": (1.0, 15.0, 0.5, 1),
                    "zeta": (0.05, 2.0, 0.05, 2)
                }
            ),
            # 3. 正弦波
            GraphFunction(
                name="Sine Wave",
                draw_func=self._draw_sine,
                params={"freq": 3, "amp": 60},
                param_ranges={
                    "freq": (1, 15, 1, 0),
                    "amp": (20, 120, 5, 0)
                }
            ),
            # 4. 3D旋转立方体
            GraphFunction(
                name="3D Cube",
                draw_func=self._draw_3d_cube,
                params={"rx": 0.5, "ry": 0.3, "rz": 0.2, "side": 30},
                param_ranges={
                    "rx": (0.0, 3.14, 0.1, 2),
                    "ry": (0.0, 3.14, 0.1, 2),
                    "rz": (0.0, 3.14, 0.1, 2),
                    "side": (15, 60, 5, 0)
                }
            ),
            # 5. 旋转球体
            GraphFunction(
                name="Wire Sphere",
                draw_func=self._draw_wire_sphere,
                params={"rx": 0.3, "ry": 0.4, "rz": 0.2, "r": 50},
                param_ranges={
                    "rx": (0.0, 3.14, 0.05, 2),
                    "ry": (0.0, 3.14, 0.05, 2),
                    "rz": (0.0, 3.14, 0.05, 2),
                    "r": (25, 80, 5, 0)
                }
            ),
            # 6. 太阳系
            GraphFunction(
                name="Solar Sys",
                draw_func=self._draw_solar_system,
                params={"rx": 0.3, "ry": 0.5, "rz": 0.1},
                param_ranges={
                    "rx": (0.0, 3.14, 0.05, 2),
                    "ry": (0.0, 3.14, 0.05, 2),
                    "rz": (0.0, 3.14, 0.05, 2)
                }
            ),
        ]

    # ========== 绘制函数实现 ==========

    def _draw_damped_sin(self, mv, tft, params):
        """绘制阻尼正弦波"""
        alpha = params["alpha"]
        omega = params["omega"]
        amp = params["amp"]

        # 绘制坐标轴
        tft.line_buf(mv, 0, CENTER_Y, SCREEN_W, CENTER_Y, COL_AXIS)
        tft.line_buf(mv, 0, 0, 0, SCREEN_H, COL_AXIS)

        # 绘制阻尼包络线
        prev_x = 0
        prev_y = CENTER_Y

        for x in range(1, SCREEN_W):
            t = x / GRAPH_CFG.DAMPED_T_SCALE
            env = math.exp(-alpha * t)
            y_sig = math.sin(omega * t)
            y = CENTER_Y - int(amp * env * y_sig)

            # 边界限制
            y = max(1, min(SCREEN_H - 2, y))

            # 绘制曲线
            tft.line_buf(mv, prev_x, prev_y, x, y, COL_CURVE)

            # 绘制包络线（限制在屏幕范围内）
            env_y = int(amp * env)
            env_top = max(1, CENTER_Y - env_y)
            env_bottom = min(SCREEN_H - 2, CENTER_Y + env_y)
            tft.pixel_buf(mv, env_top, x, GRAPH_CFG.DAMPED_ENVELOPE_COLOR)
            tft.pixel_buf(mv, env_bottom, x, GRAPH_CFG.DAMPED_ENVELOPE_COLOR)

            prev_x = x
            prev_y = y

    def _draw_2nd_order(self, mv, tft, params):
        """绘制二阶系统阶跃响应"""
        wn = params["wn"]
        zeta = params["zeta"]

        # 绘制坐标轴
        tft.line_buf(mv, 0, GRAPH_CFG.SECOND_ORDER_Y_AXIS, SCREEN_W, GRAPH_CFG.SECOND_ORDER_Y_AXIS, COL_AXIS)
        tft.line_buf(mv, 0, 0, 0, SCREEN_H, COL_AXIS)

        # 绘制稳态线
        tft.line_buf(mv, 0, GRAPH_CFG.SECOND_ORDER_STEADY, SCREEN_W, GRAPH_CFG.SECOND_ORDER_STEADY, UI_CFG.COL_DGRAY)

        # 计算二阶系统响应
        y = 0.0
        v = 0.0
        u = 1.0
        wn2 = wn * wn
        T = GRAPH_CFG.SECOND_ORDER_T

        prev_x = 0
        prev_y = GRAPH_CFG.SECOND_ORDER_Y_AXIS

        for x in range(SCREEN_W):
            a = -2.0 * zeta * wn * v - wn2 * y + wn2 * u
            v += a * T
            y += v * T
            # 映射到屏幕坐标
            screen_y = int(GRAPH_CFG.SECOND_ORDER_Y_AXIS - (y - 1) * GRAPH_CFG.SECOND_ORDER_SCALE)
            screen_y = max(0, min(SCREEN_H - 1, screen_y))
            tft.line_buf(mv, prev_x, prev_y, x, screen_y, COL_CURVE)
            prev_x = x
            prev_y = screen_y

    def _draw_sine(self, mv, tft, params):
        """绘制正弦波"""
        freq = params["freq"]
        amp = params["amp"]

        # 绘制坐标轴
        tft.line_buf(mv, 0, CENTER_Y, SCREEN_W, CENTER_Y, COL_AXIS)
        tft.line_buf(mv, 0, 0, 0, SCREEN_H, COL_AXIS)

        prev_x = 0
        prev_y = CENTER_Y

        for x in range(SCREEN_W):
            y = int(math.sin(x * freq * 2 * math.pi / SCREEN_W) * amp)
            screen_y = CENTER_Y - y
            screen_y = max(0, min(SCREEN_H - 1, screen_y))
            tft.line_buf(mv, prev_x, prev_y, x, screen_y, COL_CURVE)
            prev_x = x
            prev_y = screen_y

    def _draw_3d_cube(self, mv, tft, params):
        """绘制3D旋转立方体"""
        rx = params["rx"]
        ry = params["ry"]
        rz = params["rz"]
        side = params["side"]

        # 实时更新旋转角度
        self.cube_angle = getattr(self, 'cube_angle', 0) + GRAPH_CFG.CUBE_ANGLE_SPEED
        rx += self.cube_angle
        ry += self.cube_angle * GRAPH_CFG.CUBE_ANGLE_RY_FACTOR
        rz += self.cube_angle * GRAPH_CFG.CUBE_ANGLE_RZ_FACTOR

        color = UI_CFG.COL_HIGHLIGHT
        d = GRAPH_CFG.PERSPECTIVE_CUBE
        ox, oy = CENTER_X, GRAPH_CFG.CUBE_CENTER_Y

        cx, sx = math.cos(rx), math.sin(rx)
        cy, sy = math.cos(ry), math.sin(ry)
        cz, sz = math.cos(rz), math.sin(rz)

        r11 = cz*cy
        r12 = cz*sy*sx - sz*cx
        r13 = cz*sy*cx + sz*sx
        r21 = sz*cy
        r22 = sz*sy*sx + cz*cx
        r23 = sz*sy*cx - cz*sx
        r31 = -sy
        r32 = cy*sx
        r33 = cy*cx

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
            # 边界裁剪后绘制线段
            x1, y1 = v2d[i]
            x2, y2 = v2d[j]
            # 简单裁剪：确保端点在合理范围内
            if -200 < x1 < SCREEN_W + 200 and -200 < y1 < SCREEN_H + 200 and \
               -200 < x2 < SCREEN_W + 200 and -200 < y2 < SCREEN_H + 200:
                tft.line_buf(mv, x1, y1, x2, y2, color)

        # 绘制坐标轴
        axis_len = side * GRAPH_CFG.AXIS_LEN_FACTOR
        for axis_x, axis_y, axis_z, axis_color in [
            (axis_len, 0, 0, UI_CFG.COL_ERROR),    # X轴-红
            (0, axis_len, 0, UI_CFG.COL_SUCCESS),  # Y轴-绿
            (0, 0, axis_len, UI_CFG.COL_INFO)     # Z轴-蓝
        ]:
            xr = r11*axis_x + r12*axis_y + r13*axis_z
            yr = r21*axis_x + r22*axis_y + r23*axis_z
            zr = r31*axis_x + r32*axis_y + r33*axis_z + d
            xs = int(ox + (d * xr) / (zr + d))
            ys = int(oy + (d * yr) / (zr + d))
            # 边界裁剪
            if -200 < xs < SCREEN_W + 200 and -200 < ys < SCREEN_H + 200:
                tft.line_buf(mv, ox, oy, xs, ys, axis_color)

    def _draw_wire_sphere(self, mv, tft, params):
        """绘制旋转线框球体"""
        rx = params["rx"]
        ry = params["ry"]
        rz = params["rz"]
        r = params["r"]

        # 实时更新旋转角度
        self.sphere_angle = getattr(self, 'sphere_angle', 0) + GRAPH_CFG.SPHERE_ANGLE_SPEED
        rx += self.sphere_angle
        ry += self.sphere_angle * GRAPH_CFG.SPHERE_ANGLE_RY_FACTOR
        rz += self.sphere_angle * GRAPH_CFG.SPHERE_ANGLE_RZ_FACTOR

        color = COL_CURVE
        cx, cy = CENTER_X, GRAPH_CFG.SPHERE_CENTER_Y
        dens = GRAPH_CFG.SPHERE_DENS

        cx_rot, sx = math.cos(rx), math.sin(rx)
        cy_rot, sy = math.cos(ry), math.sin(ry)
        cz_rot, sz = math.cos(rz), math.sin(rz)

        r11 = cz_rot*cy_rot
        r12 = cz_rot*sy*sx - sz*cx_rot
        r13 = cz_rot*sy*cx_rot + sz*sx
        r21 = sz*cy_rot
        r22 = sz*sy*sx + cz_rot*cx_rot
        r23 = sz*sy*cx_rot - cz_rot*sx
        r31 = -sy
        r32 = cy_rot*sx
        r33 = cy_rot*cx_rot

        d = GRAPH_CFG.PERSPECTIVE_SPHERE

        # 纬线
        for lat in range(-dens, dens + 1, 2):
            y3 = lat * r / dens
            ry2 = y3 * y3
            if ry2 >= r * r:
                continue
            rad = math.sqrt(r * r - ry2)
            pts = []
            for lon in range(0, 361, GRAPH_CFG.SPHERE_LON_STEP):
                th = math.radians(lon)
                x0 = rad * math.cos(th)
                z0 = rad * math.sin(th)
                y0 = y3
                xr = r11*x0 + r12*y0 + r13*z0
                yr = r21*x0 + r22*y0 + r23*z0
                zr = r31*x0 + r32*y0 + r33*z0
                xs = int(cx + (d * xr) / (zr + d))
                ys = int(cy + (d * yr) / (zr + d))
                pts.append((xs, ys))
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]
                x2, y2 = pts[i+1]
                # 边界裁剪
                if -200 < x1 < SCREEN_W + 200 and -200 < y1 < SCREEN_H + 200 and \
                   -200 < x2 < SCREEN_W + 200 and -200 < y2 < SCREEN_H + 200:
                    tft.line_buf(mv, x1, y1, x2, y2, color)

        # 经线
        for lon in range(0, 360, GRAPH_CFG.SPHERE_LAT_STEP):
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
                zr = r31*x0 + r32*y0 + r33*z0
                xs = int(cx + (d * xr) / (zr + d))
                ys = int(cy + (d * yr) / (zr + d))
                pts.append((xs, ys))
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]
                x2, y2 = pts[i+1]
                # 边界裁剪
                if -200 < x1 < SCREEN_W + 200 and -200 < y1 < SCREEN_H + 200 and \
                   -200 < x2 < SCREEN_W + 200 and -200 < y2 < SCREEN_H + 200:
                    tft.line_buf(mv, x1, y1, x2, y2, color)

    def _draw_solar_system(self, mv, tft, params):
        """绘制太阳系模型"""
        rx = params["rx"]
        ry = params["ry"]
        rz = params["rz"]

        # 实时更新旋转角度（整个系统旋转）
        self.solar_angle = getattr(self, 'solar_angle', 0) + GRAPH_CFG.SOLAR_SPEED
        rx += self.solar_angle
        ry += self.solar_angle * GRAPH_CFG.SOLAR_ANGLE_RY_FACTOR
        rz += self.solar_angle * GRAPH_CFG.SOLAR_ANGLE_RZ_FACTOR

        # 旋转矩阵
        cx, sx = math.cos(rx), math.sin(rx)
        cy, sy = math.cos(ry), math.sin(ry)
        cz, sz = math.cos(rz), math.sin(rz)

        r11 = cz*cy; r12 = cz*sy*sx - sz*cx; r13 = cz*sy*cx + sz*sx
        r21 = sz*cy; r22 = sz*sy*sx + cz*cx; r23 = sz*sy*cx - cz*sx
        r31 = -sy;  r32 = cy*sx;             r33 = cy*cx

        # 太阳中心
        ox, oy = CENTER_X, CENTER_Y
        d = GRAPH_CFG.SOLAR_PERSPECTIVE

        # 绘制太阳（中心黄色圆）
        tft.circle_buf(mv, ox, oy, GRAPH_CFG.SOLAR_SUN_R, UI_CFG.COL_WARNING, fill=True)

        # 行星数据：(轨道半径, 行星半径, 颜色, 旋转速度因子)
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
            # 计算行星当前位置（绕太阳公转）
            angle = self.solar_angle * speed_factor
            px_3d = orbit_r * math.cos(angle)
            py_3d = orbit_r * math.sin(angle)
            pz_3d = 0

            # 3D到2D投影
            xr = r11*px_3d + r12*py_3d + r13*pz_3d
            yr = r21*px_3d + r22*py_3d + r23*pz_3d
            zr = r31*px_3d + r32*py_3d + r33*pz_3d + d
            xs = int(ox + (d * xr) / zr)
            ys = int(oy + (d * yr) / zr)

            # 绘制轨道线（椭圆）
            orbit_pts = []
            for i in range(0, 361, 15):
                a = math.radians(i)
                ox_3d = orbit_r * math.cos(a)
                oy_3d = orbit_r * math.sin(a)
                oz_3d = 0
                xr = r11*ox_3d + r12*oy_3d + r13*oz_3d
                yr = r21*ox_3d + r22*oy_3d + r23*oz_3d
                zr = r31*ox_3d + r32*oy_3d + r33*oz_3d + d
                sx = int(ox + (d * xr) / zr)
                sy = int(oy + (d * yr) / zr)
                orbit_pts.append((sx, sy))

            # 绘制轨道
            for i in range(len(orbit_pts) - 1):
                x1, y1 = orbit_pts[i]
                x2, y2 = orbit_pts[i+1]
                if 0 < x1 < SCREEN_W and 0 < y1 < SCREEN_H and \
                   0 < x2 < SCREEN_W and 0 < y2 < SCREEN_H:
                    tft.line_buf(mv, x1, y1, x2, y2, UI_CFG.COL_DGRAY)

            # 绘制行星（如果可见）
            if 0 < xs < SCREEN_W and 0 < ys < SCREEN_H:
                # 简单Z缓冲：靠近屏幕中心的行星更亮
                depth_factor = min(1.0, zr / d)
                tft.circle_buf(mv, xs, ys, planet_r, planet_color, fill=True)

                # 土星加光环
                if planet_r >= 5:
                    # 绘制光环（简化椭圆）
                    ring_r = planet_r + 3
                    for dx in range(-ring_r, ring_r + 1, 2):
                        dy = int(math.sqrt(ring_r * ring_r - dx * dx) * 0.4)
                        if 0 <= xs + dx < SCREEN_W and 0 <= ys + dy < SCREEN_H:
                            tft.pixel_buf(mv, ys + dy, xs + dx, 0xD4A0)
                        if 0 <= xs + dx < SCREEN_W and 0 <= ys - dy < SCREEN_H:
                            tft.pixel_buf(mv, ys - dy, xs + dx, 0xD4A0)

    # ========== 主控制逻辑 ==========

    def handle_input(self):
        """处理方向键输入"""
        state = get_ctrl_state()
        curr_x = state["x"]
        curr_y = state["y"]

        dx = curr_x - self.prev_x
        dy = curr_y - self.prev_y

        if self.state == "SELECT":
            # 函数选择模式
            if dy > CTRL_CFG.THRESHOLD:
                self.selected_func_idx = (self.selected_func_idx + 1) % len(self.functions)
                self.prev_y = curr_y
            elif dy < -CTRL_CFG.THRESHOLD:
                self.selected_func_idx = (self.selected_func_idx - 1 + len(self.functions)) % len(self.functions)
                self.prev_y = curr_y

        elif self.state == "VIEW":
            # 参数调整模式
            func = self.functions[self.selected_func_idx]
            param_names = func.get_param_names()

            if dx > CTRL_CFG.THRESHOLD:
                func.select_next_param()
                self.prev_x = curr_x
            elif dx < -CTRL_CFG.THRESHOLD:
                func.select_prev_param()
                self.prev_x = curr_x

            if dy > CTRL_CFG.THRESHOLD:
                func.adjust_param(-1)  # 向下减少
                self.prev_y = curr_y
            elif dy < -CTRL_CFG.THRESHOLD:
                func.adjust_param(1)   # 向上增加
                self.prev_y = curr_y

    def enter_view(self):
        """进入函数查看模式"""
        self.state = "VIEW"

    def exit_view(self):
        """退出查看模式，返回选择模式"""
        self.state = "SELECT"
        self.cube_angle = 0
        self.sphere_angle = 0

    def is_viewing(self):
        """是否在查看模式"""
        return self.state == "VIEW"

    # ========== 绘制 ==========

    def draw(self, mv, tft):
        """绘制画板界面"""
        if self.state == "SELECT":
            self._draw_select(mv, tft)
        else:
            self._draw_view(mv, tft)

    def _draw_select(self, mv, tft):
        """绘制函数选择界面"""
        # 标题
        tft.draw_text_mv(mv, GRAPH_CFG.TITLE_X, GRAPH_CFG.TITLE_Y1, "GRAPH", color=COL_PARAM, bg=COL_BG, size=MENU_CFG.TEXT_SIZE, space=1)
        tft.draw_text_mv(mv, GRAPH_CFG.TITLE_X, GRAPH_CFG.TITLE_Y2, "BOARD", color=COL_PARAM, bg=COL_BG, size=MENU_CFG.TEXT_SIZE, space=1)

        # 绘制函数列表
        y_offset = GRAPH_CFG.SELECT_Y_OFFSET
        line_h = GRAPH_CFG.SELECT_LINE_H

        for i, func in enumerate(self.functions):
            if i == self.selected_func_idx:
                # 选中项背景
                tft.rect_buf(mv, GRAPH_CFG.SELECT_BOX_X, y_offset + i * line_h - 2, GRAPH_CFG.SELECT_BOX_W, GRAPH_CFG.SELECT_BOX_H, UI_CFG.COL_SELECT_BG, fill=True)
                color = COL_PARAM
            else:
                color = COL_TEXT

            tft.draw_text_mv(mv, MENU_CFG.TEXT_X, y_offset + i * line_h, func.name, color=color, bg=COL_BG, size=MENU_CFG.TEXT_SIZE, space=1)

        # 底部提示
        tft.draw_text_mv(mv, MENU_CFG.TEXT_X, GRAPH_CFG.HINT_Y, "UP/DN=Select", color=UI_CFG.COL_LGRAY, bg=COL_BG, size=MENU_CFG.TEXT_SIZE, space=1)
        tft.draw_text_mv(mv, MENU_CFG.TEXT_X, SCREEN_H - 20, "OK=Enter", color=UI_CFG.COL_LGRAY, bg=COL_BG, size=MENU_CFG.TEXT_SIZE, space=1)

    def _draw_view(self, mv, tft):
        """绘制函数查看界面"""
        func = self.functions[self.selected_func_idx]

        # 清屏并绘制网格背景
        tft.rect_buf(mv, 0, 0, SCREEN_W, SCREEN_H, COL_BG, fill=True)

        # 绘制函数图像
        func.draw_func(mv, tft, func.params)

        # 绘制底部参数面板
        panel_y = SCREEN_H - GRAPH_CFG.PANEL_H
        tft.rect_buf(mv, 0, panel_y, SCREEN_W, GRAPH_CFG.PANEL_H, 0x0000, fill=True)  # 半透明黑色背景
        tft.line_buf(mv, 0, panel_y, SCREEN_W, panel_y, COL_AXIS)

        # 绘制函数名
        tft.draw_text_mv(mv, MENU_CFG.TEXT_X, panel_y + GRAPH_CFG.PANEL_Y_OFFSET, func.name, color=COL_PARAM, bg=0x0000, size=MENU_CFG.TEXT_SIZE, space=1)

        # 绘制参数
        y_offset = panel_y + 25
        x_offset = MENU_CFG.TEXT_X
        param_names = func.get_param_names()

        for i, name in enumerate(param_names):
            if i == func.selected_param:
                color = COL_PARAM
                prefix = ">"
            else:
                color = COL_TEXT
                prefix = " "

            value = func.params[name]
            param_text = f"{prefix}{name}={value}"
            # 截断过长的文本
            if x_offset + len(param_text) * 9 > SCREEN_W:
                x_offset = MENU_CFG.TEXT_X
                y_offset += 20
                if y_offset > SCREEN_H - 10:
                    break
            tft.draw_text_mv(mv, x_offset, y_offset, param_text, color=color, bg=0x0000, size=MENU_CFG.TEXT_SIZE, space=1)
            x_offset += len(param_text) * 9 + 10

        # 顶部提示
        tft.draw_text_mv(mv, MENU_CFG.TEXT_X, GRAPH_CFG.PANEL_Y_OFFSET, "LR=Param  UD=Value  BK=Exit", color=UI_CFG.COL_LGRAY, bg=0x0000, size=MENU_CFG.TEXT_SIZE, space=1)


# 全局实例
_graph_board = None


def get_graph_board():
    """获取画板实例"""
    global _graph_board
    if _graph_board is None:
        _graph_board = GraphBoard()
    return _graph_board


def init_graph_board():
    """初始化画板"""
    global _graph_board
    _graph_board = GraphBoard()
    return _graph_board
