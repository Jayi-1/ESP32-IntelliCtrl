"""
snake_game.py - 贪吃蛇游戏（优化版）
屏幕: 172 x 320 像素
使用方向键控制蛇移动
"""
from config import UI_CFG, CTRL_CFG
from remote_control import get_ctrl_state
import urandom
import time

# ========== 游戏常量 ==========
# 游戏区域设置
GAME_LEFT = 4
GAME_TOP = 30
GAME_RIGHT = 168
GAME_BOTTOM = 296

# 网格大小（蛇身体每节的大小）
GRID_SIZE = 8

# 计算游戏区域网格数量
GAME_WIDTH = GAME_RIGHT - GAME_LEFT
GAME_HEIGHT = GAME_BOTTOM - GAME_TOP
GRID_COLS = GAME_WIDTH // GRID_SIZE  # 水平网格数
GRID_ROWS = GAME_HEIGHT // GRID_SIZE  # 垂直网格数

# 游戏速度（数值越大越慢）
SPEED_INIT = 300  # 初始速度（毫秒）- 降低速度
SPEED_MIN = 150   # 最快速度

# 游戏状态
STATE_MENU = 0    # 开始菜单
STATE_PLAY = 1   # 游戏中
STATE_OVER = 2   # 游戏结束

# 方向
DIR_UP = 0
DIR_DOWN = 1
DIR_LEFT = 2
DIR_RIGHT = 3

# 颜色
COL_SNAKE_HEAD = UI_CFG.COL_HIGHLIGHT  # 蛇头颜色（黄色）
COL_SNAKE_BODY = UI_CFG.COL_SUCCESS    # 蛇身颜色（绿色）
COL_FOOD = UI_CFG.COL_ERROR            # 食物颜色（红色）
COL_WALL = UI_CFG.COL_DGRAY            # 边界颜色
COL_BG = UI_CFG.COL_BG                # 背景色（黑色）
COL_TEXT = UI_CFG.COL_TEXT            # 文字颜色（白色）


