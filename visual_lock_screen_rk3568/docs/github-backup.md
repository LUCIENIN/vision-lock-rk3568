# GitHub 备份指南（视觉识别锁屏）

## 备份目标
- 将完整工程提交到 GitHub，形成可恢复版本。
- 记录一次可追溯的备份 commit。

## 一键执行（推荐）

```bash
cd /Users/testing/Downloads/人型电脑天使心/visual_lock_screen_rk3568
./deploy/github-backup.sh
```

脚本会：
1. 初始化本地 git（若未初始化）；
2. 自动提交当前目录（排除无关项）；
3. 在你的 GitHub 账号下创建私有仓库（若不存在）；
4. 将提交推送到 `origin/main`。

## 现有仓库路径检查

- 默认仓库名：`visual-lock-screen-rk3568`
- 私有库（推荐），避免接口密钥/配置泄露；
- 如需改名或改为公开仓库，可直接改脚本 `REPO_NAME` 和 `VISIBILITY`。

## 风险说明

- 若已有同名仓库，脚本会自动跳过创建，直接使用现有仓库作为远端；
- 推送前请确认未包含敏感信息：
  - API Key
  - 真实摄像头样本
  - 私有网络配置文件

## 失败回退

- 若推送失败，手动执行：

```bash
git add .
git commit -m "backup"
git remote add origin git@github.com:<你的用户名>/<仓库>.git
git push -u origin main
```
