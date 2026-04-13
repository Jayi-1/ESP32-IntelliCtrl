# config.py - 所有可调整参数集中于此
from micropython import const

# ========== 硬件引脚配置 ==========
def _load_pins_from_gpio():
    """从gpio.json动态加载引脚配置"""
    _defaults = {
        'TFT_SCL': 40,
        'TFT_SDA': 45,
        'TFT_CS': 42,
        'TFT_DC': 41,
        'TFT_RST': 39,
        'TFT_BL': 46,
        'SD_SPI_ID': 1,
        'SD_CLK': 14,
        'SD_CMD': 15,
        'SD_D0': 16,
        'SD_CS': 21,
        'SD_BAUD': 20_000_000,
        'KEY': 0,
        'LED': 1,
    }
    try:
        import json
        with open('/gpio.json', 'r') as f:
            cfg = json.load(f)
            # 支持嵌套data格式
            if 'data' in cfg:
                cfg = cfg['data']
            # 只提取有效引脚字段
            pins = {}
            for k in _defaults:
                if k in cfg:
                    pins[k] = cfg[k]
                else:
                    pins[k] = _defaults[k]
            print(f"[CFG] 从gpio.json加载引脚配置: {pins}")
            return pins
    except Exception as e:
        print(f"[CFG] gpio.json加载失败，使用默认引脚: {e}")
        return _defaults

# 启动时加载引脚配置
_pins = _load_pins_from_gpio()

class PIN_CFG:
    """所有硬件引脚定义 - 从gpio.json动态加载"""
    # 屏幕 (ST7789)
    TFT_SCL = _pins.get('TFT_SCL', 40)
    TFT_SDA = _pins.get('TFT_SDA', 45)
    TFT_CS = _pins.get('TFT_CS', 42)
    TFT_DC = _pins.get('TFT_DC', 41)
    TFT_RST = _pins.get('TFT_RST', 39)
    TFT_BL = _pins.get('TFT_BL', 46)

    # SD卡 (SPI)
    SD_SPI_ID = _pins.get('SD_SPI_ID', 1)
    SD_CLK = _pins.get('SD_CLK', 14)
    SD_CMD = _pins.get('SD_CMD', 15)
    SD_D0 = _pins.get('SD_D0', 16)
    SD_CS = _pins.get('SD_CS', 21)
    SD_BAUD = _pins.get('SD_BAUD', 20_000_000)

    # 按键
    KEY = _pins.get('KEY', 0)

    # 板载LED（如果有）
    LED = _pins.get('LED', 1)

# ========== 系统参数配置 ==========
class SYS_CFG:
    # 屏幕
    SCR_W = 172
    SCR_H = 320
    SCR_ROT = 0

    # SPI配置
    SPI_ID_SCREEN = 2
    SPI_ID_SD = 1
    SPI_BAUD_SCREEN = 80_000_000
    SPI_BAUD_SD = 20_000_000

    # 内存
    BUF_SIZE = SCR_W * SCR_H * 2  # 110KB per buffer

    # 【整理】按键引脚引用 PIN_CFG
    PIN_KEY = PIN_CFG.KEY

    # BLE
    BLE_NAME = "ESP32-S3-FileHub"
    BLE_MTU = 512
    
# ========== UI 配置 ==========
class UI_CFG:

    SCR_W = 172
    SCR_H = 320
    SCR_ROT = 0

    # 颜色主题（RGB565）
    COL_BG = 0x0000        # 纯黑背景
    COL_TEXT = 0xFFFF      # 纯白文字
    COL_HIGHLIGHT = 0xFFE0 # 黄色高亮
    COL_ACCENT = 0xF81F    # 品红强调
    COL_SUCCESS = 0x07E0   # 绿色成功
    COL_WARNING = 0xFD20   # 橙色警告
    COL_ERROR = 0xF800     # 红色错误
    COL_INFO = 0x07FF      # 青色信息
    COL_LGRAY = 0xC618     # 浅灰色
    COL_DGRAY = 0x4208     # 深灰色
    COL_SELECT_BG = 0x4444  # 选中项背景色

    # 文件浏览器
    FILE_LINES = 9         # 每页显示文件数
    FILE_LINE_H = 35       # 行高
    FILE_Y_OFFSET = 20     # 顶部偏移
    FILE_BOX_R = 10        # 选框圆角半径
    FILE_BOX_W = SCR_W - 16  # 选框宽度（自动计算）
    FILE_BOX_X = 8         # 选框X起始位置
    
    # 动画
    ANIM_STEP = 4          # 动画帧步长（越大越快）
    ANIM_DAMPING = 7       # 阻尼系数
    ANIM_OMEGA = 1         # 振动频率

