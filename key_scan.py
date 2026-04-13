import time
from machine import Pin, Timer
from config import SYS_CFG, CTRL_CFG
import bluetooth_ctrl  # 【优化】import移到模块顶部

# 【新增】按键状态常量
_STATE_IDLE = 0
_STATE_PRESSED = 1
_STATE_LONG = 2

class KeyScanner:
    def __init__(self):
        self.pin = Pin(SYS_CFG.PIN_KEY, Pin.IN, Pin.PULL_UP)
        self.state = _STATE_IDLE
        self.press_time = 0
        self.last_value = 1
        self.debounce_cnt = 0

    def scan(self):
        """10ms调用一次"""
        val = self.pin.value()
        now = time.ticks_ms()

        # 去抖动
        if val != self.last_value:
            self.debounce_cnt += 1
            if self.debounce_cnt >= 3:  # 30ms稳定
                self.last_value = val
                self.debounce_cnt = 0
            else:
                return
        else:
            self.debounce_cnt = 0

        # 状态机
        if self.state == _STATE_IDLE and val == 0:  # 检测到按下
            self.state = _STATE_PRESSED
            self.press_time = now

        elif self.state == _STATE_PRESSED:  # 按下中
            if val == 1:  # 释放 - 短按
                if time.ticks_diff(now, self.press_time) < CTRL_CFG.LONG_PRESS_MS:
                    self._on_short_press()
                self.state = _STATE_IDLE
            elif time.ticks_diff(now, self.press_time) > CTRL_CFG.LONG_PRESS_MS:  # 长按
                self._on_long_press()
                self.state = _STATE_LONG

        elif self.state == _STATE_LONG and val == 1:  # 长按后释放
            self.state = _STATE_IDLE

    def _on_short_press(self):
        """短按：开启蓝牙"""
        bluetooth_ctrl.ble_on()
        print("[KEY] 短按: 蓝牙开启")

    def _on_long_press(self):
        """长按：关闭蓝牙"""
        bluetooth_ctrl.ble_off()
        print("[KEY] 长按: 蓝牙关闭")

# 全局实例
_key_scanner = KeyScanner()

def start():
    """启动按键扫描定时器"""
    tim = Timer(0)
    tim.init(period=10, mode=Timer.PERIODIC, callback=lambda t: _key_scanner.scan())
    print("[KEY] 按键扫描已启动")