---
name: reading-inspiration
display_name: 读书灵感捕捉
display_name_en: Reading Inspiration Capture
description: 读书灵感捕捉：用户拍一页书稿（图片）、发一个文章/文档链接、或直接粘一段文字，自动取到原文并按自然段切分，由用户挑要记录的段落（说段号 / 粘贴原句 / 图上划线，或整篇收录），再记录当时想法，保存到知识库——可同时分发到多个（IMA / 乐享 / 飞书 / 本地 Obsidian），并维护一份"读书灵感目录地图"。触发词：拍照识别、书稿、这个链接、复制链接、文章链接、公众号文章、识别文字、读书笔记、读书灵感、存到知识库、只记这一段、选段、同时存多个知识库、目录地图。
description_zh: 拍一页书、发一个文章链接、或粘一段文字，按自然段切好让你挑，只记你选中的那几段，并写下当时的想法；一次分发到乐享、飞书、本地 Obsidian，同时维护一份读书灵感目录地图。
description_en: Turn a book-page photo, an article link, or pasted text into structured reading inspiration cards. It splits the source into paragraphs, keeps only the passages you pick, records what you thought at that moment, and distributes each card to Lexiang, Feishu and Obsidian while maintaining an index map.
examples_zh:
  - "拍一页书稿照片发我，我记第 4 段，想法是「皇帝也得筹钱」"
  - "这个链接你帮我抓一下正文，全部收录，想法：大家都不容易"
  - "把刚才那条灵感补发到飞书知识库"
examples_en:
  - "Here is a photo of page 113 — capture paragraph 4 with the note 'even emperors must raise money'"
  - "Fetch the body text of this link, keep all paragraphs, note: everyone has it hard"
  - "Resend that card to the Feishu wiki"
version: 1.0.0
author: qinfangwu1991-ux
---

# 读书灵感捕捉（Reading Inspiration Capture）

把用户拍的**书页照片**、给的**文章链接**、或粘来的**一段文字**变成结构化读书灵感，落库并自动维护目录地图。

**三种来源走同一条后半程**：取原文 → 切段 → 用户选段 → 记想法 → 分发 → 更新地图。前面「怎么拿到原文」不同，后面完全一样。

**在哪都能跑**：核心链路（拍照/链接/粘贴 → 卡片 → 目录地图 → Hub）只依赖 Python 3.8+ 标准库，云沙箱里也能用；
截图、图片压缩、PDF、飞书、两个云端知识库是**可选增强**，缺哪项就跳哪项，不要报错、不要重试、不要跟用户解释一堆授权流程。
能力与降级细则见 `references/runtime.md`。

## 运行环境：先确认这台机器有什么

```bash
python3 scripts/setup.py env        # 一屏看清：有没有浏览器、压缩工具、pdftotext、lark-cli、vault
python3 scripts/setup.py selftest   # 离线把本地链路跑一遍（临时目录，6 项，跑完自动清理）
```

- 部署到云环境后、或用户说「怎么不好使」时，**先跑这两条再动手**，别猜
- 技能装在只读目录（系统级安装、云镜像挂载）时用 `READING_LIBRARY` 指素材库、`READING_CONFIG` 指配置；
  自建镜像里浏览器不在默认路径时用 `CHROME_PATH`
- 没有浏览器就**不要试截图**（重试也只会在同一堵墙上撞），直接请用户手机截一张发过来

## 配置：在对话里一次配完

**铁律：不要把用户支去各个平台后台翻设置。** 能用自动化解决的一律不让用户动手；确实需要授权的，在对话内直接发起；只有飞书的应用凭证客观上必须由用户从开放平台复制（飞书没有免应用的写入授权方式），那是唯一需要粘贴的环节。

配置进度永远以 `config.json` 的 `kb_types` 为准，**不要因为某个环境配过就假定用户也配过**。

### 第 0 步：体检（每轮首次进入本技能时做一遍，10 秒）

```bash
python3 scripts/setup.py doctor
```

它逐个报每个知识库是「已配置 / 未配置（可自动接）/ 缺什么」，末尾给一句下一步建议。**照着建议走，不要自己另起一套流程。**

- 已经全部就绪 → 直接进标准流程，别再问配置问题
- `kb_types` 为空或有未配置项 → 走第 1 步

### 第 1 步：一次问完「存到哪」（只问这一次）

用 AskUserQuestion，`multiSelect: true`，选项按「用户要付出多少操作」排序，把关键信息写进描述：

| 选项 | 描述给用户看的话 | 用户实际要做的 |
|---|---|---|
| 乐享知识库（推荐） | 你公司如果已在用乐享，我这边全自动建好文件夹，你不用动 | **0 操作** |
| 本地 Obsidian | 我已经发现你电脑上的笔记库，直接接进去 | **0 操作**（多个库时选一下） |
| 飞书 | 授权一次就能存，我自动建好目录 | 有连接器：点一次同意；没有：粘贴两段字符 |

选完立刻按下面配方执行，**拿到即落配置、落完继续流程，不再二次确认**。

### 配方 A：乐享（用户 0 操作）

1. **先确保连接器可用**：直接沿用当前会话已连接的乐享通路。若工具调不通（`need_auth` / 授权过期），**不要**给用户讲安装步骤，立即用工具发起连接请求并等他点一下同意，然后重试；仍失败才降级到其他知识库。
2. `space_describe_personal_space` → 拿个人知识库（不存在会自动创建，这就是默认落点）
   - 用户有多个知识库想指定时，才用 `team_list_teams` → `space_list_spaces` 让他选一次
