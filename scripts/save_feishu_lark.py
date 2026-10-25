#!/usr/bin/env python3
"""用飞书连接器（lark-cli，用户身份）把灵感卡片存进飞书知识库。

这条路的优势（已实测）：
  · 用户只需要在对话里授权一次，**不需要去开放平台建应用、不需要 App ID/Secret**
  · 一步到位：`docs +create --parent-token <wiki节点>` 直接把文档建在知识库里，不用"先建再移"
  · markdown 原样导入，粗体 / 引用 / 标题 / 超链接全部保留（链接可点）
  · 用户身份能读知识空间、能建知识库，这些应用身份都做不到

用法:
    python3 save_feishu_lark.py <卡片.md路径> [--dry-run] [--force]
    --force 忽略台账与卡片里的旧链接，强制重建一份

退出码:
    0 成功   3 lark-cli 未安装/未登录（调用方应降级到 save_feishu.py）
    4 上次投递未确认（核对失败/正文读不出），本次不投   1 其他失败
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
import delivery  # noqa: E402


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


def resolve_space(parent):
    """知识空间 ID。`wiki +node-list` 必填它，不传直接 validation 报错。

    取不到就返回空串 —— 调用方把空串当作「没法核对」，不会误判成「远端没有」。
    """
    fc = cfgmod.load().get("feishu") or {}
    sid = str(fc.get("wiki_space_id") or "").strip()
    if sid:
        return sid
    res, _ = lark("wiki", "+node-get", "--node-token", parent,
                  "--as", "user", "--format", "json")
    if res and res.get("ok"):
        data = res.get("data") or {}
        node = data.get("node") or {}
        return str(data.get("space_id") or node.get("space_id") or "")
    return ""


def list_children(space, parent):
    """列出父节点下的直接子节点（用户身份）。返回 (items, ok)。

    ok=False 表示**没查成**（缺参数 / 无权限 / 网络）。这和「查到了、是空列表」
    是两件完全不同的事：前者必须停下来，后者才能判定远端确实没有。
    """
    if not space:
        return [], False
    res, _ = lark("wiki", "+node-list", "--space-id", space,
                  "--parent-node-token", parent,
                  # --page-limit 0 = 不设上限。写死 5 页时子节点多的库会被截断，
                  # 截断后发现「本卡不在列表里」→ 又建一份，和只读第一页是同一个错
                  "--page-all", "--page-limit", "0",
                  "--as", "user", "--format", "json")
    if not res or not res.get("ok"):
        return [], False
    data = res.get("data") or {}
    if data.get("has_more"):
        # 还有下一页却没继续翻：不能当成「已经看全了」
        return [], False
    return (data.get("items") or data.get("nodes") or []), True


def card_marker(card_id):
    """写进远端文档末尾的卡片标识（一行普通引用，人也能看懂）。"""
    return delivery.card_marker(card_id)


def doc_has_marker(token, marker, card_id):
    """回读远端文档，确认它是不是**这张卡**建的。

    返回 True/False/None（None = 读不出来，不能下结论）。
    匹配用完整标识值精确比较（`card` 不能命中 `card2`），不做子串包含。
    """
    if not token:
        return None
    res, _ = lark("docs", "+fetch", "--doc", token,
                  "--doc-format", "markdown", "--as", "user", "--format", "json")
    if not res or not res.get("ok"):
        return None
    content = ((res.get("data") or {}).get("document") or {}).get("content") or ""
    if not content:  # 空正文也可能是读失败，不当证据
        return None
    return delivery.has_card_marker(content, card_id)


def find_existing(space, parent, title, domain, marker, card_id, exclude=()):
    """核对「这张卡」在父节点下是不是已经建成，返回 (status, node, url)。

    status:
        found      确认是本卡（文档里带本卡标识）→ 可安全复用
        absent     查到了，确认没有本卡 → 允许新建
        ambiguous  有同名文档，读成功但标识对不上 → 仍需新建，但要提示可能有重复
        unread     有同名文档，但正文读不出来 → 不许新建，保持待核对
        error      没查成（缺 space_id / 无权限 / 列表失败）→ 不许新建

    exclude：被新代次取代的远端 id（reset / --force 之前的旧文档）。
    这些候选一律跳过 —— 它们标识对得上，但用户已经要求换一份了。

    标题只用来筛候选：同一页的两张摘录卡一级标题可以完全相同，
    只按标题认领会把第二张卡的内容丢进第一张卡的文档里。
    """
    want = (title or "").strip()
    if not want:
        return "absent", "", ""
    items, ok = list_children(space, parent)
    if not ok:
        return "error", "", ""
    skip = {str(x) for x in (exclude or ()) if x}
    same_title = [it for it in items
                  if (it.get("title") or "").strip() == want
                  and (it.get("node_token") or "") not in skip
                  and (it.get("obj_token") or "") not in skip]
    if not same_title:
        return "absent", "", ""
    unread = False
    for it in same_title:
        node = it.get("node_token") or ""
        obj = it.get("obj_token") or ""
        hit = doc_has_marker(obj or node, marker, card_id)
        if hit is True:
            return "found", (node or obj), (f"https://{domain}/wiki/{node}" if node else "")
        if hit is None:
            # 读不出正文 ≠ 不是本卡。此时新建，很可能就是那份重复文档。
            unread = True
    if unread:
        return "unread", "", ""
    # 同名但标识都对不上：可能是旧版（没有标识）留下的，也可能是别的卡
    return "ambiguous", "", ""


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

    domain = fc.get("domain") or "feishu.cn"
    force = "--force" in sys.argv[2:]

    card_id = os.path.basename(card)[:-3]
    marker = card_marker(card_id)
    space = resolve_space(parent)

    if dry:
        print(f"[dry-run] 会在知识库节点 {parent} 下建文档：{title}")
        print(f"[dry-run] 空间 ID：{space or '（取不到，崩溃恢复时会没法核对）'}")
        st0 = delivery.state(card, "feishu", parent)
        print(f"[dry-run] 台账状态：{(st0['rec'] or {}).get('status') or '无记录'}"
              f" · 代次 {st0['epoch']}")
        if st0["wants_redeliver"]:
            print("[dry-run] 已标记为需重投：卡片里的旧链接不会被当作已投递")
        if st0["superseded"]:
            print(f"[dry-run] 已作废的远端文档 {len(st0['superseded'])} 个，核对时跳过")
        return

    # 代次视图：rec 是否属于当前代次、哪些旧文档已经被取代、用户是否要求重投
    st = delivery.state(card, "feishu", parent)
    rec = st["rec"]
    current = rec is not None and int(rec.get("epoch") or 1) == st["epoch"]

    # 1) 台账已建成 → 复用。链接写回可能在上次中断，所以再写一遍（这个动作可重复）
    #    只有**当前代次**的 committed 才算数：--force/reset 开了新代次后，
    #    旧记录的链接必须作废，否则重投完成后崩溃，下次又会返回上一代的旧链接。
    if current and rec.get("status") == "committed" and rec.get("remote_id") and not force:
        url = rec.get("url") or rec["remote_id"]
        if url:
            delivery.record_dest(card, "dest_feishu", url)
        print(url)
        print("（台账显示这张卡已投递过，跳过重复创建；要强制重投加 --force）",
              file=sys.stderr)
        return

    # 2) 没有台账，但卡片里已有旧版留下的 dest_feishu → 接纳为已投递，不重建
    #    例外：用户刚 reset 过（要求重投），旧链接不能当成已投递，否则永远投不出去
    if not rec and not force and not st["wants_redeliver"]:
        old = delivery.read_dest(card, "dest_feishu")
        if old:
            delivery.adopt(card, "feishu", parent, old, title)
            print(old)
            print("（卡片里已有 dest_feishu，按已投递接纳并补建台账，未重复创建）",
                  file=sys.stderr)
            return

    # 3) 上次停在 intent/failed（远端可能已建成）→ 先核对是不是本卡
    #    被取代的旧文档（st["superseded"]）不参与认领，否则重投永远换不掉旧文档
    if current and rec.get("status") in ("intent", "failed") and not force:
        status, node, url = find_existing(space, parent, title, domain, marker,
                                          card_id, exclude=st["superseded"])
        if status == "found":
            delivery.commit(rec["delivery_id"], node, url or node, title)
            delivery.record_dest(card, "dest_feishu", url or node)
            print(url or node)
            print("（上次投递未确认；远端已有本卡文档，沿用而未重建）", file=sys.stderr)
            return
        if status in ("error", "unread"):
            # 查不成 / 读不出 ≠ 不存在。此刻新建，很可能就是那份重复文档。
            why = ("缺 feishu.wiki_space_id" if not space
                   else "候选正文读不出来" if status == "unread"
                   else "查询失败")
            print(f"核对失败：没法确认上次投递是否已建成（{why}）。"
                  f"为避免重复创建，本次未投递。\n"
                  f"补救：确认配置里有 feishu.wiki_space_id，或到知识库看一眼"
                  f"「{title}」是否已存在；确认没有就加 --force 重投。",
                  file=sys.stderr)
            sys.exit(4)
        if status == "ambiguous":
            print(f"⚠️ 父节点下已有同名文档「{title}」，但它不是这张卡建的。"
                  f"继续新建；如果确认那份就是这个卡片的历史版本，"
                  f"先删掉它或加 --force 后手工整理。", file=sys.stderr)

    # 4) 登记 intent 后再建，崩在中途也能被下次核对
    rec = delivery.begin(card, "feishu", parent, title, force=force)

    # 卡片标识写进正文：下次核对时不靠标题猜，直接看文档里有没有本卡 ID
    body = body.rstrip() + f"\n\n---\n\n{marker}\n"

    # 正文走 stdin（`--content -`）：早先用 @file 必须落在 CWD，
    # 只读安装目录 / 云沙箱里 CWD 不可写就直接崩，现在这条依赖已经去掉
    res, p = lark("docs", "+create", "--as", "user",
                  "--doc-format", "markdown", "--title", title,
                  "--content", "-",
                  "--parent-token", parent,
                  "--format", "json", stdin=body)
    if not res or not res.get("ok"):
        msg = (p.stderr or p.stdout or "").strip()[:400]
        delivery.fail(rec["delivery_id"], msg)
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
    delivery.commit(rec["delivery_id"], node_token or doc_id, url, title)
    if url:
        delivery.record_dest(card, "dest_feishu", url)
    print(url)
    if not wiki_url:
        print("（没解析出 wiki 节点链接，已回退 docx 链接；文档仍在知识库里）",
              file=sys.stderr)


if __name__ == "__main__":
    main()
