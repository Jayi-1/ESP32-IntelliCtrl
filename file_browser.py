import time
from config import UI_CFG, CTRL_CFG
from remote_control import get_ctrl_state

# 导入阻尼缓冲区（从 display_ctrl 获取物理动画曲线）
try:
    from display_ctrl import _damped_buf_pos, _damped_buf_neg
except ImportError:
    # 如果 display_ctrl 未导出，使用简化线性回退
    _damped_buf_pos = bytearray([80 - i for i in range(80)])
    _damped_buf_neg = bytearray([i - 80 for i in range(80)])

class FileBrowser:
    def __init__(self):
        self.selected_idx = 0
        self.list_start = 0
        self.prev_ctrl_x = 0
        self.prev_ctrl_y = 0
        
        # 动画状态（移植自 draw_selection_box）
        self.anim_frame = 0
        self.anim_data = None      # 当前使用的阻尼缓冲区引用
        self.hit_boundary = False  # 是否碰到边界（用于抖动效果）
        
    def reset(self):
        """重置状态，切换页面时调用"""
        self.selected_idx = 0
        self.list_start = 0
        state = get_ctrl_state()
        self.prev_ctrl_x = state["x"]
        self.prev_ctrl_y = state["y"]
        self.anim_frame = 80  # 初始设为最大值，表示动画结束
        self.anim_data = None
        self.hit_boundary = False
        
    def render(self, mv, tft, files):
        """渲染文件浏览器页面"""
        if not files:
            tft.draw_text_mv(mv, 10, 50, "SD Card Empty", color=UI_CFG.COL_ERROR, size=16)
            return
        
        total = len(files)
        
        # 绘制文件列表
        self._draw_list(mv, tft, files, total)
        
        # 绘制选框（带阻尼动画）
        self._draw_selector(mv, tft, files, total)
        
    def _draw_list(self, mv, tft, files, total):
        """绘制文字列表"""
        max_chars = (UI_CFG.SCR_W - 20) // 9
        
        for i in range(UI_CFG.FILE_LINES):
            idx = self.list_start + i
            if idx >= total:
                break
            
            filename = files[idx]
            y = UI_CFG.FILE_Y_OFFSET + i * UI_CFG.FILE_LINE_H
            
            display_name = filename[:max_chars-3] + '...' if len(filename) > max_chars else filename
            
            if idx == self.selected_idx:
                color = UI_CFG.COL_HIGHLIGHT
                bg = UI_CFG.COL_SELECT_BG
            else:
                color = UI_CFG.COL_INFO
                bg = UI_CFG.COL_BG
            
            tft.draw_text_mv(mv, 10, y, display_name, color=color, bg=bg, space=1, size=8)
    
    def _draw_selector(self, mv, tft, files, total):
        """绘制动画选框（移植阻尼算法）"""
        state = get_ctrl_state()
        curr_x, curr_y = state["x"], state["y"]
        
        dx = curr_x - self.prev_ctrl_x
        dy = curr_y - self.prev_ctrl_y
        moved = False
        self.hit_boundary = False
        
        # Y轴控制（上下选择）- 移植自原 draw_selection_box
        if dy > CTRL_CFG.THRESHOLD:
            if self.selected_idx < total - 1:
                self.selected_idx += 1
                moved = True
                self.hit_boundary = False
                # 向下移动使用 buf_1（正向阻尼）
                self.anim_data = _damped_buf_pos
                if self.selected_idx >= self.list_start + UI_CFG.FILE_LINES:
                    self.list_start += 1
            else:
                # 到达底部边界，触发碰撞效果
                self.selected_idx = total - 1
                self.hit_boundary = True
                moved = False
                
        elif dy < -CTRL_CFG.THRESHOLD:
            if self.selected_idx > 0:
                self.selected_idx -= 1
                moved = True
                self.hit_boundary = False
                # 向上移动使用 buf_2（负向阻尼）
                self.anim_data = _damped_buf_neg
                if self.selected_idx < self.list_start:
                    self.list_start -= 1
            else:
                # 到达顶部边界，触发碰撞效果
                self.selected_idx = 0
                self.hit_boundary = True
                moved = False
        
        # X轴变化（保留处理，虽然文件浏览器主要用Y轴）
        if abs(dx) > CTRL_CFG.THRESHOLD and not moved:
            # X轴移动时也触发动画，但使用较小的阻尼
            moved = True
            self.anim_data = _damped_buf_pos if dx > 0 else _damped_buf_neg
        
        # 边界保护（双重保险）
        if total > 0:
            self.selected_idx = max(0, min(self.selected_idx, total - 1))
        else:
            self.selected_idx = 0
            
        if total <= UI_CFG.FILE_LINES:
            self.list_start = 0
        else:
            max_start = total - UI_CFG.FILE_LINES
            self.list_start = max(0, min(self.list_start, max_start))
            # 确保选中项在可视范围内
            if self.selected_idx < self.list_start:
                self.list_start = self.selected_idx
            elif self.selected_idx >= self.list_start + UI_CFG.FILE_LINES:
                self.list_start = self.selected_idx - UI_CFG.FILE_LINES + 1
        
        # 动画帧重置
        if moved:
            self.anim_frame = 0
            self.prev_ctrl_x = curr_x
            self.prev_ctrl_y = curr_y
        
        # 计算目标位置
        rel_row = self.selected_idx - self.list_start
        rel_row = max(0, min(rel_row, UI_CFG.FILE_LINES - 1))
        target_y = UI_CFG.FILE_Y_OFFSET + rel_row * UI_CFG.FILE_LINE_H
        target_x = UI_CFG.FILE_BOX_X  # 从配置获取
        
        # ===== 阻尼动画插值（核心移植部分） =====
        if self.anim_frame < 80 and self.anim_data is not None:
            # 从阻尼缓冲区获取偏移值（0-79帧）
            buf_idx = self.anim_frame % len(self.anim_data)
            offset = self.anim_data[buf_idx]
            
            # 关键：有符号字节转换（unsigned -> signed）
            if offset > 127:
                offset -= 256
            
            # 应用偏移到Y坐标（向上或向下滑动效果）
            y = int(target_y - offset)
            x = target_x
            
            # 动画帧推进（可配置步长）
            self.anim_frame += UI_CFG.ANIM_STEP
            
        elif self.hit_boundary and self.anim_frame < 20:
            # 边界碰撞抖动效果（移植自原 draw_selection_box）
            shake = int(3 * (1 - self.anim_frame / 20))
            if dy > 0:  # 向下碰壁
                y = target_y + shake
            else:       # 向上碰壁
                y = target_y - shake
            x = target_x
            self.anim_frame += 2  # 抖动动画较慢
            
        else:
            # 动画结束，固定在目标位置
            x = target_x
            y = target_y
        
        # 边界保护（确保不画出可视区域）
        min_y = UI_CFG.FILE_Y_OFFSET
        max_y = UI_CFG.FILE_Y_OFFSET + (UI_CFG.FILE_LINES - 1) * UI_CFG.FILE_LINE_H
        y = max(min_y, min(y, max_y))
        
        # 绘制圆角选框（使用UI_CFG配置）
        if 0 <= rel_row < UI_CFG.FILE_LINES:
            tft.round_rect_buf(
                mv, 
                x, 
                y - 5, 
                UI_CFG.FILE_BOX_W,  # BOX_W = SCR_W - 16
                UI_CFG.FILE_LINE_H - 10,  # BOX_H
                UI_CFG.FILE_BOX_R, 
                UI_CFG.COL_WARNING, 
                fill=False
            )

# 全局实例
file_browser = FileBrowser()

# 便捷函数（保持接口兼容）
def init_buffers(damped_func=None):
    """兼容旧接口，实际使用预计算的阻尼缓冲区"""
    pass

def reset_selection():
    file_browser.reset()

def render_page(mv, tft, files):
    file_browser.render(mv, tft, files)