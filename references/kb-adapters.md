# 知识库适配器参考

## 配置入口：`scripts/setup.py`（先看这个）

所有「怎么接上一个知识库」的事都收敛到一个脚本，避免用户跳平台：

| 命令 | 作用 |
|---|---|
| `setup.py doctor` | 体检：每个知识库「已配置 / 可自动接 / 缺什么」+ 一句下一步 |
| `setup.py detect obsidian` | 自动扫 vault（obsidian.json + iCloud + 常见目录 + 去嵌套） |
| `setup.py install --kb obsidian [--vault PATH]` | 落配置，自动建 `读书灵感` 子目录 |
| `setup.py install --kb feishu --app-id --app-secret [--wiki LINK]` | 验凭证 → 取域名 → 建文件夹 → 可选建知识库索引页 |
| `setup.py install --kb lexiangla --space-id --folder-entry-id --map-entry-id` | 落乐享配置（目录由 Agent 用 MCP 自动建好） |
| `setup.py pack` | 打可分发 zip，剔除 config.json / 卡片 / pycache |

设计约定（改了会被下游踩）：
- **飞书有两条已实测的通路，看 `feishu.mode` 分流**：
  - `mode = "lark-cli"`（零凭证，连接器用户身份）→ `save_feishu_lark.py`，一步建进知识库
  - 否则（自建应用凭证）→ `save_feishu.py`，云空间导入 + 移入知识库
  - 看不到连接器入口就直接走凭证那条，**不要为"更优雅"让用户反复找入口**
- 乐享、Obsidian 全部自动，IMA 桌面端不参与
- `--wiki` 失败只降级、不阻断：云空间文件夹永远是可用的落点
- 缺的是权限（`99991672` 一类）就明说「去发布版本」，不要让用户重建应用

### 飞书连接器（lark-cli）实测要点

CLI：`@larksuite/cli`，可执行文件 `lark-cli`，技能文档在连接器包 `skills/lark-*` 下。
**应用身份 vs 用户身份的差距比想象中大**：

| 操作 | 应用身份（tenant token） | 用户身份（`--as user`） |
|---|---|---|
| 列知识空间 | ❌ 131006 | ✅ `wiki +space-list` |
| 建知识库 | ❌ 99991663 | ✅ `wiki +space-create` |
| 建节点 | ✅ 仅限已授权的页面下 | ✅ 全库 |
| 建文档进知识库 | 需"上传素材→导入→轮询→设权限→移入"5 步 | ✅ `docs +create --parent-token` 一步 |
| 管成员 | ❌ | ✅ `wiki +member-add` |

命令要点：
- 建文档进知识库：`lark-cli docs +create --as user --doc-format markdown --title T --content - --parent-token <wiki_node>`，正文从 stdin 喂
  - **不要用 `--content @file`**：它只允许 CWD / `/tmp` / `~/files` 下的路径，`tempfile` 默认的 `/var/folders/…` 会被拒；
    更要紧的是「写临时文件到 CWD」在只读安装目录 / 云沙箱里直接崩。stdin（`--content -`）没有这些限制
  - 返回 `data.document.url` 是 `/docx/<id>`，要换 `/wiki/<node_token>` 得再跑 `wiki +node-get --node-token <id>`
- 读文档：`lark-cli docs +fetch --doc <docx token 或 wiki node token>`（**没有 `--url`**）
- 删节点：`lark-cli wiki +node-delete --node-token <t> --obj-type wiki --as user --yes`（异步，返回 task status）
- markdown 导入保真：`<b>` 粗体、`<blockquote>` 引用、`<h2>` 标题、`<a href>` 链接、`<code>` 行内代码都保留

---

四类知识库的分发方式与字段映射。灵感卡片统一为 Markdown + YAML frontmatter。

**一条灵感可以同时分发到多个知识库**：`config.json` 的 `kb_types` 是列表，逐个跑；每个目标成功后写自己的 `dest_<kb>` 字段，失败的目标字段留空作为补发标记。目标之间互不影响——一个失败不能拖垮其他，也不能删本地卡片。