class SnakeGame:
    """贪吃蛇游戏类"""

    def __init__(self):
        self.reset_game()
        self.exit_requested = False
        self.last_update_time = 0  # 上次更新时间
        self.need_redraw = True    # 是否需要重绘

    def request_exit(self):
        """请求退出游戏"""
        self.exit_requested = True

    def reset_game(self):
        """重置游戏状态"""
        self.exit_requested = False

        # 蛇初始位置（中间偏左）
        init_x = GRID_COLS // 3
        init_y = GRID_ROWS // 2
        self.snake = [
            (init_x, init_y),       # 蛇头
            (init_x - 1, init_y),   # 身体1
            (init_x - 2, init_y),   # 身体2
        ]

        # 蛇移动方向（初始向右）
        self.direction = DIR_RIGHT
        self.next_direction = DIR_RIGHT

        # 游戏状态
        self.state = STATE_MENU

        # 食物位置
        self.food = None
        self.spawn_food()

        # 分数
        self.score = 0

        # 速度
        self.speed = SPEED_INIT

        # 按键状态（用于检测方向变化）
        self.prev_x = 0
        self.prev_y = 0

        # 重置更新时间
        self.last_update_time = time.ticks_ms()
        self.need_redraw = True

    def spawn_food(self):
        """生成食物"""
        while True:
            x = urandom.getrandbits(8) % GRID_COLS
            y = urandom.getrandbits(8) % GRID_ROWS
            if (x, y) not in self.snake:
                self.food = (x, y)
                break

    def handle_input(self):
        """处理方向键输入"""
        state = get_ctrl_state()
        curr_x = state["x"]
        curr_y = state["y"]

        dx = curr_x - self.prev_x
        dy = curr_y - self.prev_y

        # 只有当移动超过阈值时才改变方向
        if abs(dx) > CTRL_CFG.THRESHOLD or abs(dy) > CTRL_CFG.THRESHOLD:
            if abs(dy) > abs(dx):
                if dy > 0 and self.direction != DIR_UP:
                    self.next_direction = DIR_DOWN
                elif dy < 0 and self.direction != DIR_DOWN:
                    self.next_direction = DIR_UP
            else:
                if dx > 0 and self.direction != DIR_LEFT:
                    self.next_direction = DIR_RIGHT
                elif dx < 0 and self.direction != DIR_RIGHT:
                    self.next_direction = DIR_LEFT

        self.prev_x = curr_x
        self.prev_y = curr_y

    def update(self):
        """更新游戏逻辑（基于时间控制）"""
        if self.state != STATE_PLAY:
            return

        # 时间控制 - 只有到达更新间隔时才移动
        now = time.ticks_ms()
        if time.ticks_diff(now, self.last_update_time) < self.speed:
            return  # 还没到更新时间

        self.last_update_time = now
        self.need_redraw = True

        # 更新方向
        self.direction = self.next_direction
        self.handle_input()

        # 计算蛇头新位置
        head_x, head_y = self.snake[0]

        if self.direction == DIR_UP:
            head_y -= 1
        elif self.direction == DIR_DOWN:
            head_y += 1
        elif self.direction == DIR_LEFT:
            head_x -= 1
        elif self.direction == DIR_RIGHT:
            head_x += 1

        # 检查撞墙
        if head_x < 0 or head_x >= GRID_COLS or head_y < 0 or head_y >= GRID_ROWS:
            self.state = STATE_OVER
            return

        # 检查撞到自己
        if (head_x, head_y) in self.snake:
            self.state = STATE_OVER
            return

        # 移动蛇
        new_head = (head_x, head_y)
        self.snake.insert(0, new_head)

        # 检查吃到食物
        if new_head == self.food:
            self.score += 10
            self.spawn_food()
            # 加速（但有下限）
            self.speed = max(SPEED_MIN, self.speed - 5)
        else:
            # 没吃到食物，移除尾巴
            self.snake.pop()

    def draw(self, mv, tft):
        """绘制游戏画面"""
        # 绘制背景（只绘制一次或需要时重绘）
        tft.rect_buf(mv, GAME_LEFT, GAME_TOP, GAME_WIDTH, GAME_HEIGHT,
                     COL_BG, fill=True)

        # 绘制边界线
        tft.rect_buf(mv, GAME_LEFT, GAME_TOP, GAME_WIDTH, GAME_HEIGHT,
                     COL_WALL, fill=False)

        if self.state == STATE_MENU:
            self._draw_menu(mv, tft)
        elif self.state == STATE_PLAY:
            self._draw_game(mv, tft)
        elif self.state == STATE_OVER:
            self._draw_game(mv, tft)
            self._draw_game_over(mv, tft)

    def _draw_menu(self, mv, tft):
        """绘制开始菜单"""
        # 标题 - 简化绘制
        tft.draw_text_mv(mv, 44, 80, "SNAKE", color=COL_SNAKE_HEAD, bg=COL_BG, size=16, space=1)

        # 提示
        tft.draw_text_mv(mv, 40, 120, "OK Start", color=COL_TEXT, bg=COL_BG, size=16, space=1)

        # 绘制装饰蛇（简化）
        start_x = GAME_LEFT + 20
        start_y = GAME_TOP + 50
        for i in range(6):
            x = start_x + i * 8
            color = COL_SNAKE_BODY if i < 5 else COL_SNAKE_HEAD
            tft.rect_buf(mv, x, start_y, 6, 6, color, fill=True)

    def _draw_game(self, mv, tft):
        """绘制游戏画面"""
        # 绘制食物
        if self.food:
            fx, fy = self.food
            x = GAME_LEFT + fx * GRID_SIZE + 1
            y = GAME_TOP + fy * GRID_SIZE + 1
            tft.rect_buf(mv, x, y, GRID_SIZE - 2, GRID_SIZE - 2, COL_FOOD, fill=True)

        # 绘制蛇（简化矩形）
        for i, (gx, gy) in enumerate(self.snake):
            x = GAME_LEFT + gx * GRID_SIZE + 1
            y = GAME_TOP + gy * GRID_SIZE + 1
            color = COL_SNAKE_HEAD if i == 0 else COL_SNAKE_BODY
            tft.rect_buf(mv, x, y, GRID_SIZE - 2, GRID_SIZE - 2, color, fill=True)

        # 绘制分数
        tft.draw_text_mv(mv, 4, 4, "S:{}".format(self.score), color=COL_TEXT, bg=COL_BG, size=16, space=1)

    def _draw_game_over(self, mv, tft):
        """绘制游戏结束画面"""
        # 半透明黑色遮罩
        tft.rect_buf(mv, GAME_LEFT + 10, GAME_TOP + 50, GAME_WIDTH - 20, 80,
                     0x0000, fill=True)

        # 边框
        tft.rect_buf(mv, GAME_LEFT + 10, GAME_TOP + 50, GAME_WIDTH - 20, 80,
                     COL_WALL, fill=False)

        # GAME OVER 文字
        tft.draw_text_mv(mv, 44, 65, "GAME", color=COL_FOOD, bg=0x0000, size=16, space=1)
        tft.draw_text_mv(mv, 48, 82, "OVER", color=COL_FOOD, bg=0x0000, size=16, space=1)

        # 分数和提示
        tft.draw_text_mv(mv, 36, 105, "S:{}".format(self.score), color=COL_TEXT, bg=0x0000, size=16, space=1)
        tft.draw_text_mv(mv, 28, 120, "OK Restart", color=COL_TEXT, bg=0x0000, size=16, space=1)

    def start_game(self):
        """开始游戏"""
        self.reset_game()
        self.state = STATE_PLAY

    def is_playing(self):
        """是否在游戏中"""
        return self.state == STATE_PLAY

    def is_game_over(self):
        """是否游戏结束"""
        return self.state == STATE_OVER

    def is_menu(self):
        """是否在开始菜单"""
        return self.state == STATE_MENU

    def get_score(self):
        """获取当前分数"""
        return self.score


# 全局游戏实例
_snake_game = None


def get_snake_game():
    """获取贪吃蛇游戏实例"""
    global _snake_game
    if _snake_game is None:
        _snake_game = SnakeGame()
    return _snake_game


def init_snake_game():
    """初始化贪吃蛇游戏"""
    global _snake_game
    _snake_game = SnakeGame()
    return _snake_game
