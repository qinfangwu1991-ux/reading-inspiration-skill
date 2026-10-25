#!/usr/bin/env bash
# 把本机已安装的技能（~/.workbuddy/skills/reading-inspiration）同步到本仓库并推上去。
#
# 为什么要有这个脚本：真正在用的是 WorkBuddy 技能目录里的那一份（改了立刻生效），
# 仓库只是镜像。手动 rsync 容易漏文件或漏删，所以把「同步 → 提交 → 推送」固定成一条命令。
#
#   ./tools/publish.sh                  # 同步 + 提交 + 推送
#   ./tools/publish.sh "改了什么"        # 自定义提交信息
#   ./tools/publish.sh "" --dry-run     # 只看会改什么
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${READING_SKILL_SRC:-$HOME/.workbuddy/skills/reading-inspiration}"
MSG="${1:-同步技能更新}"
DRY="${2:-}"

if [ ! -f "$SRC/SKILL.md" ]; then
  echo "找不到已安装的技能：$SRC" >&2
  echo "用 READING_SKILL_SRC=/path/to/skill ./tools/publish.sh 指定" >&2
  exit 2
fi

cd "$REPO"
ARGS=(-a --delete --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store')
[ "$DRY" = "--dry-run" ] && ARGS+=(-n -v)

echo "源：$SRC"
echo "目标：$REPO"
rsync "${ARGS[@]}" "$SRC/scripts/" scripts/
rsync "${ARGS[@]}" "$SRC/references/" references/
cp "$SRC/SKILL.md" SKILL.md

# 同步后跑一次语法检查和上架包校验，避免把坏东西推上去
for f in scripts/*.py; do
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f" \
    || { echo "语法错误：$f" >&2; exit 3; }
done
python3 scripts/setup.py pack --market >/dev/null
echo "✓ 语法检查通过，上架包校验通过"

if [ "$DRY" = "--dry-run" ]; then
  git status --short
  exit 0
fi

git add -A
if git diff --cached --quiet; then
  echo "没有变化，不用提交"
  exit 0
fi
git -c user.name="qinfangwu1991-ux" \
    -c user.email="qinfangwu1991-ux@users.noreply.github.com" \
    commit -q -m "$MSG"
git push -q origin main
echo "✓ 已推送：$(gh repo view --json url -q .url 2>/dev/null || echo "$REPO")"