| frontmatter 字段 | 含义 | 分发用途 |
|---|---|---|
| `book` | 书名 | 文档标题、归类 |
| `page` | 页码 | 正文标注 |
| `paras` | 选中的段号（`2,3` / `2(部分)` / `all`） | 标注摘录范围；目录地图显示「第 N 页·第 2,3 段」 |
| `page_paras` | 本页识别出的总段数 | 回看「这页还有几段当时没要」 |
| `date` | 捕获时间 | 排序 |
| `dest_lexiangla` | 乐享页面链接 | 目录地图显示「乐享」 |
| `dest_feishu` | 飞书文档链接 | 目录地图显示「飞书」 |
| `dest_obsidian` | vault 内文件路径 | 目录地图显示「Obsidian·本地」 |
| `dest_ima` | IMA 返回的标识 | 目录地图显示「IMA」 |
| `dest` | 旧卡片的单个链接 | 目录地图显示「原件」，仅兼容保留 |
| `tags` / `source` | 标签与来源 | 知识库标签 |

## IMA 知识库（手机端）

无需凭证。调用移动端工具 `mobile_ima_saveContent`：

- `content`: 卡片正文（去掉 frontmatter）
- `filename`: `<书名>-<YYYYMMDD>.md`

若返回"未连接 IMA"，先调用 `mobile_ima_authorize` 征得授权后重试。

## 本地 Obsidian

- 配置 `obsidian_vault` 为 vault 内目录（建议如 `<vault>/读书灵感/`）
- `save_obsidian.py` 负责复制卡片，并调用 `build_map.py --vault <目录>` 生成带 `[[文件名|Obsidian]]` 双链的《读书灵感目录地图.md`（双链在 Obsidian 里可点击跳转）
- 脚本额外输出 `obsidian://open?path=...` URI 供移动端跳转
- 若路径无效（vault 未挂载/未同步到沙箱），提示用户给出沙箱可访问的路径或改用手机本地路径

## 飞书知识库

- 凭证：App ID / App Secret（自建应用），可选 `folder_token` 指定云空间目录；不填则落到应用根目录
- 链路：`tenant_access_token` → `drive/v1/medias/upload_all` → `drive/v1/import_tasks`（md→docx）→ 轮询取 docx token → 放开链接权限
- 已实测的关键参数（踩过的坑都在这）：
  - 上传素材的 `parent_type` 必须是 `ccm_import_open`，`parent_node` 传空字符串，并带 `extra={"obj_type":"docx","file_extension":"md"}`；写成 `import_media` 会报 `1061002 params error`
  - `file_extension` 必须与实际文件后缀严格一致（`.md` 对 `md`），否则报 `1069910`
  - 上传前剥掉 YAML frontmatter，否则那几个字段会变成文档开头的正文
  - 导入得到的文档，用 `PATCH drive/v1/permissions/{doc_id}/public?type=docx` 设 `link_share_entity=tenant_readable`；不设的话应用身份建的文档只有应用自己是协作者，用户点链接打不开
  - 上传的 file_token 有效期 5 分钟，上传后要立刻创建导入任务
- 权限：`docs:document.media:upload` + `drive:drive`；**勾选后必须在「版本管理与发布」创建版本并发布才生效**，否则报 `99991672`
- 清理：`DELETE drive/v1/files/{doc_id}?type=docx` 可删掉重复导入的文档

### 归档进飞书知识库（wiki）

本机已接好：知识空间 `7700475792345533465`（读书灵感），层级是
`读书灵感目录地图（索引页，挂在知识库根目录）→ 各页卡片`。
（最初曾挂在空间自带的「项目启动」节点下，用户要求拖到根目录，已迁出。）

- **建库这一步应用做不了**：`POST /wiki/v2/spaces` 官方明确「不支持 tenant access token」，实测 `99991663`。
  只能由用户手动建，再把应用加为**页面协作者（可管理）**
