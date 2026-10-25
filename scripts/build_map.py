#!/usr/bin/env python3
"""根据 library/ 下的灵感卡片重建目录地图，并生成每本书的「灵感总览」Hub 页。

三层结构：
    library/目录地图.md          ← 总目录：按书分组，每本书指向自己的 Hub
    library/books/<书名>.md      ← 每本书一个 Hub：按章节分组列出该书所有灵感
    library/<日期>_<书名>_<页>.md ← 卡片本体

支持多知识库：卡片 frontmatter 用 dest_<kb> 记录每个目标的链接
（dest_lexiangla / dest_feishu / dest_obsidian / dest_ima），
旧的单个 dest 字段仍兼容，按「原件」显示。

用法:
    python3 build_map.py                     # 只写本地镜像
    python3 build_map.py --vault <vault目录>  # 同时写一份到 Obsidian（本地卡片用双链）
"""
import os
import sys
import re
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env  # noqa: E402
LIBRARY = env.library_dir()
BOOKS_DIR = os.path.join(LIBRARY, "books")
MAP_PATH = os.path.join(LIBRARY, "目录地图.md")

# dest 字段 → 地图里显示的标签，顺序即显示顺序
DEST_LABELS = [
    ("dest_lexiangla", "乐享"),
    ("dest_feishu", "飞书"),
    ("dest_obsidian", "Obsidian"),
    ("dest_ima", "IMA"),
    ("dest", "原件"),          # 旧卡片兼容
]


