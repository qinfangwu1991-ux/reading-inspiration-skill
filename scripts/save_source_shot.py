#!/usr/bin/env python3
"""给链接来源截「原页面截图」，存进 library/shots/，供卡片 `image:` 引用。

为什么要有这一步：卡片里的「原文摘录」是我切过的文本，一旦哪天想核对「原文到底怎么排的、
旁边有没有配图/表格」，只有原页面截图能回答。书稿有照片，链接文档也该有截图。

实现：直接走 Chrome DevTools Protocol（手写 WebSocket，纯标准库，不依赖 playwright/puppeteer）。
  · 手机 UA + 430px 视口，跟 fetch_source 抓正文时的口径一致
  · captureBeyondViewport 截整页长图，不是只截首屏
  · 输出 JPEG（quality 78），长边不需要再压

用法:
    python3 save_source_shot.py "<url>" [--slug 名字] [--wait 秒] [--width 430] [--max-height 4200]

退出码:
    0 成功（最后一行输出 shots/<文件名>.jpg，原样抄进卡片 image: 字段）
    3 找不到 Chrome / 调试端口起不来
    5 被安全验证（滑块）或登录墙拦住 —— 让用户自己截一张发过来，不要重试
"""
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env  # noqa: E402

LIBRARY = env.library_dir()
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
# 一进去就是这些字样，说明被拦了，别再试
BLOCK_HINTS = ("安全验证", "请完成验证", "拖动滑块", "滑动验证", "访问验证",
               "扫码登录", "登录后查看", "请先登录", "环境异常")
STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || {runtime: {}};
"""


class WS:
    """够用的 WebSocket 客户端：只处理文本帧 + 分片不管（CDP 的消息都小于 64KB 的掩码位无影响）。"""

    def __init__(self, url):
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=30)
        self.sock.settimeout(60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b"101" not in buf.split(b"\r\n")[0]:
            raise RuntimeError("WebSocket 握手失败")
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.mid = 0

    def _recv(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("连接被关闭")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, payload):
        data = payload.encode() if isinstance(payload, str) else payload
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def cmd(self, method, sid=None, **params):
        self.mid += 1
        mid = self.mid
        msg = {"id": mid, "method": method, "params": params}
        if sid:
            msg["sessionId"] = sid
        self._send_frame(json.dumps(msg))
        deadline = time.time() + 60
        while time.time() < deadline:
            b0, b1 = self._recv(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv(8))[0]
            payload = self._recv(n) if n else b""
            if (b0 & 0x0F) != 0x1:
                continue
            try:
                resp = json.loads(payload)
            except Exception:
                continue
            if resp.get("id") == mid:
                if "error" in resp:
                    raise RuntimeError(str(resp["error"])[:300])
                return resp.get("result", {})
        raise TimeoutError(f"等 CDP 响应超时: {method}")


def find_chrome():
    """macOS 应用包 / Linux /opt /usr/bin / Windows 安装目录 / PATH，全交给 env 探测。

    云沙箱一般没有浏览器 —— 返回 None，调用方按退出码 3 降级（让用户自己截图）。
    自建镜像里装了 Chromium 但不在默认路径时，用 CHROME_PATH 环境变量指定即可。
    """
    return env.chrome_path()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def slugify(title, fallback="page"):
    s = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", title).strip("_")
    return (s or fallback)[:40]


def grab(url, out_path, width=430, wait=5.0, full=True, max_height=4200):
    chrome = find_chrome()
    if not chrome:
        print("找不到 Chrome / Chromium（云沙箱常见），跳过原页面截图；"
              "有浏览器的环境可用 CHROME_PATH 指定路径", file=sys.stderr)
        return 3
    port = free_port()
    prof = os.path.join(env.tempdir(), f".ri_shot_{os.getpid()}")
    shutil.rmtree(prof, ignore_errors=True)

    def launch(flag):
        """起浏览器并等调试端口。返回 (proc, ws_url)；起不来 ws_url 为空。"""
        p = subprocess.Popen(
            [chrome, flag, "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
             f"--user-data-dir={prof}", f"--remote-debugging-port={port}",
             "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        u = ""
        for _ in range(40):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                    u = json.loads(r.read()).get("webSocketDebuggerUrl", "")
                if u:
                    return p, u
            except Exception:
                time.sleep(0.25)
        try:
            p.terminate()
        except Exception:
            pass
        return None, ""

    # 新版 Chromium 认 --headless=new；老镜像里的 chromium 只认 --headless，依次试
    proc, ws_url = None, ""
    for flag in ("--headless=new", "--headless"):
        proc, ws_url = launch(flag)
        if ws_url:
            break
    if not ws_url:
        print("Chrome 调试端口没起来（可能是极简镜像缺依赖库）", file=sys.stderr)
        return 3

    try:

        ws = WS(ws_url)
        tid = ws.cmd("Target.createTarget", url="about:blank")["targetId"]
        sid = ws.cmd("Target.attachToTarget", targetId=tid, flatten=True)["sessionId"]
        ws.cmd("Page.enable", sid)
        ws.cmd("Page.addScriptToEvaluateOnNewDocument", sid, source=STEALTH)
        ws.cmd("Emulation.setUserAgentOverride", sid, userAgent=MOBILE_UA,
               acceptLanguage="zh-CN,zh")
        ws.cmd("Emulation.setDeviceMetricsOverride", sid, width=width, height=1200,
               deviceScaleFactor=2, mobile=True)
        ws.cmd("Page.navigate", sid, url=url)
        time.sleep(wait)

        # 先看是不是验证页 / 登录墙
        probe = ws.cmd("Runtime.evaluate", sid,
                       expression="document.body ? document.body.innerText.slice(0,600) : ''",
                       returnByValue=True)
        text = ((probe.get("result") or {}).get("value") or "").strip()
        if any(h in text for h in BLOCK_HINTS) and len(text) < 400:
            first = text.replace("\n", " ")[:80]
            print(f"被安全验证/登录墙拦住（页面只显示：{first}）", file=sys.stderr)
            return 5

        clip_h = 0
        if full:
            h = ws.cmd("Runtime.evaluate", sid,
                       expression="Math.max(document.body.scrollHeight,"
                                  "document.documentElement.scrollHeight)",
                       returnByValue=True)
            content_h = int((h.get("result") or {}).get("value") or 0)
            clip_h = min(content_h + 40, max_height) if content_h else 0
            if content_h > max_height:
                print(f"页面比 {max_height}px 长（实际 {content_h}px），只截前 {max_height}px；"
                      f"要全篇用 --max-height 调大", file=sys.stderr)
            if clip_h:
                ws.cmd("Emulation.setDeviceMetricsOverride", sid, width=width,
                       height=clip_h, deviceScaleFactor=2, mobile=True)
                time.sleep(1.0)

        shot_args = dict(format="jpeg", quality=78, captureBeyondViewport=True)
        if clip_h:
            shot_args["clip"] = {"x": 0, "y": 0, "width": width,
                                 "height": clip_h, "scale": 1}
        res = ws.cmd("Page.captureScreenshot", sid, **shot_args)
        raw = base64.b64decode(res["data"])
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(raw)
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        shutil.rmtree(prof, ignore_errors=True)


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit("用法: save_source_shot.py \"<url>\" [--slug 名字] [--wait 秒] [--width px] "
                 "[--max-height px] [--viewport]")
    url = args[0]
    slug = ""
    wait = 5.0
    width = 430
    max_height = 4200
    full = True
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--slug" and i + 1 < len(args):
            slug = args[i + 1]
            i += 2
        elif a == "--wait" and i + 1 < len(args):
            wait = float(args[i + 1])
            i += 2
        elif a == "--width" and i + 1 < len(args):
            width = int(args[i + 1])
            i += 2
        elif a == "--max-height" and i + 1 < len(args):
            max_height = int(args[i + 1])
            i += 2
        elif a == "--viewport":
            full = False
            i += 1
        else:
            i += 1

    slug = slug or slugify(re.sub(r"^https?://", "", url).split("?")[0].split("/")[0])
    out = os.path.join(LIBRARY, "shots", f"{slug}.jpg")
    code = grab(url, out, width=width, wait=wait, full=full, max_height=max_height)
    if code == 0:
        print(f"已存 {os.path.getsize(out) // 1024} KB")
        print(f"shots/{slug}.jpg")
    sys.exit(code)


if __name__ == "__main__":
    main()