3. `space_describe_space(space_id)` → 取 `root_entry_id`
4. `entry_list_children(root_entry_id)` → 找 `entry_type=folder` 且 `name=读书灵感`；没有就 `entry_create_entry(entry_type="folder", parent_entry_id=root_entry_id, name="读书灵感")`
5. `entry_list_children(folder)` → 找 `name=读书灵感目录地图` 的页面；没有就 `entry_create_entry(entry_type="page", parent_entry_id=folder, name="读书灵感目录地图")`
6. 落配置：

```bash
python3 scripts/setup.py install --kb lexiangla \
  --space-id <id> --space-name <名> \
  --folder-entry-id <folder_id> --map-entry-id <map_id>
```

用户全程只做了一件事：在问题里选了「乐享」。

### 配方 B：Obsidian（用户 0 操作）

```bash
python3 scripts/setup.py detect obsidian     # 自动扫 obsidian.json + iCloud + 常见目录
```

- 只探测到 1 个库 → 直接 `python3 scripts/setup.py install --kb obsidian`（会在库下自动建 `读书灵感` 子目录）
- 探测到多个 → 把候选（带笔记数）列给用户选一次，再 `install --kb obsidian --vault <路径>`
- 一个都没有 → 让他打开一次 Obsidian 再试；仍没有就跳过这个目标，别纠缠

### 配方 C：飞书（两条路，按可用性选，**别在选路上耗**）

飞书不是「必须让用户建应用」——有官方连接器可用时完全不用碰开放平台。但也**不要为了追求零凭证把用户卡住**：连接器入口在部分客户端里不一定可见（推荐卡片可能发不出来），这时直接走 C2，一句都不用多问。

**C1：飞书连接器（已端到端实测跑通，首选）**

前提：`lark-cli` 存在，且 `lark-cli auth status` 里 `identities.user.status` 为 `ready`。

用户身份的能力**远强于应用身份**（实测）：能列知识空间、能建知识库（`wiki +space-create` 仅限 user）、能管成员。应用身份这些全是 131006 / 99991663。

配置（零凭证，不要 App ID/Secret）：

```bash
python3 scripts/setup.py install --kb feishu --lark \
  --wiki "https://<域名>/wiki/space/<space_id>"
```

它会校验身份 → 确认知识库对当前用户可见 → 在根目录找/建「读书灵感目录地图」索引页 → 落配置。索引页建不成不影响使用，卡片会直接建在知识库根目录。

每张卡片落库（一条命令）：

```bash
python3 scripts/save_feishu_lark.py "<卡片md路径>"
```

输出就是 `https://<域名>/wiki/<node_token>`，直接写进 `dest_feishu`。脚本内部用
`docs +create --parent-token <wiki节点>` **一步直接建在知识库里**（不是"先建文档再移入"，比应用凭证少 4 步），
自动剥 frontmatter，再用 `wiki +node-get` 把 docx token 反解成 wiki 节点链接。

⚠️ 实测踩过的坑（改脚本前先看）：
- 正文一律走 **stdin**（`--content -`）：`--content @file` 只允许 CWD / `/tmp` / `~/files` 下的路径，且在只读目录/云沙箱里写临时文件会崩，脚本已不再用 @file
- `docs +create` 返回的是 `/docx/<id>`，知识库里的固定链接必须换成 `/wiki/<node_token>`
- `docs +fetch` 的入参是 `--doc`（不是 `--url`），传 wiki node token 也能自动解析
- 身份必须显式 `--as user`，不带时可能被解析成 bot，列出的是应用空间而不是用户的知识库
- markdown 导入会保留粗体 `<b>`、引用 `<blockquote>`、标题 `<h2>`、链接 `<a href>`、行内代码 `<code>`，不需要额外的 XML 转换

转 C2 的条件：`lark-cli` 不存在 / user 身份未登录且用户不想授权 / 用户明确要用自建应用。**转就转，别在 C1 上反复试。**


⚠️ 连接器的具体命令以它自己提供的说明为准，**不要凭记忆编命令**；第一次用先看它的能力清单再动手。

**C2：自建应用凭证（默认路径，已端到端实测可用）**

它需要用户粘贴两段字符，但**这条路上每个环节都验证过**：凭证校验、域名自动提取、文件夹复用、索引页复用、权限缺失提示。在对话里把 3 步一次性说清（链接直给，不要让他自己搜）：

1. 打开 https://open.feishu.cn/app → 「创建企业自建应用」→ 名字随便（如"读书灵感"）
2. 进应用 → 「凭证与基础信息」→ 复制 **App ID** 和 **App Secret**（这两段发我）
3. 「权限管理」里勾选 `drive:drive` 与 `docs:document.media:upload`
   → 再到「版本管理与发布」创建版本并**发布**（不发布权限不生效）

（给用户看的成稿话术见 `references/get-credentials.md`，里面也列了「这些都不用你做」的清单，能显著降低用户的心理负担）

拿到两段字符后一条命令搞定，**脚本内部已自动**：验证凭证 → 取租户域名 → 找/建自己的「读书灵感」文件夹 → 写入落库位置并启用。

```bash
python3 scripts/setup.py install --kb feishu --app-id <id> --app-secret <secret>
```

- 报 `缺 drive 权限`（脚本会明说）→ 就是第 3 步没发布，让他补发布，**不要让他重新建应用**
- 想连知识库 → 追加 `--wiki <知识库链接>`（`https://<域名>/wiki/space/<space_id>` 形式），脚本会自动在根目录建索引页
  - 建不成（需要把应用加成该库成员/页面协作者，这一步应用身份做不了）→ 说清一句「知识库需要你在飞书里把应用加成成员」，**同时明确告诉他云空间文件夹已经可用**，别让他以为白配了
  - 不要为了知识库反复折腾：云空间文件夹是完整可用的落点

