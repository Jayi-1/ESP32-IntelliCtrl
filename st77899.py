# st7789.py  MicroPython-ESP32  80 MHz SPI  172*320  4-Line SPI
"""
"""
import micropython  # 用于 viper 优化
from machine import Pin, SPI, PWM
import time
from text import font, font16
import array

# ========== 从config.py导入引脚配置 ==========
from config import PIN_CFG, SYS_CFG

class ST77899:
    # ========== 硬件脚位（从config.py引用）==========
    PIN_SCL = PIN_CFG.TFT_SCL
    PIN_SDA = PIN_CFG.TFT_SDA
    PIN_CS  = PIN_CFG.TFT_CS
    PIN_DC  = PIN_CFG.TFT_DC
    PIN_RST = PIN_CFG.TFT_RST
    PIN_BL  = PIN_CFG.TFT_BL     # PIN_BL = 48(USB版本) / 46(TCPY版本)

    # ========== 命令常量 ==========
    NOP = 0x00
    SWRESET = 0x01
    SLPIN = 0x10
    SLPOUT = 0x11
    PTLON = 0x12
    NORON = 0x13
    INVOFF = 0x20
    INVON = 0x21
    DISPOFF = 0x28
    DISPON = 0x29
    CASET = 0x2A
    RASET = 0x2B
    RAMWR = 0x2C
    MADCTL = 0x36
    COLMOD = 0x3A

    # ========== 颜色常量 - 基础色 (RGB565) ==========
    BLACK = 0x0000
    WHITE = 0xFFFF
    RED = 0xF800
    GREEN = 0x07E0
    BLUE = 0x001F

    # ========== 颜色常量 - 扩展色 ==========
    CYAN = 0x07FF      # 青色(蓝绿)
    MAGENTA = 0xF81F  # 品红(洋红)
    YELLOW = 0xFFE0    # 黄色
    GRAY = 0x8410      # 灰色
    LGRAY = 0xC618     # 浅灰
    DGRAY = 0x4208     # 深灰
    BROWN = 0xA145      # 棕色
    ORANGE = 0xFD20    # 橙色
    PINK = 0xFC18      # 粉色
    PURPLE = 0x8010    # 紫色
    TEAL = 0x041F      # 蓝绿
    NAVY = 0x000F      # 藏青
    OLIVE = 0x8400     # 橄榄绿
    LIME = 0x07E0      # 亮绿
    AQUA = 0x07FF      # 水绿
    MAROON = 0x8000    # 栗色
    SILVER = 0xC618    # 银色
    GOLD = 0xFEA0      # 金色
    INDIGO = 0x4810    # 靛蓝
    BEIGE = 0xF7BB     # 米色
    IVORY = 0xFFFE     # 象牙白
    LEMON = 0xD7E0     # 柠檬黄
    SKYBLUE = 0x867D   # 天蓝
    VIOLET = 0xC819    # 紫罗兰
    TURQUOISE = 0x471A # 松石绿

    # ========== 方向常量 ==========
    ROTATE_0 = 0x00
    ROTATE_90 = 0xA0
    ROTATE_180 = 0xC0
    ROTATE_270 = 0x60

    # ========== 屏幕参数 ==========
    OFFSET_X = 34
    PHYS_W = 138 + 34    # 206
    PHYS_H = 320
    W = 172              # 默认 0° 逻辑宽
    H = 320              # 默认 0° 逻辑高
    LOG_W = {0: 172, 90: 320, 180: 172, 270: 320}
    LOG_H = {0: 320, 90: 172, 180: 320, 270: 172}

    # ============ 类级“只跑一次”逻辑 ============
    _hw_inited = False           # 类变量：硬件已初始化标志
    _spi       = None            # 类变量：共享 SPI 对象

    DISPLAY_172x320 = {
        0:   (0x00, 172, 320, 34, 0),   # 0°
        90:  (0xA0, 320, 172, 0, 34),   # 90°
        180: (0xC0, 172, 320, 34, 0),   # 180°
        270: (0x60, 320, 172, 0, 34),   # 270°
    }

    @classmethod
    def _init_once(cls):
        """真正只做一次的硬件初始化"""
        if cls._hw_inited:
            return

        # 1. 引脚对象
        cls._pin_rst = Pin(cls.PIN_RST, Pin.OUT)
        cls._pin_cs  = Pin(cls.PIN_CS,  Pin.OUT)
        cls._pin_dc  = Pin(cls.PIN_DC,  Pin.OUT)
        cls._pin_bl  = Pin(cls.PIN_BL,  Pin.OUT)

        # 2. SPI 主机
        #cls._spi = SPI(1, 80_000_000, sck=Pin(cls.PIN_SCL), mosi=Pin(cls.PIN_SDA), miso=None)
        cls._spi = SPI(2, 
               baudrate=80_000_000,
               polarity=1,  # CPOL=1: 时钟空闲高电平
               phase=1,     # CPHA=1: 第二个边沿采样
               sck=Pin(cls.PIN_SCL), 
               mosi=Pin(cls.PIN_SDA), 
               miso=None)
        
        cls._pin_cs(1)

        # 3. 硬件复位
        cls._pin_rst(0); time.sleep_ms(10)
        cls._pin_rst(1); time.sleep_ms(120)

        # 4. 寄存器初始化序列
        cls._write_cmd(cls.SWRESET); time.sleep_ms(120)
        cls._write_cmd(cls.SLPOUT);  time.sleep_ms(120)
        cls._write_cmd(cls.COLMOD);  cls._write_data(0x55)  # RGB565
        cls._write_cmd(cls.INVOFF)
        cls._write_cmd(cls.MADCTL);  cls._write_data(cls.ROTATE_0)
        cls._write_cmd(cls.CASET);   cls._write_data(bytes([0, 0, 0, 171]))
        cls._write_cmd(cls.RASET);   cls._write_data(bytes([0, 0, 0x01, 0x3F]))
        cls._write_cmd(cls.DISPON)

        cls._hw_inited = True        # 标记已完成
        

    @classmethod
    def _write_cmd(cls, cmd):
        cls._pin_cs(0); cls._pin_dc(0)
        cls._spi.write(bytes([cmd]))
        cls._pin_cs(1)

    @classmethod
    def _write_data(cls, data):
        cls._pin_cs(0); cls._pin_dc(1)
        cls._spi.write(data if isinstance(data, (bytes, bytearray)) else bytes([data]))
        cls._pin_cs(1)

    # ============ 实例接口 ============
    def __init__(self, spi_id=1, baud=80_000_000):
        # 保证硬件只初始化一次
        self._init_once()

        # 每个对象私有的状态
        self._rot = 0
        self.W = self.LOG_W[0]
        self.H = self.LOG_H[0]
