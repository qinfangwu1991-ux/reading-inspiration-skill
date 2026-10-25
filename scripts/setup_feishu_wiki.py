#!/usr/bin/env python3
"""飞书知识库接入工具。

用途：把一个飞书知识空间接成读书灵感的归档位置。

注意：飞书开放平台规定「创建知识空间」接口只接受 user_access_token，
应用身份（tenant_access_token）无法创建知识库，所以库本身需要用户在飞书里手动建一次。
建好后本脚本负责把应用侧的一切接好：建索引页、迁移已有文档、写配置。

用法：
    python3 setup_feishu_wiki.py list
        列出当前应用可见的知识空间（应用不是任何空间的成员时为空）

    python3 setup_feishu_wiki.py init --space <space_id> [--title 读书灵感]
        在空间根目录下创建「读书灵感」索引页，并把 space_id / node_token 写入配置

    python3 setup_feishu_wiki.py migrate
        把本地卡片里已在云空间的飞书文档，逐篇移进上面那个索引页下面，
        并把卡片 frontmatter 的 dest_feishu 改写成知识库链接

    python3 setup_feishu_wiki.py move --node <node_token> [--parent <父节点>]
        把知识库里某个节点（连同其子节点）挪到根目录，或挪到指定父节点下。
        不传 --parent 就是挪到知识库根目录。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env  # noqa: E402
LIBRARY = env.library_dir()
API = "https://open.feishu.cn/open-apis"


def cfg_raw():
    out = subprocess.run([env.PY, os.path.join(HERE, "config.py"), "raw"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def save_cfg(cfg):
    subprocess.run([env.PY, os.path.join(HERE, "config.py"), "save"],
                   input=json.dumps(cfg, ensure_ascii=False), text=True, check=True)


def token_of(fc):
    req = urllib.request.Request(
        API + "/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": fc["app_id"], "app_secret": fc["app_secret"]}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        res = json.loads(r.read())
    if res.get("code") != 0:
        sys.exit(f"获取 token 失败: {res}")
    return res["tenant_access_token"]


def call(token, path, method="GET", data=None):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    body = json.dumps(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(req, body) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"code": e.code, "msg": f"HTTP {e.code}"}


def cmd_list(token):
    res = call(token, "/wiki/v2/spaces?page_size=50")
    if res.get("code") != 0:
        sys.exit(f"查询知识空间失败: {res}")
    items = (res.get("data") or {}).get("items") or []
    if not items:
        print("应用当前不在任何知识空间里。")
        print("需要先在飞书里把本应用加为目标知识库的成员（管理员），再重试。")
        return
    for it in items:
        print(f"{it['space_id']}  {it.get('space_name') or it.get('name')}  "
              f"({it.get('space_type', '')})")


def cmd_init(token, space_id, title):
    # 在空间根目录下建一个 docx 节点作为索引页（wiki 节点不支持 folder 类型）
    parent = ""
    for attempt in range(3):
        res = call(token, f"/wiki/v2/spaces/{space_id}/nodes", "POST", {
            "obj_type": "docx",
            "parent_node_token": parent,
            "node_type": "origin",
            "title": title,
        })
        if res.get("code") == 0:
            break
        # 同名冲突时直接复用已有节点
        if res.get("code") in (131002, 131003):
            existing = find_node(token, space_id, title)
            if existing:
                print(f"已存在同名节点，直接复用: {existing}")
                return _write_cfg(space_id, existing)
        if attempt == 2:
            sys.exit(f"创建知识库节点失败: {res}")
        time.sleep(2)

    node = (res.get("data") or {}).get("node") or {}
    node_token = node.get("node_token")
    if not node_token:
        sys.exit(f"创建节点返回异常: {res}")
    print(f"已创建索引页: {node.get('title')} -> {node_token}")
    return _write_cfg(space_id, node_token)


def find_node(token, space_id, title):
    res = call(token, f"/wiki/v2/spaces/{space_id}/nodes?page_size=50")
    for n in (res.get("data") or {}).get("items") or []:
        if (n.get("title") or "") == title:
            return n.get("node_token")
    return None


def _write_cfg(space_id, node_token):
    cfg = cfg_raw()
    cfg.setdefault("feishu", {})
    cfg["feishu"]["wiki_space_id"] = space_id
    cfg["feishu"]["wiki_parent_token"] = node_token
    save_cfg(cfg)
    domain = cfg["feishu"].get("domain") or "feishu.cn"
    print(f"已写入配置：wiki_space_id={space_id} wiki_parent_token={node_token}")
    print(f"索引页链接: https://{domain}/wiki/{node_token}")


def cmd_migrate(token, fc):
    space = fc.get("wiki_space_id")
    parent = fc.get("wiki_parent_token")
    if not space or not parent:
        sys.exit("还没配置知识库，先跑 init --space <space_id>")
    domain = fc.get("domain") or "feishu.cn"

    moved = 0
    for fn in sorted(os.listdir(LIBRARY)):
        if not fn.endswith(".md") or fn == "目录地图.md":
            continue
        path = os.path.join(LIBRARY, fn)
        text = open(path, encoding="utf-8").read()
        m = re.search(r"^dest_feishu: https://[^/]+/docx/(\w+)", text, re.M)
        if not m:
            continue  # 已经是知识库链接，或还没导入

        doc_id = m.group(1)
        res = call(token, f"/wiki/v2/spaces/{space}/nodes/move_docs_to_wiki", "POST", {
            "parent_wiki_token": parent, "obj_type": "docx",
            "obj_token": doc_id, "apply": True,
        })
        if res.get("code") != 0:
            print(f"  {fn}: 移动失败 {res.get('code')} {res.get('msg')}")
            continue

        data = res.get("data") or {}
        wiki_token = data.get("wiki_token") or ""
        if not wiki_token and data.get("task_id"):
            wiki_token = poll_move(token, data["task_id"])
        if not wiki_token:
            print(f"  {fn}: 未拿到节点 token，跳过回写")
            continue

        url = f"https://{domain}/wiki/{wiki_token}"
        text = re.sub(r"^dest_feishu: .*$", f"dest_feishu: {url}", text, flags=re.M)
        open(path, "w", encoding="utf-8").write(text)
        print(f"  {fn}: 已移入 -> {url}")
        moved += 1

    print(f"完成，共迁移 {moved} 篇")


def poll_move(token, task_id):
    for _ in range(20):
        time.sleep(2)
        res = call(token, f"/wiki/v2/tasks/{task_id}?task_type=move")
        task = (res.get("data") or {}).get("task") or {}
        for item in task.get("move_result") or []:
            if item.get("status") == 0:
                return (item.get("node") or {}).get("node_token")
    return ""


def cmd_move(token, space_id, node_token, parent_token):
    """把 wiki 节点挪到根目录或指定父节点下（子节点跟着走）。

    两个坑（实测）：
    1. move_docs_to_wiki 对「已经在知识库里」的文档会返回 code 0 但什么也不做，
       它只适用于把云空间文档搬进知识库，不能用来在库内挪位置。
    2. 正确的接口是 POST /wiki/v2/spaces/{space}/nodes/{node}/move，
       且 target_space_id 与 target_parent_token 不能同时为空：
       想挪到本库根目录就传 target_space_id=<本库 id>。
    """
    payload = {"target_space_id": space_id}
    if parent_token:
        payload = {"target_parent_token": parent_token}
    res = call(token, f"/wiki/v2/spaces/{space_id}/nodes/{node_token}/move", "POST", payload)
    if res.get("code") != 0:
        sys.exit(f"移动节点失败: {res}")

    node = (res.get("data") or {}).get("node") or {}
    where = parent_token or "根目录"
    print(f"已移动：{node.get('title')} -> {where}")
    print(f"当前父节点：{node.get('parent_node_token') or '(根目录)'}")


def cmd_index(token, doc_id):
    """用本地目录地图整体重写知识库索引页的正文。

    索引页是 docx，靠块替换实现：先清空根块的所有子块，再按 Markdown 重建。
    链接会转成飞书文档里的超链接（text_run.link），不是裸 URL 文字。
    """
    md = open(os.path.join(LIBRARY, "目录地图.md"), encoding="utf-8").read()
    blocks = []
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("# "):
            continue
        if line.startswith("## "):
            blocks.append({"block_type": 4, "heading2": {"elements": inline(line[3:])}})
        elif line.startswith("> "):
            blocks.append({"block_type": 15, "quote": {"elements": inline(line[2:])}})
        elif line.startswith("- "):
            blocks.append({"block_type": 12, "bullet": {"elements": inline(line[2:])}})
        else:
            blocks.append({"block_type": 2, "text": {"elements": inline(line)}})

    root = call(token, f"/docx/v1/documents/{doc_id}/blocks/{doc_id}/children?page_size=500")
    old = (root.get("data") or {}).get("items") or []
    if old:
        res = call(token, f"/docx/v1/documents/{doc_id}/blocks/{doc_id}/children/batch_delete",
                   "DELETE", {"start_index": 0, "end_index": len(old)})
        if res.get("code") != 0:
            sys.exit(f"清空旧内容失败: {res}")

    for i in range(0, len(blocks), 40):
        chunk = blocks[i:i + 40]
        res = call(token, f"/docx/v1/documents/{doc_id}/blocks/{doc_id}/children", "POST",
                   {"children": chunk, "index": i})
        if res.get("code") != 0:
            sys.exit(f"写入内容失败: {res}")
    print(f"索引页已刷新：{len(blocks)} 个块")


def inline(text):
    """把 `**粗体**` 与 `[文字](链接)` 转成飞书富文本元素，普通文字照留。

    注意：飞书的超链接必须挂在 text_element_style.link 上，且 url 要 URL 编码，
    写成 {"text_run":{"link":{"url":...}}} 会被直接丢掉（不报错，只是没链接）。
    """
    els = []
    for seg in re.split(r"(\*\*.+?\*\*)", text):
        if not seg:
            continue
        bold = seg.startswith("**") and seg.endswith("**") and len(seg) > 4
        body = seg[2:-2] if bold else seg
        pos = 0
        for m in re.finditer(r"\[([^\]]+)\]\((https?://[^)]+)\)", body):
            if m.start() > pos:
                els.append(_run(body[pos:m.start()], bold))
            els.append(_run(m.group(1), bold, link=m.group(2)))
            pos = m.end()
        if pos < len(body):
            els.append(_run(body[pos:], bold))
    return els or [_run("", False)]


def _run(content, bold=False, link=None):
    style = {"bold": bold, "inline_code": False, "italic": False,
             "strikethrough": False, "underline": False}
    if link:
        style["link"] = {"url": urllib.parse.quote(link, safe="")}
    return {"text_run": {"content": content, "text_element_style": style}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "init", "migrate", "index", "move"])
    ap.add_argument("--space", help="知识空间 id（飞书知识库 URL 里的 /wiki/space/<id>）")
    ap.add_argument("--title", default="读书灵感", help="索引页标题")
    ap.add_argument("--doc", help="索引页的 docx token（index 命令用）")
    ap.add_argument("--node", help="要移动的 wiki 节点 token（move 命令用）")
    ap.add_argument("--parent", default="", help="目标父节点 token；留空即知识库根目录")
    a = ap.parse_args()

    cfg = cfg_raw()
    fc = cfg.get("feishu") or {}
    if not fc.get("app_id") or not fc.get("app_secret"):
        sys.exit("缺少飞书凭证，先用 config.py set --kb feishu 配好")
    token = token_of(fc)

    if a.cmd == "list":
        cmd_list(token)
    elif a.cmd == "init":
        if not a.space:
            sys.exit("需要 --space <space_id>")
        cmd_init(token, a.space, a.title)
    elif a.cmd == "move":
        if not a.node:
            sys.exit("需要 --node <node_token>")
        cmd_move(token, (cfg.get("feishu") or {}).get("wiki_space_id") or a.space, a.node, a.parent)
    elif a.cmd == "index":
        doc = a.doc or (cfg.get("feishu") or {}).get("wiki_index_doc") or ""
        if not doc:
            sys.exit("需要 --doc <索引页 docx token>")
        cmd_index(token, doc)
    else:
        cmd_migrate(token, fc)


if __name__ == "__main__":
    main()
