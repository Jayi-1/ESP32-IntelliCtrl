"""
menu_def.py - 菜单数据结构定义
"""
from config import UI_CFG

# ========== 一级菜单定义 ==========
MAIN_MENU = [
    {"id": "home",  "label": "Home",  "icon": "H"},
    {"id": "time",  "label": "Time",  "icon": "T"},
    {"id": "game",  "label": "Game",  "icon": "G"},
    {"id": "ble",   "label": "BLE",   "icon": "B"},
    {"id": "wifi",  "label": "WiFi",  "icon": "W"},
    {"id": "ai",    "label": "AI",    "icon": "A"},
    {"id": "file",  "label": "File",  "icon": "F"},
    {"id": "set",   "label": "Set",   "icon": "S"},
]

# ========== 二级菜单 (Nano) 定义 ==========
# 每个一级菜单对应一个 Nano 子菜单
SUB_MENUS = {
    "home": [
        {"id": "home_info",    "label": "Info",     "action": "show_info"},
        {"id": "home_about",   "label": "About",    "action": "show_about"},
    ],
    "time": [
        {"id": "time_set",     "label": "Set Time",     "action": "set_time"},
        {"id": "time_alarm",   "label": "Alarm",        "action": "set_alarm"},
        {"id": "time_stopwatch","label": "Stopwatch",   "action": "stopwatch"},
    ],
    "game": [
        {"id": "game_snake",   "label": "Snake",       "action": "snake"},
        {"id": "game_pong",    "label": "Pong",         "action": "pong"},
        {"id": "game_graph",   "label": "Graph",        "action": "graph"},
        {"id": "game_tetris",  "label": "Tetris",       "action": "tetris"},
    ],
    "ble": [
        {"id": "ble_status",   "label": "Status",       "action": "ble_status"},
        {"id": "ble_send",     "label": "Send Data",    "action": "ble_send"},
        {"id": "ble_settings", "label": "Settings",     "action": "ble_settings"},
    ],
    "wifi": [
        {"id": "wifi_sta",     "label": "Station",      "action": "wifi_sta"},
        {"id": "wifi_ap",      "label": "Access Point",  "action": "wifi_ap"},
        {"id": "wifi_scan",    "label": "Scan",         "action": "wifi_scan"},
        {"id": "wifi_off",     "label": "Turn Off",      "action": "wifi_off"},
    ],
    "ai": [
        {"id": "ai_chat",      "label": "Chat",          "action": "ai_chat"},
        {"id": "ai_voice",      "label": "Voice",         "action": "ai_voice"},
        {"id": "ai_image",      "label": "Image Gen",     "action": "ai_image"},
    ],
    "file": [
        {"id": "file_browse",  "label": "Browse",        "action": "file_browse"},
        {"id": "file_upload",  "label": "Upload",         "action": "file_upload"},
        {"id": "file_delete",  "label": "Delete",        "action": "file_delete"},
    ],
    "set": [
        {"id": "set_display",  "label": "Display",        "action": "set_display"},
        {"id": "set_sound",    "label": "Sound",          "action": "set_sound"},
        {"id": "set_system",   "label": "System",         "action": "set_system"},
    ],
}

def get_main_menu():
    """获取一级菜单"""
    return MAIN_MENU

def get_sub_menu(parent_id):
    """获取指定父菜单的二级菜单"""
    return SUB_MENUS.get(parent_id, [])

def get_menu_item(menu_list, index):
    """安全获取菜单项"""
    if 0 <= index < len(menu_list):
        return menu_list[index]
    return None

def find_menu_by_action(action):
    """根据action查找菜单项"""
    # 搜索一级菜单
    for item in MAIN_MENU:
        if item.get("action") == action:
            return item
    # 搜索二级菜单
    for parent_id, sub_list in SUB_MENUS.items():
        for item in sub_list:
            if item.get("action") == action:
                return item
    return None
