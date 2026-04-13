# file_server.py
# ESP32 高性能文件服务器（适配uni-app一键上传版）
# 支持路径: /upload (APP上传) 和 /api/upload (网页上传)
# 自动保存到 /sd/backgrounds/ 目录

import os
import socket

from base_server import BaseFileServer, DEFAULT_PORT


class RobustFileServer(BaseFileServer):
    """
    ESP32文件服务器（uni-app一键上传适配版）
    继承 BaseFileServer，复用公共方法
    """

    def __init__(self, sd_manager, port=DEFAULT_PORT):
        self.sd = sd_manager
        self.port = port
        self.sock = None
        self.active = False

        self.transfer = {
            "active": False, "type": None, "filename": "",
            "total": 0, "current": 0, "speed": 0,
            "start_time": 0, "last_update": 0, "client_ip": "",
            "aborted": False
        }

        self.max_file_size = 100 * 1024 * 1024  # 100MB
        self.chunk_size = 4096                   # 4KB块
        self.gc_interval = 32768                 # 每32KB同步+GC

        # 确保 backgrounds 目录存在
        self._ensure_bg_dir()

        print(f"[文件服务器] 初始化完成，端口:{port}")
        print(f"[上传路径] /upload 或 /api/upload")
        print(f"[存储目录] /sd/backgrounds/")

    def _ensure_bg_dir(self):
        """确保 backgrounds 目录存在"""
        try:
            os.mkdir("/sd/backgrounds")
            print("[文件服务器] 创建 /sd/backgrounds/")
        except:
            pass  # 已存在

    def start(self):
        """启动服务器"""
        if self.active:
            return True

        try:
            addr = socket.getaddrinfo('0.0.0.0', self.port)[0][-1]
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setblocking(False)
            self.sock.bind(addr)
            self.sock.listen(3)

            self.active = True
            ip = self._get_ip()
            print(f"[文件服务器] 启动: http://{ip}:{self.port}")
            return True

        except Exception as e:
            print(f"[文件服务器] 启动失败: {e}")
            return False

    def stop(self):
        """停止服务器"""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.sock = None
        self.active = False
        self._reset_transfer()
        print("[文件服务器] 已停止")

    def tick(self):
        """非阻塞处理"""
        if not self.active or not self.sock:
            return

        try:
            cl, addr = self.sock.accept()
            client_ip = addr[0]
            cl.settimeout(60)

            try:
                self._handle_client(cl, client_ip)
            except OSError:
                pass
            except Exception as e:
                err_str = str(e)
                if "ECONNRESET" not in err_str and "EPIPE" not in err_str:
                    print(f"[处理异常] {e}")
            finally:
                try:
                    cl.close()
                except:
                    pass

        except OSError as e:
            if e.args[0] != 11:
                pass
        except Exception as e:
            print(f"[Tick] {e}")

    def _handle_client(self, cl, client_ip):
        """处理 HTTP 请求（适配uni-app一键上传）"""
        # 读取 HTTP 头部
        header_str, initial_body, content_length = self._read_http_header(cl, client_ip)
        if header_str is None:
            self._send_json(cl, {"error": "请求头过大"}, 400)
            return

        lines = header_str.split('\r\n')
        if not lines or len(lines[0].split()) < 2:
            return

        parts = lines[0].split()
        method, path = parts[0], parts[1]

        # 日志
        if "/api/status" not in path:
            display_path = path[:30] if len(path) < 30 else path[:27] + "..."
            print(f"[HTTP] {method} {display_path}")

        # 路由分发（关键修改：支持 /upload 和 /api/upload）
        try:
            if method == "GET" and path == "/":
                self._send_file_list(cl)
            elif method == "GET" and path == "/api/status":
                self._send_json(cl, self.get_status())

            # ===== 上传路由（适配uni-app一键上传） =====
            elif method == "POST" and (path == "/upload" or path.startswith("/api/upload")):
                # 优先从 Header 获取文件名（APP上传方式）
                filename = None
                for line in lines:
                    if line.lower().startswith("x-filename:"):
                        filename = line.split(":", 1)[1].strip()
                        break

                # 其次从 URL 参数获取（网页上传方式）
                if not filename:
                    filename = self._parse_param(path, "file")

                # 安全检查
                if not filename:
                    self._send_json(cl, {"error": "未指定文件名"}, 400)
                    return

                if not self._is_safe_filename(filename):
                    print(f"[上传拒绝] 非法文件名: {filename[:30]}")
                    self._send_json(cl, {"error": "文件名仅允许英文、数字、符号"}, 400)
                    return

                # 强制保存到 backgrounds 目录
                if not filename.startswith("backgrounds/"):
                    filename = f"backgrounds/{filename}"

                self._handle_upload(cl, filename, content_length, initial_body, client_ip)

            elif method == "GET" and path.startswith("/download"):
                filename = self._parse_param(path, "file")
                if filename and self._is_safe_filename(filename):
                    self._handle_download(cl, filename, client_ip)
                else:
                    self._send_json(cl, {"error": "非法文件名"}, 400)

            elif method == "DELETE" and path.startswith("/api/delete"):
                filename = self._parse_param(path, "file")
                if filename and self._is_safe_filename(filename):
                    self._handle_delete(cl, filename)
                else:
                    self._send_json(cl, {"error": "非法文件名"}, 400)
            else:
                self._send_json(cl, {"error": "Not Found"}, 404)

        except Exception as e:
            print(f"[路由错误] {e}")
            try:
                self._send_json(cl, {"error": str(e)}, 500)
            except:
                pass

    def _send_file_list(self, cl):
        """发送文件列表 HTML"""
        ok, items, _ = self.sd.list()
        if not ok:
            items = []

        files_html = ""
        if not items:
            files_html = '<div class="empty">暂无文件</div>'
        else:
            for item in items:
                name = item["name"]
                is_dir = item["is_dir"]

                if is_dir:
                    size_str = "目录"
                    icon = "📁"
                else:
                    size = item["size"]
                    if size > 1024*1024:
                        size_str = f"{size//1024//1024}MB"
                    elif size > 1024:
                        size_str = f"{size//1024}KB"
                    else:
                        size_str = f"{size}B"
                    ext = name.split('.')[-1].lower() if '.' in name else ''
                    icons = {
                        'py':'🐍','txt':'📝','json':'📋','csv':'📊',
                        'jpg':'🖼️','jpeg':'🖼️','png':'🖼️','gif':'🖼️','bmp':'🖼️','raw':'🎨',
                        'mp3':'🎵','wav':'🎵','mp4':'🎬','avi':'🎬','mov':'🎬',
                        'pdf':'📄','doc':'📄','docx':'📄','ppt':'📄','pptx':'📄',
                        'zip':'📦','gz':'📦','tar':'📦','rar':'📦',
                        'bin':'⚙️','hex':'⚙️','elf':'⚙️'
                    }
                    icon = icons.get(ext, "📄")

                safe_name = name.replace('"', '&quot;').replace("'", '&#39;')
                files_html += f'''
                <div class="file-item">
                    <input type="checkbox" class="checkbox" value="{safe_name}" onchange="updateToolbar()">
                    <div class="file-main" onclick="downloadFile('{safe_name}')" title="下载 {safe_name}">
                        <div class="file-icon">{icon}</div>
                        <div class="file-info">
                            <div class="file-name">{safe_name}</div>
                            <div class="file-meta">{size_str}</div>
                        </div>
                    </div>
                    <button class="download-btn" onclick="downloadFile('{safe_name}')">下载</button>
                </div>'''

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ESP32 文件服务器</title>
    <style>
        body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f7fa;padding:12px;margin:0}}
        .container{{max-width:900px;margin:0 auto}}
        .header{{background:linear-gradient(135deg,#667eea,#764ba2);color:white;padding:24px;border-radius:12px;margin-bottom:16px;box-shadow:0 4px 12px rgba(0,0,0,0.15)}}
        .header h1{{margin:0;font-size:24px}}
        .header p{{margin:8px 0 0;opacity:0.9;font-size:14px}}
        .upload-area{{background:white;border:3px dashed #d0d0d0;border-radius:10px;padding:50px 20px;text-align:center;margin-bottom:16px;cursor:pointer;transition:all 0.3s}}
        .upload-area:hover,.upload-area.dragover{{border-color:#667eea;background:#f0f5ff}}
        .btn{{background:#667eea;color:white;border:none;padding:12px 28px;border-radius:6px;cursor:pointer;font-size:15px;font-weight:500}}
        .toolbar{{background:white;padding:12px 16px;border-radius:10px;margin-bottom:12px;display:none;box-shadow:0 2px 8px rgba(0,0,0,0.08)}}
        .toolbar.visible{{display:flex;align-items:center;gap:12px}}
        .batch-btn{{background:#ff4d4f;color:white;border:none;padding:8px 16px;border-radius:5px;cursor:pointer;font-size:14px}}
        .file-list{{background:white;border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,0.08);overflow:hidden}}
        .file-item{{display:flex;align-items:center;padding:14px 16px;border-bottom:1px solid #f0f0f0;transition:background 0.15s}}
        .file-item:hover{{background:#fafafa}}
        .file-item:last-child{{border-bottom:none}}
        .checkbox{{width:20px;height:20px;margin-right:14px;cursor:pointer}}
        .file-main{{flex:1;display:flex;align-items:center;cursor:pointer;min-width:0}}
        .file-icon{{font-size:28px;margin-right:12px;width:32px;text-align:center}}
        .file-info{{flex:1;min-width:0}}
        .file-name{{font-size:16px;font-weight:500;color:#333;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
        .file-meta{{font-size:13px;color:#999;margin-top:3px}}
        .download-btn{{background:#52c41a;color:white;border:none;padding:10px 24px;border-radius:6px;cursor:pointer;font-size:15px;font-weight:500;flex-shrink:0}}
        .empty{{text-align:center;padding:60px;color:#999;font-size:15px}}
        .progress{{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1f1f1f;color:white;padding:14px 28px;border-radius:25px;font-size:14px;display:none;box-shadow:0 6px 20px rgba(0,0,0,0.3);z-index:1000}}
        .progress.active{{display:block}}
        .speed{{color:#52c41a;font-weight:500;margin-left:8px}}
        .notice{{background:#fff3cd;border:1px solid #ffeaa7;border-radius:6px;padding:12px 16px;margin-bottom:16px;color:#856404;font-size:14px}}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>ESP32 文件服务器</h1>
            <p>支持 APP一键上传 /upload 或 网页上传 /api/upload</p>
        </div>

        <div class="notice">
            APP上传: 使用 /upload 路径 | 网页上传: 使用 /api/upload?file=xxx 路径
        </div>

        <div class="upload-area" id="uploadArea" onclick="document.getElementById('fileInput').click()">
            <p style="font-size:18px;color:#667eea;margin-bottom:8px;font-weight:500">点击或拖拽上传文件</p>
            <input type="file" id="fileInput" style="display:none" multiple onchange="handleFiles(this.files)">
            <button class="btn">选择文件</button>
            <p style="font-size:13px;color:#888;margin-top:10px">支持 100MB 以内，自动保存到 /sd/backgrounds/</p>
        </div>

        <div class="toolbar" id="toolbar">
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
                <input type="checkbox" id="selectAll" onchange="toggleSelectAll()" style="width:18px;height:18px">
                <span style="font-size:14px;color:#666">全选</span>
            </label>
            <button class="batch-btn" onclick="batchDelete()">删除选中</button>
            <span id="selectedCount" style="color:#666;font-size:13px;margin-left:auto"></span>
        </div>

        <div class="file-list" id="fileList">{files_html}</div>
    </div>

    <div class="progress" id="progress"></div>

    <script>
        const uploadArea = document.getElementById('uploadArea');
        uploadArea.ondragover = (e) => {{ e.preventDefault(); uploadArea.classList.add('dragover'); }};
        uploadArea.ondragleave = () => {{ uploadArea.classList.remove('dragover'); }};
        uploadArea.ondrop = (e) => {{ e.preventDefault(); uploadArea.classList.remove('dragover'); handleFiles(e.dataTransfer.files); }};

        function handleFiles(files) {{
            if(!files.length) return;
            const prog = document.getElementById('progress');
            prog.className = 'progress active';

            const validFiles = [];
            for(let file of files) {{
                if(/[^\\x00-\\x7F]/.test(file.name)) {{
                    prog.innerHTML = file.name.substring(0,20) + '... 含中文';
                    continue;
                }}
                validFiles.push(file);
            }}

            if(validFiles.length === 0) {{
                setTimeout(() => {{ prog.className = 'progress'; }}, 3000);
                return;
            }}

            let index = 0;
            function uploadNext() {{
                if(index >= validFiles.length) {{
                    prog.innerHTML = '上传完成';
                    setTimeout(()=>location.reload(), 800);
                    return;
                }}

                const file = validFiles[index++];
                if(file.size > 100*1024*1024) {{
                    prog.innerHTML = file.name + ' 超过100MB';
                    setTimeout(uploadNext, 1500);
                    return;
                }}

                const xhr = new XMLHttpRequest();
                let lastLoaded = 0;
                let lastTime = Date.now();

                xhr.upload.onprogress = (e) => {{
                    if(e.lengthComputable) {{
                        const pct = Math.round(e.loaded/e.total*100);
                        const now = Date.now();
                        const speed = Math.round((e.loaded - lastLoaded)/((now-lastTime)/1000)/1024);
                        lastLoaded = e.loaded;
                        lastTime = now;
                        prog.innerHTML = file.name + ' ' + pct + '% (' + speed + 'KB/s)';
                    }}
                }};

                xhr.onload = () => {{
                    try {{
                        const r = JSON.parse(xhr.responseText);
                        if(r.success) {{
                            prog.innerHTML = file.name + ' ' + r.speed + 'KB/s';
                            setTimeout(uploadNext, 500);
                        }} else {{
                            prog.innerHTML = file.name + ' 失败: ' + (r.error || '未知错误');
                            setTimeout(uploadNext, 2000);
                        }}
                    }} catch(e) {{
                        prog.innerHTML = '服务器响应错误';
                        setTimeout(uploadNext, 2000);
                    }}
                }};

                xhr.onerror = () => {{
                    prog.innerHTML = file.name + ' 网络错误';
                    setTimeout(uploadNext, 3000);
                }};

                xhr.open('POST', '/api/upload?file=' + encodeURIComponent(file.name));
                xhr.send(file);
            }}

            uploadNext();
        }}

        function downloadFile(name) {{
            const a = document.createElement('a');
            a.href = '/download?file=' + encodeURIComponent(name);
            a.download = name;
            a.style.display = 'none';
            document.body.appendChild(a);
            a.click();
            setTimeout(()=>document.body.removeChild(a), 100);
        }}

        function updateToolbar() {{
            const checked = document.querySelectorAll('.checkbox:checked');
            const toolbar = document.getElementById('toolbar');
            toolbar.classList.toggle('visible', checked.length > 0);
            document.getElementById('selectedCount').innerText = checked.length + '个文件已选择';
        }}

        function toggleSelectAll() {{
            const all = document.getElementById('selectAll').checked;
            document.querySelectorAll('.checkbox').forEach(cb => cb.checked = all);
            updateToolbar();
        }}

        function batchDelete() {{
            const files = Array.from(document.querySelectorAll('.checkbox:checked')).map(cb => cb.value);
            if(!confirm('确定删除' + files.length + '个文件？')) return;

            let completed = 0;
            const prog = document.getElementById('progress');
            prog.className = 'progress active';
            prog.innerText = '正在删除...';

            Promise.all(files.map(name =>
                fetch('/api/delete?file=' + encodeURIComponent(name), {{method:'DELETE'}})
                    .then(r => r.json())
                    .then(() => completed++)
                    .catch(() => {{}})
            )).then(() => {{
                prog.innerText = '已删除 ' + completed + '/' + files.length + ' 个文件';
                setTimeout(()=>location.reload(), 1000);
            }});
        }}

        setInterval(() => {{
            fetch('/api/status').then(r => r.json()).then(s => {{
                if(s.active) {{
                    const prog = document.getElementById('progress');
                    const pct = Math.round(s.current/s.total*100);
                    const speed = Math.round(s.speed/1024);
                    prog.className = 'progress active';
                    prog.innerHTML = (s.type==='upload'?'上传':'下载') + ' ' + s.filename + ' ' + pct + '% (' + speed + 'KB/s)';
                }}
            }}).catch(()=>{{}});
        }}, 2000);
    </script>
</body>
</html>"""

        try:
            body = html.encode('utf-8')
            headers = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            self._safe_send(cl, headers.encode() + body)
        except Exception as e:
            print(f"[页面错误] {e}")
