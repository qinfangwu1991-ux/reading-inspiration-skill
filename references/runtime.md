# 运行环境：本机 / 别人的电脑 / 云沙箱

这个技能要能在三种地方跑：**本机 macOS**、**别人的 Windows / Linux**、**云沙箱（只有 python3）**。
下面这张表是「哪一步靠什么」，缺哪一项就跳哪一项，**任何一项缺失都不该让流程中断**。

## 一、能力矩阵

| 能力 | 依赖 | 缺了怎么办（脚本已内置） | 对用户怎么说 |
|---|---|---|---|
| 取正文（网页 / 公众号 / 新闻） | 无，纯 `urllib` + `html.parser` | — | 正常 |
| 取正文（飞书文档） | `lark-cli`（飞书连接器） | `fetch_source.py` 报错退出，改走「粘贴正文」 | 「飞书这句我读不到，你把正文粘过来吧」 |
| 取正文（乐享页面） | 对话内乐享连接器 | 脚本退出码 4，交回 Agent 用 MCP 取 | 静默处理，用户无感 |
| 取正文（PDF） | `pdftotext` | 提示改用 `--from-text`（先由 workbuddy-pdf 读） | 同上 |
| 书页图片归档 | `sips` → ImageMagick → Pillow → `ffmpeg` | **四路都没有就原样复制**，不压缩也不报错 | 无感 |
| 链接原页面截图 | Chrome / Chromium / Edge，或 `CHROME_PATH` | 退出码 3，**跳过截图**，不重试 | 「这个环境没有浏览器，截不了原页面；你手机截一张发我，我十秒补进去」 |
| 写 Obsidian | 配置里的 vault 目录 | 目录无效 → 退出码 2，跳过 | 「这台机器没有 Obsidian，先存本地和别的库」 |
| 写乐享 / 飞书 | 对话内连接器（脚本不直连） | 连接器不可用 → 跳过该目标 | 不要长篇解释授权流程 |
| 本地卡片 + 目录地图 | 无 | **永远可用**，这是保底 | 「已经存到本地了」 |

结论一句话：**核心链路（拍照/链接/粘贴 → 卡片 → 目录地图 → Hub）只依赖 Python 3.8+ 标准库**；
截图、压缩、PDF、飞书、两个云端知识库都是可选增强。

## 二、两条自检命令

```bash
python3 scripts/setup.py env        # 这台机器「有/没有」什么，一屏看完
python3 scripts/setup.py selftest   # 离线把整条本地链路跑一遍（临时目录，跑完自动清理）
```

- `env` 看能力，**不会**改动任何东西
- `selftest` 不联网、不碰真实素材库和知识库，6 项全绿说明这台机器能跑通；任一项红会保留临时目录方便排查
- 云环境部署完、或用户说「怎么不好使」时，先跑这两条，别猜

## 三、三个环境变量（只读安装 / 多用户时用）

| 变量 | 作用 | 典型场景 |
|---|---|---|
| `READING_LIBRARY` | 素材库（卡片、原图、截图、目录地图）落地目录 | 技能装在只读位置（系统级安装、云镜像挂载），指到 `$HOME/reading-inspiration` |
| `READING_CONFIG` | 配置文件路径，默认 `<技能目录>/config.json` | 同一份技能给多个用户/多个会话跑，配置不能互相覆盖 |
| `CHROME_PATH` | 浏览器可执行文件路径 | 自建镜像里 Chromium 不在默认路径 |

```bash
export READING_LIBRARY=$HOME/reading-inspiration
export READING_CONFIG=$HOME/reading-inspiration.json
export CHROME_PATH=/opt/chromium/chrome
```

技能目录只读时，`config.py` 不会再抛 `PermissionError` 栈，而是直接给出上面这三行提示。

## 四、云沙箱实测记录（2026-10-25）

用 `env -i PATH=<只有 python3 的目录> HOME=<空目录>` 复现极简环境，结果：

- `setup.py env` — 正确报出「无压缩工具 / 无浏览器 / 无 pdftotext / 无 lark-cli / 未配置 Obsidian」，并说明核心链路仍可用
- `setup.py selftest` — 6 项全过；图片走「原样归档」，截图走「干净降级（退出码 3，无 traceback）」
- `fetch_source.py` 本地文件 / `--from-stdin` — 正常
- `fetch_source.py` 联网网页（MDN）— 正常，5113 字 / 84 段
- 只读技能目录 + `READING_LIBRARY` — 素材正常落到指定目录
- 只读技能目录不加 `READING_CONFIG` 写配置 — 给出三行可操作提示，退出码 3，无栈

## 五、跨平台细节（踩过的）

1. **别写死 `python3`**：Windows 是 `python.exe`，有些沙箱只装了 `python`。
   所有脚本间调用统一走 `sys.executable`（`scripts/env.py` 的 `env.PY`）。
2. **别写死 `/tmp`**：Windows 没有 `/tmp`，用 `tempfile.gettempdir()`。
3. **别写死 macOS 应用包路径**：Chrome 在 Linux 常在 `/opt/google/chrome/chrome` 或 `/snap/bin/chromium`，
   Windows 在 `C:\Program Files\...`。`env.chrome_path()` 全列了，还支持 `CHROME_PATH` 覆盖。
4. **老 Chromium 不认 `--headless=new`**：截图脚本先试 `--headless=new`，起不来再试 `--headless`。
5. **Windows 控制台是 GBK**：打印中文/emoji 会 `UnicodeEncodeError`，需要时调 `env.setup_stdout()`。
6. **`sips` 只有 macOS 有**：早期图片归档直接 `subprocess.run(["sips", ...])`，
   在没有 sips 的机器上会抛 `FileNotFoundError` 崩栈 —— 现在四路降级。
