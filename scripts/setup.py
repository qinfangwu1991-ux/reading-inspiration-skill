#!/usr/bin/env python3
"""读书灵感 · 一键配置向导。

设计原则：**能自动做的全自动，绝不把用户赶去各个平台后台翻设置。**
用户真正需要提供的只有一样东西 —— 飞书的 App ID / App Secret（开放平台没有免应用的授权方式）。
其余全部由本脚本探测或代建：
  · 乐享  → 通过已连接的乐享通路自动建「读书灵感」文件夹（Agent 侧调 MCP，本脚本只落配置）
  · Obsidian → 自动扫描本机所有 vault（读 obsidian.json + iCloud 目录 + 常见路径）
  · 飞书  → 凭证粘贴后自动验证、自动建文件夹、自动取域名、可选自动建知识库索引页

用法:
    python3 setup.py doctor
        体检：每个知识库现在是「可用 / 缺什么 / 不可用」，末尾给一句下一步建议

    python3 setup.py detect obsidian
        只探测本机 Obsidian vault，列出候选（编号 + 路径 + 笔记数）

    python3 setup.py install --kb obsidian [--vault PATH]
        装 Obsidian。不给 --vault 就自动取探测到的第一个库，并在库下建「读书灵感」子目录

    python3 setup.py install --kb feishu --app-id cli_xxx --app-secret xxx [--wiki LINK]
        装飞书。自动验证凭证 → 自动在应用空间建「读书灵感」文件夹 → 自动取租户域名
        --wiki 可选，传知识库链接（/wiki/space/<id> 或某个 wiki 页面链接）则额外建索引页

    python3 setup.py install --kb lexiangla --space-id S --folder-entry-id F [--map-entry-id M]
        装乐享。参数由 Agent 通过乐享 MCP 自动建好目录后传进来，用户零操作

    python3 setup.py pack
        打一个可以发给别人的干净安装包（剔除本机 config.json 与已录卡片）
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import config as cfgmod  # noqa: E402
import env  # noqa: E402

API = "https://open.feishu.cn/open-apis"
KB_LABEL = {"lexiangla": "乐享知识库", "obsidian": "本地 Obsidian",
            "feishu": "飞书", "ima": "IMA 知识库（手机端）"}


# ---------------------------------------------------------------- 飞书底层

def fcall(token, path, method="GET", data=None):
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
    except Exception as e:  # 断网 / DNS
        return {"code": -1, "msg": str(e)}


def tenant_token(app_id, app_secret):
    req = urllib.request.Request(
        API + "/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            res = json.loads(r.read())
    except Exception as e:
        return None, f"网络不通: {e}"
    if res.get("code") != 0:
        hint = {10003: "App ID 不存在", 10014: "App Secret 不正确",
                10012: "App ID 或 Secret 有误"}.get(res.get("code"), "")
        return None, f"{res.get('code')} {res.get('msg')} {hint}".strip()
    return res["tenant_access_token"], ""


def drive_files(token, folder_token=""):
    q = f"/drive/v1/files?page_size=50"
    if folder_token:
        q += f"&folder_token={folder_token}"
    res = fcall(token, q)
    if res.get("code") != 0:
        return None, res
    return (res.get("data") or {}).get("files") or [], res


def create_folder(token, name, parent=""):
    res = fcall(token, "/drive/v1/files/create_folder", "POST",
                {"name": name, "folder_token": parent})
    if res.get("code") != 0:
        return None, res
    return (res.get("data") or {}).get("token"), res


def parse_wiki_link(link):
    """从知识库链接里取 (domain, space_id 或 node_token)。"""
    m = re.match(r"https?://([^/]+)/wiki/space/(\w+)", link)
    if m:
        return m.group(1), m.group(2), "space"
    m = re.match(r"https?://([^/]+)/wiki/(\w+)", link)
    if m:
        return m.group(1), m.group(2), "node"
    m = re.match(r"https?://([^/]+)/(?:docx|docs)/(\w+)", link)
    if m:
        return m.group(1), m.group(2), "docx"
    return None, None, None


def wiki_index_page(token, space_id, title="读书灵感目录地图"):
    """在知识库根目录建/复用索引页，返回 (node_token, docx_token, err)。"""
    res = fcall(token, f"/wiki/v2/spaces/{space_id}/nodes?page_size=50")
    if res.get("code") == 0:
        for n in (res.get("data") or {}).get("items") or []:
            if (n.get("title") or "") == title:
                return n.get("node_token"), n.get("obj_token"), ""
    else:
        # 页面级授权下这里是 131006，属正常，继续尝试建节点
        pass

    res = fcall(token, f"/wiki/v2/spaces/{space_id}/nodes", "POST",
                {"obj_type": "docx", "parent_node_token": "",
                 "node_type": "origin", "title": title})
    if res.get("code") != 0:
        return None, None, f"{res.get('code')} {res.get('msg')}"
    node = (res.get("data") or {}).get("node") or {}
    return node.get("node_token"), node.get("obj_token"), ""


# ---------------------------------------------------------------- Obsidian

def _count_md(path, limit=4000):
    n = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        n += sum(1 for f in files if f.endswith(".md"))
        if n >= limit:
            break
    return n


def obsidian_vaults():
    raw = []
    for p in ("~/Library/Application Support/obsidian/obsidian.json",
              "~/.config/obsidian/obsidian.json",
              os.path.join(os.environ.get("APPDATA", ""), "obsidian", "obsidian.json")):
        p = os.path.expanduser(p)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for v in (json.load(f).get("vaults") or {}).values():
                    if v.get("path"):
                        raw.append(v["path"])
        except Exception:
            pass

    for base in ("~/Library/Mobile Documents/iCloud~md~obsidian/Documents",
                 "~/Obsidian", "~/Documents", "~/Notes", "~"):
        b = os.path.expanduser(base)
        if not os.path.isdir(b):
            continue
        try:
            for name in os.listdir(b):
                p = os.path.join(b, name)
                if os.path.isdir(os.path.join(p, ".obsidian")):
                    raw.append(p)
        except Exception:
            pass

    seen, out = set(), []
    for p in raw:
        rp = os.path.realpath(os.path.expanduser(p))
        if rp in seen or not os.path.isdir(rp):
            continue
        seen.add(rp)
        out.append({"path": rp, "notes": _count_md(rp)})
    out.sort(key=lambda x: -x["notes"])

    # 去嵌套：父目录也在候选里时，子目录只是父目录的一部分，不再单列
    keep = []
    for v in out:
        if any(v["path"].startswith(k["path"] + os.sep) for k in keep):
            continue
        keep.append(v)
    return keep


# ---------------------------------------------------------------- 命令

def cmd_detect_obsidian():
    vs = obsidian_vaults()
    if not vs:
        print("没找到本机 Obsidian 库。")
        print("如果装了 Obsidian，把它打开一次再重试；或者直接告诉我 vault 目录的绝对路径。")
        return
    print(f"发现 {len(vs)} 个 Obsidian 库：")
    for i, v in enumerate(vs, 1):
        print(f"  {i}) {v['path']}  （{v['notes']} 篇笔记）")


def cmd_doctor(cfg):
    enabled = cfg.get("kb_types") or []
    print("== 读书灵感 · 配置体检 ==\n")
    if enabled:
        print("已启用：" + "、".join(KB_LABEL.get(k, k) for k in enabled))
    else:
        print("已启用：无（还没配任何知识库）")
    print()

    todo = []

    # 乐享
    lx = cfg.get("lexiangla") or {}
    if lx.get("space_id") and lx.get("folder_entry_id"):
        print("[乐享知识库] 已配置 ✓")
        print(f"  知识库：{lx.get('space_name') or lx.get('space_id')}")
        print(f"  文件夹：{lx.get('folder_name') or lx.get('folder_entry_id')}")
        if lx.get("map_entry_id"):
            print("  索引页：已就位")
        else:
            print("  索引页：未建（可选）")
        print("  连接器：由对话内自动确认，不需要你操作")
    elif lx.get("token"):
        print("[乐享知识库] 旧的 API Token 模式（建议迁移到零配置的已连接通路）")
    else:
        print("[乐享知识库] 未配置 —— 可全自动接入，用户零操作")
        todo.append("lexiangla")

    # Obsidian
    vault = cfg.get("obsidian_vault") or ""
    if vault and os.path.isdir(vault):
        print("[本地 Obsidian] 已配置 ✓")
        print(f"  目录：{vault}")
    else:
        vs = obsidian_vaults()
        if vs:
            print(f"[本地 Obsidian] 未配置 —— 已自动发现 {len(vs)} 个库，可直接接")
            for i, v in enumerate(vs[:3], 1):
                print(f"  {i}) {v['path']}（{v['notes']} 篇）")
        else:
            print("[本地 Obsidian] 未配置 —— 本机没探测到 vault")
        todo.append("obsidian")

    # 飞书
    fc = cfg.get("feishu") or {}
    if fc.get("mode") == "lark-cli":
        st, _ = (lark_json("auth", "status") if shutil.which("lark-cli") else (None, None))
        user = (((st or {}).get("identities") or {}).get("user") or {})
        if user.get("status") == "ready":
            print("[飞书] 连接器身份已登录 ✓（零凭证，不需要 App Secret）")
            if fc.get("wiki_space_id"):
                print(f"  知识库：{fc.get('wiki_space_id')}")
            if fc.get("wiki_parent_token"):
                print(f"  索引页：{fc.get('wiki_parent_token')}"
                      + (f"  https://{fc.get('domain')}/wiki/{fc['wiki_parent_token']}"
                         if fc.get("domain") else ""))
        else:
            print("[飞书] 连接器已配置但身份未登录 ✗")
            print("  在对话里完成一次飞书授权即可，不需要任何凭证")
            todo.append("feishu")
    elif fc.get("app_id") and fc.get("app_secret"):
        tok, err = tenant_token(fc["app_id"], fc["app_secret"])
        if err:
            print("[飞书] 凭证无效 ✗")
            print(f"  {err}")
            todo.append("feishu")
        else:
            print("[飞书] 凭证有效 ✓")
            files, res = drive_files(tok, "")
            if files is None:
                print(f"  云空间读取失败：{res.get('code')} {res.get('msg')}")
                if res.get("code") in (99991672, 99991679, 1061004, 1770001):
                    print("  → 应用缺 drive 权限：开放平台「权限管理」勾选 drive:drive 并发布版本")
                    todo.append("feishu-perm")
            else:
                print(f"  云空间根目录可读（{len(files)} 项）✓")
            if fc.get("wiki_space_id") and fc.get("wiki_parent_token"):
                print(f"  知识库：{fc.get('wiki_space_id')} 索引页 {fc.get('wiki_parent_token')}")
            else:
                print("  知识库：未接（可选，走云空间文件夹也能用）")
            if fc.get("folder_token"):
                print(f"  落库文件夹：{fc.get('folder_token')}")
    else:
        print("[飞书] 未配置 —— 有连接器可零凭证接入；没有连接器才需要粘贴两段字符")
        todo.append("feishu")

    # IMA
    if "ima" in enabled:
        print("[IMA 知识库] 已启用，但桌面端没有对应工具 —— 每次分发会静默跳过")
        print("  想只在手机端用就保持这样；想少一次无意义跳过可 disable --kb ima")
    else:
        print("[IMA 知识库] 未启用（桌面端本来也用不了，属于正常）")

    print()
    if not enabled and not todo:
        print("建议：先选一个知识库，我这边直接配好。")
    elif todo:
        print("下一步：" + "；".join(
            {"lexiangla": "乐享我可以直接接通（零操作）",
             "obsidian": "Obsidian 我可以直接接",
             "feishu": "飞书先试连接器（点一次授权），不行才需要贴 App ID / App Secret",
             "feishu-perm": "飞书应用补 drive:drive 权限后重新发布"}.get(t, t)
            for t in todo))
    else:
        print("全部就绪，可以开始拍书页了。")


def install_obsidian(cfg, vault):
    if not vault:
        vs = obsidian_vaults()
        if not vs:
            sys.exit("没探测到 Obsidian 库，请用 --vault <绝对路径> 指定")
        vault = vs[0]["path"]
        print(f"自动选用：{vault}")
    vault = os.path.realpath(os.path.expanduser(vault))
    if not os.path.isdir(vault):
        sys.exit(f"目录不存在：{vault}")
    target = vault
    if os.path.basename(vault) != "读书灵感":
        target = os.path.join(vault, "读书灵感")
        os.makedirs(target, exist_ok=True)
    cfg["obsidian_vault"] = target
    if "obsidian" not in cfg["kb_types"]:
        cfg["kb_types"].append("obsidian")
    cfgmod.save(cfg)
    print(f"已接入 Obsidian：{target}")
    print(f"（在 Obsidian 里会自动出现在「读书灵感」文件夹下）")


def install_lexiangla(cfg, a):
    if not a.space_id:
        sys.exit("需要 --space-id，先用乐享 MCP 取到知识库 id")
    lx = cfg.setdefault("lexiangla", {})
    lx.update({"mode": "connector", "team": getattr(a, "team", None) or lx.get("team") or "lexiangla",
               "space_id": a.space_id})
    if a.space_name:
        lx["space_name"] = a.space_name
    if a.folder_entry_id:
        lx["folder_entry_id"] = a.folder_entry_id
        lx["folder_name"] = a.folder_name_lx or "读书灵感"
    if a.map_entry_id:
        lx["map_entry_id"] = a.map_entry_id
        lx["map_name"] = getattr(a, "map_name", None) or "读书灵感目录地图"
    if "lexiangla" not in cfg["kb_types"]:
        cfg["kb_types"].append("lexiangla")
    cfgmod.save(cfg)
    print(f"已接入乐享：{lx.get('space_name') or lx['space_id']}")
    if lx.get("folder_entry_id"):
        print(f"  落库文件夹：{lx.get('folder_name')}（{lx['folder_entry_id']}）")
    if lx.get("map_entry_id"):
        print(f"  目录地图：已关联（{lx['map_entry_id']}）")


def lark_json(*args, timeout=120):
    p = subprocess.run(["lark-cli"] + list(args), capture_output=True, text=True,
                       timeout=timeout)
    try:
        return json.loads((p.stdout or "").strip()), p
    except Exception:
        return None, p


def install_feishu_lark(cfg, a):
    """零凭证接飞书：用飞书连接器（lark-cli，用户身份），不碰 App ID/Secret。

    用户身份的权限比应用身份大得多，实测能直接列/建知识空间、建节点，
    所以这条路能做的比自建应用还多。
    """
    if not shutil.which("lark-cli"):
        sys.exit("没装飞书连接器（lark-cli）。要么在对话里连接飞书，要么改用 --app-id/--app-secret")

    st, _ = lark_json("auth", "status")
    ident = ((st or {}).get("identities") or {}).get("user") or {}
    if ident.get("status") != "ready":
        sys.exit("飞书连接器还没登录（user 身份不可用），先在对话里完成一次授权")

    link = a.wiki
    if not link:
        sys.exit("零凭证模式需要 --wiki <知识库链接>，我从链接里取 space_id 和域名")
    domain, ident_tok, kind = parse_wiki_link(link)
    if not domain:
        sys.exit(f"知识库链接没认出来：{link}")

    space_id = ident_tok
    if kind == "node":
        res, _ = lark_json("wiki", "+node-get", "--node-token", ident_tok,
                           "--as", "user", "--format", "json")
        if not (res or {}).get("ok"):
            sys.exit(f"解析知识库节点失败：{(res or {}).get('error')}")
        space_id = ((res.get("data") or {}).get("space_id")) or ""
    elif kind != "space":
        sys.exit("请给知识库链接（https://<域名>/wiki/space/<space_id>）")

    # 确认这个空间对当前用户可见（顺便校验授权真的通了）
    res, _ = lark_json("wiki", "+space-list", "--as", "user", "--format", "json")
    spaces = ((res or {}).get("data") or {}).get("spaces") or []
    hit = next((s for s in spaces if str(s.get("space_id")) == str(space_id)), None)
    if not hit:
        print(f"警告：知识空间 {space_id} 没出现在可访问列表里，仍尝试继续")
    else:
        print(f"知识库：{hit.get('name')}（{space_id}）✓")

    # 根目录找/建索引页
    title = "读书灵感目录地图"
    node_token, docx_token = "", ""
    res, _ = lark_json("wiki", "+node-list", "--space-id", space_id,
                       "--as", "user", "--format", "json")
    for n in ((res or {}).get("data") or {}).get("nodes") or []:
        if (n.get("title") or "") == title:
            node_token, docx_token = n.get("node_token") or "", n.get("obj_token") or ""
            print(f"复用已有索引页：{node_token}")
            break
    if not node_token:
        res, p = lark_json("wiki", "+node-create", "--space-id", space_id,
                           "--title", title, "--obj-type", "docx",
                           "--as", "user", "--format", "json")
        if not (res or {}).get("ok"):
            msg = (p.stderr or p.stdout or "")[:300]
            print(f"建索引页失败：{msg}")
            print("（不影响使用：卡片会直接建在知识库根目录）")
        else:
            data = res.get("data") or {}
            node_token = data.get("node_token") or (data.get("node") or {}).get("node_token") or ""
            docx_token = data.get("obj_token") or (data.get("node") or {}).get("obj_token") or ""
            print(f"已自动创建索引页：{node_token}")

    fc = cfg.setdefault("feishu", {})
    fc["mode"] = "lark-cli"
    fc["domain"] = domain
    fc["wiki_space_id"] = space_id
    if node_token:
        fc["wiki_parent_token"] = node_token
    if docx_token:
        fc["wiki_index_doc"] = docx_token
    if "feishu" not in cfg["kb_types"]:
        cfg["kb_types"].append("feishu")
    cfgmod.save(cfg)
    print(f"已启用：飞书（零凭证 · 连接器身份）")
    if node_token:
        print(f"索引页：https://{domain}/wiki/{node_token}")


def install_feishu(cfg, a):
    if getattr(a, "lark", False):
        return install_feishu_lark(cfg, a)
    if not (a.app_id and a.app_secret):
        sys.exit("需要 --app-id 与 --app-secret")
    tok, err = tenant_token(a.app_id, a.app_secret)
    if err:
        sys.exit(f"凭证验证失败：{err}")

    fc = cfg.setdefault("feishu", {})
    fc["app_id"], fc["app_secret"] = a.app_id, a.app_secret
    print("凭证有效 ✓")

    # 探测云空间 + 顺带拿租户域名
    files, res = drive_files(tok, "")
    domain = ""
    if files is not None:
        for it in files:
            m = re.match(r"https?://([^/]+)/", it.get("url") or "")
            if m:
                domain = m.group(1)
                break
        print(f"云空间可读（根目录 {len(files)} 项）✓" + (f"  域名 {domain}" if domain else ""))
    else:
        print(f"云空间读取失败：{res.get('code')} {res.get('msg')}")
        print("→ 这不是凭证问题，是应用缺 drive 权限。去开放平台「权限管理」勾选 drive:drive，")
        print("  再到「版本管理与发布」创建版本并发布（不发布不生效），然后重跑这条命令。")
        cfgmod.save(cfg)
        sys.exit(3)

    if domain and not fc.get("domain"):
        fc["domain"] = domain

    # 落库文件夹：给了就用，没给就在根目录找/建「读书灵感」
    folder = a.folder_token or fc.get("folder_token") or ""
    if folder:
        sub, res2 = drive_files(tok, folder)
        if sub is None:
            print(f"指定文件夹读不到（{res2.get('code')}），改用根目录新建")
            folder = ""
    if not folder:
        name = a.folder_name or "读书灵感"
        exist = next((it for it in (files or [])
                      if it.get("name") == name and it.get("type") == "folder"), None)
        if exist:
            folder = exist["token"]
            print(f"复用已有文件夹「{name}」")
        else:
            folder, res2 = create_folder(tok, name, "")
            if not folder:
                print(f"建文件夹失败：{res2.get('code')} {res2.get('msg')}")
                print("（不影响使用，卡片会落在云空间根目录）")
                folder = ""
            else:
                print(f"已自动创建文件夹「{name}」")
    if folder:
        fc["folder_token"] = folder
        print(f"落库位置：云空间 /{a.folder_name or '读书灵感'}（{folder}）")

    # 可选：知识库
    if a.wiki:
        d, ident, kind = parse_wiki_link(a.wiki)
        if not d:
            print("知识库链接没认出来，跳过知识库接入（云空间仍可用）")
        else:
            if d and not fc.get("domain"):
                fc["domain"] = d
            space_id = ident
            if kind == "node":
                r = fcall(tok, f"/wiki/v2/spaces/get_node?token={ident}")
                if r.get("code") == 0:
                    space_id = ((r.get("data") or {}).get("node") or {}).get("space_id")
                else:
                    print(f"解析 wiki 节点失败：{r.get('code')} {r.get('msg')}")
                    space_id = None
            if kind == "docx":
                space_id = None
                print("你给的是文档链接，不是知识库链接；想要知识库请给 /wiki/space/<id> 形式")
            if space_id:
                # 已经接过同一个库时直接复用，别重复建一个同名索引页
                if fc.get("wiki_space_id") == space_id and fc.get("wiki_parent_token"):
                    node, docx = fc["wiki_parent_token"], fc.get("wiki_index_doc") or ""
                    print(f"复用已配置的索引页：{node}")
                else:
                    node, docx, werr = wiki_index_page(tok, space_id)
                    if not node:
                        print(f"知识库索引页没建成：{werr}")
                        print("→ 需要把应用加为该知识库的成员/页面协作者（可管理），再重跑；")
                        print("  也可以先不用知识库，云空间文件夹照常工作。")
                if node:
                    fc["wiki_space_id"] = space_id
                    fc["wiki_parent_token"] = node
                    if docx:
                        fc["wiki_index_doc"] = docx
                    print(f"知识库已接：索引页 {node}")
    cfgmod.save(cfg)

    if "feishu" not in cfg["kb_types"]:
        cfg["kb_types"].append("feishu")
    cfgmod.save(cfg)
    print("已启用：飞书" + ("（含知识库）" if cfg["feishu"].get("wiki_parent_token") else "（云空间文件夹）"))


MARKET_FIELDS = ["name", "display_name", "display_name_en", "description",
                 "description_zh", "description_en", "version", "author"]
MARKET_DIRS = ["references", "scripts", "templates"]


def check_frontmatter():
    """检查 SKILL.md 的 frontmatter 是否满足 WorkBuddy 开放平台的上架要求。

    必填八项缺一项，开放平台解析就会失败（报错只说「解析失败」，不给具体字段）。
    """
    p = os.path.join(ROOT, "SKILL.md")
    s = open(p, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n", s, re.S)
    if not m:
        return ["SKILL.md 开头没有 YAML frontmatter"]
    keys = set()
    for line in m.group(1).splitlines():
        if re.match(r"^[A-Za-z_-]+:", line):
            keys.add(line.split(":", 1)[0].strip())
    missing = [k for k in MARKET_FIELDS if k not in keys]
    return ["缺字段 %s" % k for k in missing]


def cmd_pack(market=False):
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    name = "reading-inspiration"
    tmp = os.path.join(dist, "_pkg")
    shutil.rmtree(tmp, ignore_errors=True)
    dst = os.path.join(tmp, name)
    os.makedirs(dst)

    if market:
        # 上架包：严格按开放平台的结构，只留 SKILL.md + references/ + scripts/ + templates/
        # 不带 README/LICENSE/library/config/dist —— 那些是给 git 仓库看的，不是给解析器看的
        bad = check_frontmatter()
        if bad:
            print("✗ frontmatter 不满足上架要求：", file=sys.stderr)
            for b in bad:
                print("  - " + b, file=sys.stderr)
            sys.exit(3)
        shutil.copy2(os.path.join(ROOT, "SKILL.md"), os.path.join(dst, "SKILL.md"))
        for d in MARKET_DIRS:
            src = os.path.join(ROOT, d)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(dst, d),
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc",
                                                              ".DS_Store"))
        n = len([f for _, _, fs in os.walk(dst) for f in fs])
        zip_path = os.path.join(dist, f"{name}-market.zip")
        shutil.make_archive(zip_path[:-4], "zip", tmp, name)
        shutil.rmtree(tmp, ignore_errors=True)
        size = os.path.getsize(zip_path) / 1024
        print(f"已打包（上架版）：{zip_path}（{size:.0f} KB，{n} 个文件）")
        print("结构：SKILL.md + references/ + scripts/（无 README、LICENSE、library、config）")
        print("去 open.workbuddy.cn → 创建技能 → 提交这个 zip")
        return

    skip_dirs = {"dist", "__pycache__", ".git"}
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs and d != "library"]
        rel = os.path.relpath(root, ROOT)
        out = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(out, exist_ok=True)
        for f in files:
            if f in ("config.json", ".DS_Store") or f.endswith(".pyc"):
                continue
            shutil.copy2(os.path.join(root, f), os.path.join(out, f))

    # library 只留空目录 + 一份空白模板，绝不带用户卡片
    lib = os.path.join(dst, "library")
    os.makedirs(lib, exist_ok=True)
    open(os.path.join(lib, ".gitkeep"), "w").close()

    zip_path = os.path.join(dist, f"{name}.zip")
    shutil.make_archive(zip_path[:-4], "zip", tmp, name)
    shutil.rmtree(tmp, ignore_errors=True)
    size = os.path.getsize(zip_path) / 1024
    print(f"已打包：{zip_path}（{size:.0f} KB）")
    print("已剔除：config.json、library/ 下所有卡片、__pycache__")
    print("收到的人解压到 ~/.workbuddy/skills/ 即可，首次对话会自动走 setup.py doctor")


def _image_tools():
    """能用来压图的工具（按 env.shrink_jpeg 的降级顺序）。"""
    found = [n for n in ("sips", "magick", "convert", "ffmpeg") if env.which(n)]
    try:
        import PIL  # noqa: F401
        found.append("Pillow")
    except Exception:
        pass
    return found


def _writable(d):
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write_probe")
        with open(probe, "w") as f:
            f.write("1")
        os.remove(probe)
        return True
    except Exception:
        return False


def cmd_env(cfg=None):
    """这台机器上哪些能力有、哪些没有。云沙箱里先看这一屏，就知道能跑到哪一步。"""
    lib = env.library_dir()
    print("== 运行环境 ==\n")
    print(f"平台：{platform.system()} {platform.release()}（{sys.platform}）")
    print(f"Python：{platform.python_version()} · 解释器 {sys.executable}")
    print(f"素材库：{lib}" + ("  可写 ✓" if _writable(lib) else "  **不可写 ✗**"))
    if not _writable(lib):
        print("  → 用环境变量 READING_LIBRARY 指到一个可写目录即可，所有脚本都会跟着走")
    print(f"配置：{cfgmod.CONFIG_PATH}"
          + ("" if os.path.exists(cfgmod.CONFIG_PATH) else "（还没有，首次配置时生成）"))
    print()

    tools = _image_tools()
    chrome = env.chrome_path()
    pdf = env.which("pdftotext")
    lark = env.which("lark-cli")

    def mark(ok, yes, no):
        return f"✓ {yes}" if ok else f"✗ {no}"

    print("能力：")
    print("  网页正文抓取    ✓ 纯标准库（urllib + html.parser），无外部依赖")
    print(f"  书页图片压缩    {mark(bool(tools), '/'.join(tools), '没有压缩工具 → 原样归档，不报错')}")
    print(f"  链接原页面截图  {mark(bool(chrome), chrome, '没有浏览器 → 跳过截图，让用户自己截一张')}")
    print(f"  PDF 读取        {mark(bool(pdf), pdf, '没有 pdftotext → 用 --from-text 交正文')}")
    print(f"  飞书文档读取    {mark(bool(lark), lark, '没有 lark-cli → 飞书链接改成粘贴正文')}")
    print()
    print("分发：")
    print("  本地卡片 + 目录地图  ✓ 永远可用（不依赖任何连接器，云里也能跑）")
    if cfg is not None:
        vault = (cfg.get("obsidian_vault") or "")
        print(f"  Obsidian            {mark(bool(vault) and os.path.isdir(vault), vault, '未配置 → 跳过')}")
        lx = cfg.get("lexiangla") or {}
        print(f"  乐享知识库          {mark(bool(lx.get('folder_entry_id')), '已配置', '未配置 → 跳过')}"
              "（写入由对话内连接器完成，脚本本身不依赖）")
        fc = cfg.get("feishu") or {}
        print(f"  飞书                {mark(bool(fc.get('wiki_parent_token') or fc.get('folder_token')), '已配置', '未配置 → 跳过')}"
              "（同上）")
    print()
    print("结论：核心链路（拍照/链接/粘贴 → 卡片 → 目录地图）只依赖 Python 3.8+ 标准库；")
    print("      截图、压缩、PDF、飞书、两个云端知识库都是可选增强，缺哪个就跳哪个。")


def _make_png(path, w=8, h=8):
    """纯标准库造一张小 PNG，自检时不依赖 Pillow / 任何素材文件。"""
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * w for _ in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(blob)


def cmd_selftest():
    """离线自检：在临时目录里把整条本地链路跑一遍，验证「换一台机器/云沙箱也能用」。

    全程不联网、不碰知识库、不写用户真实素材库，跑完自动清理。
    """
    tmp = tempfile.mkdtemp(prefix="ri_selftest_")
    lib = os.path.join(tmp, "library")
    vault = os.path.join(tmp, "vault")
    os.makedirs(vault, exist_ok=True)
    cfg_path = os.path.join(tmp, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"kb_types": ["obsidian"], "obsidian_vault": vault}, f)

    run_env = dict(os.environ, READING_LIBRARY=lib, READING_CONFIG=cfg_path)

    def run(script, *args):
        return subprocess.run([env.PY, os.path.join(HERE, script)] + list(args),
                              capture_output=True, text=True, env=run_env)

    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"{'✓' if ok else '✗'} {name}" + (f" —— {detail}" if detail else ""))

    print("== 离线自检 ==（临时目录，跑完自动清理）\n")

    # 1. 本地 md → 正文抽取（不走网络）
    src = os.path.join(tmp, "post.md")
    with open(src, "w", encoding="utf-8") as f:
        f.write("# 自检文章\n\n第一段正文，带句号。\n\n第二段正文，也带句号。\n")
    r = run("fetch_source.py", src)
    check("本地文件 → 正文", r.returncode == 0,
          (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "")

    # 2. 图片归档（没有压缩工具时原样归档也算过）
    png = os.path.join(tmp, "page.png")
    _make_png(png)
    r = run("save_page_image.py", png, "--book", "自检", "--page", "1")
    check("图片归档", r.returncode == 0 and "pages/自检_1" in (r.stdout or ""),
          (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "")

    # 3. 卡片 → Obsidian + 目录地图
    card = os.path.join(lib, "2026-01-01_自检_示例.md")
    os.makedirs(lib, exist_ok=True)
    with open(card, "w", encoding="utf-8") as f:
        f.write("---\nbook: 自检书\ntitle: 自检文章\nkind: link\nurl: https://example.com/a\n"
                "paras: all\npage_paras: 2\ndate: 2026-01-01 00:00\ntags: [读书, 自检书]\n"
                "image: pages/自检_1.png\n---\n\n# 自检文章\n\n> 共 2 段。\n\n"
                "## 原文摘录\n\n> **①** 第一段正文，带句号。\n>\n> **②** 第二段正文，也带句号。\n\n"
                "## 当时的想法\n\n自检。\n")
    r = run("save_obsidian.py", card)
    ok = r.returncode == 0 and os.path.exists(os.path.join(vault, os.path.basename(card)))
    check("卡片 → Obsidian（callout/嵌图/回链）", ok,
          (r.stderr or "").strip().splitlines()[-1] if not ok and r.stderr else "")
    r = run("build_map.py")
    mapf = os.path.join(lib, "目录地图.md")
    check("目录地图生成", r.returncode == 0 and os.path.exists(mapf))

    # 4. 截图：有浏览器要求成功，没浏览器要求「干净降级」而不是崩栈
    r = run("save_source_shot.py", "https://example.com", "--slug", "selftest", "--wait", "2")
    if env.chrome_path():
        check("链接截图（有浏览器）", r.returncode in (0, 5),
              "" if r.returncode == 0 else "站点要求验证，属正常降级")
    else:
        check("链接截图（无浏览器 → 应降级）", r.returncode == 3
              and "Traceback" not in (r.stderr or ""),
              (r.stderr or "").strip().splitlines()[0] if r.stderr else "")

    # 5. 配置读写
    r = run("config.py", "targets")
    check("配置读取", r.returncode == 0, (r.stdout or "").strip()[:40])

    bad = [n for n, ok, _ in results if not ok]
    print()
    if bad:
        print(f"自检未通过 {len(bad)} 项：{('、'.join(bad))}")
        print(f"临时目录保留以便排查：{tmp}")
        sys.exit(1)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"全部 {len(results)} 项通过 —— 这台机器可以把核心链路跑通")
    print("（没有的能力见 `setup.py env`，缺哪个会跳过哪个，不会中断流程）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["doctor", "env", "selftest", "detect", "install", "pack"])
    ap.add_argument("target", nargs="?", help="detect 的目标，如 obsidian")
    ap.add_argument("--kb", help="知识库类型：lexiangla / obsidian / feishu / ima")
    ap.add_argument("--vault", help="Obsidian vault 目录")
    ap.add_argument("--app-id")
    ap.add_argument("--app-secret")
    ap.add_argument("--folder-token")
    ap.add_argument("--folder-name")
    ap.add_argument("--wiki", help="飞书知识库链接")
    ap.add_argument("--lark", action="store_true",
                    help="零凭证模式：用飞书连接器（lark-cli）身份接入，不需要 App ID/Secret")

    ap.add_argument("--team", help="乐享团队标识（一般不用填）")
    ap.add_argument("--space-id")
    ap.add_argument("--space-name")
    ap.add_argument("--folder-entry-id")
    ap.add_argument("--folder-entry-name", dest="folder_name_lx")
    ap.add_argument("--map-entry-id")
    ap.add_argument("--map-name", help="目录地图页标题，默认「读书灵感目录地图」")
    ap.add_argument("--market", action="store_true",
                    help="pack 时只出上架包（SKILL.md + references/ + scripts/），"
                         "并校验 open.workbuddy.cn 要求的 frontmatter 八项")
    a = ap.parse_args()

    cfg = cfgmod.load()

    if a.cmd == "doctor":
        cmd_doctor(cfg)
    elif a.cmd == "env":
        cmd_env(cfg)
    elif a.cmd == "selftest":
        cmd_selftest()
    elif a.cmd == "detect":
        cmd_detect_obsidian()
    elif a.cmd == "pack":
        cmd_pack(market=a.market)
    else:
        if a.kb == "obsidian":
            install_obsidian(cfg, a.vault)
        elif a.kb == "feishu":
            install_feishu(cfg, a)
        elif a.kb == "lexiangla":
            install_lexiangla(cfg, a)
        else:
            sys.exit("--kb 只支持 lexiangla / obsidian / feishu")


if __name__ == "__main__":
    main()
