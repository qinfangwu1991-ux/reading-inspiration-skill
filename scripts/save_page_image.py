#!/usr/bin/env python3
"""把书页原图归档进 library/pages/，供日后复核 OCR、重新切段。

动机：卡片里只有文字，OCR 一旦认错、或者事后想重新选段，就没有依据了；
而用户拍的图多半躺在系统临时目录里，早晚会被清掉。

压缩：sips（macOS）→ ImageMagick → Pillow → ffmpeg，四路降级；
全都没有（比如极简云沙箱）就**原样归档**，绝不报错。
- 长边压到 --max（默认 1800px），JPEG 质量 --quality（默认 78）
- **压缩后如果反而更大，就原样保留原图**（说明原图本来就已经压过）
- 输出名固定为 `<书名>_<页码>.jpg`，重跑幂等覆盖

用法:
    python3 save_page_image.py <源图片> --book 皇帝的账本 --page 113
    python3 save_page_image.py <源图片> --book 皇帝的账本 --page 113 --keep   # 不压，原样存

输出（最后一行是相对 library/ 的路径，直接写进卡片 frontmatter 的 image 字段）:
    pages/皇帝的账本_113.jpg
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env  # noqa: E402

LIBRARY = env.library_dir()
PAGES = os.path.join(LIBRARY, "pages")

# 文件名里不能出现的字符（macOS 只禁 / 和 :，但顺手把这些也换掉更稳）
_BAD = re.compile(r'[/\\:*?"<>|\n\r\t]+')


def safe_name(s):
    return _BAD.sub("_", str(s)).strip(" ._") or "未命名"


def human(n):
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f}KB"
    return f"{n / 1024 / 1024:.1f}MB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="书页原图路径（jpg / png / heic 均可）")
    ap.add_argument("--book", required=True, help="书名，用于文件名")
    ap.add_argument("--page", required=True, help="页码，用于文件名")
    ap.add_argument("--max", type=int, default=1800, help="长边像素上限，默认 1800")
    ap.add_argument("--quality", type=int, default=78, help="JPEG 质量，默认 78")
    ap.add_argument("--keep", action="store_true", help="不压缩，原样复制")
    a = ap.parse_args()

    src = os.path.abspath(os.path.expanduser(a.source))
    if not os.path.isfile(src):
        print(f"源图不存在: {src}", file=sys.stderr)
        sys.exit(2)

    os.makedirs(PAGES, exist_ok=True)
    stem = f"{safe_name(a.book)}_{safe_name(a.page)}"
    out = os.path.join(PAGES, stem + ".jpg")
    src_size = os.path.getsize(src)

    if a.keep:
        shutil.copyfile(src, out)
        how = "原样归档"
    else:
        tmp = out + ".tmp.jpg"
        ok, tool = env.shrink_jpeg(src, tmp, max_side=a.max, quality=a.quality)
        if not ok:
            # 没有任何可用的压缩工具 / 不认的格式（个别 HEIC、损坏文件）→ 原样复制
            ext = os.path.splitext(src)[1].lower() or ".jpg"
            out = os.path.join(PAGES, stem + ext)
            shutil.copyfile(src, out)
            how = f"{tool}，原样归档"
        else:
            new_size = os.path.getsize(tmp)
            if new_size >= src_size:
                os.remove(tmp)
                shutil.copyfile(src, out)
                how = "压缩无收益，保留原图"
            else:
                os.replace(tmp, out)
                how = f"已压缩 {human(src_size)} → {human(new_size)}"

    rel = os.path.relpath(out, LIBRARY)
    print(f"尺寸 {human(os.path.getsize(out))} · {how}")
    print(f"已归档: {os.path.relpath(out, os.path.dirname(LIBRARY))}")
    # 最后一行给脚本/Agent 直接取用
    print(rel)


if __name__ == "__main__":
    main()
