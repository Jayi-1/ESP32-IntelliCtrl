import machine
import sdcard
import os
from config import PIN_CFG

# ========== 短文件名生成器 ==========
class ShortFilename:
    """将长文件名转换为FAT 8.3格式"""
    counter = 0

    @classmethod
    def convert(cls, long_name):
        """
        转换规则：
        - 中文/长文件名 -> 前缀+序号.扩展名
        - 保留原始扩展名
        - 纯英文数字短名直接返回
        """
        # 提取扩展名
        if '.' in long_name:
            name_part, ext_part = long_name.rsplit('.', 1)
            ext = ext_part[:3].upper()  # 扩展名最多3字符，大写
        else:
            name_part = long_name
            ext = ''

        # 如果本来就是短文件名（纯英文数字，8.3格式），直接返回
        # 使用字符串操作替代正则，提高兼容性
        if (len(name_part) <= 8 and len(ext) <= 3 and
            all(c.isalnum() for c in name_part)):
            return long_name

        # 否则生成短名：前缀+计数器
        cls.counter += 1
        prefix = f"F{cls.counter:05d}"  # F00001, F00002...

        short_name = f"{prefix}.{ext}" if ext else prefix
        print(f"[文件名转换] '{long_name}' -> '{short_name}'")
        return short_name

# ========== SD卡管理器（增强版）==========
class SDManager:
    def __init__(self):
        self.mounted = False
        self.path = "/sd"
        self.name_map = {}  # 可选：长短名映射表
        self.lfn_supported = False  # 长文件名支持标志

    def init(self):
        """初始化SD卡（带LFN检测）"""
        try:
            # 检查是否已挂载
            try:
                os.listdir(self.path)
                self.mounted = True
                return True, None, "SD卡已挂载"
            except OSError:
                pass

            # 初始化SPI和SD卡
            spi = machine.SPI(
                PIN_CFG.SD_SPI_ID if hasattr(PIN_CFG, 'SD_SPI_ID') else 1,
                sck=machine.Pin(PIN_CFG.SD_CLK),
                mosi=machine.Pin(PIN_CFG.SD_CMD),
                miso=machine.Pin(PIN_CFG.SD_D0),
                baudrate=PIN_CFG.SD_BAUD if hasattr(PIN_CFG, 'SD_BAUD') else 20_000_000
            )
            sd = sdcard.SDCard(spi, machine.Pin(PIN_CFG.SD_CS))
            os.mount(os.VfsFat(sd), self.path)
            self.mounted = True

            # 检测LFN支持（测试创建长文件名）
            try:
                test_file = "/sd/.lfn_test_长文件名.txt"
                with open(test_file, 'w') as f:
                    f.write('test')
                os.remove(test_file)
                print("[SD卡] 长文件名(LFN)支持已启用")
                self.lfn_supported = True
            except:
                print("[SD卡] 短文件名模式(8.3) - 将自动转换文件名")
                self.lfn_supported = False

            return True, None, "SD卡初始化成功"
        except Exception as e:
            return False, None, str(e)

    def list(self, subdir=""):
        """列出文件（显示原始文件名）"""
        try:
            full_path = f"{self.path}/{subdir}" if subdir else self.path
            items = []
            for name in os.listdir(full_path):
                if name in ["System Volume Information"]:
                    continue
                full = f"{full_path}/{name}"
                stat = os.stat(full)
                is_dir = (stat[0] & 0o170000 == 0o040000)
                size = 0 if is_dir else stat[6]

                # 如果有映射，显示原始长名
                display_name = self.name_map.get(name, name)

                items.append({
                    "name": display_name,  # 显示给用户的名字
                    "real_name": name,     # 实际SD卡上的短名
                    "is_dir": is_dir,
                    "size": size
                })
            items.sort(key=lambda x: (not x["is_dir"], x["name"]))
            return True, items, f"{len(items)}项"
        except Exception as e:
            return False, [], str(e)

    def get_real_path(self, filename):
        """获取实际文件路径（长短名转换）"""
        # 先尝试直接访问（短名或LFN支持时）
        direct_path = f"{self.path}/{filename}"
        try:
            os.stat(direct_path)
            return direct_path, filename  # 直接存在，用原名
        except:
            pass

        # 如果不存在，尝试转换为短名
        short_name = ShortFilename.convert(filename)
        return f"{self.path}/{short_name}", short_name

    def delete(self, filename):
        """删除（支持长短名）"""
        try:
            real_path, real_name = self.get_real_path(filename)
            stat = os.stat(real_path)
            is_dir = (stat[0] & 0o170000 == 0o040000)

            if is_dir:
                os.rmdir(real_path)
            else:
                os.remove(real_path)

            # 清理映射表
            if real_name in self.name_map:
                del self.name_map[real_name]

            return True, None, f"已删除:{filename}"
        except Exception as e:
            return False, None, str(e)

# 全局实例
sd_manager = SDManager()
