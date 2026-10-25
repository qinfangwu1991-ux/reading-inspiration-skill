#!/usr/bin/env python3
"""把「一个链接」变成干净正文，供后续切段、选段、记想法。

拍照之外的第二个入口：用户复制一个链接（网页文章 / 飞书文档 / 乐享页面 / 本地文件），
本脚本负责取到**正文原文**并存进 library/sources/，切段仍由 Agent 读正文后做。

用法:
    python3 fetch_source.py <url 或 本地路径>
    python3 fetch_source.py <url> --title "标题" --source-name "公众号名"
    python3 fetch_source.py --from-text body.md --url-for-text <url> --title "标题"
    python3 fetch_source.py <url> --json           # 只输出摘要（机器读）
    python3 fetch_source.py <url> --no-save       # 不落 sources 目录，只打正文

退出码:
    0  成功
    2  参数/文件问题
    3  抓取失败（网络、HTTP 错误）
    4  需要 Agent 用 MCP 取正文（乐享页面）
    5  拿不到正文（需要登录 / 依赖 JS 渲染 / 正文太短），让用户直接粘贴

支持:
    http(s) 网页（含微信公众号、博客、新闻）
    飞书文档 / 知识库页面（走 lark-cli，需已登录，零凭证）
    本地 .md / .txt / .html / .docx / .pdf
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env  # noqa: E402
import naming  # noqa: E402
LIBRARY = env.library_dir()
SOURCES = os.path.join(LIBRARY, "sources")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
# 有些站（小红书）桌面端直接跳登录页、只有手机 UA 才给正文，抓不到时要换这条再试一次
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

# 这些标签里的文字不是正文
SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "head", "nav",
             "footer", "aside", "form", "button", "select", "textarea",
             "template", "video", "audio", "canvas", "figure-ignore"}
BLOCK_TAGS = {"p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5",
              "h6", "tr", "section", "article", "main", "td", "th", "pre",
              "ul", "ol", "table", "dd", "dt", "figcaption", "header"}
# 可以作为「正文容器」的标签
CONTAINER_TAGS = {"div", "article", "section", "main", "td"}

NOISE_PATTERNS = re.compile(
    r"^(首页|登录|注册|订阅|分享到|分享|收藏|点赞|在看|评论|上一篇|下一篇|返回顶部|"
    r"阅读原文|版权所有|copyright|all rights reserved|免责声明|扫描二维码|"
    r"长按识别|点击上方|点击蓝字|广告|举报|更多精彩|相关阅读|推荐阅读)[\s:：·|]*$",
    re.I)
# 「作者：」「日期：」这类元信息行，去掉前缀后只剩很短一截，不是正文
META_PREFIX = re.compile(
    r"^(上一篇|下一篇|分类|标签|话题|作者|撰文|文|编辑|责任编辑|来源|日期|"
    r"发布时间|更新时间|阅读量|浏览量|字数|评论数|归档|目录|栏目)\s*[:：]\s*",
    re.I)
BREADCRUMB = re.compile(r"[»›]|‣|返回列表|返回首页|网站地图")
# 移动端页面（小红书这类）夹带的界面文字：发布日期、「说点什么…」、话题标签串
# ⚠️ 不要在这里丢「纯数字」——公众号文章的小节号就是 01 / 02，会被误删（踩过）；
#    点赞数这类尾部数字改用 tail_strip_numbers() 只在结尾剥。
UI_NOISE = re.compile(
    r"^(\d{1,2}-\d{1,2}|"
    r"点击右上角.*|说点什么.*|在?App内打开|打开.{0,10}(App|APP|客户端).*|"
    r"[#＃][^#＃\s]+(?:[ \t]*[#＃][^#＃\s]+){2,})$")

# 页头「媒体行」：账号名下的时间戳、「XX官方账号」、关注按钮、记者署名。
# 新闻站（百度/头条/网易这类）几乎都有，出现在标题之后、正文之前，会顶歪段号。
MASTHEAD = re.compile(
    r"(^\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}.*$|"          # 2026-10-22 17:11
    r"^\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}.*$|"
    r"官方账号|官方微博|^关注$|^已关注$|^订阅$|^原创$|"
    r"^(本文)?(来源|编辑|责编|责任编辑|记者|撰文|作者|审核|校对|监制)\s*[:：])")

# 页脚杂项：分享按钮、举报、ICP 备案、App 推广 —— 一律不是正文
FOOTER = re.compile(
    r"^(【(来源|编辑|责编|记者)[:：].*】?$|"
    r"^举报(\s*/\s*反馈)?$|^(微信好友|新浪微博|QQ好友|QQ空间|复制链接|"
    r"扫码分享.*|分享到.*|手机看.*)$|"
    r"^(百度APP扫一扫|手机看更方便|设为首页|关于百度|About Baidu|"
    r"使用百度前必读|帮助中心|顶部|返回顶部)$|"
    r"^(©|Copyright|京ICP|沪ICP|粤ICP|京公网安备|浙ICP).*$)")

# 图片说明行：「阿宇将农村破屋打造成实验室。图/恩施州融媒体中心杜瑞芳」。
# 这是配图注脚不是正文，留着会把段号顶歪，用户照段号挑就会挑错段。
FIGCAP = re.compile(r"^(?:.{0,60}[。！？]\s*)?图\s*[:：/／]\s*\S.{0,60}$")

# 反爬验证页：百度是 wappass.baidu.com 的滑块，其余站也各有各的验证页。
# 认出来是为了给出**准确**的下一步，而不是笼统说「可能登录/JS 渲染」。
BLOCK_HOSTS = ("wappass.baidu.com", "captcha", "antispider", "verify.", "/verify")
BLOCK_TEXT = ("安全验证", "请完成验证", "请输入验证码", "拖动滑块", "滑动验证",
              "访问验证", "环境异常", "人机验证")


def block_reason(final_url, page):
    """返回「被验证页拦住」的原因；没有则返回空串。"""
    h = host_of(final_url)
    if any(k in h for k in BLOCK_HOSTS):
        return "跳到验证页（%s）" % h
    if page and len(page) < 30000:
        for t in BLOCK_TEXT:
            if t in page:
                return "页面是%s" % t
    return ""


def norm_ws(s):
    """压缩空白；顺手去掉中文之间的多余空格（HTML 源里换行缩进会变成空格）。"""
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\n\f\v\u00a0\u3000]+", " ", s).strip()
    cjk = r"\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef"
    s = re.sub(rf"(?<=[{cjk}]) (?=[{cjk}])", "", s)
    return s


class Extractor(HTMLParser):
    """把 HTML 拆成「块序列」，并用「文本量最大的容器」当正文。"""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.skip_depth = 0
        self.buf = []
        self.blocks = []          # [(文本, 索引)]
        self.stack = []           # [(tag, start_idx, idclass)]
        self.containers = []      # [(len, count, start, end, idclass)]
        self.title = ""
        self.h1 = ""
        self._in_title = False
        self._h1_done = False
        self._og_title = ""
        self._og_desc = ""

    # ---- 标签处理 ----
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        d = dict((k.lower(), v or "") for k, v in attrs)
        if tag == "meta":
            prop = (d.get("property") or d.get("name") or "").lower()
            if prop in ("og:title", "twitter:title") and d.get("content"):
                self._og_title = self._og_title or norm_ws(d["content"])
            if prop in ("og:description", "twitter:description", "description"):
                c = (d.get("content") or "").strip()
                # 同一页可能有 og:description 与 description 两份，留长的
                if len(c) > len(self._og_desc):
                    self._og_desc = c
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "br":
            self._flush()
            return
        if tag in BLOCK_TAGS:
            self._flush()
            if tag in CONTAINER_TAGS:
                ic = (d.get("id", "") + " " + d.get("class", "")).strip()
                self.stack.append([tag, len(self.blocks), ic])
        if tag == "h1" and not self._h1_done:
            self._h1_start = len(self.blocks)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
            return
        if tag in SKIP_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if tag in BLOCK_TAGS:
            self._flush()
            if tag in CONTAINER_TAGS and self.stack:
                for i in range(len(self.stack) - 1, -1, -1):
                    if self.stack[i][0] == tag:
                        _, start, ic = self.stack.pop(i)
                        seg = "".join(b for b, _ in self.blocks[start:])
                        if seg:
                            self.containers.append((len(seg), len(self.blocks) - start,
                                                    start, len(self.blocks), ic))
                        break
            if tag == "h1" and not self._h1_done:
                start = getattr(self, "_h1_start", 0)
                t = "".join(b for b, _ in self.blocks[start:]).strip()
                if t:
                    self.h1 = norm_ws(t)
                    self._h1_done = True

    def handle_data(self, data):
        # ⚠️ title 判断必须在 skip_depth 之前：head 在 SKIP_TAGS 里，
        # 先判 skip_depth 会导致 <title> 永远读不到，标题退化成「未命名文档」
        if self._in_title:
            self.title += data
            return
        if self.skip_depth:
            return
        self.buf.append(data)

    def handle_entityref(self, name):
        if not self.skip_depth:
            self.buf.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip_depth:
            self.buf.append(f"&#{name};")

    def _flush(self):
        if not self.buf:
            return
        t = norm_ws("".join(self.buf))
        self.buf = []
        if len(t) < 2:
            return
        # 相邻完全重复（列表/响应式重复渲染）只留一份
        if self.blocks and self.blocks[-1][0] == t:
            return
        self.blocks.append((t, len(self.blocks)))

    def finish(self):
        self._flush()
        while self.stack:
            _, start, ic = self.stack.pop()
            seg = "".join(b for b, _ in self.blocks[start:])
            if seg:
                self.containers.append((len(seg), len(self.blocks) - start,
                                        start, len(self.blocks), ic))
        return self

    def best_blocks(self):
        """选正文：文本量最大的容器（占全页足够比例时），否则用全部块。

        返回 (blocks, used_container)：used_container=False 表示没找到明确正文容器，
        这时页头噪声只能靠 clean_blocks 的「页头裁剪」兜，调用方按此决定要不要裁。
        """
        total = sum(len(b) for b, _ in self.blocks)
        if not self.blocks:
            return [], False
        best = max(self.containers, key=lambda c: c[0], default=None)
        if best and total and best[0] >= max(200, total * 0.35):
            _, _, start, end, _ = best
            picked = [b for b, _ in self.blocks[start:end]]
            if picked:
                return picked, True
        return [b for b, _ in self.blocks], False


def _looks_like_nav(t):
    """短、且没有任何标点 → 更像导航/分类/栏目名，不像正文句子。

    真正文哪怕很短通常也带标点（「这是一个关于选择的故事。」），
    所以这条规则能裁掉页头噪声，又不会把短的开头段落吃掉。
    """
    if len(t) >= 20:
        return False
    return not re.search(r"[，。！？；：、,.;:!?…—\-】）)]", t)


def clean_blocks(blocks, title="", trim_head=True, web=True):
    """去掉导航、面包屑、元信息行；trim_head 时再切掉正文之前的一小段页头。

    ⚠️ trim_head 只对「找不到明确正文容器的网页」使用。结构化文档（飞书/docx）
    和已有容器的页面都**不能**裁 —— 它们开头就有真内容，裁掉是静默丢数据。

    web=True 时额外做两件「网页专有」的清洗（结构化文档传 False）：
      - 页头媒体行（时间戳 / XX官方账号 / 关注 / 记者署名）：不裁会顶歪段号
      - 页脚杂项（分享按钮 / 举报 / ICP 备案 / App 推广）：不裁会混进正文尾巴
    """
    out = []
    for b in blocks:
        t = b.strip()
        if not t:
            continue
        if title and t == title:          # 正文里的 h1 与标题重复
            continue
        if NOISE_PATTERNS.match(t):
            continue
        if BREADCRUMB.search(t) and len(t) < 80:
            continue
        if trim_head and UI_NOISE.match(t):   # 只在网页路径启用，别误伤结构化文档
            continue
        if web and FIGCAP.match(t):           # 配图注脚，不是正文
            continue
        m = META_PREFIX.match(t)
        if m:
            rest = t[m.end():].strip()
            if len(rest) < 20:            # 「上一篇：xxx」这种残留半截，丢掉
                continue
            t = rest
        out.append(t)

    if web:
        # 页头：账号名 / 时间戳 / 关注 / 署名，最多剥 5 行、累计 120 字以内，
        # 保证不会连着把真正文开头一起削掉
        i = 0
        while (i < len(out) and i < 5
               and (MASTHEAD.search(out[i]) or FOOTER.match(out[i])
                    or _looks_like_nav(out[i]))
               and sum(len(x) for x in out[:i + 1]) < 120):
            i += 1
        if i and len(out) > i:
            out = out[i:]

        # 页脚：从尾巴往回剥，遇到第一句真正文就停
        while out and (FOOTER.match(out[-1]) or UI_NOISE.match(out[-1])
                       or _looks_like_nav(out[-1])
                       or re.fullmatch(r"\d{1,6}", out[-1])):
            out.pop()

    if trim_head:
        # 结尾的点赞数/阅读数（纯数字）只在尾部剥，别碰正文中间的小节号
        n = 0
        while out and n < 4 and re.fullmatch(r"\d{1,6}", out[-1]):
            out.pop()
            n += 1
        i = 0
        while i < len(out) and i < 12 and _looks_like_nav(out[i]):
            i += 1
        if i and sum(len(x) for x in out[:i]) < 200:
            out = out[i:]
    return out


def blocks_to_text(blocks, title="", trim_head=True, web=True):
    return "\n\n".join(clean_blocks(blocks, title, trim_head=trim_head, web=web))


def fetch_html(url, timeout=25, ua=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": ua or UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            charset = r.headers.get_content_charset()
            final = r.geturl()
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code} {e.reason}")
    except Exception as e:
        raise FetchError(f"{type(e).__name__}: {e}")
    if not charset:
        m = re.search(rb'charset=["\']?\s*([\w-]+)', raw[:4096], re.I)
        charset = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        text = raw.decode(charset, "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    return text, final


class FetchError(Exception):
    pass


def html_to_article(page, url="", structured=False):
    """把 HTML 转成正文。

    structured=True 用于飞书 / docx 这类结构化文档：
      - 不用「最大容器」，直接用全部块（它们常有多个并列区块，取最大会丢一半）
      - 不裁页头（文档开头就是真内容）
    structured=False 用于网页：可以选容器，也必须裁掉页头（账号名/导航/分类）。
    """
    ex = Extractor()
    ex.feed(page)
    ex.finish()
    blocks = [b for b, _ in ex.blocks] if structured else ex.best_blocks()[0]
    title = norm_ws(ex.h1) or norm_ws(ex.title) or ex._og_title
    body = blocks_to_text(blocks, title, trim_head=not structured, web=not structured)
    return title, body, len([b for b in body.split("\n\n") if b.strip()])


# 序号/分行标记：小红书这类的笔记正文常被压成一行，靠这些标记还原段落
_SPLIT_MARKERS = re.compile(
    r"\s*(?=[0-9]\ufe0f?\u20e3"          # 1️⃣ 2️⃣ 这类 keycap 数字
    r"|[①②③④⑤⑥⑦⑧⑨⑩])")               # 圈号
_TAIL_TAGS = re.compile(r"(?:\s+#[^\s#][^\s#]{0,30}){2,}\s*$")


def _split_meta_text(t):
    """把 meta 摘要还原成多段。

    小红书 / 微博这类页面的正文只出现在 og:description 里，换行全部丢了：
    原本的分行变成空格，小节之间夹一个孤零零的「 . 」。这里把它们还原回来。
    只做「在哪断开」，不改任何字。
    """
    t = re.sub(r"[ \t]+", " ", t).strip()
    # 「 . 」单独夹在中文之间 = 原页面的一次换行
    t = re.sub(r"\s+(?<![A-Za-z0-9])\.\s+(?![A-Za-z0-9])", "\n\n", t)
    t = _SPLIT_MARKERS.sub("\n\n", t)
    # 结尾那串话题标签单独成段，别糊在最后一句后面
    m = _TAIL_TAGS.search(t)
    if m:
        tags = norm_ws(m.group(0))
        t = t[:m.start()].rstrip() + "\n\n" + tags
    paras = [norm_ws(p) for p in t.split("\n\n")]
    return "\n\n".join([p for p in paras if p])


def meta_article(page):
    """兜底：正文抽不出来时，用页面的 og:description / description 当正文。

    适用：小红书、部分公众号转载站、靠 JS 渲染正文的站点 —— 它们把整段正文
    塞进 meta 描述里。代价是**可能被平台截断**，所以调用方要如实告知用户。
    """
    ex = Extractor()
    ex.feed(page)
    ex.finish()
    raw = (ex._og_desc or "").strip()
    if not raw:
        return "", "", 0
    body = _split_meta_text(html.unescape(raw))
    return norm_ws(ex._og_title) or norm_ws(ex.title), body, \
        len([b for b in body.split("\n\n") if b.strip()])


SITE_SUFFIX = re.compile(r"\s*[-–—_|｜]\s*(小红书|知乎|简书|CSDN|掘金|微博|哔哩哔哩|B站)\s*$")


def clean_title(t):
    """去掉「标题 - 小红书」这类站点后缀。"""
    return SITE_SUFFIX.sub("", norm_ws(t)).strip()



def feishu_tokens(url):
    """从飞书链接里取出节点 token。"""
    m = re.search(r"/(wiki|docx|docs|doc|sheets|base)/([A-Za-z0-9]+)", url)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def fetch_feishu(url):
    cli = shutil.which("lark-cli")
    if not cli:
        raise FetchError("没找到 lark-cli（飞书连接器未安装），无法读取飞书文档")
    kind, tok = feishu_tokens(url)
    if not tok:
        raise FetchError("飞书链接里没解析出文档 token")
    r = subprocess.run([cli, "docs", "+fetch", "--doc", tok, "--as", "user"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise FetchError(f"lark-cli docs +fetch 失败：{(r.stderr or r.stdout).strip()[:300]}")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise FetchError("lark-cli 返回的不是 JSON")
    doc = (data.get("data") or {}).get("document") or {}
    content = doc.get("content") or ""
    title = norm_ws(doc.get("title") or "")
    if not content:
        raise FetchError("飞书文档内容为空（可能需要文档权限）")
    # 飞书返回的是 HTML 风格正文（<b>/<blockquote>/<h2>…），复用同一套抽取
    t, body, n = html_to_article(content, url, structured=True)
    return (title or t), body, n


def docx_text(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    paras = re.findall(r"<w:p\b.*?</w:p>", xml, re.S)
    blocks = []
    for p in paras:
        # ⚠️ 必须 (?=[ >])：否则 <w:textAlignment> / <w:tbl> 都会被当成 <w:t>，
        # 把一大段 XML 属性当正文吐出来（这个坑踩过）
        txt = "".join(re.findall(r"<w:t(?=[ >])[^>]*>(.*?)</w:t>", p, re.S))
        txt = norm_ws(html.unescape(txt))
        if txt:
            blocks.append(txt)
    body = blocks_to_text(blocks, trim_head=False)
    return body, len([b for b in body.split("\n\n") if b.strip()])


def pdf_text(path):
    exe = shutil.which("pdftotext")
    if not exe:
        raise FetchError("本机没有 pdftotext，PDF 请改用 workbuddy-pdf 读取后走 --from-text")
    r = subprocess.run([exe, "-layout", "-enc", "UTF-8", path, "-"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise FetchError(f"pdftotext 失败：{r.stderr.strip()[:200]}")
    blocks = [norm_ws(b) for b in re.split(r"\n\s*\n", r.stdout)]
    body = blocks_to_text([b for b in blocks if b], trim_head=False)
    return body, len([b for b in body.split("\n\n") if b.strip()])


def host_of(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def source_name_of(url, title):
    """来源名（卡片里的 book 字段，地图按它分组）。"""
    h = host_of(url)
    if "mp.weixin.qq.com" in h:
        return "微信公众号"
    if "feishu.cn" in h or "larksuite.com" in h:
        return "飞书文档"
    if "lexiangla.com" in h:
        return "乐享"
    if "zhihu.com" in h:
        return "知乎"
    if "juejin.cn" in h:
        return "掘金"
    if "csdn.net" in h:
        return "CSDN"
    if "notion.site" in h or "notion.so" in h:
        return "Notion"
    if "xiaohongshu.com" in h or "xhslink" in h:
        return "小红书"
    if "baidu.com" in h:
        return "百度新闻"
    if "toutiao.com" in h:
        return "今日头条"
    if "news.qq.com" in h or "new.qq.com" in h:
        return "腾讯新闻"
    if "163.com" in h:
        return "网易新闻"
    if "thepaper.cn" in h:
        return "澎湃新闻"
    if "weibo.com" in h or "weibo.cn" in h:
        return "微博"
    if h:
        parts = h.split(".")
        return parts[-2] if len(parts) >= 2 else h
    return "链接文档"


def _flat(s):
    """空白归一，用于内容哈希：换行/空格差异不算「不同内容」。"""
    return re.sub(r"\s+", "", s or "")


def source_identity(url, body):
    """返回 (source_id, url_hash, content_hash)。

    source_id = 链接哈希 + 内容哈希：
      · 同一篇重复抓 = 同一个 id → 不重复写
      · 同一链接内容变了 = 内容哈希变 → 另存一份，旧版本保留
      · 用户粘贴/本地文件没有 URL → 只按内容哈希去重
    """
    content_hash = naming.hash8(_flat(body))
    url_hash = naming.hash8(url) if url else ""
    sid = f"{url_hash}-{content_hash}" if url_hash else content_hash
    return sid, url_hash, content_hash


def existing_sources():
    """扫已有存档，返回 {source_id: 路径}，供去重与版本号使用。"""
    out = {}
    if not os.path.isdir(SOURCES):
        return out
    for fn in sorted(os.listdir(SOURCES)):
        if not fn.endswith(".md"):
            continue
        p = os.path.join(SOURCES, fn)
        try:
            with open(p, encoding="utf-8") as f:
                head = f.read(2000)
        except OSError:
            continue
        m = re.match(r"^---\n(.*?)\n---", head, re.S)
        if not m:
            continue
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k.strip() == "source_id":
                sid = naming.unquote(v).strip()
                if sid:
                    out[sid] = p
                break
    return out


def save_source(title, source_name, url, body, n_paras, kind, content_status="full"):
    """把抓到的原文**完整**落盘，返回 (路径, 完整字数, 状态, source_id)。

    状态：new 新写入 / duplicate 同一内容已存在（未重复写）
    文件名同时带**链接哈希与版本/内容哈希**：
      · 同一天、同标题、不同链接 → 链接哈希不同，不覆盖
      · 同一天、同链接、内容更新 → 版本号递增，旧版本保留（不是覆盖）
      · 无链接（粘贴/本地文件）→ 用内容哈希，天然不会撞
    """
    os.makedirs(SOURCES, exist_ok=True)
    sid, url_hash, content_hash = source_identity(url, body)
    chars = len(_flat(body))
    known = existing_sources()
    if sid in known:
        return known[sid], chars, "duplicate", sid

    version = 1
    if url_hash:
        # source_id 形如 <url_hash>-<content_hash>，数一下这篇文档已有几个版本
        version = 1 + len([k for k in known if k.startswith(url_hash + "-")])

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    head = f"{datetime.now():%Y-%m-%d}_{naming.path_stem(title or source_name)}"
    if url_hash:
        # 内容哈希进文件名：同一链接的每次更新都是独立文件，旧版不会被冲掉
        path = naming.safe_join(SOURCES, f"{head}_{url_hash}-v{version}")
        # 有上限：万一路径计算退化成同一个值，宁可加内容哈希收尾，也不能死循环
        for _ in range(200):
            if not os.path.exists(path):
                break
            version += 1
            path = naming.safe_join(SOURCES, f"{head}_{url_hash}-v{version}")
        else:
            path = naming.safe_join(SOURCES, f"{head}_{url_hash}-{content_hash[:8]}")
    else:
        path = naming.safe_join(SOURCES, f"{head}_{content_hash[:8]}")
    fm = [
        "---",
        f"title: {naming.scalar(title)}",
        f"source_name: {naming.scalar(source_name)}",
        f"url: {naming.scalar(url)}",
        f"kind: {kind}",
        f"host: {host_of(url)}",
        f"fetched_at: {stamp}",
        f"chars: {chars}",
        f"paras: {n_paras}",
        f"source_id: {sid}",
        f"url_hash: {url_hash}",
        f"content_hash: {content_hash}",
        # full = 完整正文已存；summary = 只拿到页面摘要，正文可能在平台侧被截断
        f"content_status: {content_status}",
        f"version: {version}",
        "---",
        "",
        f"# {title}",
        "",
        f"> 来源：{source_name} · {url}" if url else f"> 来源：{source_name}",
        f"> 抓取于 {stamp} · 约 {chars} 字 · {n_paras} 个自然段",
        "",
        body,
        "",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(fm))
    return path, chars, "new", sid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", help="链接（http/https）或本地文件路径")
    ap.add_argument("--title", help="标题（覆盖自动识别）")
    ap.add_argument("--source-name", help="来源名（卡片 book 字段，按它分组）")
    ap.add_argument("--chapter", help="章节/分区（可选）")
    ap.add_argument("--from-text", help="不抓取，直接读这个文件当正文（MCP 取到的内容走这里）")
    ap.add_argument("--from-stdin", action="store_true",
                    help="从标准输入读正文（用户直接粘贴了一大段文字时用）")
    ap.add_argument("--url-for-text", default="", help="配合 --from-text 记录原始链接")
    ap.add_argument("--max-chars", type=int, default=20000,
                    help="终端最多显示多少字（默认 20000）；存档始终是完整原文")
    ap.add_argument("--no-save", action="store_true", help="不写 library/sources，只打正文")
    ap.add_argument("--allow-short", action="store_true", help="正文很短也接受（短文/短文档）")
    ap.add_argument("--json", action="store_true", help="只输出摘要 JSON")
    a = ap.parse_args()

    url = a.source or a.url_for_text or ""
    kind = "link"
    body = ""
    n_paras = 0
    title = (a.title or "").strip()
    meta_used = False

    try:
        # 1) 直接给文本
        if a.from_stdin:
            body = re.sub(r"\n{3,}", "\n\n", sys.stdin.read()).strip()
            n_paras = len([b for b in body.split("\n\n") if b.strip()])
            kind = "text"

        elif a.from_text:
            with open(a.from_text, encoding="utf-8") as f:
                raw = f.read()
            body = re.sub(r"\n{3,}", "\n\n", raw).strip()
            n_paras = len([b for b in body.split("\n\n") if b.strip()])
            kind = "text"

        # 2) 本地文件
        elif a.source and not re.match(r"^https?://", a.source):
            p = os.path.abspath(os.path.expanduser(a.source))
            if not os.path.exists(p):
                print(f"文件不存在: {p}", file=sys.stderr)
                sys.exit(2)
            ext = os.path.splitext(p)[1].lower()
            kind = "file"
            url = ""
            if ext in (".md", ".markdown", ".txt"):
                with open(p, encoding="utf-8", errors="replace") as f:
                    body = re.sub(r"\n{3,}", "\n\n", f.read()).strip()
                n_paras = len([b for b in body.split("\n\n") if b.strip()])
            elif ext in (".html", ".htm"):
                with open(p, encoding="utf-8", errors="replace") as f:
                    title, body, n_paras = html_to_article(f.read())
            elif ext == ".docx":
                body, n_paras = docx_text(p)
            elif ext == ".pdf":
                body, n_paras = pdf_text(p)
            else:
                print(f"不支持的文件类型: {ext}", file=sys.stderr)
                sys.exit(2)
            if not title:
                title = os.path.splitext(os.path.basename(p))[0]

        # 3) 链接
        elif a.source:
            h = host_of(a.source)
            blocked = ""
            if "lexiangla.com" in h:
                print("乐享页面请用 MCP 工具 block_fetch_page 取正文，"
                      "再用 --from-text 交回本脚本。", file=sys.stderr)
                sys.exit(4)
            if "feishu.cn" in h or "larksuite.com" in h:
                kind = "feishu"
                t, body, n_paras = fetch_feishu(a.source)
                title = title or t
            else:
                page, final = fetch_html(a.source)
                url = final or a.source
                blocked = block_reason(url, page)
                t, body, n_paras = html_to_article(page, url)
                title = title or t
                if len(re.sub(r"\s", "", body)) < 80:
                    # ① 换手机 UA 重抓：小红书这类桌面端直接跳登录，手机端才给正文
                    mpage = ""
                    try:
                        mpage, mfinal = fetch_html(a.source, ua=MOBILE_UA)
                        # 桌面端常被踢去 /login?redirectPath=…，手机端才是真的正文页
                        if mfinal and "/login" not in mfinal:
                            url = mfinal
                    except FetchError:
                        pass
                    if mpage:
                        mt, mb, mn = html_to_article(mpage, url)
                        if len(re.sub(r"\s", "", mb)) >= 80:
                            body, n_paras = mb, mn
                            title = title or mt
                    # ② 还不行就看页面摘要（有些站正文只塞在 og:description 里）
                    if len(re.sub(r"\s", "", body)) < 80:
                        for src_page in (mpage, page):
                            if not src_page:
                                continue
                            mt, mb, mn = meta_article(src_page)
                            if len(re.sub(r"\s", "", mb)) >= 80:
                                body, n_paras = mb, mn
                                title = title or mt
                                meta_used = True
                                break
                title = clean_title(title)
                # 正文开头常常又把标题重复一遍（h1 与 og:title 文本不一致时会漏删）
                if title and body.startswith(title):
                    body = body[len(title):].lstrip("\n")
                    n_paras = len([b for b in body.split("\n\n") if b.strip()])
        else:
            print("需要一个链接或文件路径", file=sys.stderr)
            sys.exit(2)

    except FetchError as e:
        msg = str(e)
        # 需要登录 / JS 渲染：让用户直接粘正文
        hits = ("HTTP 403", "HTTP 401", "HTTP 404", "登录", "login",
                "Timed out", "timeout", "URLError", "SSL")
        if any(k.lower() in msg.lower() for k in hits):
            print(f"抓不到正文：{msg}", file=sys.stderr)
            print("→ 这个链接可能需要登录、或者正文靠 JS 渲染。"
                  "直接让用户把正文复制粘贴过来（走 --from-text）最省事。", file=sys.stderr)
            sys.exit(5)
        print(f"抓取失败：{msg}", file=sys.stderr)
        sys.exit(3)

    body = (body or "").strip()
    chars = len(_flat(body))
    # 只有「从网上抓来的」才可能是登录页；用户自己粘的、本地文件不做这个判断
    if kind in ("link", "feishu") and chars < (40 if a.allow_short else 80):
        why = blocked or ""
        if why:
            print(f"正文太短（{chars} 字）：{why}。", file=sys.stderr)
            print("→ 这是反爬验证，重试也一样；让用户自己打开链接、把正文复制粘贴过来。",
                  file=sys.stderr)
        else:
            print(f"正文太短（{chars} 字），大概率是登录页或需要 JS 渲染。", file=sys.stderr)
            print("→ 让用户直接把正文粘过来，别硬解析。", file=sys.stderr)
        sys.exit(5)
    # 打印截断 ≠ 存档截断：存档永远写完整原文，--max-chars 只限制终端里显示多少
    printed = body
    print_cut = False
    if a.max_chars and len(body) > a.max_chars:
        printed = body[:a.max_chars].rstrip()
        print_cut = True

    title = title or "未命名文档"
    source_name = a.source_name or source_name_of(url, title)
    if a.chapter:
        source_name = source_name  # chapter 只影响卡片，不影响来源名

    saved, archived_chars, save_state, source_id = "", 0, "skipped", ""
    if not a.no_save:
        saved, archived_chars, save_state, source_id = save_source(
            title, source_name, url, body, n_paras, kind,
            "summary" if meta_used else "full")

    if a.json:
        print(json.dumps({
            "title": title, "source_name": source_name, "url": url,
            "kind": kind, "chars": chars, "paras": n_paras, "saved": saved,
            "source_id": source_id, "save_state": save_state,
            "archived_chars": archived_chars, "printed_chars": len(printed),
            "content_status": "summary" if meta_used else "full",
        }, ensure_ascii=False, indent=2))
        return

    print(f"标题：{title}")
    print(f"来源：{source_name}" + (f"（{host_of(url)}）" if url else ""))
    if url:
        print(f"链接：{url}")
    if meta_used:
        print("⚠️ 正文取自页面摘要（og:description）—— 这类站点要登录，正文只出现在摘要里，"
              "存档已标记 content_status: summary")
    print(f"正文：约 {chars} 字 / {n_paras} 个自然段")
    if print_cut:
        print(f"⚠️ 终端只显示前 {a.max_chars} 字（完整 {chars} 字已存档，切段请按 1B.1 小节清单来）")
    if saved:
        rel = os.path.relpath(saved, os.path.dirname(LIBRARY))
        if save_state == "duplicate":
            print(f"已存过（同一内容，未重复写）：{rel}")
        else:
            print(f"已存（完整原文）：{rel}")
        print("卡片 frontmatter（直接粘，只改 paras / date）：")
        print(f"  book: {naming.scalar(source_name)}")
        print(f"  title: {naming.scalar(title)}")
        if url:
            print(f"  url: {naming.scalar(url)}")
        print(f"  kind: {'link' if kind in ('link', 'feishu') else 'text'}")
        print(f"  source: {'链接文档' if kind in ('link', 'feishu') else '手动粘贴'}")
        if kind in ("link", "feishu", "file"):
            print(f"  source_file: {rel.replace('library/', '')}")
    print("=" * 50)
    print(printed)
    if print_cut:
        print(f"\n…（终端显示到此为止，共 {chars} 字；完整原文见存档）")


if __name__ == "__main__":
    main()
