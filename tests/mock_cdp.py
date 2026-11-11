"""模拟 DevTools 调试服务器，用于在没有真实浏览器（或沙箱禁止启动浏览器）时
验证 WebSocket 客户端与动态嗅探流程。

它实现了真实 CDP 的最小协议面：
  * GET /json/list  -> 返回 page target
  * WebSocket 升级 + 帧收发（服务端帧不掩码）
  * Network.enable / Page.enable / Runtime.enable / Network.set*  等直接返回 {}
  * Page.navigate      -> 之后异步推送 Network.* 事件与 Page.loadEventFired
  * Runtime.evaluate   -> 按表达式返回页面标题 / outerHTML / DOM 资源列表
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time

_EXPR_TITLE = "document.title"
_EXPR_HTML = "documentElement.outerHTML"
_EXPR_RES = "performance.getEntriesByType"


class MockCDPServer:
    """一个只"装作" Chromium 的 CDP 服务端。"""

    def __init__(self, port: int = 0, resources: list | None = None,
                 title: str = "模拟页面", html: str = "<html></html>"):
        self.resources = list(resources or [])
        self.title = title
        self.html = html
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.commands: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._client = None

    # ------------------------------------------------------------- 生命周期
    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass
        if self._client:
            try:
                self._client.close()
            except OSError:
                pass

    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/devtools/page/MOCK"

    # ---------------------------------------------------------------- 网络
    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _handle(self, conn: socket.socket):
        try:
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            head, rest = buf.split(b"\r\n\r\n", 1)
            lines = head.decode("latin-1").split("\r\n")
            path = lines[0].split(" ")[1]
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            if path.startswith("/json/list") or path.startswith("/json"):
                body = json.dumps([{
                    "type": "page", "url": "about:blank", "title": "mock",
                    "webSocketDebuggerUrl": self.ws_url(),
                }]).encode()
                resp = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Content-Length: " + str(len(body)).encode() +
                        b"\r\nConnection: close\r\n\r\n" + body)
                conn.sendall(resp)
                conn.close()
                return
            key = headers.get("sec-websocket-key", "")
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()).decode()
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode())
            self._client = conn
            self._serve(conn, rest)
        except OSError:
            pass

    def _serve(self, conn: socket.socket, pending: bytes):
        buf = pending

        def read_exact(n):
            nonlocal buf
            while len(buf) < n:
                chunk = conn.recv(65536)
                if not chunk:
                    raise ConnectionError("closed")
                buf += chunk
            out, buf = buf[:n], buf[n:]
            return out

        while not self._stop.is_set():
            try:
                b1, b2 = read_exact(2)
                opcode = b1 & 0x0F
                length = b2 & 0x7F
                if length == 126:
                    length = struct.unpack(">H", read_exact(2))[0]
                elif length == 127:
                    length = struct.unpack(">Q", read_exact(8))[0]
                mask = read_exact(4) if b2 & 0x80 else b""
                payload = read_exact(length) if length else b""
                if mask:
                    payload = bytes(x ^ mask[i % 4] for i, x in enumerate(payload))
                if opcode == 0x8:
                    return
                if opcode == 0x9:
                    continue
                if opcode != 0x1:
                    continue
                self._dispatch(conn, json.loads(payload.decode("utf-8")))
            except (ConnectionError, OSError, json.JSONDecodeError):
                return

    # ---------------------------------------------------------------- 协议
    def _send(self, conn: socket.socket, obj: dict):
        payload = json.dumps(obj).encode()
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(len(payload))
        elif len(payload) < 65536:
            header.append(126)
            header += struct.pack(">H", len(payload))
        else:
            header.append(127)
            header += struct.pack(">Q", len(payload))
        conn.sendall(bytes(header) + payload)

    def _emit(self, conn: socket.socket, method: str, params: dict):
        self._send(conn, {"method": method, "params": params})

    def _dispatch(self, conn: socket.socket, msg: dict):
        method = msg.get("method", "")
        msg_id = msg.get("id")
        self.commands.append(method)
        result: dict = {}
        if method == "Page.navigate":
            self._send(conn, {"id": msg_id, "result": {"frameId": "F1"}})
            threading.Thread(target=self._simulate_network, args=(conn,),
                             daemon=True).start()
            return
        if method == "Runtime.evaluate":
            expression = (msg.get("params") or {}).get("expression", "")
            value: object = None
            if _EXPR_TITLE in expression:
                value = self.title
            elif _EXPR_HTML in expression:
                value = self.html
            elif _EXPR_RES in expression:
                value = [r.get("url") for r in self.resources]
            else:
                value = 0
            result = {"result": {"type": "string", "value": value}}
            self._send(conn, {"id": msg_id, "result": result})
            return
        if method == "Browser.close":
            self._send(conn, {"id": msg_id, "result": {}})
            return
        self._send(conn, {"id": msg_id, "result": result})

    def _simulate_network(self, conn: socket.socket):
        time.sleep(0.05)
        self._emit(conn, "Page.frameNavigated", {
            "frame": {"id": "F1", "url": "http://mock/page.html"}
        })
        for index, res in enumerate(self.resources):
            # 第二个资源延迟出现，模拟"异步加载"
            if index == 1:
                time.sleep(0.4)
            self._emit(conn, "Network.responseReceived", {
                "type": res.get("type", "Image"),
                "response": {
                    "url": res["url"],
                    "status": res.get("status", 200),
                    "headers": {"content-type": res.get("mime", "image/png")},
                },
            })
        self._emit(conn, "Page.loadEventFired", {"timestamp": time.time()})