#         self.rotation(4)
        self.inversion_on()#开启颜色反转才是正确的数值
        # 清屏并开背光
        #self.clear(self.BLACK)
        #self.backlight(100)
        self._bl = PWM(Pin(self.PIN_BL), freq=25_000, duty=0)
        self.backlight(60)          # 默认 100 %
        print("spi已初始化")
        
    def rotation(self, rot):
        rot = rot % 360
        self._rot = rot
        self._write_cmd(self.MADCTL)
        self._write_data({0: self.ROTATE_0,
                         90: self.ROTATE_90,
                        180: self.ROTATE_180,
                        270: self.ROTATE_270}[rot])
        self.W = self.LOG_W[rot]
        self.H = self.LOG_H[rot]

    def set_window(self, x, y, w, h):
        x += self.OFFSET_X          
        xe  = x + w - 1
        ye  = y + h - 1
        self._write_cmd(self.CASET)
        self._write_data(bytes([x >> 8, x & 0xFF, xe >> 8, xe & 0xFF]))
        self._write_cmd(self.RASET)
        self._write_data(bytes([y >> 8, y & 0xFF, ye >> 8, ye & 0xFF]))
        self._write_cmd(self.RAMWR)



#################################################            
    def display_on(self):
        self._write_cmd(self.DISPON)

    def display_off(self):
        self._write_cmd(self.DISPOFF)
        
    # 背光 0-100
    def backlight(self, percent):
        """0–100 % 非阻塞硬件 PWM"""
        percent = max(0, min(100, percent))
        # duty 0–1023
        self._bl.duty(int(percent * 1023 // 100))        
#############DMA让屏幕显示内容#########################################
    def clear_buf(self,mv, color=0xffff):
        """
        整块 RGB565 清屏，只写 memoryview
        mv: memoryview(buf) 长度=W*H*2
        color: 16-bit 背景色
        """
        w = 172                   # 屏幕宽
        h = 320                   # 屏幕高
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF
        for i in range(w * h):
            idx = i * 2
            mv[idx]   = hi
            mv[idx+1] = lo


    def blit_buffer(self, buf, x, y, w, h):
        """
        把一块 RGB565 原始字节数组 buf（长度为 w*h*2）直接 DMA 到屏幕窗口 (x,y,w,h)
        用法：tft.blit_buffer(back_buf, 0, 0, W, H)
        """
        if len(buf) != w * h * 2:
            raise ValueError('buffer length != w*h*2')
        self.set_window(x, y, w, h)   # 设定 GRAM 写入窗口
        cls = self.__class__
        cls._pin_cs(0)
        cls._pin_dc(1)
        cls._spi.write(buf)
        cls._pin_cs(1)

    def inversion_on(self):
        """打开颜色反转（负片效果）"""
        self._write_cmd(self.INVON)

    def inversion_off(self):
        """关闭颜色反转（恢复正常）"""
        self._write_cmd(self.INVOFF)
##################使用DMA显示内容########################################
    def pixel_buf(self, mv, x, y, color):
        """
        画单点（只改 mv，不发 SPI）
        mv: memoryview(buf)
        color: RGB565
        """
        w = self.W
        if 0 <= x < w and 0 <= y < self.H:
            hi = (color >> 8) & 0xFF
            lo = color & 0xFF
            idx = (y * w + x) * 2
            mv[idx]   = hi
            mv[idx+1] = lo

    def line_buf(self, mv, x0, y0, x1, y1, color):
        w = self.W
        h = self.H

        # 快速裁剪：如果完全在屏幕外则跳过
        if (x0 < 0 and x1 < 0) or (x0 >= w and x1 >= w):
            return
        if (y0 < 0 and y1 < 0) or (y0 >= h and y1 >= h):
            return

        # 边界裁剪：Bresenham 算法改进
        dx = abs(x1 - x0); dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1; sy = 1 if y0 < y1 else -1
        err = dx - dy

        # 初始点边界检查
        if 0 <= x0 < w and 0 <= y0 < h:
            idx = (y0 * w + x0) * 2
            mv[idx]   = (color >> 8) & 0xFF
            mv[idx+1] = color & 0xFF

        while True:
            if x0 == x1 and y0 == y1:
                break
            e2 = err << 1
            if e2 > -dy:
                err -= dy; x0 += sx
            if e2 < dx:
                err += dx; y0 += sy

            # 绘制前检查边界
            if 0 <= x0 < w and 0 <= y0 < h:
                idx = (y0 * w + x0) * 2
                mv[idx]   = (color >> 8) & 0xFF
                mv[idx+1] = color & 0xFF

    def circle_buf(self, mv, xc, yc, r, color, fill=False):
        """
        画圆（只改 mv，不发 SPI）
        mv: memoryview(buf)
        xc,yc: 圆心
        r  : 半径
        color: RGB565
        fill: False=空心 True=实心
        """
        w = self.W
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF

        def _pix(x, y):
            if 0 <= x < self.W and 0 <= y < self.H:
                idx = (y * w + x) * 2
                mv[idx]   = hi
                mv[idx+1] = lo

        x = 0
        y = r
        d = 3 - (r << 1)          # Bresenham 决策参数
        while x <= y:
            if fill:
                # 实心：水平扫描线填充
                for dy in range(-y, y + 1):
                    _pix(xc - x, yc + dy)
                    _pix(xc + x, yc + dy)
                for dy in range(-x, x + 1):
                    _pix(xc - y, yc + dy)
                    _pix(xc + y, yc + dy)
            else:
                # 空心：8 对称点
                _pix(xc + x, yc + y); _pix(xc - x, yc + y)
                _pix(xc + x, yc - y); _pix(xc - x, yc - y)
                _pix(xc + y, yc + x); _pix(xc - y, yc + x)
                _pix(xc + y, yc - x); _pix(xc - y, yc - x)

            # Bresenham 迭代
            if d < 0:
                d += (x << 2) + 6
            else:
                d += ((x - y) << 2) + 10
                y -= 1
            x += 1

    def round_rect_buf(self, mv, x, y, w, h, r, color, fill=True):
        """
        画圆角矩形 - 优化版：简化算法，避免开方运算
        r: 圆角半径（像素）
        """
        # 参数校验
        if r < 0: r = 0
        r = min(r, w//2, h//2)
        if r == 0:
            return self.rect_buf(mv, x, y, w, h, color, fill)

        ww = self.W
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF

        # 边界检查
        if x < 0: x = 0
        if y < 0: y = 0
        if x + w > ww: w = ww - x
        if y + h > self.H: h = self.H - y
        if w <= 0 or h <= 0:
            return

        def _pix(px, py):
            if 0 <= px < ww and 0 <= py < self.H:
                idx = (py * ww + px) * 2
                mv[idx]   = hi
                mv[idx+1] = lo

        xe = x + w - 1
        ye = y + h - 1

        if fill:
            # 【优化】预计算圆角收缩表，避免逐行开方
            corner_table = []
            for i in range(r):
                dx = int((r * r - (r - i - 1) * (r - i - 1)) ** 0.5)
                corner_table.append(max(0, min(r, dx)))

            for yy in range(y, ye + 1):
                if yy < y + r:
                    idx = r - (y + r - yy)
                    dx = corner_table[max(0, min(idx, r-1))] if idx < r else 0
                    xs, xe_line = x + r - dx, xe - r + dx
                elif yy > ye - r:
                    idx = r - (yy - (ye - r))
                    dx = corner_table[max(0, min(idx, r-1))] if idx < r else 0
                    xs, xe_line = x + r - dx, xe - r + dx
                else:
                    xs, xe_line = x, xe

                # 整行写入
                if xs <= xe_line:
                    row_data = bytes([hi, lo] * (xe_line - xs + 1))
                    row_start = (yy * ww + xs) * 2
                    mv[row_start:row_start + len(row_data)] = row_data

        else:
            # 空心模式：四条边 + 四个圆角
            for xx in range(x + r, xe - r + 1):
                _pix(xx, y)
                _pix(xx, ye)
            for yy in range(y + r, ye - r + 1):
                _pix(x, yy)
                _pix(xe, yy)

            # 四个圆角：Bresenham画四分之一圆
            def _corner(cx, cy, quadrant):
                x0, y0, d = 0, r, 3 - 2 * r
                while x0 <= y0:
                    if quadrant == 1:
                        pts = [(cx - x0, cy - y0), (cx - y0, cy - x0)]
                    elif quadrant == 2:
                        pts = [(cx + x0, cy - y0), (cx + y0, cy - x0)]
                    elif quadrant == 3:
                        pts = [(cx - x0, cy + y0), (cx - y0, cy + x0)]
                    else:
                        pts = [(cx + x0, cy + y0), (cx + y0, cy - x0)]

                    for px, py in pts:
                        _pix(px, py)

                    if d < 0:
                        d += (x0 << 2) + 6
                    else:
                        d += ((x0 - y0) << 2) + 10
                        y0 -= 1
                    x0 += 1

            _corner(x + r, y + r, 1)
            _corner(xe - r, y + r, 2)
            _corner(x + r, ye - r, 3)
            _corner(xe - r, ye - r, 4)

    def rect_buf(self, mv, x, y, w, h, color, fill=True):
        """
        画矩形（带/不带填充）- 优化版：整行写入
        mv   : memoryview(buf)
        x,y  : 左上角
        w,h  : 宽、高
        color: RGB565
        fill : True=实心  False=空心框
        """
        ww = self.W
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF

        # 边界检查
        if x < 0: x = 0
        if y < 0: y = 0
        if x + w > ww: w = ww - x
        if y + h > self.H: h = self.H - y
        if w <= 0 or h <= 0:
            return

        xe = x + w - 1
        ye = y + h - 1

        if fill:
            # 【优化】实心矩形：整行写入（大幅减少循环次数）
            row_data = bytes([hi, lo] * w)  # 预构建一行像素数据
            for yy in range(y, ye + 1):
                row_start = (yy * ww + x) * 2
                mv[row_start:row_start + w * 2] = row_data
        else:
            # 空心：四条边（优化单边绘制）
            for xx in range(x, xe + 1):
                idx_top = (y * ww + xx) * 2
                mv[idx_top] = hi
                mv[idx_top + 1] = lo
                idx_bottom = (ye * ww + xx) * 2
                mv[idx_bottom] = hi
                mv[idx_bottom + 1] = lo
            for yy in range(y, ye + 1):
                idx_left = (yy * ww + x) * 2
                mv[idx_left] = hi
                mv[idx_left + 1] = lo
                idx_right = (yy * ww + xe) * 2
                mv[idx_right] = hi
                mv[idx_right + 1] = lo

    @micropython.viper
    def _blit_mono_mv_viper(self,mv, dx:int, dy:int, w:int, h:int, bw_data:ptr8, 
                            color:int, bg:int, scale:int, ww:int):
        """
        单色位图 → memoryview 缩放写入（viper 加速版）
        """
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF
        
        # 直接获取 memoryview 的底层指针
        mv_ptr = ptr8(mv)
        
        for row in range(h):
            mask = bw_data[row]
            for col in range(w):
                bit = (mask >> (7 - col)) & 1
                # 计算目标块起始地址（避免内层循环重复计算）
                base_x = dx + col * scale
                base_y = dy + row * scale
                
                # 缩放块（scale=1 或 2 时手动展开更快）
                for sy in range(scale):
                    for sx in range(scale):
                        px = base_x + sx
                        py = base_y + sy
                        # 边界检查（viper 里用 if 开销很小）
                        if 0 <= px < ww and 0 <= py < 320:
                            idx = (py * ww + px) * 2
                            if bit:
                                mv_ptr[idx]   = hi
                                mv_ptr[idx+1] = lo
                        # 背景块已注释掉，不用处理
#                             else:#（背景块）
#                                 mv[idx]   = bg_hi
#                                 mv[idx+1] = bg_lo

    def _blit_mono_mv(self, mv, dx, dy, w, h, bw_data, color, bg, scale, ww):
        """单色位图 → memoryview 缩放写入（普通版本）"""
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF
        ww_local = ww

        for row in range(h):
            mask = bw_data[row]
            for col in range(w):
                bit = (mask >> (7 - col)) & 1
                if bit:
                    for sy in range(scale):
                        for sx in range(scale):
                            px = dx + col * scale + sx
                            py = dy + row * scale + sy
                            if 0 <= px < ww_local and 0 <= py < 320:
                                idx = (py * ww_local + px) * 2
                                mv[idx] = hi
                                mv[idx + 1] = lo

    def draw_char_mv(self, mv, x, y, ch, color=0xFFE0, bg=0x0000, size=8):
        data = font(ch)          # 8 byte 列行式
        scale = size // 8        # 只支持 8/16
        # 直接使用普通版本（兼容所有固件）
        self._blit_mono_mv(mv, x, y, 8, 8, data, color, bg, scale, self.W)
        
    def draw_text_mv(self, mv, x, y, txt, color=0xFFE0, bg=0x0000, space=1, size=8):#显示字符串
        cx, cy = x, y
        step = size + space
        for c in txt:
            if c == '\n':
                cx, cy = x, cy + step
                continue
            self.draw_char_mv(mv, cx, cy, c, color, bg, size)
            cx += step
            if cx + size > self.W:
                cx, cy = x, cy + step

    # ========== 16x12 字体绘制方法 ==========
    def _blit_16x12(self, mv, dx, dy, data, color, bg):
        """
        绘制 16x12 单色位图
        data: 24字节字模数据 (每行2字节 x 12行)
        字节格式: [b1, b0] = [低字节(列0-7), 高字节(列8-15)]
        位格式: LSB-first (bit 0 = 列0/8, bit 7 = 列7/15)
        """
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF
        ww = self.W

        for row in range(12):
            byte_idx = row * 2
            b1 = data[byte_idx]      # 低字节 (位7-0) -> 列0-7
            b0 = data[byte_idx + 1]  # 高字节 (位15-8) -> 列8-15

            for col in range(16):
                if col < 8:
                    # 列0-7: 从b1提取，LSB-first: bit col = 列col
                    bit = (b1 >> col) & 1
                else:
                    # 列8-15: 从b0提取，bit (col-8) = 列col
                    bit = (b0 >> (col - 8)) & 1

                px = dx + col
                py = dy + row
                if 0 <= px < ww and 0 <= py < 320:
                    idx = (py * ww + px) * 2
                    if bit:
                        mv[idx] = hi
                        mv[idx + 1] = lo

    def draw_char16_mv(self, mv, x, y, ch, color=0xFFE0, bg=0x0000):
        """绘制单个16x12字符"""
        data = font16(ch)
        self._blit_16x12(mv, x, y, data, color, bg)

    def draw_text16_mv(self, mv, x, y, txt, color=0xFFE0, bg=0x0000, space=1):
        """
        显示16x12字符串
        参数:
            mv: memoryview缓冲区
            x, y: 起始坐标
            txt: 要显示的字符串
            color: 前景色
            bg: 背景色
            space: 字符间距
        """
        cx, cy = x, y
        char_w = 16
        char_h = 12
        step_w = char_w + space

        for c in txt:
            if c == '\n':
                cx = x
                cy += char_h + space
                continue

            self.draw_char16_mv(mv, cx, cy, c, color, bg)
            cx += step_w

            # 边界检查
            if cx + char_w > self.W:
                cx = x
                cy += char_h + space

    def show_num_mv(self, mv, x, y, value, color=0xFFE0, bg=0x0000, size=8): #显示数字
        txt = str(value)
        w = len(txt) * (size + 1)
        h = size + 2
        # 1. 擦除旧区域FalseTrue
#         self.rect_buf(mv, x, y, w, h, bg)
        # 2. 写新数字
        self.draw_text_mv(mv, x, y, txt, color, bg, size=size)


    def draw_hz_mv(self, mv, x, y, font, fw=32, fh=64, color=0xFFFF):
        # 关键修复：兼容字符串格式的字模数据
        if isinstance(font, str):
            # 将字符串视为原始字节序列（latin-1编码保持字节值不变）
            font = bytearray(font, 'latin-1')
        elif isinstance(font, (list, tuple)):
            font = bytearray(font)
        
        # 确保mv正确
        if isinstance(mv, bytearray):
            mv = memoryview(mv)
        
        ww = 172
        rb = fw >> 3
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF
        
        for row in range(fh):
            py = y + row
            if py < 0 or py >= 320:
                continue
            
            f_off = row * rb
            s_off = (py * ww + x) << 1
            
            for i in range(rb):
                b = font[f_off + i]
                if b == 0:
                    continue
                
                p = s_off + (i << 4)
                if b & 0x80: mv[p] = hi; mv[p+1] = lo
                if b & 0x40: mv[p+2] = hi; mv[p+3] = lo
                if b & 0x20: mv[p+4] = hi; mv[p+5] = lo
                if b & 0x10: mv[p+6] = hi; mv[p+7] = lo
                if b & 0x08: mv[p+8] = hi; mv[p+9] = lo
                if b & 0x04: mv[p+10] = hi; mv[p+11] = lo
                if b & 0x02: mv[p+12] = hi; mv[p+13] = lo
                if b & 0x01: mv[p+14] = hi; mv[p+15] = lo

    def draw_string_mv(self, mv, x, y, s, font_dict, font_w=None, font_h=None,
                       color=0xFFFF, bg=0x0000, gap=1):
        if s is None or font_dict is None:
            return
        if not s:  # 空字符串
            return
        cx, cy = x, y
        screen_w = 172
        
        # 如果未指定尺寸，从第一个字符的字模自动推断（假设正方形）
        if (font_w is None or font_h is None) and s:
            first_font = font_dict.get(s[0])
            if first_font:
                inferred = int((len(first_font) * 8) ** 0.5)
                font_w = font_w or inferred
                font_h = font_h or inferred
            else:
                font_w, font_h = 16, 16  # 默认回退
        
        step = font_w + gap
        
        for ch in s:
            if ch in font_dict:
                self.draw_hz_mv(mv, cx, cy, font_dict[ch], font_w, font_h, color)
                cx += step
                
                # 自动换行判断
                if cx + font_w > screen_w:
                    cx = x
                    cy += font_h + gap  