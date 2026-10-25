#!/usr/bin/env python3
"""把灵感卡片导入为飞书文档（markdown → docx 导入任务）。

前置条件：飞书应用需开通 drive:file（上传/导入）与 docx 相关权限。

用法: python3 save_feishu.py <卡片.md路径>
输出: 文档链接（stdout）
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env  # noqa: E402
API = "https://open.feishu.cn/open-apis"


def cfg():
    out = subprocess.run([env.PY, os.path.join(HERE, "config.py"), "raw"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["feishu"]


def req_json(path, method, token, data=None):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    body = json.dumps(data).encode() if data is not None else None
    with urllib.request.urlopen(req, body) as r:
        return json.loads(r.read())


def set_tenant_readable(token, doc_id):
    """把文档链接权限设为「组织内可阅读」。

    应用身份（tenant_access_token）创建的文档默认只有应用自己是协作者，
    用户点链接会打不开，必须放开链接分享。
    """
    req = urllib.request.Request(
        f"{API}/drive/v1/permissions/{doc_id}/public?type=docx", method="PATCH")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    body = json.dumps({"link_share_entity": "tenant_readable",
                       "external_access_entity": "closed"}).encode()
    with urllib.request.urlopen(req, body) as r:
        return json.loads(r.read())


def move_to_wiki(token, doc_id, space_id, parent_wiki_token):
    """把云空间里的文档移进知识库的指定节点，返回 wiki_token。

    应用必须是该知识空间的成员（管理员），且具备 wiki:wiki 权限。
    """
    path = f"/wiki/v2/spaces/{space_id}/nodes/move_docs_to_wiki"
    r = req_json(path, "POST", token, {
        "parent_wiki_token": parent_wiki_token or "",
        "obj_type": "docx",
        "obj_token": doc_id,
        "apply": True,
    })
    if r.get("code") != 0:
        raise RuntimeError(f"{r.get('code')} {r.get('msg')}")
    data = r.get("data") or {}
    if data.get("wiki_token"):
        return data["wiki_token"]
    # 异步任务：轮询移动结果
    task_id = data.get("task_id")
    for _ in range(15):
        time.sleep(2)
        tr = req_json(f"/wiki/v2/tasks/{task_id}?task_type=move", "GET", token)
        move = ((tr.get("data") or {}).get("task") or {}).get("move_result") or []
        for item in move:
            if item.get("status") == 0 and item.get("node", {}).get("node_token"):
                return item["node"]["node_token"]
    raise RuntimeError("移动进知识库超时")


def main():
    if len(sys.argv) != 2:
        print("用法: save_feishu.py <卡片.md路径>", file=sys.stderr)
        sys.exit(1)
    card = os.path.abspath(sys.argv[1])
    fc = cfg()
    if not fc["app_id"] or not fc["app_secret"]:
        print("缺少飞书凭证，请用 config.py 设置", file=sys.stderr)
        sys.exit(2)

    # 1. tenant_access_token
    tok = req_json("/auth/v3/tenant_access_token/internal", "POST", "",
                   {"app_id": fc["app_id"], "app_secret": fc["app_secret"]})
    if tok.get("code") != 0:
        print(f"获取 token 失败: {tok}", file=sys.stderr)
        sys.exit(3)
    token = tok["tenant_access_token"]

    title = os.path.basename(card).rsplit(".", 1)[0]
    # 优先用卡片的一级标题做文档名（如「皇帝的账本 · 第 91 页」），比文件名好看
    try:
        import re
        with open(card, encoding="utf-8") as f:
            head = f.read()
        m = re.search(r"^# (.+)$", head, re.M)
        if m:
            title = m.group(1).strip()
    except Exception:
        pass

    # 2. upload_all 单次上传 md 文件（multipart）
    #   导入场景的固定用法：parent_type=ccm_import_open，parent_node 传空，
    #   extra 声明目标格式，否则返回 1061002 params error
    #   frontmatter 不是正文，必须剥掉，否则会在文档开头变成一堆字段文字
    with open(card, encoding="utf-8") as f:
        body_md = f.read()
    if body_md.startswith("---"):
        end = body_md.find("\n---", 3)
        if end != -1:
            body_md = body_md[end + 4:].lstrip("\n")
    content = body_md.encode()
    boundary = "----readinginspiration"
    fields = {"file_name": title + ".md", "parent_type": "ccm_import_open",
              "parent_node": "", "size": str(len(content)),
              "extra": json.dumps({"obj_type": "docx", "file_extension": "md"})}
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{title}.md"\r\n'
                 f'Content-Type: text/markdown\r\n\r\n'.encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(API + "/drive/v1/medias/upload_all",
                                 data=b"".join(parts), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req) as r:
            up = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"上传失败 HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(4)
    if up.get("code") != 0:
        print(f"上传失败: {up}", file=sys.stderr)
        sys.exit(4)
    file_token = up["data"]["file_token"]

    # 3. 创建导入任务（md → docx）
    folder = fc.get("folder_token") or ""
    if not folder:
        # 未指定目标文件夹时落到应用根目录
        meta = req_json("/drive/explorer/v2/root_folder/meta", "GET", token)
        folder = (meta.get("data") or {}).get("token", "")
    imp = req_json("/drive/v1/import_tasks", "POST", token, {
        "file_extension": "md",
        "file_token": file_token,
        "type": "docx",
        "file_name": title,
        "point": {"mount_type": 1, "mount_key": folder},
    })
    if imp.get("code") != 0:
        print(f"创建导入任务失败: {imp}", file=sys.stderr)
        sys.exit(5)
    ticket = imp["data"]["ticket"]

    # 4. 轮询结果
    for _ in range(20):
        time.sleep(2)
        res = req_json(f"/drive/v1/import_tasks/{ticket}", "GET", token)
        result = res.get("data", {}).get("result", {})
        st = result.get("job_status")
        if st == 0:
            doc = result["token"]
            # 5. 放开组织内可读，否则应用建的文档用户打不开
            try:
                set_tenant_readable(token, doc)
            except Exception as e:
                print(f"（提示：设置组织内可读失败，需手动分享）{e}", file=sys.stderr)
            # 6. 配了知识库就移进去，链接变成知识库节点
            space = fc.get("wiki_space_id") or ""
            if space:
                try:
                    wt = move_to_wiki(token, doc, space, fc.get("wiki_parent_token") or "")
                    domain = fc.get("domain") or "feishu.cn"
                    print(f"https://{domain}/wiki/{wt}")
                except Exception as e:
                    print(f"（提示：移入知识库失败，文档仍在云空间）{e}", file=sys.stderr)
                    print(f"https://feishu.cn/docx/{doc}")
                return
            print(f"https://feishu.cn/docx/{doc}")
            return
        if st in (1, 2):
            continue
        print(f"导入失败: {res}", file=sys.stderr)
        sys.exit(6)
    print("导入超时，请稍后在飞书云文档查看", file=sys.stderr)
    sys.exit(7)


if __name__ == "__main__":
    main()
