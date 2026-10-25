#!/usr/bin/env python3
"""命名与编码：一处定义，全链路复用。

问题背景：书名 / 标题来自用户的原文，可能含 `/`、`..`、逗号、方括号、冒号。
这些字符在三个地方含义完全不同，必须分开处理，不能统一「清洗」：

    显示名（display）     保留原文，出现在正文标题、目录地图、前端反馈里
    文件名（path_stem）   不能含路径分隔符，不可是 `.`/`..`，长度受限
    YAML 标量（quote）    含 `:` `#` `[` `]` `{` `}` `,` 或以特殊字符开头时要引号
    Obsidian 双链          不能含 `[` `]` `|` `#` `^`（由 path_stem 保证）

文件名撞名（两个不同书名清洗后相同）用短哈希尾巴区分，不靠覆盖。
"""
import hashlib
import os
import re
import unicodedata

STEM_LIMIT = 48

# 文件系统层面真正危险的字符 + 控制字符
_PATH_BAD = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]+')
# 文件名里只保留中英文数字（可读性），其余变下划线
_PATH_KEEP = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
_WS = re.compile(r"\s+")

# 双链里会破坏语法的字符，path_stem 之后理论上不会出现，兜底再拦一次
_LINK_BAD = re.compile(r"[\[\]|#^]")

# YAML 里裸写会被误解析的起始字符
_YAML_LEAD = set("-?:,[]{}#&*!|>'\"%@`")


def squash(s):
    """压掉多余空白，保留原文所有其它字符。"""
    return _WS.sub(" ", str(s or "")).strip()


def display(s, fallback="未命名"):
    """显示名：只压空白，绝不改动用户的原文。"""
    return squash(s) or fallback


def path_stem(s, limit=STEM_LIMIT, fallback="未命名"):
    """文件名主干：去掉路径分隔符与不可见字符，保留中英文数字，限长。

    保证返回值不会是 `.` `..` 或以点/空格结尾（Windows 与 macOS 都不认）。
    """
    s = unicodedata.normalize("NFC", squash(s))
    s = _PATH_KEEP.sub("_", s).strip("_")
    s = s[:limit].strip("_")
    return s or fallback


def hash6(s):
    """短哈希：用于撞名区分与来源标识。"""
    return hashlib.sha256(str(s or "").encode("utf-8")).hexdigest()[:6]


def hash8(s):
    return hashlib.sha256(str(s or "").encode("utf-8")).hexdigest()[:8]


def quote(s):
    """把一个值写成安全的 YAML 双引号标量（永远带引号，省得逐字符判断）。"""
    v = str(s if s is not None else "")
    v = v.replace("\\", "\\\\").replace('"', '\\"')
    v = v.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{v}"'


def needs_quote(s):
    v = str(s or "")
    if not v or v != v.strip():
        return True
    if v[0] in _YAML_LEAD:
        return True
    return any(c in v for c in ':#[]{},&*!|>\'"%@`\n')


def scalar(s):
    """需要时加引号，不需要时保持裸写（让 hand-written frontmatter 也可读）。"""
    return quote(s) if needs_quote(s) else str(s)


def yaml_list(items):
    """写成 YAML 行内序列，逐项加引号（逗号、方括号不再产生歧义）。"""
    return "[" + ", ".join(quote(i) for i in items if str(i) != "") + "]"


def unquote(v):
    """解析 frontmatter 里的单个标量：支持双引号、单引号与裸写。"""
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        body = v[1:-1]
        out = []
        i = 0
        while i < len(body):
            c = body[i]
            if c == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                out.append({"n": "\n", "r": "\r", "t": "\t"}.get(nxt, nxt))
                i += 2
                continue
            out.append(c)
            i += 1
        return "".join(out)
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def parse_list(v):
    """解析 `["a", "b"]` / `[a, b]` / 裸逗号分隔 三种写法，返回列表。

    注意：这里按引号感知切分，所以 `["读书/AI,ML"]` 是一个元素，不会裂成两个。
    """
    v = (v or "").strip()
    if not v:
        return []
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    items, buf, quote_ch, esc = [], [], "", False
    for c in v:
        if esc:
            buf.append(c)
            esc = False
            continue
        if c == "\\" and quote_ch == '"':
            buf.append(c)
            esc = True
            continue
        if quote_ch:
            buf.append(c)
            if c == quote_ch:
                quote_ch = ""
            continue
        if c in "\"'":
            quote_ch = c
            buf.append(c)
            continue
        if c == ",":
            items.append("".join(buf))
            buf = []
            continue
        buf.append(c)
    items.append("".join(buf))
    return [unquote(i) for i in items if i.strip()]