# ========== 文件服务器配置 ==========
class HTTP_CFG:
    PORT = 80
    MAX_CLIENTS = 2
    CHUNK_SIZE = 1024      # 传输分块大小（字节）
    GC_INTERVAL = 10240    # 每传输多少字节执行GC
    TIMEOUT_MS = 1500      # 客户端超时

# ========== 蓝牙协议配置（扩展） ==========
class BLE_CFG:
    # 协议字节定义（原有）
    CMD_PREFIX = 0xBB      # 遥控指令前缀
    WIFI_PREFIX = 0xCC     # WiFi控制前缀
    NET_PREFIX = 0xAA      # 配网指令前缀
    
    # WiFi子命令（原有 0x30-0x34, 0x36-0x37）
    WIFI_STA_OLD = 0x30    # 切换STA（旧配置/索引0）
    WIFI_STA_NEW = 0x31    # 切换STA（新配置/立即连接）
    WIFI_AP = 0x32         # 切换AP
    WIFI_OFF = 0x33        # 关闭WiFi
    WIFI_QUERY = 0x34      # 查询状态
    WIFI_SCAN = 0x36       # 扫描周围WiFi
    
    # 注意：0x37 在你现有代码中为 WIFI_CONNECT，与 0x31 功能重复
    # 建议将 0x37 改为 WIFI_GET_LIST，或保持 0x37 不变，新指令从 0x38 开始
    
    # 【新增】WiFi列表管理指令（基于 wifi.json 操作）
    WIFI_GET_LIST = const(0x38)   # 获取已保存WiFi列表（仅SSID）
    WIFI_ADD = const(0x39)        # 添加WiFi到json（SSID+密码）
    WIFI_DEL = const(0x3A)        # 删除指定索引的WiFi
    WIFI_CONN_IDX = const(0x3B)   # 连接指定索引的WiFi（从json读取密码）
    
    # 【新增】详细错误码（用于蓝牙响应）
    ERR_WIFI_INDEX = const(0xF1)      # 索引超出范围
    ERR_WIFI_FILE = const(0xF2)       # 文件操作失败（json读写错误）
    ERR_WIFI_SSID_EMPTY = const(0xF3) # SSID为空
    ERR_WIFI_TIMEOUT = const(0xF4)    # 连接超时
    ERR_WIFI_NO_CONFIG = const(0xF5)  # 没有保存的WiFi配置
    
    # 响应码（原有）
    RSP_OK = 0x00
    RSP_FAIL = 0x01
    RSP_CHECK_ERR = 0xFD
    RSP_UNKNOWN = 0xFC
    
    # BLE配置（原有）
    BLE_NAME = "ESP32-S3-FileHub"
    BLE_MTU = 512
    
    # 【新增】状态包格式常量
    STATUS_PKT_LEN = const(16)    # 状态查询响应包固定16字节
    SSID_MAX_LEN = const(10)      # 蓝牙传输时SSID最大长度（定长）

    MAX_WIFI_SAVED = const(10)      # 最大保存WiFi数量


# ========== 控制参数 ==========
class CTRL_CFG:
    STEP = 16              # 按键步长
    THRESHOLD = 5          # 触发阈值
    DEBOUNCE_MS = 50       # 去抖动时间
    LONG_PRESS_MS = 800    # 长按时间
    
    # 边界（0起始，含边界）
    MIN_X = 0
    MAX_X = 172 - 1
    MIN_Y = 0
    MAX_Y = 320 - 1

# ========== WiFi 配置 ==========
class WIFI_CFG:
    AP_SSID = "ESP32-S3-FileHub"
    AP_PASS = "12345678"
    AP_CHAN = 6
    STA_RETRY = 2          # STA连接重试次数
    STA_TIMEOUT_S = 15     # STA连接超时（秒）
    
    # 【新增】WiFi列表限制
    #MAX_WIFI_SAVED = const(10)    # 最多保存10个WiFi（防止json过大）
    
    # 【新增】默认连接策略
    DEFAULT_CONN_INDEX = const(0) # 默认连接索引0（第一个）
    
class FILE_CFG:
    """文件传输相关常量"""
    CHUNK_SIZE = 512       # 蓝牙分包大小
    MAX_FILENAME = 32      # 文件名最大长度
    PORT = 80              # 网页访问端口（手机浏览器输入ESP的IP）
    MAX_CLIENTS = 3        # 最大同时连接数（ESP32内存小，别太大）
    TIMEOUT_MS = 3000      # 连接超时3秒（防止卡住）
    CHUNK_SIZE = 4096      # 传输块大小（4KB比较均衡）
    GC_INTERVAL = 65536    # 每传64KB清理一次内存

# ========== WiFi配网配置 ==========
class WIFI_PROV_CFG:
    MAX_SSID = 31          # SSID最大长度（WiFi标准限制）
    MAX_PWD = 63           # 密码最大长度（WiFi标准限制）
    CONNECT_TIMEOUT = 10   # 连接超时（秒）
    PROV_TAG = 0x03       # 配网命令字标签

