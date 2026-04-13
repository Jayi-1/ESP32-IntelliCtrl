# file_server_sta.py
# 静默模式：STA专用剪贴板服务器（可选禁用文件功能）
# 继承 BaseFileServer，复用公共方法

import socket
import gc
import json
import os
import time
import network
import _thread

from base_server import BaseFileServer, DEFAULT_PORT

VERBOSE = False  # 静默模式，减少串口输出

def _log(msg, level="INFO"):
    if VERBOSE or level in ["ERROR", "WARN", "FATAL"]:
        print(msg)

def _log_debug(msg):
    if VERBOSE:
        print(msg)


# 全局剪贴板历史
_CLIPBOARD_HISTORY = []
_CLIPBOARD_LOCK = _thread.allocate_lock()
_MAX_CLIPBOARD_ITEMS = 10
_MAX_CLIPBOARD_SIZE = 10240


class LANFileServer(BaseFileServer):
    """
    STA专用剪贴板服务器（可选禁用文件功能）
    继承 BaseFileServer，复用公共方法
    """

    def __init__(self, sd_manager, port=DEFAULT_PORT, clipboard_only=False):
        """
        clipboard_only: True时仅启用剪贴板功能，禁用所有文件操作（适合STA模式）
        """
        self.sd = sd_manager
        self.port = port
        self.clipboard_only = clipboard_only
        self.sock = None
        self.active = False
        self.ip = "0.0.0.0"
        self.hostname = "esp32-file"

        self.transfer = {
            "active": False, "type": None, "filename": "",
            "total": 0, "current": 0, "speed": 0,
            "start_time": 0, "last_update": 0, "client_ip": "",
            "aborted": False
        }

        self.max_file_size = 100 * 1024 * 1024
        self.chunk_size = 4096
        self.gc_interval = 32768

        mode_str = "剪贴板模式" if clipboard_only else "完整模式"
        _log(f"[LAN服务器] 初始化完成 ({mode_str})", "INFO")

    def _start_http(self):
        try:
            addr = socket.getaddrinfo('0.0.0.0', self.port)[0][-1]
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setblocking(False)
            self.sock.bind(addr)
            self.sock.listen(3)
            self.active = True
            _log(f"[HTTP] 服务启动 @ {self.ip}:{self.port}", "INFO")
            return True
        except Exception as e:
            _log(f"[HTTP] 启动失败: {e}", "ERROR")
            return False

    def start(self):
        """AP模式启动（IP固定192.168.4.1）"""
        self.ip = "192.168.4.1"
        return self._start_http()

    def start_sta(self, timeout=30):
        """STA模式启动（自动获取IP）"""
        sta = network.WLAN(network.STA_IF)
        if not sta.isconnected():
            return False, None
        self.ip = sta.ifconfig()[0]
        _log(f"[STA] IP: {self.ip}", "INFO")

        if not self._start_http():
            return False, None

        # 启动UDP广播（便于局域网发现）
        try:
            _thread.start_new_thread(self._discovery_broadcast, ())
        except Exception as e:
            _log(f"[发现服务] {e}", "WARN")

        return True, self.ip

    def _discovery_broadcast(self):
        """UDP广播设备发现"""
        try:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            beacon = json.dumps({
                "device": "ESP32-Clipboard",
                "ip": self.ip,
                "port": self.port,
                "mode": "clipboard_only" if self.clipboard_only else "full"
            })
            while self.active:
                try:
                    udp.sendto(beacon.encode(), ('255.255.255.255', 9527))
                except:
                    pass
                time.sleep(5)
        except:
            pass

    def stop(self):
        """停止服务器"""
        self.active = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.sock = None
        _log("[LAN服务器] 已停止", "INFO")

    def tick(self):
        """非阻塞处理HTTP请求（增强稳定性）"""
        if not self.active:
            return
        try:
            # 非阻塞accept
            cl, addr = self.sock.accept()
            client_ip = addr[0]
            cl.settimeout(30)  # 30秒单次操作超时

            try:
                start_proc = time.ticks_ms()
                self._handle_client(cl, client_ip)
                proc_time = time.ticks_diff(time.ticks_ms(), start_proc)
                if proc_time > 2000:
                    _log(f"[慢请求] {proc_time}ms from {client_ip}", "WARN")
            except Exception as e:
                err = str(e)
                if "ECONNRESET" not in err and "EPIPE" not in err and "ETIMEDOUT" not in err:
                    _log(f"[处理异常] {e}", "ERROR")
            finally:
                try:
                    cl.close()
                except:
                    pass
        except OSError as e:
            if e.args[0] != 11:  # EAGAIN
                pass
        except Exception as e:
            _log(f"[Tick] {e}", "ERROR")

    def _handle_client(self, cl, client_ip):
        # 读取HTTP头
        header_str, initial_body, content_length = self._read_http_header(cl, client_ip)
        if header_str is None:
            self._send_json(cl, {"error": "Header too large"}, 400)
            return

        lines = header_str.split('\r\n')
        if not lines or len(lines[0].split()) < 2:
            return

        parts = lines[0].split()
        method, path = parts[0], parts[1]

        # 剪贴板独占模式下拒绝文件操作
        if self.clipboard_only:
            file_paths = ['/api/files', '/download', '/api/upload', '/api/delete']
            if any(path.startswith(fp) for fp in file_paths):
                _log(f"[拒绝] {path} 剪贴板模式下禁用文件操作", "WARN")
                self._send_json(cl, {"error": "File ops disabled in clipboard mode"}, 403)
                return

        # 路由处理
        try:
            if path == "/api/clipboard" and method == "GET":
                self._handle_clipboard_get(cl, client_ip)
            elif path == "/api/clipboard" and method == "POST":
                self._handle_clipboard_post(cl, content_length, initial_body, client_ip)
            elif path == "/api/clipboard/clear" and method == "POST":
                self._handle_clipboard_clear(cl)
            elif method == "GET" and path == "/":
                self._send_main_page(cl)
            elif method == "GET" and path == "/api/files":
                self._send_file_list_sta(cl, client_ip)
            elif method == "GET" and path.startswith("/download"):
                filename = self._parse_param(path, "file")
                if filename and self._is_safe_filename(filename):
                    self._handle_download(cl, filename, client_ip)
                else:
                    self._send_json(cl, {"error": "Invalid filename"}, 400)
            elif method == "DELETE" and path.startswith("/api/delete"):
                filename = self._parse_param(path, "file")
                if filename and self._is_safe_filename(filename):
                    self._handle_delete(cl, filename)
                else:
                    self._send_json(cl, {"error": "Invalid filename"}, 400)
            elif method == "POST" and path.startswith("/api/upload"):
                filename = self._parse_param(path, "file")
                if not filename or not self._is_safe_filename(filename):
                    self._send_json(cl, {"error": "Only English filenames"}, 400)
                    return
                self._handle_upload(cl, filename, content_length, initial_body, client_ip)
            else:
                self._send_json(cl, {"error": "Not Found"}, 404)
        except Exception as e:
            _log(f"[路由错误] {e}", "ERROR")
            self._send_json(cl, {"error": str(e)}, 500)

    # ========== 剪贴板功能方法 ==========

    def _handle_clipboard_get(self, cl, client_ip=""):
        global _CLIPBOARD_HISTORY
        with _CLIPBOARD_LOCK:
            history = list(_CLIPBOARD_HISTORY)

        response_data = {
            "history": history,
            "count": len(history),
            "total_chars": sum(len(item["text"]) for item in history),
            "ip": self.ip
        }

        _log_debug(f"[剪贴板GET] 返回{len(history)}条记录 to {client_ip}")
        self._send_json(cl, response_data)

    def _handle_clipboard_post(self, cl, content_length, initial_body, client_ip):
        global _CLIPBOARD_HISTORY

        if content_length > 10240:
            _log(f"[剪贴板POST] 拒绝: {content_length}字节超限制 from {client_ip}", "WARN")
            self._send_json(cl, {"error": "Single item max 10KB"}, 413)
            return

        body = initial_body
        remaining = content_length - len(initial_body)
        recv_start = time.ticks_ms()

        while remaining > 0:
            try:
                to_read = min(1024, remaining)
                chunk = cl.recv(to_read)
                if not chunk:
                    if time.ticks_diff(time.ticks_ms(), recv_start) > 10000:
                        _log(f"[剪贴板POST] 接收超时 from {client_ip}", "WARN")
                        raise Exception("Receive timeout")
                    time.sleep_ms(5)
                    continue
                body += chunk
                remaining -= len(chunk)
            except OSError as e:
                if e.args[0] == 11:
                    time.sleep_ms(2)
                    continue
                raise

        try:
            text_body = body.decode('utf-8')
            data = json.loads(text_body)
            text = data.get("text", "")
            device = data.get("device", f"Device_{client_ip.split('.')[-1]}")

            if not text:
                self._send_json(cl, {"error": "Empty text"}, 400)
                return

            with _CLIPBOARD_LOCK:
                new_item = {
                    "text": text[:10240],
                    "device": device[:20],
                    "time": time.ticks_ms(),
                    "size": len(text)
                }
                _CLIPBOARD_HISTORY.insert(0, new_item)

                while len(_CLIPBOARD_HISTORY) > _MAX_CLIPBOARD_ITEMS:
                    removed = _CLIPBOARD_HISTORY.pop()
                    _log_debug(f"[剪贴板] 超数淘汰: {removed['device'][:10]}")

                total_size = sum(item["size"] for item in _CLIPBOARD_HISTORY)
                while total_size > _MAX_CLIPBOARD_SIZE and len(_CLIPBOARD_HISTORY) > 1:
                    removed = _CLIPBOARD_HISTORY.pop()
                    total_size -= removed["size"]
                    _log_debug(f"[剪贴板] 超容删除: {removed['size']}chars")

            _log(f"[剪贴板] 新记录: {device[:15]} {len(text)}chars, 共{len(_CLIPBOARD_HISTORY)}条", "INFO")
            self._send_json(cl, {
                "success": True,
                "items": len(_CLIPBOARD_HISTORY),
                "total_size": total_size
            })

        except Exception as e:
            _log(f"[剪贴板POST] 解析错误 from {client_ip}: {e}", "ERROR")
            self._send_json(cl, {"error": str(e)}, 400)

    def _handle_clipboard_clear(self, cl):
        global _CLIPBOARD_HISTORY
        with _CLIPBOARD_LOCK:
            count = len(_CLIPBOARD_HISTORY)
            _CLIPBOARD_HISTORY = []
        _log(f"[剪贴板] 已清空 {count} 条记录", "INFO")
        self._send_json(cl, {"success": True, "cleared": count})

    def get_clipboard_status(self):
        """供主函数获取剪贴板状态显示在屏幕上"""
        global _CLIPBOARD_HISTORY
        with _CLIPBOARD_LOCK:
            if _CLIPBOARD_HISTORY:
                latest = _CLIPBOARD_HISTORY[0]
                return {
                    "text": latest["text"][:30],
                    "size": latest["size"],
                    "items": len(_CLIPBOARD_HISTORY),
                    "device": latest["device"]
                }
            return None

    def get_transfer_status(self):
        return self.transfer.copy() if self.transfer["active"] else {"active": False}

    def _send_file_list_sta(self, cl, client_ip=""):
        try:
            if hasattr(self.sd, 'init'):
                self.sd.init()

            ok, items, _ = self.sd.list()
            if not ok:
                items = []

            files = []
            for item in items:
                if not item.get("is_dir", False):
                    files.append({
                        "name": str(item["name"]),
                        "size": int(item.get("size", 0))
                    })

            _log_debug(f"[文件列表] 返回{len(files)}个文件 to {client_ip}")
            self._send_json(cl, {"files": files, "ip": self.ip})

        except Exception as e:
            _log(f"[文件列表错误] {e}", "ERROR")
            self._send_json(cl, {"files": [], "error": str(e), "ip": self.ip})

    def _send_main_page(self, cl):
        """根据模式返回不同界面"""
        if self.clipboard_only:
            self._send_clipboard_page(cl)
        else:
            self._send_full_page(cl)  # 非剪贴板模式 = 完整文件服务

    def _send_clipboard_page(self, cl):
        """剪贴板专用极简界面（STA模式）"""
        html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ESP32 剪贴板同步</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
        body{background:#f0f2f5;padding:16px}
        .container{max-width:600px;margin:0 auto}
        .header{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:20px;border-radius:12px;margin-bottom:16px}
        .header h1{font-size:18px}
        .header p{font-size:12px;opacity:0.9;margin-top:4px}
        .card{background:#fff;border-radius:12px;padding:16px;margin-bottom:12px;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
        textarea{width:100%;padding:12px;border:1px solid #ddd;border-radius:8px;min-height:120px;font-size:15px;resize:vertical}
        textarea:focus{outline:none;border-color:#667eea}
        .btn-row{display:flex;gap:10px;margin-top:12px}
        .btn{flex:1;background:#667eea;color:#fff;border:none;padding:12px;border-radius:8px;font-size:15px;cursor:pointer}
        .btn.secondary{background:#52c41a}
        .btn:active{opacity:0.8}
        .history{max-height:400px;overflow-y:auto}
        .item{padding:12px;border-bottom:1px solid #f0f0f0;cursor:pointer;border-radius:8px;margin-bottom:8px;background:#fafafa}
        .item:hover{background:#f0f5ff}
        .item.selected{background:#e6f7ff;border:2px solid #667eea}
        .text{font-size:14px;color:#333;line-height:1.4;word-break:break-all}
        .meta{font-size:11px;color:#999;margin-top:6px;display:flex;justify-content:space-between}
        .badge{background:#667eea;color:#fff;padding:2px 8px;border-radius:4px;font-size:10px}
        .status{font-size:12px;color:#666;text-align:center;padding:12px}
        .notice{background:#fff3cd;border:1px solid #ffeaa7;border-radius:6px;padding:10px;margin-bottom:12px;color:#856404;font-size:13px}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>ESP32 剪贴板同步</h1>
            <p>IP: <span id="ip">--</span> | STA模式 | 仅同步剪贴板</p>
        </div>

        <div class="notice">
            当前为<strong>剪贴板专用模式</strong>。如需文件管理功能，请在设置中切换为完整文件服务模式，仅支持英文、数字和符号，出现错误或无响应点击清空重新输入！
        </div>

        <div class="card">
            <textarea id="input" placeholder="仅支持英文、数字和符号..."></textarea>
            <div class="btn-row">
                <button class="btn" onclick="send()">发送到设备</button>
            </div>
            <div class="status" id="status">就绪</div>
        </div>

        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <h3 style="font-size:16px">历史记录 <span id="count" style="font-size:12px;color:#999">(0)</span></h3>
                <button onclick="clearAll()" style="background:#ff4d4f;color:#fff;border:none;padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer">清空</button>
            </div>
            <div class="history" id="history">
                <div class="status">加载中...</div>
            </div>
        </div>
    </div>

    <script>
        let historyData = [], selectedIdx = -1;

        async function load() {
            try {
                const res = await fetch('/api/clipboard');
                const data = await res.json();
                document.getElementById('ip').textContent = data.ip || '--';
                historyData = data.history || [];
                document.getElementById('count').textContent = '(' + historyData.length + '/10)';

                const list = document.getElementById('history');
                if(!historyData.length) {
                    list.innerHTML = '<div class="status">暂无记录，在上方输入框发送内容</div>';
                    return;
                }

                list.innerHTML = historyData.map((item, idx) => {
                    const text = item.text ? item.text.substring(0, 100) : '';
                    return '<div class="item ' + (idx===selectedIdx?'selected':'') + '" onclick="select(' + idx + ')">' +
                        '<div class="text">' + text + (item.text.length>100?'...':'') + '</div>' +
                        '<div class="meta"><span>' + (item.device||'Unknown') + '</span><span class="badge">' + (item.size||0) + '字</span></div>' +
                    '</div>';
                }).join('');
            } catch(e) {
                document.getElementById('status').textContent = '连接失败';
            }
        }

        function select(idx) {
            selectedIdx = idx;
            load();
            if(historyData[idx]) {
                document.getElementById('input').value = historyData[idx].text;
                updateStatus('已选中 #' + (idx+1));
            }
        }

        async function send() {
            const text = document.getElementById('input').value;
            if(!text.trim()) { updateStatus('输入为空'); return; }

            updateStatus('发送中...');
            try {
                const res = await fetch('/api/clipboard', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text: text, device: navigator.userAgent.slice(0,30)})
                });
                const data = await res.json();
                if(data.success) {
                    document.getElementById('input').value = '';
                    updateStatus('已保存！共' + data.items + '条');
                    load();
                } else {
                    updateStatus((data.error||'失败'));
                }
            } catch(e) {
                updateStatus('网络错误');
            }
        }

        async function clearAll() {
            if(!confirm('确定清空所有记录？')) return;
            await fetch('/api/clipboard/clear', {method: 'POST'});
            selectedIdx = -1;
            load();
            updateStatus('已清空');
        }

        function updateStatus(msg) {
            document.getElementById('status').textContent = msg;
        }

        window.onload = () => { load(); setInterval(load, 3000); };
    </script>
</body>
</html>"""

        try:
            body = html.encode('utf-8')
            headers = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            cl.send(headers.encode() + body)
        except Exception as e:
            _log(f"[页面错误] {e}", "ERROR")

    def _send_full_page(self, cl):
        """完整功能页面（STA模式预留，与剪贴板模式互斥）"""
        html = """<!DOCTYPE html>
<html><body style="font-family:sans-serif;padding:40px;text-align:center">
<h1>剪贴板模式</h1>
<p>当前为<strong>剪贴板专用模式</strong></p>
<p>如需文件管理，请使用AP模式</p>
</body></html>"""
        try:
            body = html.encode('utf-8')
            headers = f"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {len(body)}\r\n\r\n"
            cl.send(headers.encode() + body)
        except:
            pass