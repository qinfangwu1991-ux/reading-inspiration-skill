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
import naming  # noqa: E402
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
            # 值可能被引号包着（含 : # [ ] , 的书名必须引号），交给 naming 解析
            fm[k.strip()] = naming.unquote(v)
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
            # 标题里可能有 [ ]，不转义会把链接语法拆坏
            return f"[{naming.md_text(label)}]({e['url']})"
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


def build_lines(books, cards, hub_targets, wikilink=False):
    """总目录正文。hub_targets：书名 → Hub 的链接目标（已按落盘文件名解析过）。

    链接目标必须用「实际文件名」，不能直接用书名 —— 书名里的 `/`、`..` 会把链接指到库外。
    """
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
        hub_text = f"{book} · 灵感总览" if link_group else f"《{book}》· 灵感总览"
        target = hub_targets.get(book) or naming.link_target(naming.path_stem(book))
        hub_link = (f"[[{target}]]" if wikilink
                    else f"[{naming.md_text(hub_text)}](books/{target}.md)")
        lines.append(f"> 总览：{hub_link}")
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
    # 书名进 frontmatter 一律加引号（含 : # [ ] , 都不会写坏），标签单独做分量清洗
    tags = naming.yaml_list(["读书", f"读书/{naming.tag_part(book)}"])
    lines = [
        "---",
        # 生成者签名：归属判断靠它，不能只靠 book 字段 —— 用户手写的同主题笔记
        # 也可能写着 `book: 书名`，只比对 book 就覆盖等于吃掉了人家的正文
        f"generator: {naming.GENERATOR}",
        "type: book-hub",
        f"book: {naming.quote(book)}",
        f"book_key: {naming.quote(naming.hash6(naming.squash(book)))}",
        f"entries: {len(entries)}",
        f"chapters: {len(chapters)}",
        f"updated: {stamp}",
        f"tags: {tags}",
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


def hub_paths(out_dir, books, tail=""):
    """一次分配全部 Hub 的**完整路径**，返回 ({书: 路径}, {书: 链接目标})。

    必须一次分配全量：`AI/ML` 与 `AI,ML` 清洗后同名，逐本分配时先来的拿干净名字，
    和「先保存卡片、后构建地图」的分配顺序不一致，回链就会指错 Hub。
    naming.allocate_paths 按书名排序分配，因此与调用顺序无关。

    链接目标由**实际写下的文件名**反推（去掉 .md），不再拿主干拼第二遍：
    tail 是「 · 灵感总览」时，冲突检查看的是 `书名 · 灵感总览.md`，
    若这里只补 `.md` 就成了 `书名.md` —— 检查的和写的不是同一个文件，
    用户已有的 `书名.md` 会被覆盖。
    """
    suffix = f"{tail}.md"
    paths = naming.allocate_paths(out_dir, list(books), suffix=suffix,
                                 ident_key="book", accept=naming.hub_owned)
    targets = {b: naming.link_stem(p) for b, p in paths.items()}
    return paths, targets


def write_hubs(books, paths, wikilink=False):
    """按「书名 → 落盘路径」写 Hub；路径已由 naming 保证在目标目录内。

    写之前再确认一次「这个位置是我自己的 Hub」：只有文件不存在，或它确实是
    本工具为这本书生成的（`hub_owned`）才允许覆盖。naming 已经保证了这一点，
    这里是最后一道闸 —— 名字逻辑以后改坏了，也不会静默吃掉用户的手写笔记。
    """
    written = []
    for book, entries in books.items():
        path = paths[book]
        if os.path.exists(path) and not naming.hub_owned(path, book):
            raise RuntimeError(
                f"拒绝覆盖已有笔记：{path}（不是本工具为《{book}》生成的 Hub）")
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

    # 先解析落盘路径，再据此生成总目录里的链接（链接目标 = 真实文件名）
    os.makedirs(BOOKS_DIR, exist_ok=True)
    l_paths, l_targets = hub_paths(BOOKS_DIR, list(books))

    with open(MAP_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(build_lines(books, cards, l_targets)))
    print(f"目录地图已更新: {os.path.abspath(MAP_PATH)}（{len(books)} 个来源 / {len(cards)} 条灵感）")

    hubs = write_hubs(books, l_paths)
    print(f"来源 Hub 已更新: {len(hubs)} 个（{os.path.abspath(BOOKS_DIR)}）")

    if args.vault:
        os.makedirs(args.vault, exist_ok=True)
        v_paths, v_targets = hub_paths(args.vault, list(books), tail=" · 灵感总览")
        vpath = os.path.join(args.vault, "读书灵感目录地图.md")
        with open(vpath, "w", encoding="utf-8") as f:
            f.write("\n".join(build_lines(books, cards, v_targets, wikilink=True)))
        print(f"Obsidian 版地图已写入: {vpath}")
        vhubs = write_hubs(books, v_paths, wikilink=True)
        print(f"Obsidian 版 Hub 已写入: {len(vhubs)} 个")

        # 上一版把 tail 丢了，写成 `<书名>.md`；本次写的是 `<书名> · 灵感总览.md`。
        # 旧文件没被覆盖（也没被删），提示一句，让用户自己决定要不要清理。
        tail_txt = " · 灵感总览"
        stale = []
        for p in vhubs:
            if not p.endswith(tail_txt + ".md"):
                continue
            old = p[: -len(tail_txt + ".md")] + ".md"
            if os.path.exists(old) and naming.read_frontmatter_value(old, "type") == "book-hub":
                stale.append(old)
        if stale:
            print("提示：发现上一版误命名的旧 Hub（内容已写到正确文件名，旧文件保留未删）：")
            for s in stale:
                print(f"  · {s}")


if __name__ == "__main__":
    main()
