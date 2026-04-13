"""
remote_control.py - 遥控输入处理
支持主页、文件页、菜单导航

页面状态:
  - home: 首页
  - files: SD卡文件浏览
  - menu_main: 主菜单（一级菜单）
  - menu_sub: Nano菜单（二级菜单）
  - app: 应用页面
"""
from config import CTRL_CFG

# 页面常量
PAGE_HOME = "home"
PAGE_FILES = "files"
PAGE_MENU_MAIN = "menu_main"
PAGE_MENU_SUB = "menu_sub"
PAGE_APP = "app"


class MenuState:
    """菜单导航状态"""

    def __init__(self):
        self.main_idx = 0  # 一级菜单选中索引
        self.sub_idx = 0  # 二级菜单选中索引
        self.current_parent = None  # 当前父菜单ID
        self.app_id = None  # 当前应用ID
        self.app_param = None  # 应用参数


class InputState:
    """输入状态管理"""

    def __init__(self):
        self.x = 0
        self.y = 42  # 初始Y位置
        self.page = PAGE_HOME  # 当前页面

        # 菜单状态
        self.menu = MenuState()

    def reset(self):
        self.x = (CTRL_CFG.MIN_X + CTRL_CFG.MAX_X) // 2
        self.y = (CTRL_CFG.MIN_Y + CTRL_CFG.MAX_Y) // 2


# 全局状态
_state = InputState()


def get_page():
    return _state.page


def set_page(page):
    _state.page = page
    if page == PAGE_HOME:
        _state.reset()


def get_ctrl_state():
    """获取当前控制状态（供 file_browser 和 menu 使用）"""
    return {"x": _state.x, "y": _state.y}


def get_menu_state():
    """获取菜单状态"""
    return _state.menu


def _navigate_up():
    """上移动"""
    _state.y = max(CTRL_CFG.MIN_Y, _state.y - CTRL_CFG.STEP)


def _navigate_down():
    """下移动"""
    _state.y = min(CTRL_CFG.MAX_Y, _state.y + CTRL_CFG.STEP)


def _navigate_left():
    """左移动"""
    _state.x = max(CTRL_CFG.MIN_X, _state.x - CTRL_CFG.STEP)


def _navigate_right():
    """右移动"""
    _state.x = min(CTRL_CFG.MAX_X, _state.x + CTRL_CFG.STEP)


def handle_input(cmd):
    """
    处理蓝牙/按键输入
    返回响应字节
    """
    # ===== 方向控制 =====
    if cmd == 0x10:  # UP
        _navigate_up()
        return b'UP'
    elif cmd == 0x11:  # DOWN
        _navigate_down()
        return b'DN'
    elif cmd == 0x12:  # LEFT
        _navigate_left()
        return b'LF'
    elif cmd == 0x13:  # RIGHT
        _navigate_right()
        return b'RT'

    # ===== 页面切换控制 =====
    elif cmd == 0x14:  # OK
        return _handle_ok()
    elif cmd == 0x15:  # BACK
        return _handle_back()

    return b'\xE1'  # 未知命令


def _handle_ok():
    """处理OK键"""
    page = _state.page

    if page == PAGE_HOME:
        # 首页 -> 进入主菜单
        _state.page = PAGE_MENU_MAIN
        _state.menu.main_idx = 0
        return b'OK'

    elif page == PAGE_FILES:
        # 文件页保持不变（文件浏览使用自己的逻辑）
        return b'OK'

    elif page == PAGE_MENU_MAIN:
        # 主菜单 -> 进入Nano子菜单
        from menu_def import get_main_menu
        menu_items = get_main_menu()
        if menu_items:
            idx = min(_state.menu.main_idx, len(menu_items) - 1)
            parent = menu_items[idx]
            _state.menu.current_parent = parent["id"]
            _state.menu.sub_idx = 0
            _state.page = PAGE_MENU_SUB
        return b'OK'

    elif page == PAGE_MENU_SUB:
        # Nano子菜单 -> 进入应用
        from menu_def import get_sub_menu
        sub_items = get_sub_menu(_state.menu.current_parent)
        if sub_items:
            idx = min(_state.menu.sub_idx, len(sub_items) - 1)
            app = sub_items[idx]
            _state.menu.app_id = app["id"]
            _state.menu.app_param = app.get("param")
            _state.page = PAGE_APP
            # 【新增】如果是贪吃蛇游戏，初始化游戏
            if _state.menu.app_id == "game_snake":
                import snake_game
                snake_game.init_snake_game()
            # 【新增】如果是画板游戏，初始化画板
            elif _state.menu.app_id == "game_graph":
                import graph_board
                graph_board.init_graph_board()
        return b'OK'

    elif page == PAGE_APP:
        # 应用页面：OK键启动/重新开始游戏
        app_id = _state.menu.app_id
        if app_id == "game_snake":
            import snake_game
            game = snake_game.get_snake_game()
            # 在菜单状态按OK开始游戏，在游戏结束时按OK重新开始
            if game.is_menu() or game.is_game_over():
                game.start_game()
        elif app_id == "game_graph":
            # 画板：OK键进入查看模式
            import graph_board
            board = graph_board.get_graph_board()
            if board.state == "SELECT":
                board.enter_view()
        return b'OK'

    return b'OK'


def _handle_back():
    """处理BACK键"""
    page = _state.page

    if page == PAGE_HOME:
        # 首页：BACK无效
        return b'BK'

    elif page == PAGE_FILES:
        # 文件页 -> 返回首页
        _state.page = PAGE_HOME
        _state.reset()
        return b'BK'

    elif page == PAGE_MENU_MAIN:
        # 主菜单 -> 返回首页
        _state.page = PAGE_HOME
        _state.reset()
        return b'BK'

    elif page == PAGE_MENU_SUB:
        # Nano子菜单 -> 返回主菜单
        _state.page = PAGE_MENU_MAIN
        _state.menu.sub_idx = 0
        return b'BK'

    elif page == PAGE_APP:
        # 应用页面 -> 返回Nano子菜单
        app_id = _state.menu.app_id
        if app_id == "game_snake":
            # 【修改】贪吃蛇游戏，重置游戏状态
            import snake_game
            game = snake_game.get_snake_game()
            game.reset_game()  # 重置游戏状态
        elif app_id == "game_graph":
            # 【新增】画板游戏处理返回
            import graph_board
            board = graph_board.get_graph_board()
            if board.state == "VIEW":
                # 在查看模式：退出到选择模式
                board.exit_view()
                return b'BK'
            # 在选择模式：退出到菜单（fallthrough）
        _state.page = PAGE_MENU_SUB
        _state.menu.app_id = None
        return b'BK'

    return b'BK'


def update_main_index(idx):
    """外部更新主菜单选中索引（供 display_menu 调用）"""
    _state.menu.main_idx = idx


def update_sub_index(idx):
    """外部更新子菜单选中索引（供 display_menu 调用）"""
    _state.menu.sub_idx = idx