### 配方 D：IMA — 桌面端不要推荐

`mobile_ima_*` 只在手机端存在。桌面会话里**不要把它列为可选项**，也不要在 `kb_types` 里启用它（会在分发时每次都产生一次无意义跳过）。

### 配置完成后的判定

```bash
python3 scripts/config.py targets      # 非空 = 配好了
python3 scripts/setup.py doctor        # 全部 ✓ = 真配好了
```

之后所有轮次直接读配置，**不再询问用户存到哪**。用户临时说"这次只存乐享"时，不动全局配置，只在本次分发跑子集，并在收尾说明。

### 分发给别人

```bash
python3 scripts/setup.py pack
```

生成 `dist/reading-inspiration.zip`，已剔除 `config.json`、`library/` 下所有卡片、`__pycache__`。对方解压到 `~/.workbuddy/skills/` 后首次对话会自动从 doctor 开始，不会继承任何人的凭证和笔记。

### 增删知识库（配置已存在时）

- 想再加一个（"以后也存到飞书"）→ 只跑对应配方，**不重问已经配好的**
- 想停用某个 → `python3 scripts/config.py disable --kb obsidian`，本地已有卡片不动
- 查当前启用 → `python3 scripts/config.py targets`；查全量配置 → `config.py show`（凭证打码）／`config.py raw`（明文，只给脚本用）

## 标准流程

### 1. 先看用户给的是哪种来源（照片 / 链接 / 粘贴）

| 用户给的 | 走哪条 | 拿到原文的方式 |
|---|---|---|
| 图片、照片、截图 | **1A 拍照** | 直接读图（多模态识别） |
| `https://…` 链接 | **1B 链接** | `scripts/fetch_source.py` 自动抓 |
| 一大段文字 | **1C 粘贴** | 直接用，不必抓取 |

三者后面的切段、选段、记想法、分发完全一致，**不要为链接单独设计一套流程**。切段规则见 1A 末尾，三种来源通用。

### 1A. 拍照：OCR 识别 + 切段（无脚本，直接读图）

用 Read 工具直接读取用户的图片（多模态识别），提取：

- **书名**：从页面/页眉/封面判断；识别不出时问用户一次
- **页码/章节**：可得则记录，不可得留空
- **整页原文**：逐字保留原书文字，仅修正 OCR 明显错误（形近字、断行）
- 不确定的内容用 `[?]` 标注，不要臆造

**切段是这一步的产物**：按原文的自然段把整页切成 `① ② ③ …`，每段记录段号 + 全文。切段规则：

- 尊重原书排版的分段，不按字数强行切；一段过长（超过约 200 字）且原文有句群分层时，按句群再分并标 `②-1 ②-2`
- 标题行、页眉页脚、页码本身不算独立段（并入相邻段或丢弃），但要写进卡片的 `page` / 章节信息
- 同时识别图上有没有**手写标记**（划线、圈选、荧光笔、折角、星号、问号、旁注），标记覆盖到的段记为「用户已选」

#### 1A.1 归档原页照片（拍照来源必做，一条命令）

识别完立刻把原图存进库里 —— **用户拍的图在系统临时目录里，早晚会被清掉**，而卡片只有文字。OCR 一旦认错、或者事后想重新切段，没有原图就没有依据。

```bash
python3 scripts/save_page_image.py "<图片路径>" --book <书名> --page <页码>
```

- 自动压到长边 1800px / JPEG 78（实测文字仍清晰可读，657KB → 551KB）；压缩没收益就保留原图
- 落在 `library/pages/<书名>_<页码>.jpg`，重跑幂等覆盖
- **最后一行输出**（如 `pages/皇帝的账本_139.jpg`）就是卡片 frontmatter 里 `image:` 的值，原样抄进去
- 注意：图里不止一页、或用户一次拍了好几页时，逐页跑一次，别混成一张

### 1B. 链接：自动抓正文（一条命令）

用户发来链接（公众号文章、网页、飞书文档、在线报表、本地文件都算），**不要自己拼 curl 或让用户复制正文**，先跑：

```bash
python3 scripts/fetch_source.py "<链接或文件路径>"
```

它自动按来源选路，输出「标题 / 来源 / 字数 / 段数 / 存档路径」，**接着把正文全文打到 stdout** —— 直接读这段 stdout 就能切段，不用再 Read 存档文件。

| 链接类型 | 脚本内部怎么取 |
|---|---|
| 微信公众号、博客、新闻、一般网页 | 抓 HTML，自动抽正文容器，去掉导航/面包屑/「作者：」等噪声 |
| 飞书文档 / 飞书知识库页 | 走 `lark-cli docs +fetch`（用已登录的用户身份，**零凭证**） |
| 小红书（`xhslink.cn` 短链 / 笔记页） | 桌面 UA 会跳登录页 → 脚本自动换手机 UA 重抓；再不行用 `og:description` 兜底 |
| 本地 `.md / .txt / .html / .docx` | 直接读（docx 走 zipfile，不依赖 python-docx） |
| 本地 `.pdf` | 有 `pdftotext` 就直接抽；**没有就让 `workbuddy-pdf` 读成文本再 `--from-text` 交回** |
| 乐享页面 | **脚本取不到**（要 MCP），见下 |

**退出码决定下一步，别硬猜：**

- `0` 成功 → 直接切段
- `4` 是乐享页面 → 用 MCP `block_fetch_page`（`content_format=markdown`，`entry_id` 取链接里 `/pages/<id>` 那段）取正文，写到临时文件后用
  `python3 scripts/fetch_source.py --from-text /tmp/x.md --url-for-text "<原链接>" --title "<标题>"` 交回来，后续存档/切段一致
