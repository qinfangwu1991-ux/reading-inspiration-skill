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
import naming  # noqa: E402
import delivery  # noqa: E402
LIBRARY = env.library_dir()

HUB_SUFFIX = " · 灵感总览.md"


def all_books():
    """素材库里出现过的全部书名（决定 Hub 文件名要用全量，不能只看当前这本）。"""
    books = set()
    if not os.path.isdir(LIBRARY):
        return books
    for fn in sorted(os.listdir(LIBRARY)):
        if not fn.endswith(".md"):
            continue
        try:
            with open(os.path.join(LIBRARY, fn), encoding="utf-8") as f:
                text = f.read(4000)
        except OSError:
            continue
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not m:
            continue
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k.strip() == "book":
                b = naming.unquote(v).strip()
                if b:
                    books.add(b)
                break
    return books


def hub_stem_for(book, vault):
    """这本书在 vault 里的 Hub 文件名（双链目标，不含 .md）。

    必须和 build_map.py 用**同一套全量分配**（naming.allocate_paths + hub_owned）：
    只看自己这一本时，`AI/ML` 和 `AI,ML` 都会认为干净名字没人用；
    先保存的那本拿到干净名字，之后地图按排序把这个名字分给了另一本，
    回链就指到别人的 Hub 去了。归属判断也共用，否则 vault 里有手写的同名
    总览（带 `book:` 但没有生成者签名）时，两边会算出不同的文件名。
    """
    paths = naming.allocate_paths(vault, all_books(), suffix=HUB_SUFFIX,
                                  ident_key="book", accept=naming.hub_owned)
    p = paths.get(book)
    return naming.link_stem(p) if p else naming.path_stem(book)


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
            book = naming.unquote(v)
        elif k.strip() == "image":
            image = naming.unquote(v)
        elif k.strip() == "url":
            url = naming.unquote(v)
        elif k.strip() == "kind":
            kind = naming.unquote(v)

    # 1. 章节摘录 → callout
    body = re.sub(r"^> \*\*(.+?)\*\* (.*)$", r"> [!quote] 第 \1 段\n> \2", body, flags=re.M)

    # 2. 层级标签
    if book:
        tag = naming.tag_part(book)
        new_tags = f"tags: {naming.yaml_list(['读书/' + tag])}"
        if re.search(r"^tags: .*$", fm, flags=re.M):
            fm = re.sub(r"^tags: .*$", new_tags, fm, flags=re.M)
        else:
            # 卡片没写 tags 也要补上，否则 vault 副本在侧栏里归不了类
            fm = fm.rstrip() + "\n" + new_tags

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

    # 4. 回链 Hub：用全量分配出的真实文件名，书名里的 / .. 不会把链接指到别处
    if book:
        hub_stem = hub_stem_for(book, vault)
        body = body.rstrip() + f"\n\n---\n\n← 回到 [[{naming.link_target(hub_stem)}]]\n"

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
    rec = delivery.begin(card, "obsidian", vault, os.path.basename(card)[:-3])
    with open(dest, "w", encoding="utf-8") as f:
        f.write(out_text)
    # 本地写是原子的（覆盖同名文件），写完即视为投递成功；台账 + dest_obsidian 由脚本自己记，
    # 这样 Agent 不必再手动 Edit，也不会出现「副本写了、卡片没记」的半截状态。
    delivery.commit(rec["delivery_id"], dest, dest)
    delivery.record_dest(card, "dest_obsidian", dest)
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
