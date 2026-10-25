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
import glob
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


def cmd_selftest(online=False):
    """自检：在临时目录里把本地链路跑一遍。

    默认**完全离线**：不联网、不碰知识库、不写用户素材库，跑完自动清理。
    每个用例都断言**内容**，不只看退出码 —— 旧版只查 returncode，
    所以「同名覆盖」「存档被截断」这类问题一路绿灯，漏到了发布版。
    `--online` 才额外跑联网抓取与截图降级。
    """
    import naming as nm

    tmp = tempfile.mkdtemp(prefix="ri_selftest_")
    lib = os.path.join(tmp, "library")
    vault = os.path.join(tmp, "vault")
    os.makedirs(vault, exist_ok=True)
    cfg_path = os.path.join(tmp, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"kb_types": ["obsidian"], "obsidian_vault": vault}, f)

    run_env = dict(os.environ, READING_LIBRARY=lib, READING_CONFIG=cfg_path)

    def run(script, *args, stdin=None):
        return subprocess.run([env.PY, os.path.join(HERE, script)] + list(args),
                              capture_output=True, text=True, env=run_env, input=stdin)

    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"{'✓' if ok else '✗'} {name}" + (f" —— {detail}" if detail else ""))

    print(f"== 自检（{'含联网' if online else '离线'} · 临时目录 · 跑完自动清理）==\n")

    # 1. 本地文件 → 正文（断言正文内容）
    src = os.path.join(tmp, "post.md")
    with open(src, "w", encoding="utf-8") as f:
        f.write("# 自检文章\n\n第一段正文，带句号。\n\n第二段正文，也带句号。\n")
    r = run("fetch_source.py", src)
    check("本地文件 → 正文", r.returncode == 0 and "第二段正文" in (r.stdout or ""),
          (r.stderr or "").strip()[:80])
    base_srcs = set(glob.glob(os.path.join(lib, "sources", "*.md")))  # 第 1 步自己也存了一份

    # 2. 同一天同标题、不同内容 → 不得互相覆盖（回归：文件名只有日期+标题）
    a_txt, b_txt = os.path.join(tmp, "a.txt"), os.path.join(tmp, "b.txt")
    open(a_txt, "w", encoding="utf-8").write("来源A的正文，长度足够，请记住我。\n\n第二段甲。\n")
    open(b_txt, "w", encoding="utf-8").write("来源B的正文，完全不同的内容。\n\n第二段乙。\n")
    run("fetch_source.py", a_txt, "--title", "同一标题", "--source-name", "来源A")
    run("fetch_source.py", b_txt, "--title", "同一标题", "--source-name", "来源B")
    srcs = sorted(glob.glob(os.path.join(lib, "sources", "*.md")))
    fresh = [x for x in srcs if x not in base_srcs]
    texts = "".join(open(x, encoding="utf-8").read() for x in fresh)
    check("同标题不同内容不覆盖",
          len(fresh) == 2 and "来源A" in texts and "来源B" in texts,
          f"新增 {len(fresh)} 个存档")

    # 3. 同一份内容再存 → 去重，不新增
    r = run("fetch_source.py", a_txt, "--title", "同一标题", "--source-name", "来源A")
    srcs2 = glob.glob(os.path.join(lib, "sources", "*.md"))
    check("同内容重复保存去重",
          len(srcs2) == len(srcs) and "已存过" in (r.stdout or ""),
          f"{len(srcs)} → {len(srcs2)} 个文件")

    # 4. 超长正文：终端只显示前 N 字，存档必须是完整原文
    long_txt = os.path.join(tmp, "long.txt")
    paras = [f"第{i}段：" + ("内容" * 40) for i in range(1, 9)]
    open(long_txt, "w", encoding="utf-8").write("\n\n".join(paras))
    r = run("fetch_source.py", long_txt, "--title", "长文", "--max-chars", "200")
    hit = glob.glob(os.path.join(lib, "sources", "*长文*.md"))
    body = open(hit[0], encoding="utf-8").read() if hit else ""
    full = "第8段" in body and "已截断" not in body
    check("超长正文存档完整", bool(hit) and full,
          "末段在存档里" if full else "存档丢了末尾")

    # 5. 特殊书名：不崩、不越界、frontmatter 能反解回原名
    bad = ["AI/ML", "../escaped", "AI,ML", "读书]管理", "AI: 机器学习"]
    for i, b in enumerate(bad):
        q = b.replace("\\", "\\\\").replace('"', '\\"')
        open(os.path.join(lib, f"2026-01-0{i+1}_卡{i}.md"), "w", encoding="utf-8").write(
            f'---\nbook: "{q}"\ntitle: "文{i}"\nkind: link\nurl: "https://x.com/{i}"\n'
            f'date: "2026-01-0{i+1} 10:00"\nparas: 1\npage_paras: 1\n'
            f'tags: {nm.yaml_list(["读书", "读书/测试"])}\n---\n\n# 文{i}\n\n'
            f'## 原文摘录\n\n> **①** 段落。\n\n## 当时的想法\n\n想法{i}\n')
    r = run("build_map.py", "--vault", vault)
    hubs = glob.glob(os.path.join(lib, "books", "*.md"))
    inside = all(os.path.dirname(os.path.abspath(x)) == os.path.join(lib, "books")
                 for x in hubs)
    escaped = os.path.exists(os.path.join(tmp, "escaped.md"))
    check("特殊书名不崩/不越界",
          r.returncode == 0 and inside and not escaped and len(hubs) == len(bad),
          f"{len(hubs)} 个 Hub" + ("，越界写入！" if escaped else "")
          + ("" if r.returncode == 0 else (r.stderr or "").strip()[:80]))

    # 6. Hub frontmatter 能反解（书名含 : # [ ] , 也不会写坏）
    parsed, tags_ok = True, True
    for x in hubs:
        head = open(x, encoding="utf-8").read().split("---")[1]
        line = [l for l in head.splitlines() if l.startswith("book:")]
        tline = [l for l in head.splitlines() if l.startswith("tags:")]
        if not line or not tline:
            parsed = False
            continue
        if nm.unquote(line[0].partition(":")[2].strip()) not in bad:
            parsed = False
        if len(nm.parse_list(tline[0].partition(":")[2].strip())) != 2:
            tags_ok = False
    check("Hub frontmatter 可反解", parsed and tags_ok and bool(hubs))

    # 7. 卡片 → Obsidian：callout、层级标签、回链都要在
    card0 = os.path.join(lib, "2026-01-01_卡0.md")
    r = run("save_obsidian.py", card0)
    vcopy = os.path.join(vault, "2026-01-01_卡0.md")
    vt = open(vcopy, encoding="utf-8").read() if os.path.exists(vcopy) else ""
    check("卡片 → Obsidian（callout/标签/回链）",
          r.returncode == 0 and "> [!quote]" in vt
          and "读书/AI/ML" in vt and "回到 [[" in vt,
          (r.stderr or "").strip()[:80])

    # 8. 目录地图里的 Hub 链接必须指向真实存在的文件
    maptext = open(os.path.join(lib, "目录地图.md"), encoding="utf-8").read()
    links = re.findall(r"\]\(books/([^)]+\.md)\)", maptext)
    ok = bool(links) and all(os.path.exists(os.path.join(lib, "books", x)) for x in links)
    check("目录地图链接有效", ok, f"{len(links)} 条 Hub 链接")

    # 9. 投递台账：重复 begin 不再新建，commit 后 pending 清空
    card = os.path.join(lib, "2026-01-01_卡0.md")
    r1 = run("delivery.py", "begin", card, "--kb", "feishu", "--parent", "P", "--title", "T")
    did = (r1.stdout or "").split()[0] if (r1.stdout or "").strip() else ""
    run("delivery.py", "commit", did, "--remote-id", "docX", "--url", "https://x/docX")
    run("delivery.py", "begin", card, "--kb", "feishu", "--parent", "P")
    show = run("delivery.py", "show", card)
    pend = run("delivery.py", "pending")
    rows = [l for l in (show.stdout or "").splitlines() if "feishu" in l]
    check("投递台账幂等",
          bool(did) and len(rows) == 1 and "committed" in rows[0]
          and "没有待核对" in (pend.stdout or ""),
          f"{len(rows)} 条记录")

    # 10. 配置读取
    r = run("config.py", "targets")
    check("配置读取", r.returncode == 0, (r.stdout or "").strip()[:40])


    # ---- 连接器投递：离线桩（**会校验必填参数**，和真实 CLI 一致）----
    # 上一版桩程序对任何参数都答 ok，所以 `wiki +node-list` 少了必填的 --space-id
    # 也能「通过」——测试是假绿的。桩必须比被测代码更严格。
    stub_dir = os.path.join(tmp, "bin")
    os.makedirs(stub_dir, exist_ok=True)
    stub_state = os.path.join(tmp, "stub.json")
    stub_py = os.path.join(stub_dir, "lark-cli")
    with open(stub_py, "w", encoding="utf-8") as f:
        f.write(r"""#!/usr/bin/env python3
import json, os, re, sys
STATE = os.environ["STUB_STATE"]

def load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"docs": {}, "nodes": [], "creates": 0, "updates": 0, "fetch_mode": "ok"}

def save(d):
    json.dump(d, open(STATE, "w"), ensure_ascii=False)

def opt(name, default=""):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

def out(obj):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(0 if obj.get("ok") else 1)

a = sys.argv[1:]
d = load()

if a[:2] == ["docs", "+create"]:
    body = sys.stdin.read() if opt("--content") == "-" else opt("--content")
    if os.environ.get("STUB_FAIL_CREATE"):
        out({"ok": False, "error": {"type": "server", "message": "stub create failure"}})
    d["creates"] = d.get("creates", 0) + 1
    doc_id = "doc%d" % d["creates"]
    d["docs"][doc_id] = {"content": body, "title": opt("--title"),
                         "parent": opt("--parent-token")}
    d["nodes"].append({"node_token": "wik" + doc_id, "obj_token": doc_id,
                       "title": opt("--title"), "parent": opt("--parent-token")})
    save(d)
    out({"ok": True, "data": {"document": {"document_id": doc_id,
        "url": "https://ex.feishu.cn/docx/" + doc_id}}})

elif a[:2] == ["docs", "+fetch"]:
    d["fetches"] = d.get("fetches", 0) + 1
    save(d)
    if d.get("fetch_mode") == "fail":
        out({"ok": False, "error": {"type": "server", "message": "stub fetch failure"}})
    doc = opt("--doc")
    info = d["docs"].get(doc) or {}
    content = info.get("content", "")
    if d.get("fetch_mode") == "empty":
        content = ""
    elif d.get("fetch_mode") == "plain":
        content = re.sub(r"\]\((https?://[^)]+)\)", "]", content)
    out({"ok": True, "data": {"document": {"content": content}}})

elif a[:2] == ["docs", "+update"]:
    body = sys.stdin.read() if opt("--content") == "-" else opt("--content")
    d["updates"] = d.get("updates", 0) + 1
    d["last_update"] = body
    d["docs"].setdefault(opt("--doc"), {})["content"] = body
    save(d)
    out({"ok": True, "data": {"document_id": opt("--doc")}})

elif a[:2] == ["wiki", "+node-get"]:
    tok = opt("--node-token")
    node = tok if tok.startswith("wik") else "wik" + tok
    out({"ok": True, "data": {"node_token": node,
        "space_id": os.environ.get("STUB_SPACE", "space1")}})

elif a[:2] == ["wiki", "+node-list"]:
    if not opt("--space-id"):
        out({"ok": False, "error": {"type": "validation",
            "subtype": "invalid_argument",
            "message": "required flag(s) 'space-id' not set"}})
    parent = opt("--parent-node-token")
    items = [n for n in d["nodes"] if (n.get("parent") or "") == parent]
    out({"ok": True, "data": {"items": items}})

else:
    out({"ok": False, "error": {"type": "unsupported", "message": " ".join(a)}})
""")
    os.chmod(stub_py, 0o755)

    def stub_reset(**kw):
        d = {"docs": {}, "nodes": [], "creates": 0, "updates": 0, "fetch_mode": "ok"}
        d.update(kw)
        with open(stub_state, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)

    def stub_get(key=None, default=None):
        """key=None 时返回整个桩状态；否则返回单个字段。"""
        try:
            with open(stub_state, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return default if key else {}
        return data if key is None else data.get(key, default)

    stub_reset()

    def run_in(env2, script, *args, stdin=None):
        return subprocess.run([env.PY, os.path.join(HERE, script)] + list(args),
                              capture_output=True, text=True, env=env2, input=stdin)

    # 飞书连接器模式用独立库，别和前面的用例互相干扰
    flib = os.path.join(tmp, "flib")
    os.makedirs(flib, exist_ok=True)
    fcfg = os.path.join(tmp, "fcfg.json")
    with open(fcfg, "w", encoding="utf-8") as f:
        json.dump({"kb_types": ["feishu"], "feishu": {
            "mode": "lark-cli", "wiki_parent_token": "parentX",
            "wiki_space_id": "spaceX", "wiki_index_doc": "docINDEX",
            "domain": "ex.feishu.cn"}}, f)
    fenv = dict(os.environ, READING_LIBRARY=flib, READING_CONFIG=fcfg,
                PATH=stub_dir + os.pathsep + os.environ.get("PATH", ""),
                STUB_STATE=stub_state)

    def mkcard(lib_dir, fn, title, book="测试书", thought="想法一"):
        with open(os.path.join(lib_dir, fn), "w", encoding="utf-8") as f:
            f.write(f'---\nbook: {nm.quote(book)}\ntitle: {nm.quote(title)}\n'
                    f'kind: link\nurl: "https://x.com/1"\n'
                    f'date: "2026-01-01 10:00"\nparas: 1\npage_paras: 1\n---\n\n'
                    f'# {title}\n\n## 原文摘录\n\n> **①** 段落。\n\n'
                    f'## 当时的想法\n\n{thought}\n')

    # 11. 同一链接内容更新：另存一版，旧原文不能被覆盖
    post = os.path.join(tmp, "post.txt")
    with open(post, "w", encoding="utf-8") as f:
        f.write("这是同一个链接的旧版本正文，足够长，内容甲。\n\n第二段甲。\n")
    run("fetch_source.py", "--from-text", post, "--title", "会变的文章",
        "--url-for-text", "https://blog.example.com/same")
    with open(post, "w", encoding="utf-8") as f:
        f.write("这是同一个链接的新版本正文，足够长，内容乙。\n\n第二段乙。\n")
    run("fetch_source.py", "--from-text", post, "--title", "会变的文章",
        "--url-for-text", "https://blog.example.com/same")
    vers = glob.glob(os.path.join(lib, "sources", "*会变的文章*"))
    vtext = "".join(open(x, encoding="utf-8").read() for x in vers)
    check("同链接更新保留旧版本",
          len(vers) == 2 and "内容甲" in vtext and "内容乙" in vtext,
          f"{len(vers)} 个存档")

    # 12. 崩溃恢复：远端确已建成本卡 → 沿用；查不成 → 停下等核对，不许新建
    c1 = os.path.join(flib, "2026-01-01_卡甲.md")
    mkcard(flib, "2026-01-01_卡甲.md", "书 · 第 1 页", thought="甲的想法")
    run_in(fenv, "save_feishu_lark.py", c1)
    created = stub_get("creates", 0)
    # 模拟「远端建成、本地台账还停在 intent」
    with open(os.path.join(flib, ".delivery.json"), encoding="utf-8") as f:
        led = json.load(f)
    for rec in led["deliveries"]:
        rec.update({"status": "intent", "remote_id": "", "url": ""})
    with open(os.path.join(flib, ".delivery.json"), "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False)
    r = run_in(fenv, "save_feishu_lark.py", c1)
    check("崩溃后沿用本卡文档（不重建）",
          stub_get("creates", 0) == created and "沿用" in (r.stderr or ""),
          f"创建 {stub_get('creates', 0)} 次（期望 {created}）")

    # 13. 查不成时不许当成「不存在」：去掉 space_id 且反查不到 → 未投递
    with open(fcfg, encoding="utf-8") as f:
        cfg2 = json.load(f)
    cfg2["feishu"].pop("wiki_space_id", None)
    with open(fcfg, "w", encoding="utf-8") as f:
        json.dump(cfg2, f, ensure_ascii=False)
    with open(os.path.join(flib, ".delivery.json"), encoding="utf-8") as f:
        led = json.load(f)
    for rec in led["deliveries"]:
        rec.update({"status": "intent", "remote_id": "", "url": ""})
    with open(os.path.join(flib, ".delivery.json"), "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False)
    before = stub_get("creates", 0)
    env2 = dict(fenv, STUB_SPACE="")
    r = run_in(env2, "save_feishu_lark.py", c1)
    check("核对不成时不新建（保留待核对）",
          r.returncode == 4 and stub_get("creates", 0) == before,
          f"退出码 {r.returncode}，创建 {stub_get('creates', 0)} 次")
    with open(fcfg, "w", encoding="utf-8") as f:
        cfg2["feishu"]["wiki_space_id"] = "spaceX"
        json.dump(cfg2, f, ensure_ascii=False)

    # 13b. 候选正文读不出来 ≠ 不是本卡：必须停在待核对，不能新建
    #      （doc_has_marker 返回 None 被吞成 ambiguous，就会又建一份）
    with open(os.path.join(flib, ".delivery.json"), encoding="utf-8") as f:
        led = json.load(f)
    for rec in led["deliveries"]:
        if rec.get("card_id") == "2026-01-01_卡甲":
            rec.update({"status": "intent", "remote_id": "", "url": ""})
    with open(os.path.join(flib, ".delivery.json"), "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False)
    st = stub_get() or {}
    st["fetch_mode"] = "fail"
    with open(stub_state, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    before = stub_get("creates", 0)
    r = run_in(fenv, "save_feishu_lark.py", c1)
    check("候选正文读不出时不新建",
          r.returncode == 4 and stub_get("creates", 0) == before
          and "读不出来" in (r.stderr or ""),
          f"退出码 {r.returncode}，新建 {stub_get('creates', 0) - before} 份")
    st = stub_get() or {}
    st["fetch_mode"] = "ok"
    with open(stub_state, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)

    # 14. 同一页的两张卡（一级标题相同）不能互相认领
    #     关键是先给乙登记 intent，否则走的是「新卡」分支，压根不触发核对
    c2 = os.path.join(flib, "2026-01-02_卡乙.md")
    mkcard(flib, "2026-01-02_卡乙.md", "书 · 第 1 页", thought="乙的想法")
    run_in(fenv, "delivery.py", "begin", c2, "--kb", "feishu",
           "--parent", "parentX", "--title", "书 · 第 1 页")
    before = stub_get("creates", 0)
    r = run_in(fenv, "save_feishu_lark.py", c2)
    docs = stub_get("docs", {}) or {}
    got = [d for d in docs.values() if "乙的想法" in (d.get("content") or "")]
    check("同标题不同卡不互相认领",
          stub_get("creates", 0) == before + 1 and got and len(got) == 1,
          f"新建 {stub_get('creates', 0) - before} 份，乙的内容落在自己文档里"
          if got else "乙的内容丢了")

    # 14b. 卡片 ID 只是别人文档标识的**前缀**时不能认领
    #      （marker in content 这种子串判断，会让 card 命中 card2 的文档）
    c4 = os.path.join(flib, "2026-01-04_书页.md")
    mkcard(flib, "2026-01-04_书页.md", "书 · 第 4 页", thought="丁的想法")
    st = stub_get() or {}
    st.setdefault("docs", {})["docPX"] = {
        "content": "别人写的文档。\n\n> 卡片：2026-01-04_书页2\n",
        "title": "书 · 第 4 页", "parent": "parentX"}
    st.setdefault("nodes", []).append({
        "node_token": "wikPX", "obj_token": "docPX",
        "title": "书 · 第 4 页", "parent": "parentX"})
    with open(stub_state, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    run_in(fenv, "delivery.py", "begin", c4, "--kb", "feishu",
           "--parent", "parentX", "--title", "书 · 第 4 页")
    before = stub_get("creates", 0)
    r = run_in(fenv, "save_feishu_lark.py", c4)
    ct4 = open(c4, encoding="utf-8").read()
    check("前缀相同的卡片 ID 不认领",
          stub_get("creates", 0) == before + 1 and "wikPX" not in ct4,
          f"新建 {stub_get('creates', 0) - before} 份，"
          f"{'认错成别人的文档' if 'wikPX' in ct4 else '用的是自己的新文档'}")

    # 15. 台账已 committed 但链接没写回 → 重跑要补写
    with open(c2, encoding="utf-8") as f:
        ct = f.read()
    ct = re.sub(r"^dest_feishu:.*$", "", ct, flags=re.M)
    with open(c2, "w", encoding="utf-8") as f:
        f.write(ct)
    before = stub_get("creates", 0)
    run_in(fenv, "save_feishu_lark.py", c2)
    ct = open(c2, encoding="utf-8").read()
    check("已投递但缺链接时补写",
          "dest_feishu:" in ct and stub_get("creates", 0) == before,
          "dest_feishu 已补回" if "dest_feishu:" in ct else "仍缺字段")

    # 16. 旧卡片有 dest_feishu、没有台账 → 接纳，不重复创建
    c3 = os.path.join(flib, "2026-01-03_卡丙.md")
    mkcard(flib, "2026-01-03_卡丙.md", "书 · 第 9 页", thought="丙的想法")
    with open(c3, "a", encoding="utf-8") as f:
        pass
    txt = open(c3, encoding="utf-8").read().replace(
        'page_paras: 1\n', 'page_paras: 1\ndest_feishu: "https://ex.feishu.cn/wiki/wikOLD"\n')
    with open(c3, "w", encoding="utf-8") as f:
        f.write(txt)
    before = stub_get("creates", 0)
    r = run_in(fenv, "save_feishu_lark.py", c3)
    check("旧卡片有链接则接纳",
          stub_get("creates", 0) == before and "wikOLD" in (r.stdout or ""),
          (r.stdout or "").strip()[:40])

    # 17. reset 必须清得掉带父节点的正常记录
    r = run_in(fenv, "delivery.py", "reset", c3, "--kb", "feishu")
    check("reset 能清掉正常记录", "已清除 1 条" in (r.stdout or ""),
          (r.stdout or "").strip()[:40])

    # 17b. reset 之后必须真的重投：卡片里的旧 dest_feishu 不能又被「接纳」回去
    before = stub_get("creates", 0)
    r = run_in(fenv, "save_feishu_lark.py", c3)
    ct3 = open(c3, encoding="utf-8").read()
    led3 = json.load(open(os.path.join(flib, ".delivery.json"), encoding="utf-8"))
    tomb3 = [t for t in led3.get("redeliver", [])
             if t.get("card_id") == "2026-01-03_卡丙"]
    check("reset 后能强制重投",
          stub_get("creates", 0) == before + 1 and "wikOLD" not in ct3 and not tomb3,
          f"创建 {stub_get('creates', 0) - before} 次，"
          f"{'链接仍是旧的' if 'wikOLD' in ct3 else '已换成新链接'}，"
          f"墓碑={'残留' if tomb3 else '已清除'}")

    # 17c. reset 后**首次重投失败**、第二次重试：不能把上一代的旧文档认回来
    #      （只按 card_id 记账时，旧文档的标识也对得上，重投意图就丢了）
    c5 = os.path.join(flib, "2026-01-05_卡戊.md")
    mkcard(flib, "2026-01-05_卡戊.md", "书 · 第 5 页", thought="戊的想法")
    r0 = run_in(fenv, "save_feishu_lark.py", c5)
    url_old5 = (r0.stdout or "").strip().splitlines()
    url_old5 = url_old5[0] if url_old5 else ""
    run_in(fenv, "delivery.py", "reset", c5, "--kb", "feishu")
    before = stub_get("creates", 0)
    r1 = run_in(dict(fenv, STUB_FAIL_CREATE="1"), "save_feishu_lark.py", c5)
    r2 = run_in(fenv, "save_feishu_lark.py", c5)
    ct5 = open(c5, encoding="utf-8").read()
    check("reset 首投失败后重试不认旧文档",
          r2.returncode == 0 and stub_get("creates", 0) == before + 1
          and url_old5 and url_old5 not in ct5,
          f"首投{'未建成(预期)' if r1.returncode != 0 else '意外成功'}，"
          f"重试新建 {stub_get('creates', 0) - before} 份，"
          f"{'仍指向旧文档' if url_old5 in ct5 else '已换新文档'}")

    # 17d. --force 创建失败（远端可能已建成）时，台账不能还停在「已建成 + 旧链接」
    #      否则下一次重试会直接返回旧链接，用户以为重投成功
    c6 = os.path.join(flib, "2026-01-06_卡己.md")
    mkcard(flib, "2026-01-06_卡己.md", "书 · 第 6 页", thought="己的想法")
    r0 = run_in(fenv, "save_feishu_lark.py", c6)
    url_old6 = (r0.stdout or "").strip().splitlines()
    url_old6 = url_old6[0] if url_old6 else ""
    before = stub_get("creates", 0)
    run_in(dict(fenv, STUB_FAIL_CREATE="1"), "save_feishu_lark.py", c6, "--force")
    led6 = json.load(open(os.path.join(flib, ".delivery.json"), encoding="utf-8"))
    rec6 = next((d for d in led6["deliveries"]
                 if d.get("card_id") == "2026-01-06_卡己"), {})
    still_committed = (rec6.get("status") == "committed"
                       and (rec6.get("url") or "") == url_old6)
    r2 = run_in(fenv, "save_feishu_lark.py", c6)
    ct6 = open(c6, encoding="utf-8").read()
    check("--force 失败后台账不沿用旧链接",
          not still_committed and url_old6 and url_old6 not in ct6
          and stub_get("creates", 0) == before + 1,
          f"台账={'仍 committed 旧链接' if still_committed else rec6.get('status')}"
          f"(epoch {rec6.get('epoch')})，重试新建 "
          f"{stub_get('creates', 0) - before} 份")

    # 17e. 台账记录比墓碑旧（上一版记录没有 superseded 字段）时，
    #      也必须靠墓碑里的「已作废文档」把旧文档排除掉
    c7 = os.path.join(flib, "2026-01-07_卡庚.md")
    mkcard(flib, "2026-01-07_卡庚.md", "书 · 第 7 页", thought="庚的想法")
    run_in(fenv, "save_feishu_lark.py", c7)
    run_in(fenv, "delivery.py", "reset", c7, "--kb", "feishu")
    run_in(fenv, "delivery.py", "begin", c7, "--kb", "feishu",
           "--parent", "parentX", "--title", "书 · 第 7 页")
    led7 = json.load(open(os.path.join(flib, ".delivery.json"), encoding="utf-8"))
    for d in led7["deliveries"]:
        if d.get("card_id") == "2026-01-07_卡庚":
            d.pop("superseded", None)   # 模拟旧版台账：没这个字段
    with open(os.path.join(flib, ".delivery.json"), "w", encoding="utf-8") as f:
        json.dump(led7, f, ensure_ascii=False)
    before = stub_get("creates", 0)
    r = run_in(fenv, "save_feishu_lark.py", c7)
    check("旧记录无 superseded 也认墓碑",
          stub_get("creates", 0) == before + 1,
          f"新建 {stub_get('creates', 0) - before} 份"
          f"（0 份说明又认领了被取代的旧文档）")

    # 17f. 连续两代之后，被取代的文档要一直留在作废清单里
    #      （否则下一代重投失败时，核对又会把第一代的旧文档捡回来）
    c8 = os.path.join(flib, "2026-01-08_卡辛.md")
    mkcard(flib, "2026-01-08_卡辛.md", "书 · 第 8 页", thought="辛的想法")
    r0 = run_in(fenv, "save_feishu_lark.py", c8)
    url_a = ((r0.stdout or "").strip().splitlines() or [""])[0]
    run_in(fenv, "delivery.py", "reset", c8, "--kb", "feishu")
    r1 = run_in(fenv, "save_feishu_lark.py", c8)
    url_b = ((r1.stdout or "").strip().splitlines() or [""])[0]
    before = stub_get("creates", 0)
    run_in(dict(fenv, STUB_FAIL_CREATE="1"), "save_feishu_lark.py", c8, "--force")
    led8 = json.load(open(os.path.join(flib, ".delivery.json"), encoding="utf-8"))
    rec8 = next((d for d in led8["deliveries"]
                 if d.get("card_id") == "2026-01-08_卡辛"), {})
    sup8 = rec8.get("superseded") or []
    run_in(fenv, "save_feishu_lark.py", c8)
    ct8 = open(c8, encoding="utf-8").read()
    check("作废清单跨代次累积",
          len(sup8) >= 2 and url_a and url_b and url_a != url_b
          and stub_get("creates", 0) == before + 1
          and url_a not in ct8 and url_b not in ct8,
          f"作废 {len(sup8)} 个 id，重投新建 {stub_get('creates', 0) - before} 份，"
          f"{'又捡回旧文档' if (url_a in ct8 or url_b in ct8) else '未回退'}")

    # 18. Obsidian：脚本自己记 dest_obsidian + 台账
    ocard = os.path.join(hlib := os.path.join(tmp, "hlib"), "2026-01-01_卡甲.md")
    os.makedirs(hlib, exist_ok=True)
    hvault = os.path.join(tmp, "hvault")
    os.makedirs(hvault, exist_ok=True)
    hcfg = os.path.join(tmp, "hcfg.json")
    with open(hcfg, "w", encoding="utf-8") as f:
        json.dump({"kb_types": ["obsidian"], "obsidian_vault": hvault}, f)
    henv = dict(os.environ, READING_LIBRARY=hlib, READING_CONFIG=hcfg)
    mkcard(hlib, "2026-01-01_卡甲.md", "同名书 · 第 1 页", book="AI/ML", thought="甲")
    mkcard(hlib, "2026-01-02_卡乙.md", "同名书 · 第 1 页", book="AI,ML", thought="乙")
    # 先保存 AI/ML 那张（它在分配里排后，只能拿到带哈希的名字）；
    # 若只看自己这一本，会误得干净名字 AI_ML，正好撞到 AI,ML 的 Hub
    run_in(henv, "save_obsidian.py", os.path.join(hlib, "2026-01-01_卡甲.md"))
    otext = open(os.path.join(hlib, "2026-01-01_卡甲.md"), encoding="utf-8").read()
    lead = os.path.join(hlib, ".delivery.json")
    has_led = os.path.exists(lead) and any(
        d.get("kb") == "obsidian" and d.get("status") == "committed"
        for d in json.load(open(lead, encoding="utf-8"))["deliveries"]) if os.path.exists(lead) else False
    check("Obsidian 自动记账", "dest_obsidian:" in otext and has_led,
          "已写回并登记台账" if ("dest_obsidian:" in otext and has_led) else
          f"dest={'有' if 'dest_obsidian:' in otext else '缺'} 台账={'有' if has_led else '缺'}")

    # 19. 先保存卡片、后建地图：回链必须指向自己那本 Hub（AI/ML 不能串到 AI,ML）
    vtext2 = open(os.path.join(hvault, "2026-01-01_卡甲.md"), encoding="utf-8").read()
    mt = re.search(r"回到 \[\[(.+?)\]\]", vtext2)
    target = mt.group(1) if mt else ""
    owner = ""
    for fn in glob.glob(os.path.join(hvault, "*.md")):
        base = os.path.basename(fn)[:-3]
        if base != target:
            continue
        head = open(fn, encoding="utf-8").read().split("---")
        if len(head) > 2:
            line = [l for l in head[1].splitlines() if l.startswith("book:")]
            if line:
                owner = nm.unquote(line[0].partition(":")[2].strip())
    check("Hub 回链不串书",
          owner == "AI/ML" and os.path.exists(os.path.join(hvault, target + ".md")),
          f"回链指向 book={owner!r}，文件{'存在' if os.path.exists(os.path.join(hvault, target + '.md')) else '不存在'}")

    # 19b. Hub 的「冲突检查 / 实际写入 / 回链」必须是同一个文件名
    #      旧版检查的是「书名 · 灵感总览.md」，写下去的却是「书名.md」，
    #      vault 里用户手写的同名笔记会被直接覆盖，而且脚本还报成功。
    nlib = os.path.join(tmp, "nlib")
    nvault = os.path.join(tmp, "nvault")
    os.makedirs(nlib, exist_ok=True)
    os.makedirs(nvault, exist_ok=True)
    ncfg = os.path.join(tmp, "ncfg.json")
    with open(ncfg, "w", encoding="utf-8") as f:
        json.dump({"kb_types": ["obsidian"], "obsidian_vault": nvault}, f)
    nenv = dict(os.environ, READING_LIBRARY=nlib, READING_CONFIG=ncfg)
    mkcard(nlib, "2026-02-01_卡A.md", "重名书 · 第 1 页", book="重名书", thought="甲")
    user_note = os.path.join(nvault, "重名书.md")
    note_text = "# 我自己写的笔记\n\n别动我。\n"
    with open(user_note, "w", encoding="utf-8") as f:
        f.write(note_text)
    hand_hub = os.path.join(nvault, "重名书 · 灵感总览.md")
    hand_text = "手写的同名总览，没有 frontmatter。\n"
    with open(hand_hub, "w", encoding="utf-8") as f:
        f.write(hand_text)
    run_in(nenv, "save_obsidian.py", os.path.join(nlib, "2026-02-01_卡A.md"))
    note_ok = open(user_note, encoding="utf-8").read() == note_text
    hand_ok = open(hand_hub, encoding="utf-8").read() == hand_text
    map_text = open(os.path.join(nvault, "读书灵感目录地图.md"), encoding="utf-8").read()
    mt2 = re.search(r"\[\[(.+?)\]\]", map_text)
    hub_target = mt2.group(1) if mt2 else ""
    hub_file = os.path.join(nvault, hub_target + ".md") if hub_target else ""
    bt = re.search(r"回到 \[\[(.+?)\]\]",
                   open(os.path.join(nvault, "2026-02-01_卡A.md"), encoding="utf-8").read())
    backlink = bt.group(1) if bt else ""
    ok19b = (note_ok and hand_ok and hub_target and os.path.exists(hub_file)
             and backlink == hub_target)
    check("Hub 不覆盖既有笔记且三处同名", ok19b,
          f"用户笔记={'完好' if note_ok and hand_ok else '被改'}"
          f"，Hub={hub_target!r}，回链={'一致' if backlink == hub_target else backlink!r}")

    # 19c. 手写总览就算写了**同样的 book 字段**，也不是我们的生成物
    #      归属判断不能只看 book：那样会把用户正文当自己的 Hub 重写掉
    glib = os.path.join(tmp, "glib")
    gvault = os.path.join(tmp, "gvault")
    os.makedirs(glib, exist_ok=True)
    os.makedirs(gvault, exist_ok=True)
    gcfg = os.path.join(tmp, "gcfg.json")
    with open(gcfg, "w", encoding="utf-8") as f:
        json.dump({"kb_types": ["obsidian"], "obsidian_vault": gvault}, f)
    genv = dict(os.environ, READING_LIBRARY=glib, READING_CONFIG=gcfg)
    mkcard(glib, "2026-03-01_卡B.md", "同名字段 · 第 1 页", book="同名字段", thought="乙")
    hand2 = os.path.join(gvault, "同名字段 · 灵感总览.md")
    hand2_text = '---\nbook: "同名字段"\n---\n\n我自己写的总览，别动。\n'
    with open(hand2, "w", encoding="utf-8") as f:
        f.write(hand2_text)
    # 连哈希名也被手写文件占着：必须继续让位到 _2，而不是覆盖
    h2 = nm.hash6("同名字段")
    hand3 = os.path.join(gvault, f"同名字段_{h2} · 灵感总览.md")
    hand3_text = "第二个手写文件，也别动。\n"
    with open(hand3, "w", encoding="utf-8") as f:
        f.write(hand3_text)
    run_in(genv, "save_obsidian.py", os.path.join(glib, "2026-03-01_卡B.md"))
    kept = open(hand2, encoding="utf-8").read() == hand2_text
    kept3 = open(hand3, encoding="utf-8").read() == hand3_text
    gmap = open(os.path.join(gvault, "读书灵感目录地图.md"), encoding="utf-8").read()
    gt = re.search(r"\[\[(.+?)\]\]", gmap)
    gtarget = gt.group(1) if gt else ""
    gpath = os.path.join(gvault, gtarget + ".md") if gtarget else ""
    check("手写总览带同名字段也不覆盖",
          kept and kept3 and gtarget and gtarget != "同名字段 · 灵感总览"
          and "_2 · 灵感总览" in gtarget and os.path.exists(gpath)
          and "generator: reading-inspiration" in open(gpath, encoding="utf-8").read(),
          f"手写={'完好' if kept and kept3 else '被覆盖'}，Hub={gtarget!r}")

    # 20. 索引页：不带本地死链；回读按预期链接核对
    with open(os.path.join(flib, "目录地图.md"), "w", encoding="utf-8") as f:
        f.write("# 读书灵感目录地图\n\n> 更新于今天\n\n## 《书》 (1)\n\n"
                "> 总览：《书》· 灵感总览 → books/书.md\n\n"
                "- 2026-01-01 [卡甲](https://ex.feishu.cn/wiki/wikAAA)\n")
    stub_reset()
    r = run_in(fenv, "setup_feishu_wiki.py", "index")
    upd = stub_get("last_update", "") or ""
    check("索引页去掉本地死链",
          r.returncode == 0 and "总览：" not in upd and "books/书.md" not in upd,
          (r.stderr or "").strip()[:60])
    st = stub_get() or {}
    st["fetch_mode"] = "plain"
    with open(stub_state, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    r = run_in(fenv, "setup_feishu_wiki.py", "index")
    check("回读发现链接丢失要告警", "个链接在文档里找不到" in (r.stderr or ""),
          (r.stderr or "").strip()[:60])
    st = stub_get() or {}
    st["fetch_mode"] = "fail"
    with open(stub_state, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    r = run_in(fenv, "setup_feishu_wiki.py", "index")
    check("回读失败要说明未验证", "未验证" in (r.stderr or ""),
          (r.stderr or "").strip()[:60])

    # 21. 应用身份刷新索引页复用同一份内容
    #     这条路要 App Secret + 真实 API，离线跑不了端到端，只能锁住结构：
    #     一旦有人改回直接读原始地图，本地 Hub 死链又会被写进远端。
    wsrc = open(os.path.join(HERE, "setup_feishu_wiki.py"), encoding="utf-8").read()
    seg = wsrc[wsrc.index("def cmd_index(token, doc_id):"):]
    seg = seg.split("\ndef ")[0]
    check("应用身份索引页复用统一内容",
          "index_markdown()" in seg and "目录地图.md" not in seg,
          "走的 index_markdown()" if "index_markdown()" in seg else "仍在直接读原始地图")

    # 21b. 应用身份列目录：业务错误码不能被读成「目录是空的」而继续新建
    #      飞书无权限时常常是 HTTP 200 + code=131006 + 没有 files。
    probe = os.path.join(tmp, "probe_app.py")
    with open(probe, "w", encoding="utf-8") as f:
        f.write(
            "import sys\n"
            f"sys.path.insert(0, {HERE!r})\n"
            "import save_feishu as sf\n"
            "for c in ({'code': 131006, 'msg': 'permission denied'},\n"
            "          {'code': 131006, 'data': {'files': []}},\n"
            "          {'code': 0, 'data': {}},\n"
            "          {'code': 0, 'data': {'files': []}},\n"
            "          {'code': 0, 'data': {'files': [{'name': 'x', 'type': 'docx',\n"
            "                                           'token': 'tok1'},\n"
            "                                          {'name': 'x', 'type': 'docx',\n"
            "                                           'token': 'tok2'}]}}):\n"
            "    sf.req_json = lambda *a, **k: c\n"
            "    st, toks = sf.find_drive_doc('t', 'folder', 'x')\n"
            "    print(st, len(toks))\n")
    r = subprocess.run([env.PY, probe], capture_output=True, text=True, env=run_env)
    got = (r.stdout or "").split()
    check("应用身份业务错误码不当空目录",
          got == ["error", "0", "error", "0", "error", "0", "absent", "0",
                  "found", "2"], f"得到 {got}")

    # 21c. 写 Hub 的最后一道闸：目标位置是别人的笔记时必须拒绝，而不是覆盖
    #      （naming 已经保证了这点，但这条断言让「闸」本身也能被单独验证）
    probe2 = os.path.join(tmp, "probe_hub_guard.py")
    guard_file = os.path.join(nvault, "重名书.md")
    with open(probe2, "w", encoding="utf-8") as f:
        f.write(
            "import sys\n"
            f"sys.path.insert(0, {HERE!r})\n"
            "import build_map as bm\n"
            f"target = {guard_file!r}\n"
            "try:\n"
            "    bm.write_hubs({'重名书': []}, {'重名书': target})\n"
            "    print('NO-GUARD')\n"
            "except RuntimeError:\n"
            "    print('GUARD')\n"
            f"print(open({guard_file!r}, encoding='utf-8').read() == {note_text!r})\n")
    r = subprocess.run([env.PY, probe2], capture_output=True, text=True, env=nenv)
    out2 = (r.stdout or "").split()
    check("写 Hub 拒绝覆盖别人的笔记",
          out2 == ["GUARD", "True"], f"得到 {out2}")

    # 21d. 应用身份路径同样要按代次核对：重投失败后重试，不能认领旧文档
    #      这条全靠 mock（离线跑不到真实 API），但走的正是 main() 的分支逻辑
    alib = os.path.join(tmp, "alib")
    os.makedirs(alib, exist_ok=True)
    acfg = os.path.join(tmp, "acfg.json")
    with open(acfg, "w", encoding="utf-8") as f:
        json.dump({"kb_types": ["feishu"], "feishu": {
            "app_id": "id", "app_secret": "sec", "folder_token": "FOLDER",
            "wiki_space_id": "", "domain": "ex.feishu.cn"}}, f)
    aenv = dict(os.environ, READING_LIBRARY=alib, READING_CONFIG=acfg)
    probe3 = os.path.join(tmp, "probe_app_redeliver.py")
    with open(probe3, "w", encoding="utf-8") as f:
        f.write(
            "import json, os, sys\n"
            f"sys.path.insert(0, {HERE!r})\n"
            "import save_feishu as sf, delivery as dl\n"
            "lib = os.environ['READING_LIBRARY']\n"
            "card = os.path.join(lib, 'app_card.md')\n"
            "open(card, 'w', encoding='utf-8').write(\n"
            "    '---\\nbook: \"书\"\\n---\\n\\n# 书 · 第 1 页\\n\\n## 当时的想法\\n\\n想法。\\n')\n"
            "st = {'import_calls': 0, 'fail_first': True}\n"
            "class R:\n"
            "    def __init__(self, o): self.b = json.dumps(o).encode()\n"
            "    def read(self): return self.b\n"
            "    def __enter__(self): return self\n"
            "    def __exit__(self, *a): return False\n"
            "def fake_urlopen(req, body=None):\n"
            "    u = req.full_url\n"
            "    if 'upload_all' in u: return R({'code': 0, 'data': {'file_token': 'FT'}})\n"
            "    if 'permissions' in u: return R({'code': 0})\n"
            "    raise AssertionError(u)\n"
            "def fake_req(path, method, token, data=None):\n"
            "    if 'tenant_access_token' in path: return {'code': 0, 'tenant_access_token': 'T'}\n"
            "    if path.endswith('/import_tasks') and method == 'POST':\n"
            "        st['import_calls'] += 1\n"
            "        if st['fail_first']: return {'code': 999, 'msg': 'stub import failure'}\n"
            "        return {'code': 0, 'data': {'ticket': 'T1'}}\n"
            "    if '/import_tasks/' in path:\n"
            "        return {'code': 0, 'data': {'result': {'job_status': 0, 'token': 'NEW_DOC'}}}\n"
            "    if '/drive/v1/files' in path:\n"
            "        return {'code': 0, 'data': {'files': [\n"
            "            {'name': '书 · 第 1 页', 'type': 'docx', 'token': 'OLD_DOC'}]}}\n"
            "    if '/raw_content' in path:\n"
            "        return {'code': 0, 'data': {'content': '# 旧文档\\n\\n> 卡片：app_card\\n'}}\n"
            "    if 'root_folder' in path: return {'code': 0, 'data': {'token': 'FOLDER'}}\n"
            "    raise AssertionError(path)\n"
            "sf.req_json = fake_req\n"
            "sf.urllib.request.urlopen = fake_urlopen\n"
            "sf.time.sleep = lambda *a: None\n"
            "rec = dl.begin(card, 'feishu', 'FOLDER', '书 · 第 1 页')\n"
            "dl.commit(rec, 'OLD_DOC', 'https://ex.feishu.cn/docx/OLD_DOC')\n"
            "dl.record_dest(card, 'dest_feishu', 'https://ex.feishu.cn/docx/OLD_DOC')\n"
            "dl.reset(card, 'feishu', 'FOLDER')\n"
            "def run():\n"
            "    sys.argv = ['save_feishu.py', card]\n"
            "    try:\n"
            "        sf.main(); return 0\n"
            "    except SystemExit as e:\n"
            "        return e.code\n"
            "first = run()\n"
            "st['fail_first'] = False\n"
            "second = run()\n"
            "rec = dl.get(card, 'feishu', 'FOLDER')\n"
            "print('RESULT', first, second, rec.get('status'), rec.get('remote_id'),\n"
            "      st['import_calls'])\n")
    r = subprocess.run([env.PY, probe3], capture_output=True, text=True, env=aenv)
    parts3 = (r.stdout or "").split()
    i3 = parts3.index("RESULT") if "RESULT" in parts3 else -1
    out3 = parts3[i3 + 1:i3 + 6] if i3 >= 0 else []
    check("应用身份重投不认旧文档",
          out3 == ["5", "0", "committed", "NEW_DOC", "2"],
          f"得到 {out3}" + (f"  {r.stderr.strip()[:80]}" if r.returncode else ""))

    # 21e. 应用身份下同名候选有多份时，必须**逐份**回读标识，不能只看第一份
    #      A：第一份是别的卡、第二份才是本卡 → 认出第二份，不新建
    #      B：其中一份正文读不出 → 不许新建（退出码 4，保持待核对）
    #      C：全部读成功且都不是本卡 → 这时才允许新建
    probe4 = os.path.join(tmp, "probe_app_multi.py")
    with open(probe4, "w", encoding="utf-8") as f:
        f.write(
            "import json, os, sys\n"
            f"sys.path.insert(0, {HERE!r})\n"
            "import save_feishu as sf, delivery as dl\n"
            "lib = os.environ['READING_LIBRARY']\n"
            "st = {'calls': 0, 'files': [], 'body': {}}\n"
            "class R:\n"
            "    def __init__(self, o): self.b = json.dumps(o).encode()\n"
            "    def read(self): return self.b\n"
            "    def __enter__(self): return self\n"
            "    def __exit__(self, *a): return False\n"
            "def fake_urlopen(req, body=None):\n"
            "    u = req.full_url\n"
            "    if 'upload_all' in u: return R({'code': 0, 'data': {'file_token': 'FT'}})\n"
            "    if 'permissions' in u: return R({'code': 0})\n"
            "    raise AssertionError(u)\n"
            "def fake_req(path, method, token, data=None):\n"
            "    if 'tenant_access_token' in path:\n"
            "        return {'code': 0, 'tenant_access_token': 'T'}\n"
            "    if '/drive/v1/files' in path:\n"
            "        return {'code': 0, 'data': {'files': st['files']}}\n"
            "    if '/raw_content' in path:\n"
            "        t = path.split('/documents/')[1].split('/')[0]\n"
            "        if t not in st['body']:\n"
            "            return {'code': 131006, 'msg': 'cannot read'}\n"
            "        return {'code': 0, 'data': {'content': st['body'][t]}}\n"
            "    if path.endswith('/import_tasks') and method == 'POST':\n"
            "        st['calls'] += 1\n"
            "        return {'code': 0, 'data': {'ticket': 'T1'}}\n"
            "    if '/import_tasks/' in path:\n"
            "        return {'code': 0,\n"
            "                'data': {'result': {'job_status': 0, 'token': 'NEW_DOC'}}}\n"
            "    if 'root_folder' in path:\n"
            "        return {'code': 0, 'data': {'token': 'FOLDER'}}\n"
            "    raise AssertionError(path)\n"
            "sf.req_json = fake_req\n"
            "sf.urllib.request.urlopen = fake_urlopen\n"
            "sf.time.sleep = lambda *a: None\n"
            "def scenario(name, files, body):\n"
            "    card = os.path.join(lib, name + '.md')\n"
            "    open(card, 'w', encoding='utf-8').write(\n"
            "        '---\\nbook: \"书\"\\n---\\n\\n# 书 · 第 1 页\\n\\n"
            "## 当时的想法\\n\\n想法。\\n')\n"
            "    dl.begin(card, 'feishu', 'FOLDER', '书 · 第 1 页')\n"
            "    st['files'], st['body'] = files, body\n"
            "    before = st['calls']\n"
            "    sys.argv = ['save_feishu.py', card]\n"
            "    try:\n"
            "        sf.main(); code = 0\n"
            "    except SystemExit as e:\n"
            "        code = e.code\n"
            "    rec = dl.get(card, 'feishu', 'FOLDER') or {}\n"
            "    print('CASE', name, code, st['calls'] - before,\n"
            "          rec.get('status'), rec.get('remote_id') or '-')\n"
            "SAME = '书 · 第 1 页'\n"
            "scenario('multiA',\n"
            "         [{'name': SAME, 'type': 'docx', 'token': 'OTHER'},\n"
            "          {'name': SAME, 'type': 'docx', 'token': 'MINE'}],\n"
            "         {'OTHER': 'x\\n\\n> 卡片：other_card\\n',\n"
            "          'MINE': 'x\\n\\n> 卡片：multiA\\n'})\n"
            "scenario('multiB',\n"
            "         [{'name': SAME, 'type': 'docx', 'token': 'OTHER'},\n"
            "          {'name': SAME, 'type': 'docx', 'token': 'MINE'}],\n"
            "         {'OTHER': 'x\\n\\n> 卡片：other_card\\n'})\n"
            "scenario('multiC',\n"
            "         [{'name': SAME, 'type': 'docx', 'token': 'OTHER'},\n"
            "          {'name': SAME, 'type': 'docx', 'token': 'OTHER2'}],\n"
            "         {'OTHER': 'x\\n\\n> 卡片：card_x\\n',\n"
            "          'OTHER2': 'x\\n\\n> 卡片：card_y\\n'})\n")
    r = subprocess.run([env.PY, probe4], capture_output=True, text=True, env=aenv)
    rows4 = []
    parts4 = (r.stdout or "").split("\n")
    for line in parts4:
        p = line.split()
        if p and p[0] == "CASE":
            rows4.append(p[1:])
    want4 = [["multiA", "0", "0", "committed", "MINE"],
             ["multiB", "4", "0", "intent", "-"],
             ["multiC", "0", "1", "committed", "NEW_DOC"]]
    check("应用身份遍历全部同名候选", rows4 == want4,
          f"得到 {rows4}" + (f"  {r.stderr.strip()[-120:]}" if r.returncode else ""))

    # 21f. 列目录必须**翻完所有页**：只读第一页时，落在第二页的本卡会被判成「不存在」
    #      A 第二页命中 → found 两份  B 后续页失败 → error（不是 absent）
    #      C 说还有下一页却没给 token → error  D token 重复 → error（防死循环）  E 单页 → found
    probe5 = os.path.join(tmp, "probe_app_pages.py")
    with open(probe5, "w", encoding="utf-8") as f:
        f.write(
            "import sys\n"
            f"sys.path.insert(0, {HERE!r})\n"
            "import save_feishu as sf\n"
            "P1 = {'name': '书', 'type': 'docx', 'token': 'P1DOC'}\n"
            "P2 = {'name': '书', 'type': 'docx', 'token': 'P2DOC'}\n"
            "def script(pages):\n"
            "    calls = []\n"
            "    def req(path, method, token, data=None):\n"
            "        calls.append(path)\n"
            "        i = len(calls) - 1\n"
            "        page = pages[i] if i < len(pages) else {'code': 0, 'data': {'files': []}}\n"
            "        if page is None:\n"
            "            raise RuntimeError('network down')\n"
            "        return page\n"
            "    sf.req_json = req\n"
            "    st, toks = sf.find_drive_doc('t', 'folder', '书')\n"
            "    return st, (','.join(toks) or '-'), len(calls)\n"
            "OK1 = {'code': 0, 'data': {'files': [P1], 'has_more': True,\n"
            "                           'next_page_token': 'N2'}}\n"
            "OK2 = {'code': 0, 'data': {'files': [P2], 'has_more': False}}\n"
            "print('A', *script([OK1, OK2]))\n"
            "print('B', *script([OK1, {'code': 131006, 'msg': 'denied'}]))\n"
            "print('C', *script([{'code': 0, 'data': {'files': [P1], 'has_more': True}}]))\n"
            "print('D', *script([\n"
            "    {'code': 0, 'data': {'files': [P1], 'has_more': True, 'next_page_token': 'X'}},\n"
            "    {'code': 0, 'data': {'files': [], 'has_more': True, 'next_page_token': 'X'}}]))\n"
            "print('E', *script([{'code': 0, 'data': {'files': [P1], 'has_more': False}}]))\n"
            "print('F', *script([None]))\n")
    r = subprocess.run([env.PY, probe5], capture_output=True, text=True, env=run_env)
    rows5 = [ln.split() for ln in (r.stdout or "").split("\n") if ln.strip()]
    want5 = [["A", "found", "P1DOC,P2DOC", "2"],
             ["B", "error", "-", "2"],
             ["C", "error", "-", "1"],
             ["D", "error", "-", "2"],
             ["E", "found", "P1DOC", "1"],
             ["F", "error", "-", "1"]]
    check("列目录翻完所有页", rows5 == want5, f"得到 {rows5}")

    # 21g. 连接器路径同样不能只翻有限几页：--page-limit 写死会截断，截断＝「找不到」＝再建一份
    lsrc = open(os.path.join(HERE, "save_feishu_lark.py"), encoding="utf-8").read()
    lseg = lsrc[lsrc.index("def list_children"):].split("\ndef ")[0]
    check("连接器列目录不限页数、半截列表按失败处理",
          '"--page-limit", "0"' in lseg and 'data.get("has_more")' in lseg,
          "page-limit=%s，has_more 校验=%s"
          % ('"--page-limit", "0"' in lseg, 'data.get("has_more")' in lseg))

    # 22. 可选联网：抓一个真实页面 + 截图降级（不联网时整段跳过）
    if online:
        r = run("fetch_source.py", "https://example.com", "--no-save")
        check("联网抓正文", r.returncode == 0 and "Example" in (r.stdout or ""),
              (r.stderr or "").strip()[:80])
        r = run("save_source_shot.py", "https://example.com", "--slug", "selftest",
                "--wait", "2")
        if env.chrome_path():
            check("链接截图（有浏览器）", r.returncode in (0, 5),
                  "" if r.returncode == 0 else "站点要求验证，属正常降级")
        else:
            check("链接截图（无浏览器 → 应降级）",
                  r.returncode == 3 and "Traceback" not in (r.stderr or ""),
                  (r.stderr or "").strip().splitlines()[0] if r.stderr else "")
    else:
        print("· 联网项已跳过（加 --online 才跑抓取与截图）")

    bad_rows = [n for n, ok_, _ in results if not ok_]
    print()
    if bad_rows:
        print(f"自检未通过 {len(bad_rows)} 项：{('、'.join(bad_rows))}")
        print(f"临时目录保留以便排查：{tmp}")
        sys.exit(1)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"全部 {len(results)} 项通过 —— 这台机器可以把核心链路跑通")
    print("（缺的能力见 `setup.py env`，缺哪个跳哪个，不中断流程）")


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
    ap.add_argument("--online", action="store_true",
                    help="selftest 时额外跑联网抓取与截图（默认完全离线）")
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
        cmd_selftest(online=a.online)
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