- `5` 正文太短 / 需要登录 / 靠 JS 渲染（知乎专栏、Notion、部分付费墙常这样）→ **不要反复重试**，直接跟用户说一句「这个链接要登录，你把正文粘过来就行」，等他粘贴后走 1C
  - 例外：**小红书已经能抓**（换手机 UA + `og:description` 兜底，脚本自动做）。若小红书仍然 exit 5，说明该笔记正文超长被平台截断或需要登录，这时才让用户粘贴
  - 脚本输出里带「⚠️ 正文取自页面摘要」时，**切段前先看结尾是否被截断**，并在收尾如实告诉用户
- `3` 网络失败 → 重试一次；仍失败按 `5` 处理

抓到的正文会存到 `library/sources/<日期>_<标题>_<链接哈希>-v<版本>.md`（本地文件/粘贴则用内容哈希），
frontmatter 含 `title / url / source_name / chars / paras / source_id / content_hash / content_status / version`。
**这张存档要留着**：卡片只摘几段，原文随时可回查。

- 同一篇重复抓 → 内容哈希相同，直接去重，不新增文件
- 同一链接内容更新了 → 版本号递增，**旧版原文保留**，不会覆盖
- 同一标题、不同链接 → 链接哈希不同，各存各的

#### 1B.1 长文档先给「小节清单」，不要一次切几十段

链接文档通常远长于一页书。**超过约 1500 字时，不要直接把整篇切成几十上百段丢给用户挑** —— 他无从下手。正确做法：

1. 先认文中的小标题（`01` / `一、` / `##` / 「第一部分」这类），列成小节清单
2. 让用户先选**记哪一节**（每节带一句内容摘要，别只给编号，同第 2 步的铁律）
3. 再在那一节内部按自然段切段，让他选具体段/句

短的（< 1500 字，比如一篇短文、一份周报）可以直接整篇切段。

#### 1B.2 原页面截图（用户说「附原页面截图」「把页面也存下来」时做）

文字摘录是我切过的，排版、配图、表格都丢了。要留证据就截一张原页：

```bash
python3 scripts/save_source_shot.py "<链接>" --slug "<日期>_<来源>_<标题简写>"
```

- 内部走 Chrome DevTools Protocol（手写 WebSocket，**纯标准库**，不装 playwright），手机 UA + 430px 视口，跟抓正文的口径一致
- 截**整页**（默认最多前 4200 css px，够 3~5 屏；要更长加 `--max-height 8000`），输出 JPEG 到 `library/shots/<slug>.jpg`
- **最后一行输出**（如 `shots/2026-10-25_小红书_xxx.jpg`）就是卡片 frontmatter 里 `image:` 的值
- 卡片其他字段照旧，`image:` 写法与拍照来源一致（Obsidian 会内嵌成「原页照片/原页面截图」折叠块）

**退出码 5 = 被安全验证（滑块）或登录墙拦住**：小红书、知乎、部分公众号会这样，脚本会自动识别「安全验证 / 拖动滑块 / 扫码登录」这类字样并**立刻退出，不要重试**（越试越进风控）。这时只有一条路：

> 这个平台把自动截图拦了（滑块验证）。你把它截一张图发我就行，我补进去。

拿到图后按 1A.1 归档（`save_page_image.py <图> --book <来源名> --page <日期>` 一样能用），把 `image:` 补进卡片，再重跑 `save_obsidian.py`；乐享那边用「申请附件上传 → PUT → 建 image 块」三步补进去（已实测，见 `references/kb-adapters.md`）。

### 1C. 粘贴：用户直接给了一段文字

不抓取、不套来源，直接切段。若用户希望留档（以后想回看全文），用：

```bash
python3 scripts/fetch_source.py --from-stdin --title "<标题>" --source-name "<来源>" < body.txt
```

### 2. 选出要记录的段落（关键步骤：默认不整篇收录）

按下面优先级决定记哪几段，**能自己判断的就别问**：

1. **用户发图 / 发链接时已说了**——引用某句原话、说「第 2 段」「划了线那段」「最后一段」、或直接说「这页全要」→ 按其指示定位到段号，直接往下走，不再追问
2. **图上有标记**（仅拍照来源）——采用标记覆盖的段，在回复里一句话说明「按你划线的第 2、4 段记录」，不额外要确认
3. **两者都没有**——把段落清单发给用户，等一次回复：

```
这页识别出 5 段，记哪几段？
① 我们总是高估……
② 真正的约束不在……
③ ……
回我段号就行：1,3 或 2-4；也可以直接粘贴想记的原句；「全部」就整页收。
```

链接文档把「这页」换成「这篇」、把段数换成实际段数，其余一样；长文档先走 1B.1 的小节清单。

#### 铁律：选段**默认用聊天正文的纯文本清单**，不用问答卡片

手机端**只显示按钮上的那一行 `label`，`description` 完全不渲染**（2026-10-25 实测截图确认）。所以
任何把正文放在 `description` 里的做法，在手机上等于没写；而按钮宽度有限，塞不下整段原文。

**默认做法（唯一推荐）**：直接在回复正文里列段落 —— 消息正文在手机、电脑两端都完整可见：

```
这页识别出 5 段，记哪几段？
① 那几年，败兵临时当强盗是很正常的事……（已存）
② 唐代宗终于领悟到，原来不折腾也是一种智慧……
③ 对于周智光无法无天的行为，唐代宗都予以容忍……
④ 周智光的地盘虽然很小，但是包含了两个极其重要的关卡……
⑤ 朔方郭子仪的地盘要大得多，眼下关中外围的第一道防线灵州……
回段号就行：3 / 3,4 / 3-5 / 全部
```

