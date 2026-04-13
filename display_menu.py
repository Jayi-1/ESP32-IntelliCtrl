"""
display_menu.py - 菜单渲染模块
显示区域: (0,30) - (172,300)
复用 file_browser 的阻尼动画效果
使用8x8字体
"""
from config import UI_CFG, CTRL_CFG, MENU_CFG
from remote_control import get_ctrl_state

# 导入阻尼缓冲区（与 file_browser 相同）
try:
    from display_ctrl import _damped_buf_pos, _damped_buf_neg
except ImportError:
    _damped_buf_pos = bytearray([80 - i for i in range(80)])
    _damped_buf_neg = bytearray([i - 80 for i in range(80)])


class MenuRenderer:
    """菜单渲染器（带动画选框）"""

    def __init__(self):
        self.selected_idx = 0
        self.list_start = 0
        self.prev_ctrl_x = 0
        self.prev_ctrl_y = 0

        # 动画状态
        self.anim_frame = 80  # 初始为最大值，表示无动画
        self.anim_data = None
        self.hit_boundary = False

    def reset(self):
        """重置状态，切换菜单时调用"""
        self.selected_idx = 0
        self.list_start = 0
        state = get_ctrl_state()
        self.prev_ctrl_x = state["x"]
        self.prev_ctrl_y = state["y"]
        self.anim_frame = 80
        self.anim_data = None
        self.hit_boundary = False

    def render(self, mv, tft, menu_items, title=None, title_color=None):
        """
        渲染菜单

        参数:
            mv: 内存视图缓冲区
            tft: 显示驱动实例
            menu_items: 菜单项列表
            title: 菜单标题（如 "Main Menu" 或 "Nano"）
            title_color: 标题颜色
        """
        if not menu_items:
            tft.draw_text_mv(mv, 10, 120, "No Items", color=UI_CFG.COL_ERROR, bg=UI_CFG.COL_BG, size=16)
            return

        total = len(menu_items)

        # 绘制标题（使用8x8字体放大）
        if title:
            color = title_color if title_color else MENU_CFG.TITLE_COLOR
            tft.draw_text_mv(mv, MENU_CFG.MENU_X_OFFSET, MENU_CFG.TITLE_Y, title,
                             color=color, bg=UI_CFG.COL_BG, size=MENU_CFG.TEXT_SIZE, space=1)

        # 绘制菜单列表
        self._draw_list(mv, tft, menu_items, total)

        # 绘制选框（带动画）
        self._draw_selector(mv, tft, menu_items, total)

    def _draw_list(self, mv, tft, menu_items, total):
        """绘制菜单文字列表（使用8x8字体放大）"""
        # 最大显示字符数：屏幕宽 - 边距 - 间距
        max_chars = 19  # 172像素 / 9像素(8+1) ≈ 19

        line_h = MENU_CFG.MENU_LINE_H
        y_offset = MENU_CFG.MENU_Y_OFFSET
        visible_lines = MENU_CFG.MENU_LINES

        for i in range(visible_lines):
            idx = self.list_start + i
            if idx >= total:
                break

            item = menu_items[idx]
            label = item.get("label", "???")
            icon = item.get("icon", "")

            y = y_offset + i * line_h

            # 构建显示文本: [Icon]Label
            display_text = "[{}]{}".format(icon, label)
            if len(display_text) > max_chars:
                display_text = display_text[:max_chars - 2] + ".."

            if idx == self.selected_idx:
                color = MENU_CFG.TEXT_SELECT_COLOR
                bg = MENU_CFG.TEXT_BG_COLOR
            else:
                color = MENU_CFG.TEXT_COLOR
                bg = UI_CFG.COL_BG

            # 使用8x8字体放大到16像素
            tft.draw_text_mv(mv, MENU_CFG.TEXT_X, y, display_text,
                            color=color, bg=bg, size=MENU_CFG.TEXT_SIZE, space=1)

    def _draw_selector(self, mv, tft, menu_items, total):
        """绘制动画选框"""
        state = get_ctrl_state()
        curr_x, curr_y = state["x"], state["y"]

        dx = curr_x - self.prev_ctrl_x
        dy = curr_y - self.prev_ctrl_y
        moved = False
        self.hit_boundary = False

        line_h = MENU_CFG.MENU_LINE_H
        y_offset = MENU_CFG.MENU_Y_OFFSET
        visible_lines = MENU_CFG.MENU_LINES

        # Y轴控制（上下选择）
        if dy > CTRL_CFG.THRESHOLD:
            if self.selected_idx < total - 1:
                self.selected_idx += 1
                moved = True
                self.hit_boundary = False
                self.anim_data = _damped_buf_pos
                if self.selected_idx >= self.list_start + visible_lines:
                    self.list_start += 1
            else:
                self.selected_idx = total - 1
                self.hit_boundary = True
                moved = False

        elif dy < -CTRL_CFG.THRESHOLD:
            if self.selected_idx > 0:
                self.selected_idx -= 1
                moved = True
                self.hit_boundary = False
                self.anim_data = _damped_buf_neg
                if self.selected_idx < self.list_start:
                    self.list_start -= 1
            else:
                self.selected_idx = 0
                self.hit_boundary = True
                moved = False

        # X轴变化（次要）
        if abs(dx) > CTRL_CFG.THRESHOLD and not moved:
            moved = True
            self.anim_data = _damped_buf_pos if dx > 0 else _damped_buf_neg

        # 边界保护
        if total > 0:
            self.selected_idx = max(0, min(self.selected_idx, total - 1))
        else:
            self.selected_idx = 0

        if total <= visible_lines:
            self.list_start = 0
        else:
            max_start = total - visible_lines
            self.list_start = max(0, min(self.list_start, max_start))
            if self.selected_idx < self.list_start:
                self.list_start = self.selected_idx
            elif self.selected_idx >= self.list_start + visible_lines:
                self.list_start = self.selected_idx - visible_lines + 1

        # 动画帧重置
        if moved:
            self.anim_frame = 0
            self.prev_ctrl_x = curr_x
            self.prev_ctrl_y = curr_y

        # 计算目标位置
        rel_row = self.selected_idx - self.list_start
        rel_row = max(0, min(rel_row, visible_lines - 1))
        target_y = y_offset + rel_row * line_h
        target_x = MENU_CFG.BOX_X

        # ===== 阻尼动画插值 =====
        if self.anim_frame < 80 and self.anim_data is not None:
            buf_idx = self.anim_frame % len(self.anim_data)
            offset = self.anim_data[buf_idx]

            if offset > 127:
                offset -= 256

            y = int(target_y - offset)
            x = target_x
            self.anim_frame += UI_CFG.ANIM_STEP

        elif self.hit_boundary and self.anim_frame < 20:
            shake = int(2 * (1 - self.anim_frame / 20))
            if dy > 0:
                y = target_y + shake
            else:
                y = target_y - shake
            x = target_x
            self.anim_frame += 2

        else:
            x = target_x
            y = target_y

        # 边界保护
        min_y = y_offset
        max_y = y_offset + (visible_lines - 1) * line_h
        y = max(min_y, min(y, max_y))

        # 绘制选框 - 【优化】使用普通矩形代替圆角矩形
        if 0 <= rel_row < visible_lines:
            tft.rect_buf(
                mv,
                x,
                y - 3,  # 选框比文字略高
                MENU_CFG.BOX_W,
                MENU_CFG.BOX_H,
                MENU_CFG.BOX_COLOR,
                fill=False
            )


# 全局实例
menu_renderer = MenuRenderer()


def render_main_menu(mv, tft, menu_items):
    """渲染一级主菜单（无标题）"""
    menu_renderer.render(mv, tft, menu_items, title=None)


def render_sub_menu(mv, tft, menu_items, parent_label=""):
    """渲染二级 Nano 菜单（无标题）"""
    menu_renderer.render(mv, tft, menu_items, title=None)


def reset_menu():
    """重置菜单状态"""
    menu_renderer.reset()


def get_selected_index():
    """获取当前选中索引"""
    return menu_renderer.selected_idx
