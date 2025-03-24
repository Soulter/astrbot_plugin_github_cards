# GitHub Plugin for AstrBot

一个能够自动识别 GitHub 仓库链接并发送卡片图片的插件，同时支持订阅仓库的 Issue 和 PR 更新，查询 Issue 和 PR 详情。

## 功能

1. 自动识别群聊中的 GitHub 仓库链接，发送卡片图片
2. 订阅 GitHub 仓库的 Issue 和 PR 更新
3. 当订阅的仓库有新的 Issue 或 PR 时，自动发送通知
4. 查询指定 Issue 或 PR 的详细信息
5. 支持默认仓库设置，简化命令使用
6. 查看 GitHub API 速率限制状态

## 使用方法

### 卡片展示

当在聊天中发送 GitHub 仓库链接时，机器人会自动识别并发送该仓库的卡片图片。支持以下格式的链接：

- `https://github.com/用户名/仓库名`
- `https://github.com/用户名/仓库名/issues/123`
- `https://github.com/用户名/仓库名/pull/123`

### 订阅命令

- `/ghsub 用户名/仓库名` - 订阅指定 GitHub 仓库的 Issue 和 PR 更新
- `/ghunsub 用户名/仓库名` - 取消订阅指定仓库
- `/ghunsub` - 取消所有订阅
- `/ghlist` - 列出当前已订阅的仓库

### 默认仓库设置

- `/ghdefault 用户名/仓库名` - 设置默认仓库，之后在当前会话中使用简化命令
- `/ghdefault` - 查看当前默认仓库设置

### 查询命令

- `/ghissue 用户名/仓库名#123` - 查询指定 Issue 的详细信息
- `/ghissue 用户名/仓库名 123` - 查询指定 Issue 的详细信息（使用空格分隔）
- `/ghpr 用户名/仓库名#123` - 查询指定 PR 的详细信息
- `/ghpr 用户名/仓库名 123` - 查询指定 PR 的详细信息（使用空格分隔）

如果已设置默认仓库或已订阅了单个仓库，也可以直接使用：
- `/ghissue 123` - 查询默认仓库的指定 Issue
- `/ghpr 123` - 查询默认仓库的指定 PR

### 工具命令

- `/ghlimit` - 查看当前 GitHub API 速率限制状态

## 示例

```
# 订阅仓库
/ghsub Soulter/AstrBot

# 设置默认仓库
/ghdefault Soulter/AstrBot

# 查询 Issue
/ghissue 42

# 查询 PR
/ghpr Soulter/AstrBot#36

# 查看 API 速率限制
/ghlimit
```

## 配置项

在 AstrBot 管理面板中可以配置以下选项：

1. **GitHub API访问令牌**：可选，提供令牌可增加 API 请求限制以及访问私有仓库
2. **检查更新间隔时间**：设置检查 GitHub 更新的间隔时间，单位为分钟，默认为 30 分钟
3. **仓库名使用小写存储**：将仓库名转换为小写进行存储，以避免大小写敏感性问题，默认为开启

## 注意事项

- 机器人会根据配置的时间间隔检查订阅的仓库更新（默认30分钟）
- 订阅数据存储在 `data/github_subscriptions.json` 文件中
- 默认仓库设置存储在 `data/github_default_repos.json` 文件中
- 命令中的仓库名不区分大小写
- 使用 GitHub API Token 可以提高 API 请求限制并访问私有仓库
- 未使用 Token 时，API 速率限制为每小时 60 次请求；使用 Token 后可提高到每小时 5,000 次请求
