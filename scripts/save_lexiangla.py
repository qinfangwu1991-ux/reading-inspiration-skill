#!/usr/bin/env python3
"""把灵感卡片发布到乐享知识库（创建文档）。

用法: python3 save_lexiangla.py <卡片.md路径>
输出: 文档链接（stdout）
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env  # noqa: E402


def main():
    if len(sys.argv) != 2:
        print("用法: save_lexiangla.py <卡片.md路径>", file=sys.stderr)
        sys.exit(1)
    card = os.path.abspath(sys.argv[1])
    out = subprocess.run([env.PY, os.path.join(HERE, "config.py"), "raw"],
                         capture_output=True, text=True, check=True).stdout
    lx = json.loads(out)["lexiangla"]
    if not lx["team"] or not lx["token"]:
        print("缺少乐享凭证，请用 config.py 设置 --team 与 --token", file=sys.stderr)
        sys.exit(2)

    with open(card, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    body = m.group(2) if m else text
    title_m = re.search(r"^# (.+)$", body, re.M)
    title = title_m.group(1).strip() if title_m else os.path.basename(card).rsplit(".", 1)[0]
    content = body[title_m.end():].strip() if title_m else body

    data = json.dumps({"title": title, "content": content}).encode()
    req = urllib.request.Request("https://api.lexiangla.com/cgi-bin/v1/docs",
                                 data=data, method="POST")
    req.add_header("Authorization", f"Bearer {lx['token']}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"乐享创建失败 HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(3)

    doc_id = (res.get("data") or {}).get("id", "")
    if doc_id:
        print(f"https://{lx['team']}.lexiangla.com/docs/{doc_id}")
    else:
        print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