def tag_part(s, fallback="未命名"):
    """标签分量：Obsidian 标签不能含逗号/方括号/冒号等，替换而非丢弃。"""
    s = squash(s)
    s = re.sub(r"[,;:\[\]{}#|^]+", "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"\.{2,}", "_", s)      # `..` 在标签里没有意义，替换掉
    s = re.sub(r"_{2,}", "_", s).strip("_")
    if not s.strip("."):
        return fallback
    return s


def link_target(stem):
    """双链目标：path_stem 之后应该已经安全，这里兜底替换。"""
    return _LINK_BAD.sub("_", str(stem or "未命名"))


def md_text(s):
    """Markdown 链接文字 / 普通文本：转义会破坏语法的字符。"""
    v = str(s if s is not None else "")
    return (v.replace("\\", "\\\\").replace("[", "\\[")
             .replace("]", "\\]").replace("|", "\\|"))


def safe_join(base, stem, suffix=".md"):
    """在 base 下拼一个文件名，并校验结果确实落在 base 内。

    目录穿越（`../`）和分隔符注入在这里被彻底拦住；真出问题时抛 ValueError，
    调用方据此报错，而不是把文件写到库外面。
    """
    name = f"{stem}{suffix}"
    path = os.path.abspath(os.path.join(base, name))
    root = os.path.abspath(base)
    if os.path.commonpath([root, path]) != root or os.path.dirname(path) != root:
        raise ValueError(f"拒绝写到目标目录之外：{name!r}")
    return path


def read_frontmatter(path):
    """读 frontmatter，返回 ({字段: 值}, 正文)。读不出返回 ({}, "")。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}, ""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        fm[k.strip()] = unquote(v)
    return fm, m.group(2)


GENERATOR = "reading-inspiration"


def artifact_owned(path, kind, name, name_key="book", legacy_marker=""):
    """这个路径上的文件，是不是「本工具为 name 生成的 kind 产物」。

    归属判断不能只看一个字段：用户手写的同主题笔记也可能带 `book: 书名`。
    只比对 book 就覆盖，等于把用户的正文当自己的生成物重写掉。
    规则（从严到宽）：
        1. 名字字段对不上 → 不是（False）
        2. 带生成者签名（`generator: reading-inspiration`）且 `type == kind` → 是
        3. 带了**别人的** generator → 不是（不碰其他工具的文件）
        4. 没有签名但 `type == kind`、正文带生成痕迹（legacy_marker + 回链）→
           是（旧版产物，允许一次迁移覆盖）
        5. 其余 → 不是
    """
    fm, body = read_frontmatter(path)
    if not fm:
        return False
    if squash(fm.get(name_key, "")) != squash(name):
        return False
    gen = (fm.get("generator") or "").strip()
    if gen:
        return gen == GENERATOR and fm.get("type") == kind
    if fm.get("type") != kind:
        return False
    if legacy_marker and legacy_marker not in body:
        return False
    return "← 返回" in body


def hub_owned(path, book):
    """Hub 页归属：build_map 与 save_obsidian 必须用**同一个**判断。"""
    return artifact_owned(path, "book-hub", book, name_key="book",
                          legacy_marker="· 灵感总览")


def _read_frontmatter_value(path, key):
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(2000)
    except OSError:
        return None
    m = re.match(r"^---\n(.*?)\n---", head, re.S)
    if not m:
        return None
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        if k.strip() == key:
            return unquote(v)
    return None


def resolve_md_path(base, s, suffix=".md", ident_key=None, taken=None, accept=None):
    """在 base 下为显示名 s 找一个不冲突的落盘路径。

    规则（确定性，多个脚本算出同一个结果）：
        1. path_stem(s) 没被占用 → 用它
        2. 已被占用：`accept(path, s)` 认这个文件是「自己的」→ 复用它
           （没给 accept 时退化为「frontmatter 的 ident_key 等于 s」）
        3. 否则用 path_stem(s)_<hash6(s)>（哈希区分，**绝不覆盖**别人的文件；
           若这个哈希名也被别人占了，继续 _2、_3… 直到找到空位）

    accept：归属判断回调。手写的同主题笔记可能也带 `book: 书名`，
    只看 ident_key 会把它当成自己的旧文件覆盖掉 —— 所以必须由调用方给出
    「这文件确实是我生成的」判据（见 artifact_owned）。

    taken：本批次已分配的文件名集合。一次写多个名字时**必须传**，
    否则两个不同书名清洗成同一个文件名（`AI/ML` 与 `AI,ML`）时会在同一批里打架。
    """
    os.makedirs(base, exist_ok=True)
    taken = taken if taken is not None else set()
    stem = path_stem(s)
    first = safe_join(base, stem, suffix)
    in_taken = os.path.basename(first) in taken
    if not in_taken and not os.path.exists(first):
        taken.add(os.path.basename(first))
        return first
    if not in_taken and os.path.exists(first):
        owned = (accept(first, s) if accept else
                 bool(ident_key) and _read_frontmatter_value(first, ident_key) == display(s))
        if owned:
            taken.add(os.path.basename(first))
            return first
    hashed = f"{stem}_{hash6(s)}"
    for i in range(100):
        cand = safe_join(base, hashed if i == 0 else f"{hashed}_{i + 1}", suffix)
        name = os.path.basename(cand)
        if name in taken:
            continue
        if not os.path.exists(cand) or (accept(cand, s) if accept else True):
            taken.add(name)
            return cand
    raise ValueError(f"目标目录里找不到可用文件名：{s!r}（同名/哈希名都被占用）")


def allocate_paths(base, names, suffix=".md", ident_key=None, accept=None):
    """给一批显示名**一次性**分配完整落盘路径，返回 {显示名: 绝对路径}。

    路径必须由这一次分配直接给出，调用方**不能**再拿主干拼一遍：
    suffix 可能是「 · 灵感总览.md」这种多段后缀，只取回主干再补 `.md`，
    会算出和冲突检查完全不同的文件名 —— 检查的是 A，写下去的是 B，
    用户目录里原本就有的 B 就被覆盖了（且脚本还报成功）。

    这里按名字排序后分配，因此只要**名字集合相同**，无论谁先跑、跑的哪一部分，
    结果都一样（磁盘上属于本工具的文件优先复用，别人的文件一律让开）。
    """
    os.makedirs(base, exist_ok=True)
    out = {}
    taken = set()
    for s in sorted({str(n) for n in names}):
        out[s] = resolve_md_path(base, s, suffix=suffix, ident_key=ident_key,
                                 taken=taken, accept=accept)
    return out


def link_stem(path):
    """双链目标 = 文件名去掉 `.md`（不去别的后缀）。

    调用方直接拿 allocate_paths 给的路径来算，不要再自己拼：
    以前有过一个「按 suffix 剥主干」的版本，把「 · 灵感总览」这种名字的一部分
    也剥掉了，双链指向不存在的文件；这里只保留这一个正确的入口。
    """
    name = os.path.basename(str(path or ""))
    return name[: -len(".md")] if name.endswith(".md") else name


def main():
    """给 Agent / shell 用的小工具，避免手写 YAML 时踩坑。"""
    import argparse
    ap = argparse.ArgumentParser(description="命名与 YAML 编码工具")
    ap.add_argument("cmd", choices=["quote", "stem", "hash", "tag", "list"])
    ap.add_argument("text", nargs="+")
    a = ap.parse_args()
    text = " ".join(a.text)
    if a.cmd == "quote":
        print(scalar(text))
    elif a.cmd == "stem":
        print(path_stem(text))
    elif a.cmd == "hash":
        print(hash6(text))
    elif a.cmd == "tag":
        print(tag_part(text))
    else:
        print(yaml_list(a.text))


if __name__ == "__main__":
    main()
