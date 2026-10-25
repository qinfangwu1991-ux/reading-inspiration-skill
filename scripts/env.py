#!/usr/bin/env python3
"""跨平台 / 云环境公共设施：路径、解释器、外部命令探测、图片压缩、编码。

为什么单独抽一个文件：这个技能要在这三种地方都跑得通 ——
  1) 本机 macOS（有 sips、有 Chrome、有 iCloud vault）
  2) 别人的 Windows / Linux（没有 sips，Chrome 装在别处）
  3) 云端沙箱（只有 python3 标准库，可能没有浏览器、没有 Obsidian、没有连接器）
所以「本机特有」的东西一律经过这里探测：拿得到就用，拿不到就返回 False 让调用方降级，
**不能直接抛 FileNotFoundError 崩栈**。
"""
import os
import shutil
import subprocess
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SKILL_DIR, "scripts")
# 别写死 "python3"：Windows 上是 python.exe，有些云沙箱只装了 python
PY = sys.executable or "python3"


def library_dir(create=False, override=None):
    """素材库落地目录。

    默认是技能目录下的 library/；若技能装在只读位置（云沙箱、系统级安装），
    用环境变量 READING_LIBRARY 指到一个可写目录即可，所有脚本都会跟着走。
    """
    d = (override
         or os.environ.get("READING_LIBRARY")
         or os.path.join(SKILL_DIR, "library"))
    d = os.path.abspath(os.path.expanduser(d))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def which(names):
    """按顺序找第一个存在的命令，返回绝对路径或 None。"""
    if isinstance(names, str):
        names = [names]
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def run_py(script, *args, **kw):
    """用「当前解释器」跑同目录下的另一个脚本，避免 python3/python 之争。"""
    return subprocess.run([PY, os.path.join(SCRIPTS, script)] + list(args), **kw)


def setup_stdout():
    """Windows 控制台默认 GBK，打印中文/emoji 会 UnicodeEncodeError；统一改 UTF-8。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def shrink_jpeg(src, dst, max_side=1800, quality=78):
    """把图压成 JPEG 并限制长边，返回 (成功?, 用了什么办法)。

    四路降级，全都不行就返回 False，由调用方原样归档：
      sips（macOS）→ ImageMagick（Linux 常见）→ Pillow（装了就用）→ ffmpeg
    """
    if os.path.exists(dst):
        try:
            os.remove(dst)
        except OSError:
            pass

    exe = which("sips")
    if exe:
        r = subprocess.run(
            [exe, "-s", "format", "jpeg", "-s", "formatOptions", str(quality),
             "-Z", str(max_side), src, "--out", dst],
            capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            return True, "sips"
        if os.path.exists(dst):
            os.remove(dst)

    for name in ("magick", "convert"):
        exe = which(name)
        if not exe:
            continue
        cmd = [exe, src, "-auto-orient",
               "-resize", f"{max_side}x{max_side}>",
               "-quality", str(quality), dst]
        if name == "convert":
            cmd = [exe] + cmd[1:]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            return True, name
        if os.path.exists(dst):
            os.remove(dst)

    try:
        from PIL import Image           # 可选依赖，装了才用
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((max_side, max_side))
            im.save(dst, "JPEG", quality=quality, optimize=True)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            return True, "Pillow"
    except Exception:
        if os.path.exists(dst):
            os.remove(dst)

    exe = which("ffmpeg")
    if exe:
        r = subprocess.run(
            [exe, "-y", "-loglevel", "error", "-i", src,
             "-vf", f"scale='min({max_side},iw)':-1", "-q:v", "4", dst],
            capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            return True, "ffmpeg"
        if os.path.exists(dst):
            os.remove(dst)

    return False, "无可用压缩工具"


def chrome_path():
    """找 Chrome / Chromium / Edge。macOS 应用包 → PATH → Linux/Windows 常见装法。

    云沙箱通常没有浏览器：返回 None，调用方应给出「让用户自己截图」的降级路径，
    而不是报错崩掉。
    """
    env = os.environ.get("CHROME_PATH") or os.environ.get("CHROME_BIN")
    if env and os.path.exists(env):
        return env

    cands = [
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        # Linux
        "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/snap/bin/chromium", "/opt/google/chrome/chrome",
        # Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in cands:
        if os.path.exists(p):
            return p
    return which(["google-chrome", "google-chrome-stable", "chromium",
                  "chromium-browser", "chrome", "microsoft-edge", "msedge"])


def tempdir():
    """临时目录：别写死 /tmp（Windows 没有）。"""
    import tempfile
    return tempfile.gettempdir()
