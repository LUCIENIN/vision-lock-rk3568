#!/usr/bin/env bash
set -euo pipefail

REPO_NAME="visual-lock-screen-rk3568"
VISIBILITY="private"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if ! command -v gh >/dev/null 2>&1; then
  echo "缺少 gh CLI，请先安装 github cli 并登录"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "请先 gh auth login"
  exit 1
fi

USER_LOGIN="$(gh api user -q .login)"
if [[ -z "${USER_LOGIN}" ]]; then
  echo "无法识别 GitHub 登录用户"
  exit 1
fi

REPO_FULL="${USER_LOGIN}/${REPO_NAME}"
REPO_URL="https://github.com/${REPO_FULL}.git"

if [[ ! -d .git ]]; then
  git init
fi

if git status --porcelain | grep -q .; then
  git add .
  if ! git diff --cached --quiet; then
    git commit -m "Backup visual lock screen project"
  fi
fi

git branch -M main

if gh repo view "${REPO_FULL}" >/dev/null 2>&1; then
  if git remote | grep -q '^origin$'; then
    git remote set-url origin "$REPO_URL"
  else
    git remote add origin "$REPO_URL"
  fi
  git fetch origin main || true
else
  gh repo create "$REPO_NAME" --"${VISIBILITY}" --source . --remote origin
fi

git push -u origin main
echo "✅ GitHub 备份完成：${REPO_FULL}"