**只有下面三个条件全满足时，才可以用 AskUserQuestion**（它长于「三选一」这类短判断，不长于「认段落」）：

1. 候选 ≤ 4 个
2. 每个 `label` 都能放下该段**原文开头 10~20 字**（放不下就说明该用纯文本清单）
3. 不存在「其余段自己回段号」的需求

`label` 里必须是**逐字原文**，**禁止任何概括**：

| ❌ 概括（用户看不出原文） | ✅ 逐字原文段首 |
|---|---|
| `④ 那两座关卡怎么卡住郭子仪` | `④ 周智光的地盘虽然很小，但是包含了两…` |
| `④ 政治考量那段` | `④ 唐玄宗为什么要这么费劲地存私房钱呢…` |
| `第 4 段` / `④` | 同左，纯编号一律禁止 |

其他约定：

- 用户回数字（如「4」）时，**落库前先复述该段正文第一句**（「第 4 段，即『唐玄宗为什么要这么费劲地存私房钱呢……』那段」），让他有机会当场纠正
- 已经是同一页第二次记录时，清单里对已存段落标「（已存）」，别让用户重复选
- 用户在问答卡片里跳过/不答 ≠ 没有偏好，但**也不能一律当整篇**——长文整篇收录猜错的代价太大。
  按 `config.json` 的 `capture.default_when_unclear` 处理：
  - `ask`（默认）：本页/本篇 ≤ `capture.short_max` 段（默认 8 段）→ 按整页（整篇）记录并在收尾说明；
    超过阈值 → **不落库**，留待选草稿，一句话问「要哪几段」（长文一次就几十上百段，静默全收违背本意）
  - `all`：一律按整页（整篇）记录（用户明确选过这个偏好）
  - `draft`：一律保留待选草稿，等用户回话

选择解析规则：

- 段号 / 区间：`1,3`、`2-4`、`第2段`、`①②` 都认
- 粘贴原句：按文字匹配定位到对应段（模糊匹配即可，不必逐字一致）
- `全部` / `都要` / `随便` / `你定` → 整页/整篇收录，并在收尾反馈里说明「本次按整页（整篇）记录」
- 用户想要的只是某段里的**半句**时，按他粘贴/指定的句子范围截取，段号记 `2(部分)`，原文摘录里只放这一句
- 用户回得含糊（如只说「嗯」）→ 不要反复追问，按上一条的偏好处理（短文记整页并说明；长文留草稿）

**只把选中的段落写进卡片**；没选中的段不落库、不写进「原文摘录」，但段数要记进 frontmatter（`page_paras` / `paras`），方便日后回看「这页（这篇）还有哪几段当时没要」。

### 3. 征集当时想法

针对**选中的那几段**问（可一次 AskUserQuestion，带"跳过"选项）："看到这一段，你当时想到了什么？"用户口述即可，原话整理成一段，不润色观点、只顺语句。用户跳过则记 `无`。

用户在发图时已经讲了想法的话，直接用，别再问一遍。若选中的是多段，允许用户分段说，也允许一段话说完整页。

### 4. 生成 Markdown 灵感卡片并写本地镜像

每条灵感必须先写本地镜像（目录地图的数据源）：

```
拍照来源：~/.workbuddy/skills/reading-inspiration/library/<日期>_<书名简写>_<页码>.md
链接来源：~/.workbuddy/skills/reading-inspiration/library/<日期>_<来源名简写>_<标题简写>.md
```

**拍照来源**的卡片模板：

```markdown
---
book: 书名
chapter: 第 N 章 章节名
page: 页码或 ""
paras: 2,3
page_paras: 5
date: YYYY-MM-DD HH:mm
tags: [读书, 书名]
source: 书稿拍照
image: pages/书名_页码.jpg
---

# 书名 · 第 N 页

> 第 N 章 章节名 · 本页共 5 段，摘录第 2、3 段。

## 原文摘录

> **②** 逐字摘录……
>
> **③** 逐字摘录……

## 当时的想法

自己的想法……
```

**链接来源**的卡片模板（`kind: link` 是地图识别「这是文档不是书」的开关，别漏）：

```markdown
---
book: 微信公众号            # 来源名：书→书名；链接→站点/公众号/飞书文档，地图按它分组
title: 不懂构建知识体系，你把自己累死也是个低效学习者
url: https://mp.weixin.qq.com/s/xxxx
kind: link
source: 链接文档
page: ""
paras: 3,5
page_paras: 170
date: YYYY-MM-DD HH:mm
tags: [读书, 微信公众号]
source_file: sources/2026-10-25_不懂构建知识体系.md
---

# 不懂构建知识体系，你把自己累死也是个低效学习者

> 文章共 170 段，摘录第 3、5 段。

## 原文摘录

> **③** 逐字摘录……
>
> **⑤** 逐字摘录……

## 当时的想法

自己的想法……
```

字段规则（两套模板共用）：

- `paras`：选中的段号，逗号分隔；整页/整篇收录时写 `all`
- `page_paras`：本页（本篇）识别出的总段数
- `chapter`：章节名（有就填，Hub 页按它分组）
- `image`：拍照来源，第 1A.1 步归档脚本最后一行输出的相对路径；归档失败就留空，不要瞎编
- `title` / `url` / `kind: link` / `source_file`：链接来源必填。`source_file` = `fetch_source.py` 输出的「已存」路径，指向抓下来的全文存档
- **书名/标题进 frontmatter 一律加引号**：含 `: # [ ] { } , & * ! | >` 或以上述字符开头时必须写
  `book: "AI: 机器学习"`（内部 `"` 用 `\"` 转义）。拿不准就跑
  `python3 scripts/naming.py quote "文本"`，把它输出的那一段直接粘进 frontmatter