# ========== 菜单配置 ==========
class MENU_CFG:
    # 菜单显示区域 (0,30) - (172,300)
    SCR_W = 172
    SCR_H = 320
    AREA_X = 0
    AREA_Y = 30
    AREA_H = 300
    AREA_END_Y = 300  # 底部边界

    # 菜单布局 (适配8x8字体放大到16像素)
    MENU_LINES = 8            # 每页显示菜单项数（8行）
    MENU_LINE_H = 32          # 行高（可容纳16像素高度字体）
    MENU_Y_OFFSET = 32       # 内容起始Y（无标题，更靠上）
    MENU_X_OFFSET = 5        # X边距

    # 标题
    TITLE_Y = 28              # 标题Y位置
    TITLE_COLOR = UI_CFG.COL_ACCENT
    SUB_TITLE_COLOR = UI_CFG.COL_SUCCESS

    # 选框
    BOX_X = 4                 # 选框X起始
    BOX_W = SCR_W - 8        # 选框宽度
    BOX_H = MENU_LINE_H - 4  # 选框高度
    BOX_R = 6                # 圆角半径
    BOX_COLOR = UI_CFG.COL_ACCENT

    # 8x8字体配置（放大到16像素）
    TEXT_SIZE = 8            # 字体大小（8x8放大2倍）
    TEXT_X = 8               # 文字X位置
    TEXT_COLOR = UI_CFG.COL_TEXT
    TEXT_SELECT_COLOR = UI_CFG.COL_HIGHLIGHT
    TEXT_BG_COLOR = UI_CFG.COL_SELECT_BG

# ========== 画板(Graph Board)配置 ==========
class GRAPH_CFG:
    # 屏幕尺寸（引用系统配置）
    SCR_W = UI_CFG.SCR_W
    SCR_H = UI_CFG.SCR_H

    # 选择界面布局
    SELECT_Y_OFFSET = 70      # 函数列表起始Y
    SELECT_LINE_H = 30        # 函数列表行高
    SELECT_BOX_X = 10         # 选中框X
    SELECT_BOX_W = 152        # 选中框宽度
    SELECT_BOX_H = 26         # 选中框高度
    TITLE_X = 44              # 标题X
    TITLE_Y1 = 20             # 第一行标题Y
    TITLE_Y2 = 38             # 第二行标题Y
    HINT_Y = 280              # 提示文字Y

    # 查看界面布局
    PANEL_H = 50              # 底部面板高度
    PANEL_Y_OFFSET = 3        # 面板内文字Y偏移

    # 3D动画配置
    CUBE_ANGLE_SPEED = 0.02   # 立方体旋转速度
    CUBE_ANGLE_RY_FACTOR = 0.7 # Y轴旋转因子
    CUBE_ANGLE_RZ_FACTOR = 0.3 # Z轴旋转因子
    SPHERE_ANGLE_SPEED = 0.01  # 球体旋转速度
    SPHERE_ANGLE_RY_FACTOR = 0.8 # 球体Y轴旋转因子
    SPHERE_ANGLE_RZ_FACTOR = 0.5 # 球体Z轴旋转因子
    CUBE_CENTER_Y = 150        # 立方体中心Y
    SPHERE_CENTER_Y = 140     # 球体中心Y
    PERSPECTIVE_CUBE = 250    # 立方体透视距离
    PERSPECTIVE_SPHERE = 300  # 球体透视距离
    SPHERE_DENS = 10          # 球体网格密度
    SPHERE_LON_STEP = 15      # 球体经线步进
    SPHERE_LAT_STEP = 30      # 球体纬线步进
    AXIS_LEN_FACTOR = 1.2     # 坐标轴长度因子

    # 2D图形配置
    DAMPED_T_SCALE = 20.0     # 阻尼正弦时间缩放
    DAMPED_ENVELOPE_COLOR = UI_CFG.COL_WARNING  # 包络线颜色
    SECOND_ORDER_Y_AXIS = 150 # 二阶系统Y轴位置
    SECOND_ORDER_STEADY = 86  # 稳态线Y
    SECOND_ORDER_T = 0.03     # 积分时间步长
    SECOND_ORDER_SCALE = 86   # Y轴缩放

    # 太阳系配置
    SOLAR_SPEED = 0.02        # 太阳系旋转速度
    SOLAR_ANGLE_RY_FACTOR = 0.3  # Y轴旋转因子
    SOLAR_ANGLE_RZ_FACTOR = 0.1  # Z轴旋转因子
    SOLAR_PERSPECTIVE = 200    # 太阳系透视距离
    SOLAR_SUN_R = 12          # 太阳半径