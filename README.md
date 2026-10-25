# 读书灵感捕捉 · Reading Inspiration Capture

> 拍一页书 → 挑几段 → 写一句当时的想法 → 自动落进你的知识库，并且长期维护一份「读书灵感目录地图」。
> 文章链接、复制粘贴的一整段文字，走同一条路。

这是一个 [WorkBuddy](https://www.workbuddy.cn) 技能（Skill）。它不生成"读书笔记"，只做一件事：
**把你在某一页/某一篇上被打动的那几段，连同当时的想法，原样留存下来**，并且以「段」为最小单位，不整篇糊成一团。

## 它能做什么

三种来源，一条后半程：

| 入口 | 怎么做 |
|---|---|
| **拍照** | 直接把书页照片发给 WorkBuddy，自动识别书名、页码、正文并按自然段切分 |
| **链接** | 发一个网页 / 公众号 / 小红书 / 新闻链接，自动抓正文、切段（抓不到时会给准确原因，请你粘贴） |
| **粘贴** | 直接粘一段文字，或给一个本地文件（txt / md / html / docx / pdf） |

拿到段落之后：

- 你回段号（`1,3`、`2-4`、`全部`）、粘原句、或在图上划线，指哪记哪；
- 说一句当时想到了什么，这句话会成为卡片的核心；
- 卡片一次分发到多个知识库：**乐享知识库 / 飞书知识库 / 本地 Obsidian / IMA**（配了哪个写哪个，没配的静默跳过）；
- 每次新增都自动更新「读书灵感目录地图」索引页，以及每本书/每个来源的 Hub 页；
- 原书页照片 / 原页面整页截图一并归档，附在卡片上。

## 安装

### 方式一：git clone（推荐）

```bash
git clone https://github.com/qinfangwu1991-ux/reading-inspiration-skill.git \
  ~/.workbuddy/skills/reading-inspiration
```

装完重开一个对话，技能会出现在「技能」列表里。

### 方式二：下载 zip

从 [Releases](https://github.com/qinfangwu1991-ux/reading-inspiration-skill/releases) 下载 `reading-inspiration.zip`，
解压到 `~/.workbuddy/skills/` 下（目录名保持 `reading-inspiration`）。

### 方式三：只在某个项目里用

放到项目的 `.workbuddy/skills/reading-inspiration/`，随项目一起被团队共享。

## 先确认这台机器有什么

```bash
cd ~/.workbuddy/skills/reading-inspiration
python3 scripts/setup.py env        # 能力体检：浏览器 / 压缩工具 / pdftotext / lark-cli / vault
python3 scripts/setup.py selftest   # 离线自检 6 项，临时目录跑完自动清理
```

`env` 会把每项能力标成「可用 / 缺（会怎么降级）」，不需要猜。`selftest` 不联网、不碰你的知识库。

## 配置知识库

```bash
python3 scripts/setup.py doctor     # 逐个报：已配置 / 可自动接 / 缺什么
python3 scripts/setup.py install --kb obsidian    # 自动找 vault
python3 scripts/setup.py install --kb lexiangla   # 自动建文件夹 + 索引页（走乐享连接器）
python3 scripts/setup.py install --kb feishu --lark   # 走飞书连接器，零凭证
```

- **乐享 / 飞书优先走连接器**：在 WorkBuddy 里连上，配置和使用都在对话内完成，不用去后台翻设置、不用贴 App Secret。
- 飞书连接器不可用时，才回退到自建应用凭证（唯一需要你复制粘贴的环节）。
- 不想装连接器也行：**只落本地 Obsidian**，或干脆只留本地 Markdown 镜像，之后一句话补发。

配置存在技能目录下的 `config.json`，模板见 `config.example.json`。

## 环境变量（只读安装 / 多人共用时用）

| 变量 | 作用 |
|---|---|
| `READING_LIBRARY` | 素材库（卡片、照片、截图、目录地图）落地的目录，默认 `<技能目录>/library` |
| `READING_CONFIG` | 配置文件路径，默认 `<技能目录>/config.json` |
| `CHROME_PATH` | 浏览器路径（不在默认位置时指定；没有浏览器则跳过原页面截图） |

## 目录结构

```
reading-inspiration/
├── SKILL.md                  # 技能主文档（触发条件、完整流程、卡片模板）
├── scripts/
│   ├── setup.py              # doctor / env / selftest / detect / install / pack
│   ├── env.py                # 跨平台探测：解释器、素材库、压缩工具、浏览器、临时目录
│   ├── fetch_source.py       # 链接 / 本地文件 / 粘贴 → 正文 → 切段（纯标准库）
│   ├── save_page_image.py    # 书页照片归档（长边 1800、JPEG q78，四路压缩工具降级）
│   ├── save_source_shot.py   # 原页面整页截图（走 Chrome DevTools 协议，手写 WebSocket）
│   ├── save_obsidian.py      # 卡片 → Obsidian（callout / 层级标签 / 嵌图 / 回链 Hub）
│   ├── save_feishu_lark.py   # 卡片 → 飞书知识库（走连接器，一步成文档）
│   ├── save_feishu.py        # 卡片 → 飞书（自建应用凭证，回退路径）
│   ├── save_lexiangla.py     # 卡片 → 乐享知识库（走连接器）
│   ├── build_map.py          # 生成目录地图 + 每本书/每个来源的 Hub 页
│   └── config.py             # 配置读写、开关知识库
├── references/
│   ├── runtime.md            # 能力矩阵、三环境变量、跨平台六个坑、沙箱实测记录
│   ├── sources.md            # 抓取路由、退出码、抓取质量五个坑、已知限制
│   ├── kb-adapters.md        # 四类知识库的接口细节与字段映射
│   └── design-refs.md        # 设计参考：同类技能的取舍
└── assets/get-credentials.md # 飞书应用凭证获取（回退路径才需要）
```

## 设计取舍

- **核心链路只依赖 Python 3.8+ 标准库**：取正文、切段、写卡片、生成目录地图，在只有 `python3` 的云沙箱里也能跑通。
- **可选能力一律降级，不报错**：没有浏览器就不截图（请你手机截一张发我）、没有压缩工具就原样归档、没有连接器就只落本地。**缺件是「跳过」，不是「失败」**，不重试、也不拿一串授权流程烦人。
- **段号必须和你看到的一一对应**：网页页脚的分享按钮、ICP 备案、配图注脚都会被剥掉，避免段号整体偏移。
- **只存你选的那几段**：全文存档另放 `library/sources/`，卡片里放的永远是你当时划到的那几句 + 你那句话。

## 已知限制

- 小红书原页面截图抓不到（反爬给浏览器看的是滑块验证页，无头/有头/stealth 都撞墙）——请用户自己截一张。
- 需要登录或强 JS 渲染的页面会退出码 5，并说明是被哪类验证拦住的，此时应请用户粘贴正文。
- IMA 只能在手机端用，桌面端分发会静默跳过。

## 许可

MIT，见 [LICENSE](LICENSE)。