- `tags` 里的书名分量要清洗：`python3 scripts/naming.py tag "书名"`（逗号/方括号/冒号会破坏标签含义）
- 摘录只放选中的段，按原顺序；段号用 `**②**` 打头，读者一眼能看出是从哪段摘的
- 只摘半句时，`paras` 写 `2(部分)`，摘录里就只放那一句

### 5. 分发到知识库（逐个跑 `kb_types` 里的每个目标）

先 `python3 scripts/config.py targets` 拿到本次要跑的目标列表（用户说"这次只存乐享"时，用他指定的子集）。**按下面的顺序逐个执行，每个目标独立成败**：失败的那个只影响它自己，绝不能因为一个失败就中止其他目标，也绝不能删本地卡片。

- **ima**：把卡片 Markdown 正文（去掉 frontmatter）通过移动端 IMA 保存工具（`mobile_ima_saveContent`）直接落库，filename 用 `<书名或来源名>-<日期>.md`。**桌面端没有这个工具**——当前环境调不到 `mobile_ima_*` 时，把该目标记为「跳过」，在反馈里写一句「IMA 需在手机端补落库」，不要算作失败、不要留 `dest_ima` 以外的痕迹
- **obsidian**：`python3 scripts/save_obsidian.py "<卡片md路径>"`。它会顺带做 Obsidian 专属转换（**只改 vault 里那一份，本地卡片保持中立格式**）：摘录转 `> [!quote] 第 ④ 段` callout、`tags` 改成层级标签 `读书/<书名>`、有 `image:` 就把原页照片复制进 `<vault>/pages/` 并内嵌成可折叠 callout；有 `url:` 就把原文链接做成 `> [!info]- 原文` callout；末尾回链 `<来源名> · 灵感总览`
- **feishu**：看配置里的 `feishu.mode` 分流，别猜
  - `mode = "lark-cli"`（配置时走的是 C1）→ `python3 scripts/save_feishu_lark.py "<卡片md路径>"`，直接输出 `https://<域名>/wiki/<node_token>`
  - 否则（走的是 C2 应用凭证）→ `python3 scripts/save_feishu.py "<卡片md路径>"`，用 markdown 导入任务创建飞书文档（已实测可用，脚本内部已处理好以下三件事，正常情况不需要人工干预）
  - 上传素材必须用 `parent_type=ccm_import_open` + `parent_node=""` + `extra={"obj_type":"docx","file_extension":"md"}`；写成 `import_media` 会返回 `1061002 params error`
  - 上传前剥掉 frontmatter，否则那几行字段会变成文档开头的正文
  - 链接权限**按终态决定**，不再无条件放开：只留云空间的文档才设 `tenant_readable`；
    进知识库的文档由空间成员权限决定，提前放开全租户属于多余暴露。需要改默认行为用
    `feishu.link_share`（`auto` 默认按终态 / `tenant` 强制组织内可读 / `closed` 仅协作者 / `skip` 不动）
  - 应用身份建的文档默认只有应用自己是协作者——只留云空间时必须放权，否则用户点链接无权限
  - 应用需开通 `docs:document.media:upload` 与 `drive:drive`，**勾选后必须在「版本管理与发布」创建版本并发布才生效**，否则报 `99991672`
  - 报权限错误时如实告知用户缺哪个权限，**不要**静默降级为忽略这个目标
  - **已接进飞书知识库**（`feishu.wiki_space_id` / `wiki_parent_token` 有值时）：脚本会自动把文档从云空间移进知识库的 `wiki_parent_token` 节点下面，链接变成 `https://<domain>/wiki/<node_token>`。**移入知识库失败时脚本回落到云空间 docx 链接**，那说明授权掉了
  - 关于知识库的硬限制（不要再试图绕过）：
    - `POST /wiki/v2/spaces`（建知识库）**只接受 user_access_token**，应用身份必失败（`99991663`）。知识库必须由用户手动建
    - 应用被加为**页面的「协作者/文档应用」**时，只能在该节点下建子节点、移动文档进去；`GET /wiki/v2/spaces`、`/spaces/{id}`、`/spaces/{id}/nodes` 会一直 `131006 tenant needs read permission`。**这是正常的，不代表没授权**，不要去"修"它
    - 因此索引页内容只能由本地地图反推写入，不能靠遍历知识库节点生成
- **lexiangla（本机默认，走已连接的乐享通路，不需要 Token）**：用乐享的 `entry_import_content` 直接建页
  - `name` = 卡片一级标题（如 `书名 · 第 N 页`）
  - `content` = 卡片正文 Markdown（去掉 frontmatter，保留 `## 原文摘录` / `## 当时的想法` 两个小节）
  - `parent_id` = 配置里的 `lexiangla.folder_entry_id`（`读书灵感` 文件夹）；该字段缺失时回退 `space_id` = `lexiangla.space_id`
  - ⚠️ `space_id` 与 `parent_id` **必须同时传**，只传 `parent_id` 会报 `parent entry not belong to space`
  - 返回 `entry.id`，卡片链接为 `https://lexiangla.com/pages/{entry.id}`（工具说明里写的 `mcp.lexiang-app.com` 域名实测 404，不要用）
  - 只有乐享通路不可用、且用户自备 API Token 时，才回退 `python3 scripts/save_lexiangla.py "<卡片md路径>"`

**投递结果由脚本自己记账**，你不用猜有没有建成：

