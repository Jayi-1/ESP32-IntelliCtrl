# boot.py - 系统启动最先执行
import machine
import gc
import time

from machine import Pin
from config import SYS_CFG, PIN_CFG


# ========== 预分配大内存块 ==========
def pre_alloc_memory():
    global FRONT_BUF, BACK_BUF, DRAW_MV
    
    try:
        FRONT_BUF = bytearray(SYS_CFG.BUF_SIZE)
        BACK_BUF = bytearray(SYS_CFG.BUF_SIZE)
        DRAW_MV = memoryview(BACK_BUF)
        print(f"[BOOT] 双缓冲已分配: {SYS_CFG.BUF_SIZE*2//1024}KB")
    except MemoryError:
        print("[BOOT] 内存不足，使用单缓冲")
        FRONT_BUF = None
        BACK_BUF = bytearray(SYS_CFG.BUF_SIZE)
        DRAW_MV = memoryview(BACK_BUF)
    
    gc.collect()
    return FRONT_BUF, BACK_BUF, DRAW_MV

# ========== 硬件自检 ==========
def hw_init():
    Pin(PIN_CFG.TFT_BL, Pin.OUT).value(0)
    
    try:
        led = Pin(PIN_CFG.LED, Pin.OUT)
        led.value(1)
        time.sleep_ms(100)
        led.value(0)
    except (AttributeError, OSError):
        pass
    
    print("[BOOT] 硬件自检通过")
    gc.collect()

# ========== 执行 ==========
hw_init()
FRONT_BUF, BACK_BUF, DRAW_MV = pre_alloc_memory()
print("[BOOT] 系统就绪，即将加载 main.py")
gc.collect()