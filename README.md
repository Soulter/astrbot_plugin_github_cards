# GitHub Plugin for AstrBot

一个能够自动识别 GitHub 仓库链接并发送卡片图片的插件，同时支持订阅仓库的 Issue 和 PR 更新，查询 Issue 和 PR 详情。插件默认使用轮询检查更新，也可以开启 Webhook 模式以获得更及时和丰富的通知。

## 功能

1. 自动识别群聊中的 GitHub 仓库链接，发送卡片图片
2. 支持轮询与 Webhook 两种更新方式，可按需选择
3. 当订阅的仓库有新的 Issue、PR、评论、Star、Fork 等事件时自动发送通知
4. 查询指定 Issue 或 PR 的详细信息
5. 支持默认仓库设置，简化命令使用
6. 查看 GitHub API 速率限制状态

## 使用方法

### 卡片展示

当在聊天中发送 GitHub 仓库链接时，机器人会自动识别并发送该仓库的卡片图片。支持以下格式的链接：

- `https://github.com/用户名/仓库名`
- `https://github.com/用户名/仓库名/issues/123`
- `https://github.com/用户名/仓库名/pull/123`

可以通过配置项或 `/ghlink` 指令来控制是否自动解析 GitHub 链接。

### 订阅命令

- `/ghsub 用户名/仓库名` - 订阅指定 GitHub 仓库的更新
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
- `/ghreadme 用户名/仓库名#123` - 查询指定 readme 的信息
- `/ghreadme 用户名/仓库名 123` - 查询指定 readme 的信息（使用空格分隔）

如果已设置默认仓库或已订阅了单个仓库，也可以直接使用：

- `/ghissue 123` - 查询默认仓库的指定 Issue
- `/ghpr 123` - 查询默认仓库的指定 PR

### 工具命令

- `/ghlimit` - 查看当前 GitHub API 速率限制状态
- `/ghlink on/off` - 开启或关闭当前会话的 GitHub 链接自动解析功能

## Webhook 模式

当需要更及时的提醒或订阅大量仓库时，推荐启用 Webhook 模式。

### 前置配置

1. 在 AstrBot 管理面板中启用 **使用 Webhook 接收更新**。
2. 视需要调整以下选项：
   - **Webhook 监听地址**（默认 `0.0.0.0`）
   - **Webhook 监听端口**（默认 `6192`）
   - **Webhook 路径**（默认 `/github/webhook`）
   - **Webhook Secret**（可选，若设置需与 GitHub Webhook 保持一致）
3. 保存配置后重启 AstrBot 或重新加载插件，插件会启动一个基于 Quart 的 HTTP 服务。

### GitHub 端设置

1. 前往目标仓库的 **Settings → Webhooks**。
2. 点击 **Add webhook** 并填写：
   - **Payload URL**：`http://<服务器公网地址>:<端口><路径>`（以默认值为例为 `http://your-host:6192/github/webhook`）
   - **Content type**：选择 `application/json`
   - **Secret**：若在插件中设置了 Secret，请在此填写相同内容
3. 在 **Which events would you like to trigger this webhook?** 保持默认「Just the push event」改为 **Let me select individual events**，并勾选以下事件（建议全选以获得完整体验）：
   - `Issues`
   - `Issue comments`
   - `Pull requests`
   - `Pull request reviews`
   - `Pull request review comments`
   - `Pull request review threads`
   - `Commit comments`
   - `Discussions`
   - `Discussion comments`
   - `Forks`
   - `Stars`
   - `Create`
4. 保存设置。GitHub 会立即发送一次 `ping` 请求确认配置是否可达。

### 已支持的 Webhook 事件

- Issue：新建、关闭、重新打开
- Issue 评论：创建、编辑、删除
- Pull Request：新建、关闭（含合并）、重新打开
- Pull Request 审查及评论、审查线程更新
- 提交评论（commit_comment）
- Discussion 及 Discussion 评论
- Fork、Star、仓库/分支/标签创建

启用 Webhook 后，轮询任务会自动停止，减少不必要的 API 调用。如需退回到轮询模式，只需关闭配置项并重启插件即可。

## 示例

```bash
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

1. **GitHub API 访问令牌**：可选，提供令牌可增加 API 请求限制以及访问私有仓库
2. **检查更新间隔时间**：轮询模式下生效，单位为分钟，默认为 30 分钟
3. **仓库名使用小写存储**：将仓库名转换为小写进行存储，以避免大小写敏感性问题，默认为开启
4. **自动解析 GitHub 链接**：是否自动解析群聊中的 GitHub 链接并发送卡片，默认为开启。可通过 `/ghlink` 指令在特定会话中覆盖此设置
5. **使用 Webhook 接收更新**：启用后将不再启动轮询任务
6. **Webhook 监听地址 / 端口 / 路径**：控制插件内部 HTTP 服务的监听参数
7. **Webhook Secret**：可选，用于校验 GitHub Webhook 签名

## 注意事项

- 机器人会根据配置的时间间隔检查订阅的仓库更新（默认 30 分钟），Webhook 模式下不再发起轮询
- 订阅数据存储在 `data/github_subscriptions.json` 文件中
- 默认仓库设置存储在 `data/github_default_repos.json` 文件中
- 命令中的仓库名不区分大小写
- 使用 GitHub API Token 可以提高 API 请求限制并访问私有仓库
- 未使用 Token 时，API 速率限制为每小时 60 次请求；使用 Token 后可提高到每小时 5,000 次请求