- 飞书（两条路）与 Obsidian：脚本成功后**自己把链接写回** `dest_feishu` / `dest_obsidian`（同名 key 原地替换，不会追加重复行），并记进 `library/.delivery.json`
- 乐享 / IMA：脚本管不到远端 id，仍由你在成功后 Edit 补写 `dest_lexiangla` / `dest_ima`
- **重复投递是安全的**：脚本投递前查台账——已建成直接复用（并补写可能缺失的 `dest_*`），跳过创建
- **崩溃恢复不会认错卡**：飞书文档末尾带一行 `> 卡片：<卡片文件名>`；上次停在「已决定投但没确认」时，
  脚本在目标父节点下列同名候选，**逐个回读正文、按完整标识值精确比对**（`card` 不会命中 `card2` 的文档），
  是才沿用。标题只用来筛候选——同一页的两张摘录卡一级标题可以完全一样，只按标题认领会把第二张卡的内容并进第一张
- **同名候选有多份时逐份核对，不能只看第一份**：第一条可能是别的卡建的、本卡在后面；
  只看第一条会误判成「本卡没建过」再建一份。只有**全部候选都读成功且都不是本卡**，才允许新建
- **列目录要翻完所有页**：只读第一页时，本卡若在第二页就会被判成「不存在」。
  应用身份翻 `page_token`（看 `has_more`/`next_page_token`），连接器用 `--page-limit 0`（不限页数）。
  翻到一半失败、或说还有下一页却没给 token，都算「没查成」，不是「确实没有」
- **核对不成就不投**：列不到目录（缺 `feishu.wiki_space_id`、无权限、网络失败、没翻完页）、
  **或有一份候选正文读不出来**时，脚本以**退出码 4** 结束并保留待核对状态（读不出来 ≠ 不是本卡）。
  这时你要做的是「确认配置或去知识库看一眼」，**不要**自行判断成「远端没有」再建一份
- **升级前的旧卡片不会被重投**：卡片里已有 `dest_feishu` 但没有台账的，脚本按已投递接纳并补建记录
- 真需要强制重投（比如远端那份被手动删了），两条路都可以：
  - `python3 scripts/delivery.py reset "<卡片路径>" --kb feishu` 再跑一次分发
    （不给 `--parent` 就清这个目标下**全部落点**的记录）
  - 或直接 `save_feishu.py / save_feishu_lark.py "<卡片路径>" --force`
- **重投会带「代次」**：reset / `--force` 会让该卡这一目标的代次 +1，旧文档 id 进
  `.delivery.json` 的作废清单（`superseded`）。核对时这些旧文档**不参与认领**，
  所以「重投第一枪失败、第二次重试」也不会把上一代那份捡回来。
  墓碑（重投要求）只有一次投递**真正成功**才消失——不是一开始投就清掉。
- **台账只认当前代次**：`--force` 创建完、还没写台账就崩了时，下次重跑不会再把
  上一代的旧链接当结果返回（旧记录已降级为 intent + 作废清单）
- 排查用：`python3 scripts/delivery.py pending` 看上次没确认的投递，`... show "<卡片路径>"` 看这张卡在各目标的记录

**Obsidian 收尾补刀**：`save_obsidian.py` 现在会自己写 `dest_obsidian` 并登记台账，但它生成的是**拷贝**：
如果你在飞书/乐享落地之前跑，vault 里那份副本拿不到后来的链接。所以多目标分发时，
所有目标都写完之后**再跑一次** `python3 scripts/save_obsidian.py "<卡片md路径>"`，让副本和地图带上最终链接。

多目标时，同一条灵感的正文完全相同，只是落到不同地方；不要为此改标题或正文。

### 6. 更新目录地图与每本书的 Hub 页

每次落库后执行：

```bash
python3 scripts/build_map.py
```

一次生成三层结构：

```
library/目录地图.md             总目录：按来源分组，每个来源指向自己的 Hub
library/books/<来源名>.md       Hub：书按章节分组；链接文档按时间平铺
library/<日期>_<书名>_<页>.md    卡片本体（拍照来源）
library/sources/<日期>_<标题>.md 链接原文存档（链接来源，fetch_source.py 自动写）
```

- `目录地图.md`：按来源分组 → 按时间列出每条灵感（页码或文章标题 + 段号 + **每个知识库各一个链接** + 一句话想法），并在来源下给一行 `> 总览：…`。链接来源的条目直接显示文章标题（点开回原文），分组标题不带书名号
- `books/<来源名>.md`：一个来源一页，**按 `chapter` 字段自动分组**、章内按页码排序；链接来源没有章节，按时间平铺。每条列「页码/标题 · 段号 — 想法」+ 各知识库链接。来源一多就靠这一层，不要让总目录无限长
- 若 `kb_types` 里有 obsidian，用 `python3 scripts/build_map.py --vault "<vault目录>"` 再写一份到 vault：本地卡片渲染成 `[[文件名|Obsidian]]` 双链（点一下能跳到卡片），Hub 写成 `<书名> · 灵感总览.md`，卡片末尾自动回链 Hub。**冲突检查、实际写入、回链用的是同一个文件名**
- **Hub 只覆盖自己生成的文件**：识别靠 frontmatter 的生成者签名（`generator: reading-inspiration` + `type: book-hub` + 书名一致）。vault 里的手写笔记——**哪怕也写了 `book: 书名`**——一律不被当成 Hub：脚本让位到带短哈希的文件名（`书名_<hash> · 灵感总览.md`，再撞就 `_2`），并在写前再校验一次。旧版留下的 Hub 没有签名，按结构识别后**就地迁移**（补上签名，文件名不变）
- 其余目标地图保留在本地镜像，用户要看时展示
- **飞书知识库里的索引页**（`feishu.wiki_index_doc` 有值时）：每次落库后刷新，命令
  `python3 scripts/setup_feishu_wiki.py index`（会自动读 `wiki_index_doc`）。
  两种身份都支持：`mode=lark-cli` 用用户身份整页重写（`docs +update --command overwrite`），
  应用凭证模式用块替换；两边**共用同一份内容**（`index_markdown()`，去掉 H1 与 `> 总览：` 这类本地死链行）。
  写完会回读一次，**按写入前记录的链接清单逐项核对**：链接丢了会说「n/m 个链接找不到」，
  回读失败会说「未验证」——这两种都不是成功，别当成已经好了。它用块替换重写整页正文，
  把 `**粗体**` 和 `[文字](链接)` 都转成飞书富文本。⚠️ 飞书超链接必须写成
  `text_run.text_element_style.link.url` 且 **url 要 URL 编码**，写成 `text_run.link.url` 会被静默丢弃（不报错、只是没链接）
