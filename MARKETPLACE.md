# 上架材料（WorkBuddy 技能市场）

提交入口：<https://open.workbuddy.cn> → 【添加技能】→【创建技能】→ 补全提示词 / 提交 zip。
官方结构说明：<https://open.workbuddy.cn/docs/skill>。解析失败可邮件 `openworkbuddy@tencent.com`。

## 一、要提交的包

```bash
cd ~/.workbuddy/skills/reading-inspiration
python3 scripts/setup.py pack --market
# → dist/reading-inspiration-market.zip（约 89 KB）
```

这个包严格按平台结构，只含：

```
reading-inspiration/
├── SKILL.md
├── references/     (sources / runtime / kb-adapters / design-refs / get-credentials)
└── scripts/        (16 个 Python 脚本)
```

**不含** README、LICENSE、tools/、library/、config.json、dist/ —— 那些是给 git 仓库看的，不是给解析器看的。
`pack --market` 会先校验 frontmatter 八个必填字段，缺一项直接报错退出，不会把注定被拒的包传上去。

## 二、商店卡片文案（可直接复制）

| 字段 | 内容 |
|---|---|
| `name` | `reading-inspiration` |
| `display_name` | 读书灵感捕捉 |
| `display_name_en` | Reading Inspiration Capture |
| `version` | `1.0.0` |
| `author` | `qinfangwu1991-ux` |

**description_zh（详情页正文）**

> 拍一页书、发一个文章链接、或粘一段文字，按自然段切好让你挑，只记你选中的那几段，并写下当时的想法；一次分发到乐享、飞书、本地 Obsidian，同时维护一份读书灵感目录地图。

**description_en**

> Turn a book-page photo, an article link, or pasted text into structured reading inspiration cards. It splits the source into paragraphs, keeps only the passages you pick, records what you thought at that moment, and distributes each card to Lexiang, Feishu and Obsidian while maintaining an index map.

**examples_zh（详情页「试试这样用」）**

1. 拍一页书稿照片发我，我记第 4 段，想法是「皇帝也得筹钱」
2. 这个链接你帮我抓一下正文，全部收录，想法：大家都不容易
3. 把刚才那条灵感补发到飞书知识库

**examples_en**

1. Here is a photo of page 113 — capture paragraph 4 with the note "even emperors must raise money"
2. Fetch the body text of this link, keep all paragraphs, note: everyone has it hard
3. Resend that card to the Feishu wiki

## 三、一句话卖点（给运营同学看）

> 大多数读书笔记工具在「整篇摘要」，这个技能反过来：**以自然段为最小单位，只留你被击中的那几句，加上你自己的一句话**。
> 三种输入（照片 / 链接 / 粘贴）、四个落地目标（乐享 / 飞书 / Obsidian / IMA），卡片和索引地图都自动维护。

## 四、审核时可能要解释的点

1. **不采集隐私数据**：所有原文、照片、卡片都落在用户自己的 `library/` 目录和用户自己的知识库；不经过任何第三方服务器。
2. **外部依赖是可选的**：截图需要本机 Chrome，PDF 需要 `pdftotext`，云端知识库需要对应连接器。缺任何一项只跳该功能，不报错、不阻塞主流程（`scripts/setup.py env` 会如实列出）。
3. **不写死凭证**：仓库和 zip 里没有 App Secret、token、个人路径。凭证由用户自己的 `config.json` 或环境变量提供；`config.json` 与 `library/` 已在 `.gitignore` 里。
4. **脚本只做本地文件与用户明确指定的知识库操作**，不发起任何后台请求。网络访问仅限用户给的那个链接本身。
5. 小红书原页面截图受反爬限制抓不到，这是**已知限制**，已在 README 与 `references/sources.md` 写明，技能会引导用户自己截图而不是反复重试。

## 五、截图建议（详情页用）

| 顺序 | 拍什么 |
|---|---|
| 1 | 对话里发一页书稿照片，AI 回「①…②…③…」段落清单 |
| 2 | 用户回「4，皇帝也得筹钱」，AI 生成的卡片（含原文摘录 + 当时的想法） |
| 3 | 乐享/飞书里的成稿页面（截图挂在顶部，①–⑩ 逐段原文） |
| 4 | 「读书灵感目录地图」索引页，一眼看到按书分组的全部灵感 |

## 六、版本节奏

- 改技能 → `./tools/publish.sh "改了什么"` 同步到仓库并推送
- 要发新版本：改 `SKILL.md` 里的 `version`，`gh release create vX.Y.Z` 附上两个 zip
