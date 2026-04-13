# time_sync.py
# 时间同步模块 - 使用字符串分割，避免 re 模块

import json
import time
import machine

try:
    import urequests as requests
except ImportError:
    import requests

# ========== 全局配置缓存 ==========
_config_cache = None
_last_fetch_time = 0
_MIN_INTERVAL_MS = 5000  # 最小请求间隔 5 秒

# 【优化】星期映射表（全局常量）
_WEEKDAY_MAP = {
    '星期一': 0, '星期二': 1, '星期三': 2, '星期四': 3,
    '星期五': 4, '星期六': 5, '星期日': 6, '星期天': 6
}


def load_api_config():
    """从 /api.json 加载配置"""
    global _config_cache
    try:
        with open('/api.json', 'r') as f:
            cfg = json.load(f)
            # 【修复】支持嵌套结构：如果包含 data 字段，提取内部实际配置
            if isinstance(cfg, dict) and 'data' in cfg and isinstance(cfg['data'], dict):
                _config_cache = cfg['data']
                print(f"[TimeSync] 配置已加载: 嵌套模式 ({cfg.get('file', 'unknown')})")
            else:
                _config_cache = cfg
                print(f"[TimeSync] 配置已加载: 扁平模式")
            return _config_cache
    except OSError:
        # 文件不存在是正常的，直接跳过
        print("[TimeSync] /api.json 不存在，跳过时间同步")
        return None
    except Exception as e:
        # 其他错误（如JSON格式错误），跳过不崩溃
        print(f"[TimeSync] 配置读取失败: {e}，跳过时间同步")
        return None


def fetch_time(show_raw=False):
    """获取网络时间并设置 RTC"""
    global _last_fetch_time

    now = time.ticks_ms()
    if time.ticks_diff(now, _last_fetch_time) < _MIN_INTERVAL_MS:
        print("[TimeSync] 请求过于频繁")
        return None

    _last_fetch_time = now

    config = _config_cache or load_api_config()
    if not config:
        return None

    url = config.get('url')
    if not url:
        print("[TimeSync] 错误: 配置中缺少 URL")
        return None

    print(f"[TimeSync] 请求: {url[:50]}...")

    try:
        response = requests.get(url, timeout=5)

        if response.status_code != 200:
            print(f"[TimeSync] HTTP 错误: {response.status_code}")
            response.close()
            return None

        raw_text = response.text
        response.close()

        if show_raw:
            print(f"[TimeSync] 原始响应: {raw_text[:150]}")

        try:
            data = json.loads(raw_text)
        except Exception:
            print(f"[TimeSync] 错误: 响应不是有效的 JSON")
            return None

        # 解析时间数据
        result = parse_time_response(data)

        if result and result.get('datetime'):
            print(f"[TimeSync] 获取成功: {result['datetime']}")

            # 自动设置 RTC
            if config.get('enabled'):
                set_rtc_time(result)

        return result

    except Exception as e:
        print(f"[TimeSync] 请求错误: {e}")
        return None


def parse_time_response(data):
    """
    解析时间 API 返回的 JSON 数据
    适配 k780/nowapi 格式
    """
    result = {
        'datetime': None,
        'weekday': 4,  # 默认星期五
        'timestamp': None
    }

    try:
        if 'result' in data:
            res = data['result']

            # 提取 datetime 字符串（优先级最高）
            result['datetime'] = res.get('datetime_1') or res.get('datetime')
            result['timestamp'] = res.get('timestamp') or res.get('ts')

            # 处理星期
            week_str = res.get('week_1') or res.get('week')
            if week_str:
                result['weekday'] = _WEEKDAY_MAP.get(week_str, 4)

        elif 'timestamp' in data:
            result['timestamp'] = data.get('timestamp')
            result['datetime'] = data.get('datetime')

    except Exception as e:
        print(f"[TimeSync] 解析错误: {e}")

    return result


def _parse_datetime(dt_str):
    """
    【优化】解析日期时间字符串
    兼容格式: "2026-03-20 21:39:14", "2026-03-20T21:39:14", "2026/03/20 21:39:14"
    返回: (year, month, day, hour, minute, second) 或 None
    """
    try:
        # 统一分隔符
        dt_str = dt_str.replace('T', ' ').replace('/', '-')
        parts = dt_str.split(' ')
        if len(parts) < 2:
            return None

        date_nums = parts[0].split('-')
        time_nums = parts[1].split(':')

        if len(date_nums) < 3 or len(time_nums) < 3:
            return None

        return (
            int(date_nums[0]),  # year
            int(date_nums[1]),  # month
            int(date_nums[2]),  # day
            int(time_nums[0]),  # hour
            int(time_nums[1]),  # minute
            int(time_nums[2]) if len(time_nums) > 2 else 0  # second
        )
    except:
        return None


def set_rtc_time(time_data):
    """设置系统 RTC 时间"""
    try:
        rtc = machine.RTC()

        dt_str = time_data.get('datetime')
        if not dt_str:
            print("[TimeSync] 错误: 没有 datetime 字符串")
            return False

        # 使用公共解析函数
        dt = _parse_datetime(dt_str)
        if not dt:
            print(f"[TimeSync] 格式错误: {dt_str}")
            return False

        year, month, day, hour, minute, second = dt
        weekday = time_data.get('weekday', 4)  # 默认星期五

        # 设置 RTC: (year, month, day, weekday, hour, minute, second, microsecond)
        rtc.datetime((year, month, day, weekday, hour, minute, second, 0))

        print(f"[TimeSync] RTC 已同步: {year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}")
        return True

    except Exception as e:
        print(f"[TimeSync] 设置 RTC 失败: {e}")
        return False


def get_local_time():
    """获取当前本地 RTC 时间"""
    try:
        rtc = machine.RTC()
        dt = rtc.datetime()
        # dt: (year, month, day, weekday, hour, minute, second, microsecond)
        time_str = f"{dt[0]}-{dt[1]:02d}-{dt[2]:02d} {dt[4]:02d}:{dt[5]:02d}:{dt[6]:02d}"
        print(f"[TimeSync] 本地时间: {time_str}")
        return dt
    except Exception as e:
        print(f"[TimeSync] 读取失败: {e}")
        return None


def test():
    """测试函数"""
    print("=" * 50)
    print("[TimeSync] 时间同步测试")
    print("=" * 50)

    print("\n[同步前]")
    get_local_time()

    print("\n[请求网络时间]")
    result = fetch_time(show_raw=True)

    print("\n[同步后]")
    get_local_time()

    print("\n" + "=" * 50)
    return result