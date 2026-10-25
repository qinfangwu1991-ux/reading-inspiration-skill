# 设计参考：同类技能与我们借鉴了什么

> 2026-10-25 做过一轮横向调研，用来校验这个技能的设计取舍。结论记在这里，
> 免得下次又从头搜一遍；也方便判断「某个想法为什么不采用」。

## 1. `masterleopold/book-capture`（最接近的同类）

拍书页 / Kindle 截图 / Apple Books / PDF → macOS Vision OCR → Obsidian Markdown。
MIT，纯本地，不调外部 API。

**借鉴进来的三条**

| 做法 | 我们的落地 |
|---|---|
| 每本书一个 Hub 页 + 若干主题页 | `scripts/build_map.py` 生成 `library/books/<书名>.md`，按 `chapter` 自动分组；Obsidian 里额外一份 `<书名> · 灵感总览.md`。**没有照搬「8~14 个主题」**——那需要模型对整本书记忆做聚类，我们是增量落库、没有全本，硬凑会瞎编 |
| 原页图归档（WebP 2000px q85） | `scripts/save_page_image.py`，落 `library/pages/`，卡片记 `image:`，Obsidian 里内嵌。**没用 WebP**：macOS 自带 `sips` 的 webp 是只读的，写不出来；改 JPEG 长边 1800 / q78，实测文字仍清晰（657KB → 551KB） |
| 把「会静默失败」的坑写在最显眼处 | 本技能里对应的三条：飞书链接必须 `text_element_style.link.url` + URL 编码、飞书 `move_docs_to_wiki` 对已在库内的文档静默不动、乐享 `block_update_page` 只传 `replace_content: true` 会报 `command is required` |

**看清但没照搬的**：它主打「批量导入一本书」，需要页图排序、EXIF 方向烘焙（手机存横向像素 + rotate 90 标签，
Vision 对侧躺页面返回**空结果而不是报错**）。我们是单页即时捕捉，用户自己在对话里发图，
顺序天然明确；如果以后要做批量导入，这两条必须补上。

## 2. `kepano/obsidian-skills`（Obsidian CEO 官方）

Obsidian Flavored Markdown 的规范来源。

**借鉴**：vault 内用 `[[双链]]`（改名自动跟随），只有外部 URL 才用 `[文字](链接)`；
摘录用 `> [!quote]` callout；标签用层级 `读书/<书名>`，侧栏能按书收起。

**落地位置**：`scripts/save_obsidian.py` 的 `transform()` —— 只转换写入 vault 的那一份，
`library/` 里的卡片保持中立格式（因为同一份正文还要喂给乐享和飞书，callout 语法在那边会变成乱码）。

**没用的**：`![[文档.pdf#page=3]]` 这类嵌入，以及 Bases / JSON Canvas / Knap
——本技能的产出是单页卡片，用不上。

## 3. 微信读书官方 Skill + Readwise

**值得借鉴但本轮没做**

- **增量合并**：新划线并入已有笔记，而非每次新建文件。我们是「一次拍一页 = 一张卡片」，
  同一页拍两次会产生两张卡（目前只在文件名上靠 `<日期>_<书名>_<页码>` 区分）
- **每日 / 定期回顾**：Readwise 每天推几条旧划线，靠间隔重复对抗「读完就忘」。
  我们的库目前只进不出。可行的轻量做法：每周挑 1~3 条旧灵感推一次，而不是每天
- **知识卡片**：一条划线 → 一张「观点 + 原文引用」的卡片，适合再加工成文章素材

## 4. `virgiliojr94/book-to-skill`

把整本书编译成 Skill（`SKILL.md` + 分章文件 + 术语表 + 速查表），按需加载。

**方向不同**（它读电子书全文，我们只收你读到时的那几段），但有一个想法可以借：
**一本书攒够 N 条之后，自动生成一页「本书要点」**。等某一本书记忆量足够再考虑，现在做等于瞎编。
