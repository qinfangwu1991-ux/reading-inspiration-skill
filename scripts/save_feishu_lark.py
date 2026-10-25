#!/usr/bin/env python3
"""用飞书连接器（lark-cli，用户身份）把灵感卡片存进飞书知识库。

这条路的优势（已实测）：
  · 用户只需要在对话里授权一次，**不需要去开放平台建应用、不需要 App ID/Secret**
  · 一步到位：`docs +create --parent-token <wiki节点>` 直接把文档建在知识库里，不用"先建再移"
  · markdown 原样导入，粗体 / 引用 / 标题 / 超链接全部保留（链接可点）
  · 用户身份能读知识空间、能建知识库，这些应用身份都做不到

用法:
    python3 save_feishu_lark.py <卡片.md路径> [--dry-run]

退出码:
    0 成功   3 lark-cli 未安装/未登录（调用方应降级到 save_feishu.py）   1 其他失败
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cfgmod  # noqa: E402


def lark(*args, timeout=180, stdin=None):
    """跑一条 lark-cli 命令，返回解析后的 JSON。

    stdin 不为 None 时把正文从标准输入喂进去（`--content -`）——
    这样不依赖「当前目录可写」，只读安装目录 / 云沙箱里也能用。
    """
    p = subprocess.run(["lark-cli"] + list(args), capture_output=True, text=True,
                       timeout=timeout, input=stdin)
    out = (p.stdout or "").strip()
    try:
        return json.loads(out), p
    except Exception:
        return None, p


def strip_frontmatter(text):
    if text.startswith("---"):
        m = re.match(r"^---\n.*?\n---\n", text, re.S)
        if m:
            return text[m.end():].lstrip()
    return text


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: save_feishu_lark.py <卡片.md路径> [--dry-run]")
    card = os.path.abspath(sys.argv[1])
    dry = "--dry-run" in sys.argv[2:]
    if not os.path.exists(card):
        sys.exit(f"卡片不存在: {card}")

    if not shutil.which("lark-cli"):
        print("没装飞书连接器（lark-cli）", file=sys.stderr)
        sys.exit(3)

    cfg = cfgmod.load()
    fc = cfg.get("feishu") or {}
    parent = fc.get("wiki_parent_token") or ""
    if not parent:
        print("配置里没有 feishu.wiki_parent_token（没接知识库）", file=sys.stderr)
        sys.exit(3)

    text = open(card, encoding="utf-8").read()
    body = strip_frontmatter(text)
    m = re.search(r"^#\s+(.+)$", body, re.M)
    title = m.group(1).strip() if m else os.path.basename(card)[:-3]

    if dry:
        print(f"[dry-run] 会在知识库节点 {parent} 下建文档：{title}")
        return

    # 正文走 stdin（`--content -`）：早先用 @file 必须落在 CWD，
    # 只读安装目录 / 云沙箱里 CWD 不可写就直接崩，现在这条依赖已经去掉
    res, p = lark("docs", "+create", "--as", "user",
                  "--doc-format", "markdown", "--title", title,
                  "--content", "-",
                  "--parent-token", parent,
                  "--format", "json", stdin=body)
    if not res or not res.get("ok"):
        msg = (p.stderr or p.stdout or "").strip()[:400]
        if "auth" in msg.lower() or "未认证" in msg or "login" in msg.lower():
            print(f"飞书连接器未登录：{msg}", file=sys.stderr)
            sys.exit(3)
        sys.exit(f"建文档失败：{msg}")

    doc = (res.get("data") or {}).get("document") or {}
    doc_id = doc.get("document_id") or ""
    doc_url = doc.get("url") or ""
    domain = ""
    m2 = re.match(r"https?://([^/]+)/", doc_url)
    if m2:
        domain = m2.group(1)
    domain = domain or fc.get("domain") or "feishu.cn"

    # docs +create 返回的是 /docx/<id>，知识库里的固定链接要用 /wiki/<node_token>
    node_token, wiki_url = "", ""
    res2, _ = lark("wiki", "+node-get", "--node-token", doc_id,
                   "--as", "user", "--format", "json")
    if res2 and res2.get("ok"):
        node_token = (res2.get("data") or {}).get("node_token") or ""
        if node_token:
            wiki_url = f"https://{domain}/wiki/{node_token}"

    url = wiki_url or doc_url
    print(url)
    if not wiki_url:
        print("（没解析出 wiki 节点链接，已回退 docx 链接；文档仍在知识库里）",
              file=sys.stderr)


if __name__ == "__main__":
    main()
