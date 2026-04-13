# base_server.py
# 文件服务器公共基类
# 提供HTTP解析、URL解码、安全检查、JSON响应等通用功能

import socket
import gc
import json
import os
import time

# 尝试导入config
try:
    from config import HTTP_CFG
    DEFAULT_PORT = HTTP_CFG.PORT
except:
    DEFAULT_PORT = 80


class BaseFileServer:
    """
    文件服务器基类
    包含所有公共方法，供 RobustFileServer(AP模式) 和 LANFileServer(STA模式) 继承
    """

    # ========== 子类需实现的属性 ==========
    sd = None           # SD卡管理器
    port = DEFAULT_PORT
    sock = None
    active = False
    transfer = None      # 传输状态字典

    # ========== 子类需实现的配置 ==========
    max_file_size = 100 * 1024 * 1024  # 100MB
    chunk_size = 4096                    # 4KB块
    gc_interval = 32768                  # 每32KB同步+GC

    # ========== 公共方法 ==========

    def _unquote(self, s):
        """URL解码（仅支持ASCII字符）"""
        if not s:
            return s

        result = []
        i = 0
        while i < len(s):
            if s[i] == '%' and i + 2 < len(s):
                try:
                    hex_pair = s[i+1:i+3]
                    char_code = int(hex_pair, 16)
                    if char_code > 127:
                        return None
                    result.append(chr(char_code))
                    i += 3
                except:
                    result.append(s[i])
                    i += 1
            elif s[i] == '+':
                result.append(' ')
                i += 1
            else:
                result.append(s[i])
                i += 1
        return ''.join(result)

    def _parse_param(self, path, key):
        """解析 URL 参数"""
        try:
            if "?" in path:
                query = path.split("?")[1]
                for p in query.split("&"):
                    if "=" in p:
                        k, v = p.split("=", 1)
                        if k == key:
                            decoded = self._unquote(v)
                            if decoded is None:
                                return None
                            return decoded
        except:
            pass
        return None

    def _is_safe_filename(self, filename):
        """严格安全检查"""
        if not filename or len(filename) > 100:
            return False

        if '..' in filename or '/' in filename or '\\' in filename:
            return False

        allowed_special = " ._-()"
        for c in filename:
            code = ord(c)
            if code > 127:
                return False

            is_allowed = (
                (48 <= code <= 57) or
                (65 <= code <= 90) or
                (97 <= code <= 122) or
                (c in allowed_special)
            )
            if not is_allowed:
                return False

        return True

    def _get_ip(self):
        """获取本机 IP"""
        try:
            import network
            sta = network.WLAN(network.STA_IF)
            if sta.active() and sta.isconnected():
                return sta.ifconfig()[0]
            ap = network.WLAN(network.AP_IF)
            if ap.active():
                return ap.ifconfig()[0]
        except:
            pass
        return "192.168.4.1"

    def _safe_send(self, cl, data):
        """安全发送数据"""
        if not data:
            return True

        total_sent = 0
        length = len(data)
        retries = 0

        while total_sent < length and retries < 3:
            try:
                sent = cl.send(data[total_sent:])
                if sent == 0:
                    return False
                total_sent += sent
            except OSError as e:
                err = e.args[0]
                if err == 11:
                    time.sleep_ms(10)
                    retries += 1
                    continue
                elif err in [104, 128, 32, 113]:
                    return False
                else:
                    retries += 1

        return total_sent == length

    def _send_json(self, cl, data, status=200):
        """发送 JSON 响应"""
        try:
            body = json.dumps(data)
            resp = (
                f"HTTP/1.1 {status}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n"
                f"{body}"
            )
            self._safe_send(cl, resp.encode())
        except:
            pass

    def _reset_transfer(self):
        """重置传输状态"""
        self.transfer = {
            "active": False, "type": None, "filename": "",
            "total": 0, "current": 0, "speed": 0,
            "start_time": 0, "last_update": 0, "client_ip": "",
            "aborted": False
        }

    def _update_progress(self, delta):
        """更新进度"""
        self.transfer["current"] += delta

    def get_status(self):
        """获取传输状态"""
        if not self.transfer["active"]:
            return self.transfer

        now = time.ticks_ms()
        if time.ticks_diff(now, self.transfer["last_update"]) > 500:
            elapsed = time.ticks_diff(now, self.transfer["start_time"]) / 1000
            if elapsed > 0:
                self.transfer["speed"] = int(self.transfer["current"] / elapsed)
            self.transfer["last_update"] = now

        return self.transfer.copy()

    get_transfer_status = get_status  # 别名兼容

    def _read_http_header(self, cl, client_ip, max_size=4096, timeout_ms=5000):
        """
        读取HTTP请求头，返回 (header_str, initial_body, content_length)
        """
        header_buffer = b""
        header_end_pos = -1
        start_time = time.ticks_ms()

        while len(header_buffer) < max_size:
            try:
                chunk = cl.recv(1024)
                if not chunk:
                    break

                header_buffer += chunk
                header_end_pos = header_buffer.find(b"\r\n\r\n")
                if header_end_pos != -1:
                    break

            except OSError as e:
                if e.args[0] == 11:
                    if time.ticks_diff(time.ticks_ms(), start_time) > timeout_ms:
                        return None, None, 0
                    time.sleep_ms(1)
                    continue
                raise

        if header_end_pos == -1:
            return None, None, 0

        header_bytes = header_buffer[:header_end_pos]
        initial_body = header_buffer[header_end_pos + 4:]

        try:
            header_str = header_bytes.decode('ascii', 'ignore')
        except:
            header_str = header_bytes.decode('utf-8', 'ignore')

        # 解析 Content-Length
        content_length = 0
        lines = header_str.split('\r\n')
        for line in lines:
            if line.lower().startswith("content-length:"):
                try:
                    content_length = int(line.split(":", 1)[1].strip())
                except:
                    content_length = 0
                break

        return header_str, initial_body, content_length

    def _handle_upload(self, cl, filename, content_length, initial_body, client_ip):
        """处理文件上传（增强稳定性）"""
        print(f"[上传开始] {filename}, 大小: {content_length} 字节")

        # 设置socket超时，防止长时间阻塞
        try:
            cl.settimeout(60)  # 60秒超时
        except:
            pass

        if content_length == 0:
            self._send_json(cl, {"error": "无效文件大小"}, 400)
            return

        if content_length > self.max_file_size:
            self._send_json(cl, {"error": f"文件过大，最大{self.max_file_size//1024//1024}MB"}, 400)
            return

        # SD卡检查（带超时保护）
        try:
            sd_root = os.listdir("/sd")
        except Exception as e:
            print(f"[上传错误] SD卡无法访问: {e}")
            self._send_json(cl, {"error": "SD卡错误"}, 500)
            return

        # SD卡写入测试
        try:
            test_path = "/sd/.write_test"
            with open(test_path, 'wb') as tf:
                tf.write(b'test_write')
            os.remove(test_path)
        except Exception as e:
            print(f"[上传错误] SD卡写入测试失败: {e}")
            self._send_json(cl, {"error": "SD卡不可写"}, 500)
            return

        self._reset_transfer()
        self.transfer.update({
            "active": True, "type": "upload", "filename": filename,
            "total": content_length, "current": 0,
            "start_time": time.ticks_ms(), "last_update": time.ticks_ms(),
            "client_ip": client_ip, "aborted": False
        })

        filepath = f"/sd/{filename}"
        received = 0
        last_activity = time.ticks_ms()
        f = None

        try:
            # 打开文件
            f = open(filepath, 'wb')

            # 写入初始数据
            if initial_body:
                to_write = initial_body[:content_length] if len(initial_body) > content_length else initial_body
                f.write(to_write)
                received = len(to_write)
                self._update_progress(received)

            # 继续接收
            while received < content_length:
                # 检查接收超时（60秒无数据）
                if time.ticks_diff(time.ticks_ms(), last_activity) > 60000:
                    raise Exception("接收超时(60s无数据)")

                try:
                    remaining = content_length - received
                    to_read = min(self.chunk_size, remaining)
                    chunk = cl.recv(to_read)

                    if not chunk:
                        time.sleep_ms(2)
                        continue

                    last_activity = time.ticks_ms()
                    f.write(chunk)
                    received += len(chunk)
                    self._update_progress(len(chunk))

                    # 每32KB GC
                    if received % self.gc_interval == 0:
                        gc.collect()

                except OSError as e:
                    err = e.args[0] if e.args else 0
                    if err == 11:  # EAGAIN
                        time.sleep_ms(2)
                        continue
                    elif err in [104, 128]:  # ECONNRESET, ESHUTDOWN
                        raise Exception(f"连接断开")
                    else:
                        raise

            # 关闭文件
            f.close()
            f = None

            # 验证文件
            try:
                stat = os.stat(filepath)
                actual_size = stat[6]
            except:
                actual_size = received

            if actual_size != received:
                print(f"[上传警告] 大小不一致: {received} vs {actual_size}")

            # 成功响应
            speed = self.transfer["speed"] // 1024
            print(f"[上传成功] {filename}: {received//1024}KB")
            self._send_json(cl, {
                "success": True,
                "filename": filename,
                "size": received,
                "speed": speed
            })

        except Exception as e:
            error_msg = str(e)
            print(f"[上传失败] {filename}: {error_msg}")
            try:
                self._send_json(cl, {"success": False, "error": error_msg})
            except:
                pass
        finally:
            # 安全关闭文件
            if f is not None:
                try:
                    f.close()
                except:
                    pass
            self.transfer["active"] = False
            gc.collect()

    def _handle_download(self, cl, filename, client_ip):
        """高速下载（增强稳定性）"""
        filepath = f"/sd/{filename}"

        # 设置socket超时
        try:
            cl.settimeout(120)  # 下载超时2分钟
        except:
            pass

        try:
            # 检查文件是否存在
            stat = os.stat(filepath)
            size = stat[6]
            print(f"[下载] {filename} ({size//1024}KB)")

            # 检查内存
            gc.collect()
            free_mem = gc.mem_free()
            if free_mem < 32768:  # 小于32KB时尝试回收
                gc.collect()

            self._reset_transfer()
            self.transfer.update({
                "active": True, "type": "download", "filename": filename,
                "total": size, "current": 0,
                "start_time": time.ticks_ms(), "last_update": time.ticks_ms(),
                "client_ip": client_ip
            })

            # 发送HTTP头部
            safe_name = filename.replace('"', '').replace('\r', '').replace('\n', '')[:50]
            headers = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: application/octet-stream\r\n"
                f"Content-Disposition: attachment; filename=\"{safe_name}\"\r\n"
                f"Content-Length: {size}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode()

            if not self._safe_send(cl, headers):
                print("[下载] 发送头部失败")
                return

            # 传输文件
            sent_total = 0
            last_progress = 0
            last_activity = time.ticks_ms()

            with open(filepath, 'rb') as f:
                while sent_total < size:
                    # 检查超时（60秒无活动）
                    if time.ticks_diff(time.ticks_ms(), last_activity) > 60000:
                        print(f"[下载超时] {filename} at {sent_total//1024}KB")
                        self.transfer["aborted"] = True
                        break

                    to_read = min(self.chunk_size, size - sent_total)
                    chunk = f.read(to_read)

                    if not chunk:
                        break

                    last_activity = time.ticks_ms()

                    if not self._safe_send(cl, chunk):
                        self.transfer["aborted"] = True
                        break

                    sent_total += len(chunk)
                    self._update_progress(len(chunk))

                    # 定期GC
                    if sent_total % self.gc_interval == 0:
                        gc.collect()

                    # 进度日志
                    if sent_total - last_progress >= 524288:
                        pct = (sent_total * 100) // size
                        speed = self.transfer["speed"] // 1024
                        print(f"[下载进度] {filename}: {pct}% ({speed}KB/s)")
                        last_progress = sent_total

            if self.transfer["aborted"]:
                print(f"[下载中止] {filename} at {sent_total//1024}KB")
            else:
                speed = self.transfer["speed"] // 1024
                print(f"[下载完成] {filename} @ {speed}KB/s")

        except OSError as e:
            print(f"[下载错误] 文件不存在: {filename}")
            try:
                self._send_json(cl, {"error": "文件不存在"}, 404)
            except:
                pass
        except Exception as e:
            err_str = str(e)
            if "ECONNRESET" in err_str or "EPIPE" in err_str:
                print(f"[下载] 客户端断开: {filename}")
            else:
                print(f"[下载错误] {err_str}")

        finally:
            self.transfer["active"] = False
            gc.collect()

    def _handle_delete(self, cl, filename):
        """删除文件（增强稳定性）"""
        filepath = f"/sd/{filename}"

        # 设置超时
        try:
            cl.settimeout(10)  # 删除操作10秒超时
        except:
            pass

        try:
            # 检查文件是否存在
            if not os.path.exists(filepath):
                print(f"[删除] 文件不存在: {filename}")
                self._send_json(cl, {"success": False, "error": "文件不存在"}, 404)
                return

            # 删除文件
            os.remove(filepath)
            print(f"[删除成功] {filename}")

            # GC回收
            gc.collect()

            self._send_json(cl, {"success": True})

        except OSError as e:
            err_str = str(e)
            print(f"[删除失败] {filename}: {err_str}")
            self._send_json(cl, {"success": False, "error": err_str}, 500)
        except Exception as e:
            print(f"[删除错误] {filename}: {e}")
            self._send_json(cl, {"success": False, "error": str(e)}, 500)
        finally:
            gc.collect()
