#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# 一键推送到 GitHub（小白版）
#
# 用法：
#   1. 先在 GitHub 网页上建一个空仓库（不要勾 "Add README"），复制它的地址，
#      形如 https://github.com/你的用户名/equity-research-suite.git
#   2. 在本文件夹打开命令行，运行：
#         bash push_to_github.sh https://github.com/你的用户名/equity-research-suite.git
#   3. 按提示输入 GitHub 用户名和 token（不是密码，见下方说明）
#
# 脚本会：初始化 git → 提交所有文件 → 推送到你的仓库。
# 已配置 .gitignore，运行产物（PDF/缓存）不会被推上去。
# ═══════════════════════════════════════════════════════════════════
set -e

REMOTE="$1"
if [ -z "$REMOTE" ]; then
  echo "❌ 缺少仓库地址。"
  echo "   用法: bash push_to_github.sh https://github.com/你的用户名/仓库名.git"
  exit 1
fi

# 基本身份（首次用 git 需要）——若已配过会跳过
if [ -z "$(git config --global user.email 2>/dev/null)" ]; then
  read -p "你的 Git 邮箱: " GIT_EMAIL
  read -p "你的 Git 用户名: " GIT_NAME
  git config --global user.email "$GIT_EMAIL"
  git config --global user.name "$GIT_NAME"
fi

# 初始化（幂等：已是 git 仓库则跳过）
if [ ! -d .git ]; then
  git init
  git branch -M main
fi

echo "→ 添加文件..."
git add .

echo "→ 提交..."
git commit -m "equity-research-suite: 个股研究助手（可对账研报生成）" || echo "（无改动可提交）"

# 设置远程
if git remote | grep -q origin; then
  git remote set-url origin "$REMOTE"
else
  git remote add origin "$REMOTE"
fi

echo ""
echo "→ 推送到 $REMOTE"
echo "  提示：弹出输入时，'Username' 填 GitHub 用户名，"
echo "        'Password' 填 GitHub Personal Access Token（不是登录密码！）。"
echo "        生成 token：GitHub → Settings → Developer settings →"
echo "        Personal access tokens → Tokens(classic) → 勾选 repo 权限 → 生成。"
echo ""
git push -u origin main

echo ""
echo "✅ 完成！打开 $REMOTE 即可看到你的项目。"
