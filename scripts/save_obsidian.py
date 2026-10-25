#!/usr/bin/env python3
"""把灵感卡片写进本地 Obsidian vault，并顺带做 Obsidian 专属的格式升级。

本地卡片是「中立格式」（同时喂给乐享/飞书），所以**只在写入 vault 这一份时**做转换：
- 原文摘录 → Obsidian callout（`> [!quote] 第 ④ 段`），reading view 里是带边框的引用卡
- tags 改成层级标签 `读书/<书名>`，侧栏里按书收起，按「读书」也能筛到
- 卡片里有 `image:` 时，把原页照片复制进 vault 的 pages/ 并内嵌（`![[...|420]]`）
- 末尾加一条回链，指向上层「<书名> · 灵感总览」Hub

用法: python3 save_obsidian.py <卡片.md路径>
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env  # noqa: E402
LIBRARY = env.library_dir()


def read_vault():
    cfg = subprocess.run(
        [env.PY, os.path.join(HERE, "config.py"), "show"],
        capture_output=True, text=True, check=True).stdout
    return json.loads(cfg).get("obsidian_vault", "")


def transform(text, vault):
    """把中性卡片转成 Obsidian 版：callout / 层级标签 / 原图 / 回链。返回 (新文本, 需复制的图片相对路径)。"""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return text, None
    fm, body = m.group(1), m.group(2)

    book = ""
    image = ""
    url = ""
    kind = ""
    for line in fm.splitlines():
        k, _, v = line.partition(":")
        if k.strip() == "book":
            book = v.strip()
        elif k.strip() == "image":
            image = v.strip()
        elif k.strip() == "url":
            url = v.strip()
        elif k.strip() == "kind":
            kind = v.strip()

    # 1. 章节摘录 → callout
    body = re.sub(r"^> \*\*(.+?)\*\* (.*)$", r"> [!quote] 第 \1 段\n> \2", body, flags=re.M)

    # 2. 层级标签
    if book:
        fm = re.sub(r"^tags: \[.*?\]$", f"tags: [读书/{book}]", fm, flags=re.M)

    # 3. 原页照片：插在「# 书名 · 第 N 页」那行之后
    img_block = ""
    img_src = None
    fname = ""
    if image:
        fname = os.path.basename(image)
        img_src = os.path.join(LIBRARY, image)
        if os.path.exists(img_src):
            body = re.sub(r"^(# .+)$",
                          lambda mm: (f"{mm.group(1)}\n\n"
                                      f"> [!example]- {'原页面截图' if kind == 'link' else '原页照片'}\n"
                                      f"> ![[{fname}|420]]"),
                          body, count=1, flags=re.M)
        else:
            img_src = None

    # 3.5 链接文档：标题下给一条可点的原文，正文里的「> 来源：…」就不再重复
    if url.startswith("http"):
        body = re.sub(r"^(# .+)$",
                      lambda mm: f"{mm.group(1)}\n\n> [!info]- 原文\n> [{book or '链接'}]({url})",
                      body, count=1, flags=re.M)

    # 4. 回链 Hub
    if book:
        body = body.rstrip() + f"\n\n---\n\n← 回到 [[{book} · 灵感总览]]\n"

    return f"---\n{fm}\n---\n{body}", (image, img_src, fname) if img_src else None


def main():
    if len(sys.argv) != 2:
        print("用法: save_obsidian.py <卡片.md路径>", file=sys.stderr)
        sys.exit(1)
    card = os.path.abspath(sys.argv[1])
    if not os.path.exists(card):
        print(f"卡片不存在: {card}", file=sys.stderr)
        sys.exit(1)

    vault = read_vault()
    if not vault or not os.path.isdir(vault):
        print(f"Obsidian 目录无效: {vault!r}，请用 config.py 重新设置", file=sys.stderr)
        sys.exit(2)

    with open(card, encoding="utf-8") as f:
        text = f.read()

    out_text, img = transform(text, vault)

    dest = os.path.join(vault, os.path.basename(card))
    with open(dest, "w", encoding="utf-8") as f:
        f.write(out_text)
    print(f"已写入 Obsidian: {dest}")

    if img:
        rel, src, fname = img
        vpages = os.path.join(vault, "pages")
        os.makedirs(vpages, exist_ok=True)
        shutil.copyfile(src, os.path.join(vpages, fname))
        print(f"原页照片已复制: {fname}")

    # 同步目录地图与本书 Hub（vault 版用双链，点一下能跳到卡片）
    subprocess.run([env.PY, os.path.join(HERE, "build_map.py"), "--vault", vault],
                   check=False)

    print(f"obsidian://open?path={dest}")


if __name__ == "__main__":
    main()
