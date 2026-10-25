#!/usr/bin/env python3
"""读写 reading-inspiration 技能配置（支持同时启用多个知识库）。

用法:
  python3 config.py show                                   # 查看当前配置
  python3 config.py targets                                # 只列出当前启用的知识库
  python3 config.py enable  --kb lexiangla --kb obsidian    # 启用知识库（可多个）
  python3 config.py disable --kb feishu                    # 停用知识库
  python3 config.py set --kb-type ima                      # 兼容旧用法：设为唯一目标
  python3 config.py set --kb obsidian --vault /path/to/dir
  python3 config.py set --kb feishu --app-id X --app-secret Y [--folder-token Z]
  python3 config.py set --kb lexiangla --team X --token Y
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env  # noqa: E402
# 允许用 READING_CONFIG 指到别处：云环境里同一份技能给多个用户跑时，配置不该互相覆盖
CONFIG_PATH = os.path.abspath(os.path.expanduser(
    os.environ.get("READING_CONFIG") or os.path.join(HERE, "..", "config.json")))
LIBRARY_DIR = env.library_dir()

KB_ALL = ["lexiangla", "feishu", "obsidian", "ima"]

DEFAULT = {
    "kb_types": [],          # 已启用的知识库列表，可多个，如 ["lexiangla", "obsidian"]
    "obsidian_vault": "",
    "feishu": {"app_id": "", "app_secret": "", "folder_token": "",
               "wiki_space_id": "", "wiki_parent_token": "", "domain": ""},
    "lexiangla": {"team": "", "token": ""},
    "ima": {},
}


def load():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}
    merged = json.loads(json.dumps(DEFAULT))
    merged.update(cfg)
    # 兼容旧的单目标写法：只有 kb_type 时迁移为 kb_types
    if not merged.get("kb_types") and cfg.get("kb_type"):
        merged["kb_types"] = [cfg["kb_type"]]
    if isinstance(merged.get("kb_types"), str):
        merged["kb_types"] = [merged["kb_types"]]
    return merged


def save(cfg):
    cfg.pop("kb_type", None)  # 统一只存 kb_types
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        # 技能装在只读目录时（云沙箱、系统级安装）不要让用户看 traceback
        print(f"配置写不进去：{CONFIG_PATH}（{e.strerror or e}）", file=sys.stderr)
        print("→ 技能目录是只读的，用环境变量把配置指到可写位置再跑：", file=sys.stderr)
        print("   export READING_CONFIG=$HOME/reading-inspiration.json", file=sys.stderr)
        sys.exit(3)
    try:
        os.makedirs(LIBRARY_DIR, exist_ok=True)
    except OSError:
        pass


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")       # 给人看：凭证打码
    sub.add_parser("raw")        # 给脚本用：凭证原样输出
    sub.add_parser("targets")
    sub.add_parser("save")       # 从 stdin 读完整 JSON 覆盖写入（供其他脚本回写配置）
    for name in ("enable", "disable"):
        s = sub.add_parser(name)
        s.add_argument("--kb", action="append", default=[], required=True)
    s2 = sub.add_parser("set")
    for key in ("--kb-type", "--kb", "--vault", "--app-id", "--app-secret",
                "--folder-token", "--team", "--token", "--wiki-space-id",
                "--wiki-parent-token", "--domain"):
        s2.add_argument(key, default=None)
    a = p.parse_args()

    cfg = load()

    if a.cmd == "show":
        out = json.loads(json.dumps(cfg))
        if out.get("feishu", {}).get("app_secret"):
            out["feishu"] = dict(out["feishu"], app_secret="***")
        if out.get("lexiangla", {}).get("token"):
            out["lexiangla"] = dict(out["lexiangla"], token="***")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if a.cmd == "raw":
        print(json.dumps(cfg, ensure_ascii=False))
        return

    if a.cmd == "targets":
        print(json.dumps(cfg["kb_types"], ensure_ascii=False))
        return

    if a.cmd == "save":
        import sys as _sys
        new = json.loads(_sys.stdin.read())
        save(new)
        print("配置已写入")
        return

    if a.cmd == "enable":
        types = list(cfg["kb_types"])
        for kb in a.kb:
            if kb not in KB_ALL:
                print(f"未知知识库类型: {kb}（可选: {', '.join(KB_ALL)}）")
                return
            if kb not in types:
                types.append(kb)
        cfg["kb_types"] = types
        save(cfg)
        print("已启用:", ", ".join(types))
        return

    if a.cmd == "disable":
        types = [t for t in cfg["kb_types"] if t not in a.kb]
        cfg["kb_types"] = types
        save(cfg)
        print("已启用:", ", ".join(types) if types else "（无）")
        return

    # set
    if a.kb_type:
        cfg["kb_types"] = [a.kb_type]
    elif a.kb:
        types = list(cfg["kb_types"])
        if a.kb not in types:
            types.append(a.kb)
        cfg["kb_types"] = types
    if a.vault:
        cfg["obsidian_vault"] = a.vault
    if a.app_id:
        cfg["feishu"]["app_id"] = a.app_id
    if a.app_secret:
        cfg["feishu"]["app_secret"] = a.app_secret
    if a.folder_token:
        cfg["feishu"]["folder_token"] = a.folder_token
    if a.team:
        cfg["lexiangla"]["team"] = a.team
    if a.token:
        cfg["lexiangla"]["token"] = a.token
    if getattr(a, "wiki_space_id", None):
        cfg["feishu"]["wiki_space_id"] = a.wiki_space_id
    if getattr(a, "wiki_parent_token", None):
        cfg["feishu"]["wiki_parent_token"] = a.wiki_parent_token
    if getattr(a, "domain", None):
        cfg["feishu"]["domain"] = a.domain
    save(cfg)
    print("配置已保存:", CONFIG_PATH, "｜已启用:", ", ".join(cfg["kb_types"]) or "（无）")


if __name__ == "__main__":
    main()