- 加完之后的表现很容易误判：
  - ✅ `move_docs_to_wiki` 成功、`POST /spaces/{id}/nodes`（在授权页下建子节点）成功
  - ❌ `GET /wiki/v2/spaces` 仍是空、`GET /wiki/v2/spaces/{id}` 和 `/nodes` 一直 `131006 tenant needs read permission`
  - 这是**页面级授权**的正常表现，不是没配好。别反复让用户去加成员
- 移文档：`POST /wiki/v2/spaces/{space_id}/nodes/move_docs_to_wiki`
  body `{parent_wiki_token, obj_type:"docx", obj_token:<docx token>, apply:true}`
  - 同步返回 `data.wiki_token`，或异步返回 `data.task_id`
  - 任务查询：`GET /wiki/v2/tasks/{task_id}?task_type=move`，取 `task.move_result[0].node.node_token`
  - 移动后原 `drive` 文档读不到了（`GET /drive/v1/files/{token}` 返回 404），但 `obj_token` 不变
  - 链接：`https://<domain>/wiki/<node_token>`
- 索引页正文刷新：docx 块替换
  - 读子块 `GET /docx/v1/documents/{doc}/blocks/{doc}/children?page_size=500`
  - 清空 `DELETE /docx/v1/documents/{doc}/blocks/{doc}/children/batch_delete` body `{start_index,end_index}`
  - 写入 `POST /docx/v1/documents/{doc}/blocks/{doc}/children` body `{children:[...], index}`
  - block_type：2 文本 / 4 二级标题 / 12 无序列表 / 15 引用
  - ⚠️ 超链接必须写进 `text_run.text_element_style.link.url`，且 **url 要 percent-encode**；
    写成 `text_run.link.url` 不报错但链接会被静默丢弃
- 库内挪位置：`POST /wiki/v2/spaces/{space_id}/nodes/{node_token}/move`
  body 二选一：`{target_space_id:<本库 id>}` = 挪到本库根目录；
  `{target_parent_token:<父节点>}` = 挪到指定父节点下。子节点会跟着走。
  - ⚠️ 两个字段都空会报 `131002 param err`
  - ⚠️ **不要用 `move_docs_to_wiki` 做库内移动**：对已经在知识库里的文档它返回 `code:0 success`
    但什么都不做（静默无效），它只负责把云空间文档搬进知识库
  - 节点 token、`obj_token` 移动前后都不变，只是 `parent_node_token` 变了，所以配置无需改动
- 相关脚本：`setup_feishu_wiki.py list|init|migrate|index|move`

## 乐享知识库

**默认走已连接的乐享通路（无需 Token）**

- 建页工具：`entry_import_content`，参数 `name` / `content`(markdown) / `parent_id`(目标文件夹 entry_id) / `space_id`(**必传**，与 parent_id 同时给，否则报 `parent entry not belong to space`)
- 返回 `entry.id`，链接：`https://lexiangla.com/pages/{entry_id}`
- 本机已建好结构：知识库「沁芳居士的个人知识库」→ 文件夹「读书灵感」→ 索引页「读书灵感目录地图」，三个 id 都记在 `config.json` 的 `lexiangla` 段

### 给乐享页面补一张图（原页面截图 / 原页照片）——已实测

`entry_import_content` 的 markdown 里的 `![](本地路径)` **不会上传**，只是个文字标记。
要真出图，必须走块上传三连（2026-10-25 实测通过，返回的 image block 带 `file_id`）：

1. `block_apply_block_attachment_upload` → `{entry_id, name:"xxx.jpg", size:"<字节数>", mime_type:"image/jpeg"}`
   拿到 `session_id` + 预签名 `upload_url`
2. HTTP PUT 直传（**不是 MCP 调用**）：
   `curl -X PUT -H "Content-Type: image/jpeg" --data-binary @<本地图> '<upload_url>'`
   - 签名里带了 `content-length`，**文件大小必须与第 1 步的 `size` 一字不差**，否则 403
   - 成功返回 HTTP 200，无响应体