- 乐享模式下文件夹里有一页《读书灵感目录地图》（`lexiangla.map_entry_id`）：它是文件夹索引，**每次落库后都要同步** —— 用 `block_update_page` 的 `command=replace_content` + `new_str`（`content_format=markdown`，`entry_id` = `map_entry_id`）整页覆盖（注意：只传 `replace_content: true` 会报 `command is required`）。内容 = 固定头部（一句说明）+ `## 怎么用` 那五步 + `## 已收录`；「已收录」下面贴本地地图正文，但要把书名那行降一级成 `### 《书名》`，否则和 `## 已收录` 同级、结构会乱
- **用户说「拖到根目录 / 挪到 XX 下面」**（飞书知识库）：`python3 scripts/setup_feishu_wiki.py move --node <节点token>`（不带 `--parent` 就是知识库根目录，带了就挪到该父节点下），子节点会跟着走。⚠️ 别用 `move_docs_to_wiki` 做库内挪位置——它对已经在知识库里的文档返回 `code:0` 但实际不动；库内移动只能用 `/wiki/v2/spaces/{space}/nodes/{node}/move`，且 `target_space_id` 与 `target_parent_token` 不能同时为空（挪到根目录要传 `target_space_id=<本库 id>`）
- 若 `kb_types` 只有 ima/feishu，地图本身不额外推送（避免刷屏），用户明确要求时才同步一份过去
- ⚠️ 把地图贴到**远端**索引页（飞书 / 乐享）时，要**删掉 `> 总览：…` 那一行** —— 它指向 `books/<书名>.md`，是本地相对路径，在远端是死链。远端目前只有「一本书一节」的平铺结构，还没有每本书的 Hub 页

### 7. 收尾反馈（简洁）

告诉用户：**来源是什么（照片 / 链接）**、识别出的书名+页码或文章标题、**记了哪几段（第 2、3 段 / 全页 5 段）**、摘录字数、想法是否已记录、**每个知识库分别落到哪了（附链接或路径）**、原文已归档（照片 → `library/pages/`；链接 → `library/sources/`）、目录地图与来源 Hub 已更新。按整页（整篇）记录时明确提一句；有目标失败时单独列出来并说清原因。

链接来源额外补一句原文链接，方便用户回看全文。

## 失败处理

- 图片无法识别 → 让用户重拍（光线充足、文字占画面主体）
- **链接抓不到正文**（`fetch_source.py` 退出码 5：要登录、靠 JS 渲染、付费墙）→ 一句话请用户把正文粘过来，不要反复换抓取方式硬试；粘贴后按 1C 继续，正文一样能存档
- **链接文档太长** → 存档永远是完整原文（抓到的全文都写进 `library/sources/`），`--max-chars`
  只限制终端显示多少字。终端看到「只显示前 N 字」时，按 1B.1 先给小节清单，不要拿截断的尾部当全文；
  需要看末尾就用 `sed -n` 或直接读存档文件
- 乐享页面链接 → 脚本一定返回 4，走 MCP `block_fetch_page` 取正文，不要试图用 curl 抓（拿不到）
- **某个目标分发失败 → 其他目标照常跑完**，本地镜像保留，告知用户是哪个知识库失败、原因是什么，不删除、不暂停；该卡片缺的那个 `dest_<kb>` 字段就是待补发的标记，下次触发本技能时先补发再继续
- 所有目标都失败 → 只落本地镜像，明确告诉用户这次的卡片只在本地，等他处理完凭证后可以一句话补发
- **环境缺件（没有浏览器 / 没有压缩工具 / 没有 vault / 没有连接器）→ 跳过，不算失败**：
  在 `setup.py env` 里能提前看到，不要重试、不要当作错误报给用户，只在结果里说清「哪一项这次没做、怎么补」
- 技能目录只读 → 素材库/配置用 `READING_LIBRARY` / `READING_CONFIG` 指到可写位置，别去改技能目录权限
- Token 过期类错误 → 提示用户重新提供凭证并更新 config（只影响该目标，其他目标不受牵连）

## 参考

- `references/runtime.md`：本机 / Windows / Linux / 云沙箱的能力矩阵、降级行为、三个环境变量、跨平台踩坑
- `references/kb-adapters.md`：四类知识库的接口细节与字段映射
- `references/sources.md`：链接来源的抓取路由、退出码、抓取质量五个坑与已知限制
- `scripts/fetch_source.py`：链接/文件 → 干净正文（公众号、网页、飞书文档、docx、pdf、粘贴），自带 `--from-stdin` / `--from-text` / `--json`
- `scripts/naming.py`：文件名 / YAML / 标签 / 双链的编码规则，写 frontmatter 拿不准时用它出结果
- `scripts/delivery.py`：投递台账（三层 ID：source / card / delivery），补发前查它
- `references/design-refs.md`：同类技能（book-capture / obsidian-skills / 微信读书 / book-to-skill）调研结论，含「借鉴了什么、为什么没照搬」
