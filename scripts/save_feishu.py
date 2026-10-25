#!/usr/bin/env python3
"""把灵感卡片导入为飞书文档（markdown → docx 导入任务）。

前置条件：飞书应用需开通 drive:file（上传/导入）与 docx 相关权限。

用法: python3 save_feishu.py <卡片.md路径> [--force]
      --force 忽略台账与卡片里的旧链接，强制重建一份
输出: 文档链接（stdout）
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env  # noqa: E402
import delivery  # noqa: E402
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


def set_tenant_readable(token, doc_id, policy="tenant"):
    """设置文档链接分享范围。

    应用身份（tenant_access_token）创建的文档默认只有应用自己是协作者；
    只留在云空间时，用户点链接会打不开，必须放开。但**进知识库的文档不必放开全租户**
    —— 那种情况下可见性由空间成员决定，提前放开等于扩大暴露面。
    policy: tenant=组织内可读 / closed=仅协作者
    """
    entity = "tenant_readable" if policy == "tenant" else "closed"
    req = urllib.request.Request(
        f"{API}/drive/v1/permissions/{doc_id}/public?type=docx", method="PATCH")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    body = json.dumps({"link_share_entity": entity,
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


# 一页 200 条，50 页 = 1 万个文件。翻不完就按「没查成」处理，不能当「不存在」。
PAGE_LIMIT = 50


def list_folder_files(token, folder):
    """把一个文件夹里的条目**全部**翻出来，返回 (ok, files)。

    只读第一页是个陷阱：目标文档完全可能落在第二页。第一页恰好只有别的同名文档时，
    就会判成「本卡没建过」再建一份 —— 重复投递最常见的来源。
    任何一页失败都返回 ok=False，调用方必须当成「没查成」，不能当成「不存在」。

    分页字段（`drive/v1/files`）：请求用 `page_token`，响应里 `has_more` +
    `next_page_token`。两者都没有才算翻完。
    """
    out = []
    page_token = ""
    seen = set()
    for _ in range(PAGE_LIMIT):
        q = f"/drive/v1/files?folder_token={folder}&page_size=200"
        if page_token:
            q += f"&page_token={urllib.parse.quote(page_token)}"
        try:
            r = req_json(q, "GET", token)
        except Exception:
            return False, []
        if r.get("code") not in (0, None):
            # 业务层失败：权限不足 / token 失效 / 参数错，都不能当作「目录是空的」
            print(f"（列目录失败：code={r.get('code')} msg={r.get('msg')}）",
                  file=sys.stderr)
            return False, []
        data = r.get("data") or {}
        files = data.get("files")
        if files is None:
            # code=0 但没有 files 字段，同样不能断言「没有这个文档」
            return False, []
        out.extend(files)
        nxt = data.get("next_page_token") or ""
        if not data.get("has_more") and not nxt:
            return True, out
        if not nxt:
            # 说还有下一页却没给 token：接口异常，不能当成「已经翻完」
            print("（列目录异常：has_more=true 但没有 next_page_token）", file=sys.stderr)
            return False, []
        if nxt in seen:
            print("（列目录异常：next_page_token 重复）", file=sys.stderr)
            return False, []
        seen.add(nxt)
        page_token = nxt
    print(f"（列目录超过 {PAGE_LIMIT} 页仍未翻完，按未查成处理）", file=sys.stderr)
    return False, []


def find_drive_doc(token, folder, title, exclude=()):
    """在云空间文件夹里按名字找**全部**候选，返回 (status, [doc_token, ...])。

    status: found / absent / error
    error 表示**没查成**（无权限、网络、folder 为空、业务错误码、翻页没翻完），
    调用方不能当成「不存在」，否则崩溃恢复会直接再建一份。

    注意飞书的错误不一定抛 HTTP 异常：无权限时常常是 HTTP 200 + `code=131006`，
    `data` 里没有 files。只看有没有异常、再看 files 是否为空，会把权限错误读成
    「空目录」——所以必须先确认 `code == 0`。

    为什么返回列表而不是第一份：同名文档可能不止一份（以前发生过重复投递）。
    只取 `hits[0]` 的话，第一份恰好是别的卡片建的、本卡在后面，就会判定
    「不是我的」再建一份 —— 越修越乱。调用方必须**遍历全部候选、逐个回读标识**，
    全都核对过（而不是都没核对）才允许说「确实没有」。

    exclude：被新代次取代的远端 id（reset / --force 之前的旧文档），一律跳过。
    """
    if not folder:
        return "error", []
    ok, files = list_folder_files(token, folder)
    if not ok:
        return "error", []
    skip = {str(x) for x in (exclude or ()) if x}
    hits = [it for it in files
            if (it.get("name") or "").strip() == (title or "").strip()
            and (it.get("type") or "") in ("docx", "doc")
            and (it.get("token") or "") not in skip]
    tokens = [it.get("token") or "" for it in hits if it.get("token")]
    if not tokens:
        return "absent", []
    return "found", tokens


def doc_has_marker(token, doc_id, card_id):
    """回读 docx 纯文本，确认这份文档是不是这张卡导入的。

    True / False / None（None = 读不出来，不能下结论）。
    匹配用完整标识值精确比较（`card` 不能命中 `card2`）。
    """
    try:
        r = req_json(f"/docx/v1/documents/{doc_id}/raw_content", "GET", token)
    except Exception:
        return None
    if r.get("code") != 0:
        return None
    text = (r.get("data") or {}).get("content") or ""
    if not text:
        return None
    return delivery.has_card_marker(text, card_id)


def main():
    if len(sys.argv) < 2:
        print("用法: save_feishu.py <卡片.md路径> [--force]", file=sys.stderr)
        sys.exit(1)
    card = os.path.abspath(sys.argv[1])
    force = "--force" in sys.argv[2:]
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
    # 卡片标识写进正文：补发核对时靠它判断「这份文档是不是这张卡」，而不是只看标题
    card_id = os.path.basename(card).rsplit(".", 1)[0]
    marker = f"卡片：{card_id}"
    body_md = body_md.rstrip() + f"\n\n---\n\n> {marker}\n"
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
    # 3.5 台账：这张卡在这个落点是不是已经投过了
    space = fc.get("wiki_space_id") or ""
    parent_key = (fc.get("wiki_parent_token") or "") if space else (folder or "")
    # 代次视图：rec 是否属于当前代次、哪些旧文档已被取代、用户是否要求重投
    st = delivery.state(card, "feishu", parent_key)
    rec = st["rec"]
    current = rec is not None and int(rec.get("epoch") or 1) == st["epoch"]
    # 只有**当前代次**的 committed 才算已投递：--force / reset 开了新代次后，
    # 旧记录的链接必须作废，否则重投中途崩掉，下次又会把上一代的旧链接当结果返回
    if current and rec.get("status") == "committed" and rec.get("remote_id") and not force:
        url = rec.get("url") or rec["remote_id"]
        if url:
            delivery.record_dest(card, "dest_feishu", url)  # 上次可能倒在写回这一步
        print(url)
        print("（台账显示这张卡已投递过，跳过重复创建；要重投加 --force）",
              file=sys.stderr)
        return
    # 旧版卡片只有 dest_feishu、没有台账：接纳为已投递，不能当成新卡再建一份
    # 例外：用户刚 reset 过（要求重投），旧链接不能再被认成已投递
    if not rec and not force and not st["wants_redeliver"]:
        old = delivery.read_dest(card, "dest_feishu")
        if old:
            delivery.adopt(card, "feishu", parent_key, old, title)
            print(old)
            print("（卡片里已有 dest_feishu，按已投递接纳并补建台账，未重复创建）",
                  file=sys.stderr)
            return
    if current and rec.get("status") in ("intent", "failed") and not force:
        status, candidates = find_drive_doc(token, folder, title, exclude=st["superseded"])
        if status == "error":
            print("核对失败：没法确认上次投递是否已建成（列目录失败、无权限或没配 folder_token）。"
                  "为避免重复创建，本次未投递。到云空间看一眼「%s」是否已存在；"
                  "确认没有就加 --force 重投。" % title, file=sys.stderr)
            sys.exit(4)
        if status == "found":
            # 同名候选可能不止一份：**每一份**都要回读标识，不能只看第一份。
            # 第一份可能是别的卡建的，本卡在后面 —— 只看第一份就会误判成「没有」再建一份。
            unread = []
            for doc_tok in candidates:
                hit = doc_has_marker(token, doc_tok, card_id)
                if hit is True:
                    url = f"https://{fc.get('domain') or 'feishu.cn'}/docx/{doc_tok}"
                    delivery.commit(rec["delivery_id"], doc_tok, url, title)
                    delivery.record_dest(card, "dest_feishu", url)
                    print(url)
                    print("（上次投递未确认；云端已有本卡文档，沿用而未重建）",
                          file=sys.stderr)
                    return
                if hit is None:
                    # 读不出正文 ≠ 不是本卡。只要有一份读不出，就不能断言「本卡没建过」。
                    unread.append(doc_tok)
            if unread:
                print(f"核对失败：云端有 {len(candidates)} 份同名文档「{title}」，"
                      f"其中 {len(unread)} 份正文读不出来，无法确认是不是这张卡。"
                      f"为避免重复创建，本次未投递。要到云空间逐个确认，"
                      f"或加 --force 重投。", file=sys.stderr)
                sys.exit(4)
            # 全部候选都读成功、且都不是本卡 → 确实没建过，可以新建
            print(f"⚠️ 云空间已有 {len(candidates)} 份同名文档「{title}」，"
                  f"但正文里都没有本卡标识。继续新建，请自行清理重复。",
                  file=sys.stderr)
    rec = delivery.begin(card, "feishu", parent_key, title, force=force)

    imp = req_json("/drive/v1/import_tasks", "POST", token, {
        "file_extension": "md",
        "file_token": file_token,
        "type": "docx",
        "file_name": title,
        "point": {"mount_type": 1, "mount_key": folder},
    })
    if imp.get("code") != 0:
        delivery.fail(rec["delivery_id"], imp)
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
            # 5. 权限：按**终态**决定，而不是无脑放开全租户
            #    进知识库 → 可见性由空间成员决定，提前放开链接属于多余暴露
            #    只留云空间 → 应用建的文档默认只有应用是协作者，必须显式放权
            #    feishu.link_share 可配：auto（默认，按终态）/ tenant / closed / skip
            policy = (fc.get("link_share") or "auto").strip()
            if policy == "auto":
                policy = "skip" if space else "tenant"
            if policy in ("tenant", "closed"):
                try:
                    set_tenant_readable(token, doc, policy)
                except Exception as e:
                    print(f"（提示：设置链接权限失败，需手动分享）{e}", file=sys.stderr)
            elif policy == "skip":
                print("（链接权限未改动：知识库内由空间成员权限决定）", file=sys.stderr)
            # 6. 配了知识库就移进去，链接变成知识库节点
            if space:
                try:
                    wt = move_to_wiki(token, doc, space, fc.get("wiki_parent_token") or "")
                    domain = fc.get("domain") or "feishu.cn"
                    url = f"https://{domain}/wiki/{wt}"
                    delivery.commit(rec["delivery_id"], wt, url, title)
                    delivery.record_dest(card, "dest_feishu", url)
                    print(url)
                except Exception as e:
                    print(f"（提示：移入知识库失败，文档仍在云空间）{e}", file=sys.stderr)
                    url = f"https://feishu.cn/docx/{doc}"
                    delivery.commit(rec["delivery_id"], doc, url, title)
                    delivery.record_dest(card, "dest_feishu", url)
                    print(url)
                return
            url = f"https://feishu.cn/docx/{doc}"
            delivery.commit(rec["delivery_id"], doc, url, title)
            delivery.record_dest(card, "dest_feishu", url)
            print(url)
            return
        if st in (1, 2):
            continue
        print(f"导入失败: {res}", file=sys.stderr)
        sys.exit(6)
    print("导入超时，请稍后在飞书云文档查看", file=sys.stderr)
    sys.exit(7)


if __name__ == "__main__":
    main()