3. `block_create_block_descendant`：
   `{entry_id, index:-1, descendant:[{block_type:"image", image:{session_id, caption:"原页面截图", align:"center"}}]}`
   - 属性名是 **`align`**，写成 `alignment` 会被忽略（默认变左对齐）
   - 想插到开头用 `index: 0`；想插到某段之后用 `parent_block_id` + `index`

图片块的 `file_id` 由服务端按 `session_id` 自动生成并回填；**`block_update_page` 不接受 `session_id`**，
它只认 `file_id`，所以「补图」这件事必须用 `block_create_block_descendant`，不能整页 replace。

**旧式 API（仅在连接不可用且用户自备 Token 时回退）**

- 凭证：企业 API Token（管理后台 → 系统设置 → API 获取），team 为企业标识（域名前缀）
- 接口：`POST https://api.lexiangla.com/cgi-bin/v1/docs`，Body `{title, content}`，Bearer 鉴权
- content 支持 Markdown；返回 `data.id` 拼接文档链接

### 链接权限：按终态决定，不要无脑放开

应用身份建的文档默认只有应用自己是协作者，所以「必须放权」只对**留在云空间**的文档成立。
一旦要移进知识库，可见性由空间成员决定，提前把链接设成组织内可读属于多余暴露。

| 终态 | 默认动作 | 理由 |
|---|---|---|
| 移进知识库（`wiki_space_id` 有值） | 不动链接权限 | 空间成员权限已经覆盖；用户是空间成员就能看 |
| 只留云空间 | `tenant_readable` | 否则用户点链接提示无权限 |

`feishu.link_share` 可覆盖：`auto`（默认，按上表）/ `tenant`（强制组织内可读）/ `closed`（仅协作者）/ `skip`（完全不动）。

### 投递幂等：台账 + 远端核对

「本地脚本 + 远端 API」两段式投递有个天然窗口：远端建成了、本地还没记下来。只靠卡片的
`dest_<kb>` 字段补不上这个窗口（它是事后记录），所以另有一份台账 `library/.delivery.json`：

- 三层 ID：`source_id`（原文）→ `card_id`（卡片文件名）→ `delivery_id`（card + kb + 父节点）
- 投递前写 `intent`，成功后写 `committed`（含 `remote_id`/`url`）
- 下次投递：`committed` 直接复用，并把可能缺失的 `dest_*` 再写一遍（上次可能倒在写回这一步）
- 卡在 `intent` 时核对，**标题只用来筛候选，必须回读正文确认是本卡**：
  - 飞书连接器：`wiki +node-list --space-id <space> --parent-node-token <parent> --page-all --page-limit 0`
    （`--space-id` 是**必填**，缺了直接被 validation 拒绝；取不到就用 `wiki +node-get` 反查；
    `--page-limit 0` 是「不限页数」——写死 `5` 时子节点多的库会被截断，截断后找不到本卡 = 再建一份）
    → 再 `docs +fetch --doc-format markdown` 找正文里的 `卡片：<card_id>` 标识
  - 应用身份：`drive/v1/files?folder_token=` 列候选 → `docx/v1/documents/<id>/raw_content` 找标识
  - **列目录必须翻完所有页**（`drive/v1/files` 用 `page_token` 翻页，看 `has_more` + `next_page_token`）：
    只读第一页时，本卡若在第二页就会被判成「不存在」。**任何一页失败 / 说还有下一页却没给 token /
    token 重复 / 超过页数上限没翻完**，都按「没查成」处理（退出码 4），不能当成「确实没有」
  - **同名候选可能不止一份，必须逐份回读，不能只看第一份**（`find_drive_doc` 返回全部 token）：
    第一份可能是别的卡建的、本卡在后面，只看第一份就会判成「不是我的」再建一份，越修越乱。
    只有**全部候选都读成功且都不是本卡**，才算「确实没建过」
  - 标识必须**整值精确比较**（`delivery.has_card_marker`），不能用子串：卡片 `card` 会命中
    另一张 `card2` 文档里的 `> 卡片：card2`，把别人的文档认成自己的
  - 应用身份的列目录**先看业务码**：`code != 0`（如 131006 无权限）时 `data.files` 会缺失，
    若只捕获异常、或把缺失当空列表，就会把「没权限」读成「目录是空的」然后新建
