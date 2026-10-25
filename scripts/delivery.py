#!/usr/bin/env python3
"""投递台账：记录「打算投 → 远端已建成」，让重试可以核对而不是重建。

为什么需要它：卡片分发到知识库是「本地脚本 + 远端 API」两段式，
中途任何一步中断（进程被杀、网络断、Agent 没来得及写回链接）都会让下一次补发
再建一份同样的文档。只靠卡片里的 `dest_<kb>` 字段不够——那是事后记录，
覆盖不了「远端已建成、本地还没写」这个时间窗口。

三层 ID（粒度不同，不能混）：
    source_id    原文身份（fetch_source.py 生成），一篇文章一个
    card_id      卡片身份（卡片文件名），同一篇可以有多张摘录卡
    delivery_id  card_id + 目标知识库 + 父节点，一次投递一条

状态：
    intent     已决定投递、尚未确认远端结果（下次启动先核对这一层）
    committed  远端已建成，记下 remote_id 与 url
    failed     失败，可重试（重试沿用同一个 delivery_id）

`reset` 删除记录时会留下一条「用户要求重投」的墓碑（redeliver 列表）：
卡片里的 `dest_<kb>` 还在，若只删台账，下次运行会被「旧链接接纳」逻辑当成已投递，
重新登记回去 —— reset 就白做了。墓碑让两条飞书路径都跳过接纳、真正重建。

用法（Agent 排查用）：
    python3 delivery.py pending                 # 上次没确认的投递（需要核对远端）
    python3 delivery.py show <卡片路径>          # 这张卡在哪些地方的投递情况
    python3 delivery.py reset <卡片路径> --kb feishu   # 清掉记录并要求重投
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env  # noqa: E402

LIBRARY = env.library_dir()
LEDGER_PATH = os.path.join(LIBRARY, ".delivery.json")
VERSION = 1


def card_marker(card_id):
    """写进远端文档的卡片标识行（人也能看懂）。"""
    return f"> 卡片：{card_id}"


# 远端回读的形式不统一：markdown 带 `> `，docx 纯文本没有引用符。
# 先整行取出「标识值」，再和当前卡片 ID **精确相等**比较 —— 用子串判断的话，
# 卡片 `card` 会命中另一张 `card2` 文档里的 `> 卡片：card2`，把别人的文档认成自己的。
_MARKER_RE = re.compile(r"^\s*(?:>\s*)*卡片\s*[：:]\s*(.+?)\s*$", re.M)


def marker_values(text):
    """抽出正文里所有「卡片：X」的 X（去掉首尾空白）。"""
    return [m.strip() for m in _MARKER_RE.findall(str(text or ""))]


def has_card_marker(text, card_id):
    """正文里的卡片标识是否**正好**是这张卡。"""
    want = str(card_id or "").strip()
    if not want:
        return False
    return want in marker_values(text)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load():
    if not os.path.exists(LEDGER_PATH):
        return {"version": VERSION, "deliveries": [], "redeliver": []}
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        # 台账坏了不能阻断投递，但也不能静默丢掉历史：备份一份再重建
        try:
            os.replace(LEDGER_PATH, LEDGER_PATH + ".broken")
        except OSError:
            pass
        return {"version": VERSION, "deliveries": [], "redeliver": []}
    data.setdefault("deliveries", [])
    data.setdefault("redeliver", [])
    data.setdefault("version", VERSION)
    return data


def save(data):
    """原子写：先写临时文件再 rename，避免中断后留下半个 JSON。"""
    os.makedirs(os.path.dirname(LEDGER_PATH) or ".", exist_ok=True)
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LEDGER_PATH)


def card_id(card_path):
    """卡片身份 = 文件名（跨 re-save 稳定，dest_* 写回也不会改变它）。"""
    return os.path.splitext(os.path.basename(os.path.abspath(str(card_path))))[0]


def delivery_id(cid, kb, parent=""):
    raw = f"{cid}|{kb}|{parent or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def find(data, cid, kb, parent=""):
    did = delivery_id(cid, kb, parent)
    for d in data["deliveries"]:
        if d.get("delivery_id") == did:
            return d
    return None


def get(card_path, kb, parent=""):
    return find(load(), card_id(card_path), kb, parent or "")


def _redeliver_hit(t, cid, kb, parent=""):
    """墓碑是否覆盖这次投递：同卡同目标，且（指定了落点 / 墓碑是通配）。"""
    if t.get("card_id") != cid or t.get("kb") != kb:
        return False
    tp = t.get("parent") or ""
    return tp == "*" or tp == (parent or "")


def _find_tomb(data, cid, kb, parent=""):
    for t in data.get("redeliver", []):
        if _redeliver_hit(t, cid, kb, parent):
            return t
    return None


def _ids_of(*recs):
    """一条记录里出现过的全部远端标识（remote_id / url / superseded）。"""
    out = []
    for r in recs:
        if not r:
            continue
        for k in ("remote_id", "url"):
            v = r.get(k)
            if v and v not in out:
                out.append(v)
        for v in (r.get("superseded") or []):
            if v and v not in out:
                out.append(v)
    return out


def state(card_path, kb, parent=""):
    """一次读全这次投递的「代次视图」。

    返回 {rec, epoch, superseded, wants_redeliver, tombstone, card_id}：
        epoch            当前代次（reset / force 会 +1）
        superseded       被新代次取代的远端 id —— 核对时**绝不能**再认领这些文档
        wants_redeliver  用户仍要求重投（墓碑还在；只有 commit 成功才消失）

    为什么需要代次：一条记录只按 card_id 存，区分不出「这一份」和「上一代那份」。
    reset 后第一次重投若失败，记录停在 intent，下一次核对会看到上一代的旧文档，
    卡片标识也对得上（同一张卡），于是又被认领回去 —— 重投意图就这样丢了。
    """
    data = load()
    cid = card_id(card_path)
    rec = find(data, cid, kb, parent or "")
    tomb = _find_tomb(data, cid, kb, parent or "")
    superseded, epoch = set(), 1
    if rec:
        epoch = int(rec.get("epoch") or 1)
        superseded.update(rec.get("superseded") or [])
    if tomb:
        epoch = max(epoch, int(tomb.get("epoch") or (epoch + 1)))
        superseded.update(tomb.get("superseded") or [])
    if rec and int(rec.get("epoch") or 1) != epoch:
        # 记录属于旧代次：它的 remote_id 一律作废，不能当成「已建成」
        superseded.update(x for x in (rec.get("remote_id"), rec.get("url")) if x)
    return {"rec": rec, "epoch": epoch, "superseded": superseded,
            "wants_redeliver": tomb is not None, "tombstone": tomb,
            "card_id": cid}


def wants_redeliver(card_path, kb, parent=""):
    """用户是否对这张卡的某个目标显式要求过重投（墓碑还在）。"""
    return state(card_path, kb, parent)["wants_redeliver"]


def _clear_redeliver(data, cid, kb, parent=""):
    """确认交付后划掉墓碑（已在内存里改，由调用方保存）。"""
    before = len(data.get("redeliver", []))
    data["redeliver"] = [t for t in data.get("redeliver", [])
                         if not _redeliver_hit(t, cid, kb, parent)]
    return before - len(data["redeliver"])


def begin(card_path, kb, parent="", title="", source_id="", force=False):
    """投递前登记。返回记录 dict。

    已有 committed 记录时**不重置**状态（调用方据此跳过重复投递）。
    force=True 或存在重投墓碑 → 开新代次：`epoch + 1`，旧 `remote_id` 记进
    `superseded`，状态回到 intent。**墓碑不在这里划掉**——只有 commit() 成功才划掉，
    否则「首次重投失败」会把重投意图丢掉，下一次又去认领上一代的旧文档。
    """
    data = load()
    cid = card_id(card_path)
    did = delivery_id(cid, kb, parent or "")
    rec = find(data, cid, kb, parent or "")
    tomb = _find_tomb(data, cid, kb, parent or "")
    if (rec and rec.get("status") == "committed" and rec.get("remote_id")
            and not force and not tomb):
        return rec

    old_epoch = int(rec.get("epoch") or 1) if rec else 1
    if tomb:
        epoch = max(old_epoch, int(tomb.get("epoch") or (old_epoch + 1)))
        superseded = _ids_of(rec, tomb)
    elif force:
        epoch = old_epoch + 1
        superseded = _ids_of(rec)
    else:
        epoch = old_epoch
        superseded = list(rec.get("superseded") or []) if rec else []

    if rec is None:
        rec = {
            "delivery_id": did, "card_id": cid, "source_id": source_id or "",
            "kb": kb, "parent": parent or "", "title": title or "",
            "status": "intent", "remote_id": "", "url": "",
            "attempts": 1, "created_at": _now(), "last_attempt_at": _now(),
            "committed_at": "", "error": "",
            "epoch": epoch, "superseded": superseded,
        }
        data["deliveries"].append(rec)
    else:
        rec.update({
            "status": "intent", "remote_id": "", "url": "",
            "attempts": int(rec.get("attempts") or 0) + 1,
            "last_attempt_at": _now(), "error": "",
            "epoch": epoch, "superseded": superseded,
        })
    save(data)
    return rec


def commit(rec_or_id, remote_id, url="", remote_title=""):
    data = load()
    did = rec_or_id["delivery_id"] if isinstance(rec_or_id, dict) else rec_or_id
    for d in data["deliveries"]:
        if d.get("delivery_id") == did:
            d.update({"status": "committed", "remote_id": remote_id or "",
                      "url": url or "", "committed_at": _now(), "error": ""})
            if remote_title:
                d["remote_title"] = remote_title
            # 这一代真的交付成功了，重投要求到此结束。
            # superseded 保留：以后核对不该再认领被取代掉的旧文档。
            _clear_redeliver(data, d.get("card_id") or "", d.get("kb") or "",
                             d.get("parent") or "")
            save(data)
            return d
    return None


def fail(rec_or_id, error=""):
    data = load()
    did = rec_or_id["delivery_id"] if isinstance(rec_or_id, dict) else rec_or_id
    for d in data["deliveries"]:
        if d.get("delivery_id") == did:
            d.update({"status": "failed", "error": str(error)[:400],
                      "last_attempt_at": _now()})
            save(data)
            return d
    return None


def reset(card_path, kb, parent=None, remember=True):
    """清掉某张卡在某个目标的记录，让它能重新投递。

    parent=None 表示**这个目标下的全部记录**（含正常投递记录，它们都带父节点）；
    给 parent 才只清那一个落点。旧版默认 parent="" 只匹配空父节点，
    结果正常记录一条都清不掉，命令行却报「已清除 0 条」。

    remember=True 时给被清的落点留一条墓碑（data["redeliver"]）：
    卡片里的 `dest_<kb>` 不会被清掉，只删台账的话，下次运行会立刻被
    「旧链接接纳」逻辑认回已投递状态，创建 0 次 —— 用户以为重投了，其实什么都没发生。

    墓碑同时记下 `epoch`（新代次）和被清记录的远端 id（`superseded`），
    这样「重投第一枪失败、第二次重试」也不会把上一代的旧文档认回来。
    """
    data = load()
    cid = card_id(card_path)
    before = len(data["deliveries"])
    removed = []

    def keep(d):
        if d.get("card_id") != cid or d.get("kb") != kb:
            return True
        if parent is None or (d.get("parent") or "") == (parent or ""):
            removed.append(d)
            return False
        return True

    data["deliveries"] = [d for d in data["deliveries"] if keep(d)]
    if remember:
        groups = {}
        for d in removed:
            groups.setdefault(d.get("parent") or "", []).append(d)
        if not groups:
            groups["*" if parent is None else (parent or "")] = []
        for p, recs in groups.items():
            epoch = max([int(r.get("epoch") or 1) for r in recs] + [0]) + 1
            ids = _ids_of(*recs)
            old = next((t for t in data["redeliver"]
                        if (t.get("parent") or "") == p and _redeliver_hit(t, cid, kb, p)),
                       None)
            if old:
                old["epoch"] = max(int(old.get("epoch") or 1), epoch)
                old["superseded"] = list(dict.fromkeys((old.get("superseded") or []) + ids))
                old["at"] = _now()
            else:
                data["redeliver"].append({
                    "card_id": cid, "kb": kb, "parent": p,
                    "epoch": epoch, "superseded": ids,
                    "at": _now(), "reason": "reset",
                })
    save(data)
    return before - len(data["deliveries"])


def pending(kb=""):
    """上次停在 intent 的投递：远端可能已经建成，需要先核对再决定。"""
    return [d for d in load()["deliveries"]
            if d.get("status") == "intent" and (not kb or d.get("kb") == kb)]


def of_card(card_path):
    cid = card_id(card_path)
    return [d for d in load()["deliveries"] if d.get("card_id") == cid]


def read_dest(card_path, key):
    """读卡片 frontmatter 里已有的 `dest_*`（旧版卡片只有这个，没有台账）。"""
    import re
    import naming
    try:
        with open(os.path.abspath(card_path), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        if k.strip() == key:
            return naming.unquote(v).strip()
    return ""


def remote_id_of(url):
    """从链接里取远端 ID（/wiki/<node> 或 /docx/<doc> 的最后一段）。"""
    tail = str(url or "").rstrip("/").rsplit("/", 1)[-1]
    return tail if tail and " " not in tail else ""


def adopt(card_path, kb, parent, url, title=""):
    """把卡片里已有的 `dest_*` 接纳为已投递，并补建台账记录。

    升级前的卡片有 `dest_feishu` 但没有台账。若只查台账，这些卡会被当成
    「从未投递」，重新分发时再建一份、并把原链接覆盖掉。
    """
    data = load()
    cid = card_id(card_path)
    did = delivery_id(cid, kb, parent or "")
    rec = find(data, cid, kb, parent or "")
    if rec is None:
        rec = {"delivery_id": did, "card_id": cid, "source_id": "",
               "kb": kb, "parent": parent or "", "title": title or "",
               "attempts": 1, "created_at": _now(), "last_attempt_at": _now()}
        data["deliveries"].append(rec)
    rec.update({"status": "committed", "remote_id": remote_id_of(url),
                "url": url or "", "committed_at": _now(), "error": "",
                "adopted": True})
    if title:
        rec["remote_title"] = title
    save(data)
    return rec


def record_dest(card_path, key, value):
    """把目标链接直接写回卡片 frontmatter（同名 key 原地替换，不重复追加）。

    这一步以前交给 Agent 手动 Edit，一旦中断，卡片就缺 `dest_*`，
    下次补发会当成新卡片再建一份。放在脚本里做，投递结果与记录同源。
    """
    import re
    import naming
    path = os.path.abspath(card_path)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return False
    fm, body = m.group(1), m.group(2)
    line = f"{key}: {naming.scalar(value)}"
    if re.search(rf"^{re.escape(key)}\s*:", fm, re.M):
        fm = re.sub(rf"^{re.escape(key)}\s*:.*$", line, fm, flags=re.M)
    else:
        fm = fm.rstrip() + "\n" + line
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"---\n{fm}\n---\n{body}")
    os.replace(tmp, path)
    return True


def main():
    ap = argparse.ArgumentParser(description="投递台账")
    ap.add_argument("cmd", choices=["pending", "show", "reset", "begin", "commit"])
    ap.add_argument("card", nargs="?")
    ap.add_argument("--kb", default="")
    ap.add_argument("--parent", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--remote-id", dest="remote_id", default="")
    ap.add_argument("--url", default="")
    a = ap.parse_args()

    if a.cmd == "begin":
        if not a.card:
            sys.exit("需要卡片路径")
        rec = begin(a.card, a.kb or "unknown", a.parent, a.title)
        print(f"{rec['delivery_id']} {rec['status']}")
        return

    if a.cmd == "commit":
        if not a.card:
            sys.exit("需要 delivery_id 作为第一个参数")
        d = commit(a.card, a.remote_id, a.url)
        print("ok" if d else "没有这条记录")
        return

    if a.cmd == "pending":
        rows = pending(a.kb)
        if not rows:
            print("没有待核对的投递")
            return
        for d in rows:
            print(f"{d['delivery_id']}  {d['kb']:10s}  {d['card_id']}  "
                  f"尝试 {d.get('attempts')} 次 · {d.get('last_attempt_at')}")
        print("\n→ 这些投递的远端可能已经建成了。补发前先按 title 在目标父节点下核对，"
              "有同名文档就 commit 复用，不要直接再建一份。")
        return

    if not a.card:
        sys.exit("需要卡片路径")
    card = os.path.abspath(a.card)
    if a.cmd == "show":
        rows = of_card(card)
        if not rows:
            print("这张卡还没有投递记录")
        for d in rows:
            print(f"{d['kb']:10s} {d['status']:9s} attempts={d.get('attempts')} "
                  f"{d.get('url') or d.get('remote_id') or ''} {d.get('error') or ''}")
        data = load()
        marks = [t for t in data.get("redeliver", [])
                 if t.get("card_id") == card_id(card)]
        for t in marks:
            print(f"{t['kb']:10s} {'redeliver':9s} parent={t.get('parent') or '-'} "
                  f"（用户要求重投，旧 dest_* 不再当作已投递）")
        return

    n = reset(card, a.kb, a.parent or None)
    scope = f"{a.kb or '全部目标'}" + (f" · {a.parent}" if a.parent else " · 该目标全部落点")
    print(f"已清除 {n} 条记录（{scope}）；已标记为需重投，"
          f"下次运行会真正重建，不会再被旧 dest_* 接纳")


if __name__ == "__main__":
    main()