def parse_card(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    fm_m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not fm_m:
        return None
    fm = {}
    for line in fm_m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"')
    body = fm_m.group(2)
    thought_m = re.search(r"## 当时的想法\n\n?(.*?)(?:\n## |\Z)", body, re.S)
    thought = (thought_m.group(1).strip() if thought_m else "").splitlines()
    first_thought = next((l.strip() for l in thought if l.strip() and l.strip() != "无"), "")

    dests = []
    for key, label in DEST_LABELS:
        v = fm.get(key, "").strip()
        if v:
            dests.append((label, v))

    return {
        "book": fm.get("book", "未知书名"),
        "title": fm.get("title", "").strip(),
        "url": fm.get("url", "").strip(),
        # 卡片有两种来源：书页（kind 缺省）与链接文档（kind=link）
        "kind": (fm.get("kind", "").strip()
                 or ("link" if fm.get("url", "").startswith("http") else "page")),
        "chapter": fm.get("chapter", "").strip(),
        "page": fm.get("page", ""),
        "paras": fm.get("paras", ""),
        "page_paras": fm.get("page_paras", ""),
        "date": fm.get("date", ""),
        "image": fm.get("image", "").strip(),
        "dests": dests,
        "thought": first_thought,
        "file": os.path.basename(path),
    }


def is_link(e):
    return e["kind"] == "link"


def entry_head(e, wikilink=False):
    """条目的「位置」部分：书页是「第 N 页」，链接文档是标题本身（点开回原文）。"""
    if is_link(e):
        label = e["title"] or (e["url"] or e["book"])
        if e["url"].startswith("http"):
            return f"[{label}]({e['url']})"
        return label
    return f"第 {e['page']} 页" if e["page"] else ""


def render_links(dests, file_name, wikilink=False):
    """把目标链接渲染成地图里的一小段文本。

    wikilink=True（同步进 Obsidian 时）把本地卡片渲染成可点击的双链，
    否则 Obsidian 那一条只显示「本地」，避免出现打不开的 file:// 链接。
    """
    if not dests:
        return f"[{file_name}]({file_name})"
    parts = []
    for label, v in dests:
        if v.startswith("http"):
            parts.append(f"[{label}]({v})")
        elif wikilink and v.endswith(".md"):
            stem = os.path.basename(v)[:-3]
            parts.append(f"[[{stem}|{label}]]")
        else:
            # 本地路径（如 Obsidian vault 内的文件）不做成链接，避免产生坏链
            parts.append(f"{label}·本地")
    return " ".join(parts)


def _page_no(e):
    """页码转数字，用于章节排序；转不了给一个大数排后面。"""
    m = re.search(r"\d+", str(e.get("page", "")))
    return int(m.group()) if m else 10 ** 6


def group_by_chapter(entries):
    """按 chapter 字段分组，返回 [(章节名, [条目…])]，按章内最小页码排序。"""
    groups = {}
    for e in entries:
        groups.setdefault(e["chapter"] or "未标章节", []).append(e)
    out = []
    for name, items in groups.items():
        items.sort(key=lambda x: (_page_no(x), x["date"]))
        out.append((name, items))
    # 「未标章节」永远垫底
    out.sort(key=lambda kv: (kv[0] == "未标章节", min(_page_no(e) for e in kv[1]), kv[0]))
    return out


def build_lines(books, cards, wikilink=False):
    n_books = sum(1 for b, es in books.items() if not all(is_link(e) for e in es))
    n_docs = sum(1 for b, es in books.items() if all(is_link(e) for e in es))
    scope = " · ".join(x for x in (
        f"{n_books} 本书" if n_books else "",
        f"{n_docs} 篇链接文档" if n_docs else "") if x) or "还没有来源"
    lines = [
        "# 📚 读书灵感目录地图",
        "",
        f"> 共 {scope} · {len(cards)} 条灵感 · 更新于 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    for book in sorted(books):
        entries = sorted(books[book], key=lambda x: x["date"])
        link_group = all(is_link(e) for e in entries)
        name = f"{book} ({len(entries)})" if link_group else f"《{book}》({len(entries)})"
        lines.append(f"## {name}")
        lines.append("")
        hub = f"{book} · 灵感总览"
        hub_text = f"{book} · 灵感总览" if link_group else f"《{book}》· 灵感总览"
        lines.append(f"> 总览：{f'[[{hub}]]' if wikilink else f'[{hub_text}](books/{book}.md)'}")
        lines.append("")
        for e in entries:
            loc = entry_head(e, wikilink=wikilink)
            para = ""
            p = e["paras"].strip()
            if p and p != "all":
                para = f"第 {p} 段"
            elif p == "all" and e["page_paras"]:
                para = f"全页 {e['page_paras']} 段"
            # 书页是「第 53 页·第 2 段」紧排；链接是「标题 第 2 段」空格隔开
            if para:
                para = f"{para}" if is_link(e) else f"·{para}"
            links = render_links(e["dests"], e["file"], wikilink=wikilink)
            thought = f" — {e['thought'][:40]}" if e["thought"] else ""
            sep = " " if para and is_link(e) else ""
            lines.append(f"- **{e['date']}** {loc}{sep}{para} {links}{thought}")
        lines.append("")
    return lines


def build_hub(book, entries, wikilink=False):
    """一个来源的 Hub 页：书按章节分组，链接文档按时间平铺。"""
    entries = sorted(entries, key=lambda x: x["date"])
    link_group = all(is_link(e) for e in entries)
    chapters = [("", entries)] if link_group else group_by_chapter(entries)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    headline = f"{book} · 灵感总览" if link_group else f"《{book}》· 灵感总览"
    scope = "" if link_group else f" · {len(chapters)} 个章节"
    lines = [
        "---",
        f"book: {book}",
        "type: book-hub",
        f"entries: {len(entries)}",
        f"chapters: {len(chapters)}",
        f"updated: {stamp}",
        f"tags: [读书, 读书/{book}]",
        "---",
        "",
        f"# {headline}",
        "",
        f"> {len(entries)} 条灵感{scope} · 更新于 {stamp}",
        "",
    ]
    for name, items in chapters:
        if name:
            lines.append(f"## {name}")
            lines.append("")
        for e in items:
            loc = entry_head(e, wikilink=wikilink) or "位置未知"
            p = e["paras"].strip()
            para = f"第 {p} 段" if p and p != "all" else (
                f"全页 {e['page_paras']} 段" if p == "all" and e["page_paras"] else "")
            head = f"**{loc}**" + (f" · {para}" if para else "")
            links = render_links(e["dests"], e["file"], wikilink=wikilink)
            lines.append(f"- {head} — {e['thought'] or '（未记想法）'}")
            lines.append(f"  - {links}")
        lines.append("")
    lines.append("---")
    lines.append("")
    if wikilink:
        lines.append("← 返回 [[读书灵感目录地图]]")
    else:
        lines.append("← 返回 [读书灵感目录地图](../目录地图.md)")
    return "\n".join(lines) + "\n"


def write_hubs(books, out_dir, wikilink=False, name_suffix=""):
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for book, entries in books.items():
        path = os.path.join(out_dir, f"{book}{name_suffix}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_hub(book, entries, wikilink=wikilink))
        written.append(path)
    return written


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", help="同时写一份到指定 Obsidian 目录（本地卡片用双链，可点击）")
    args = ap.parse_args()

    os.makedirs(LIBRARY, exist_ok=True)
    cards = []
    for fn in sorted(os.listdir(LIBRARY)):
        if fn.endswith(".md") and fn != "目录地图.md":
            c = parse_card(os.path.join(LIBRARY, fn))
            if c:
                cards.append(c)

    books = {}
    for c in cards:
        books.setdefault(c["book"], []).append(c)

    with open(MAP_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(build_lines(books, cards)))
    print(f"目录地图已更新: {os.path.abspath(MAP_PATH)}（{len(books)} 个来源 / {len(cards)} 条灵感）")

    hubs = write_hubs(books, BOOKS_DIR)
    print(f"来源 Hub 已更新: {len(hubs)} 个（{os.path.abspath(BOOKS_DIR)}）")

    if args.vault:
        os.makedirs(args.vault, exist_ok=True)
        vpath = os.path.join(args.vault, "读书灵感目录地图.md")
        with open(vpath, "w", encoding="utf-8") as f:
            f.write("\n".join(build_lines(books, cards, wikilink=True)))
        print(f"Obsidian 版地图已写入: {vpath}")
        vhubs = write_hubs(books, args.vault, wikilink=True, name_suffix=" · 灵感总览")
        print(f"Obsidian 版 Hub 已写入: {len(vhubs)} 个")


if __name__ == "__main__":
    main()