- 结果分开处理，不能混：
  | 结果 | 动作 |
  |---|---|
  | found（任意一份带本卡标识） | commit 复用，不重建 |
  | absent（查到了、确实没有） | 允许新建 |
  | ambiguous（全部候选都读过、标识都对不上） | 新建，并提示可能有重复；**绝不认领** |
  | unread（有一份正文读不出来） | 不新建，退出码 4，保持 intent 待核对 |
  | error（没查成） | 不新建，退出码 4，保持 intent 待核对 |
- 旧卡片（有 `dest_*`、无台账）按已投递接纳，不重投
- 链接回写也由脚本做（`dest_feishu` / `dest_obsidian`），不再依赖 Agent 手动 Edit
- 排查：`python3 scripts/delivery.py pending` / `show <卡片>` / `reset <卡片> --kb feishu`
  （`reset` 不给 `--parent` 时清该目标下**全部落点**）
- `reset` 只删台账不够：卡片里的 `dest_*` 还在，下次运行会被「旧链接接纳」认回已投递。
  所以 `reset` 会同时留一条 `redeliver` 墓碑（`library/.delivery.json` 的 `redeliver` 数组）。
  也可以给脚本加 `--force`（两条飞书路径都支持），效果等同「这次不看台账与旧链接」。
- **代次（epoch）**：`reset` / `--force` 把该卡在该目标的代次 +1，旧记录的 `remote_id`
  进 `superseded`（作废清单），记录状态回到 `intent`。规则：
  | 判断 | 依据 |
  |---|---|
  | 台账「已建成」能不能直接复用 | 只有 `rec.epoch == 当前代次` 才算 |
  | 核对候选能不能被认领 | 候选的 node/obj token 在 `superseded` 里 → 跳过 |
  | 重投要求什么时候消失 | `commit()` 成功那一次，**不是**发起投递时 |
  没有代次时，「重投第一枪失败 → 第二次重试」会看到上一代那份旧文档：标识对得上、
  标题也对得上，于是又被认领回去，重投意图静默丢失。`--force` 创建完但没来得及写台账时，
  旧记录也会被当成「已建成 + 旧链接」直接返回，用户以为重投成功。
- `superseded` 跨代次累积（保留历史作废 id），所以连续重投几次之后，任何一代的旧文档
  都不会被重新认领。

### 索引页刷新：两种身份都要能跑

`feishu.wiki_index_doc` 有值就必须每次刷新，而连接器安装时**会写入这个字段**，所以连接器模式
不能只支持应用凭证：

| 身份 | 实现 | 备注 |
|---|---|---|
| `mode=lark-cli`（连接器） | `lark-cli docs +update --command overwrite --doc-format markdown --content -` | 用户身份，零凭证 |
| 应用凭证 | `docx` 块替换（batch_delete + children 分批写入） | 链接用 `text_element_style.link.url`，URL 要编码 |

两种身份共用同一份内容（`index_markdown()`，来自本地目录地图，去掉 H1 与 `> 总览：` 这类本地死链行），
避免两套渲染跑偏。

写完回读一次，**按写入前记录的链接清单逐项核对**（`expected_links()`）：
只检查内容里有没有 `href` / `"link"` 说明不了问题——链接被全部丢掉、或只剩纯文字时都可能是别的形态。

| 回读结果 | 输出 |
|---|---|
| 链接都在 | `回读确认：n 个链接都在文档内容里` |
| 缺了若干 | `⚠️ 回读：n/m 个链接在文档里找不到`（文字刷新成功，但链接可能不可点） |
| 读不出来 | `⚠️ 回读失败…未验证`（**不是成功**，别当成已经好了） |
