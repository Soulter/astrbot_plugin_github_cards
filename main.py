import asyncio
import base64
import json
import os
import re
import sys
import uuid
from datetime import datetime
from typing import Any

import aiohttp

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

from . import formatters
from .webhook_server import GitHubWebhookServer

PLUGIN_DIR = os.path.dirname(__file__)
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

GITHUB_URL_PATTERN = r"https://github\.com/[\w\-]+/[\w\-]+(?:/(pull|issues)/\d+)?"
GITHUB_REPO_OPENGRAPH = "https://opengraph.githubassets.com/{hash}/{appendix}"
STAR_HISTORY_URL = "https://api.star-history.com/svg?repos={identifier}&type=Date"
GITHUB_API_URL = "https://api.github.com/repos/{repo}"
GITHUB_README_API_URL = (
    "https://api.github.com/repos/{repo}/readme"  # 新增 README API URL
)
GITHUB_ISSUES_API_URL = "https://api.github.com/repos/{repo}/issues"
GITHUB_COMMITS_API_URL = "https://api.github.com/repos/{repo}/commits"
GITHUB_RELEASES_API_URL = "https://api.github.com/repos/{repo}/releases"
GITHUB_ISSUE_API_URL = "https://api.github.com/repos/{repo}/issues/{issue_number}"
GITHUB_PR_API_URL = "https://api.github.com/repos/{repo}/pulls/{pr_number}"
GITHUB_RATE_LIMIT_URL = "https://api.github.com/rate_limit"

# Path for storing subscription data
SUBSCRIPTION_FILE = "data/github_subscriptions.json"
# Path for storing default repo data
DEFAULT_REPO_FILE = "data/github_default_repos.json"
# Path for storing link resolution settings
LINK_SETTINGS_FILE = "data/github_link_settings.json"


@register(
    "astrbot_plugin_github_cards",
    "Soulter",
    "根据群聊中 GitHub 相关链接自动发送 GitHub OpenGraph 图片，支持订阅仓库的 Issue 和 PR",
    "1.1.0",
    "https://github.com/Soulter/astrbot_plugin_github_cards",
)
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.subscriptions = self._load_subscriptions()
        self.default_repos = self._load_default_repos()
        self.link_settings = self._load_link_settings()
        self.last_check_time = {}  # Store the last check time for each repo
        self.use_lowercase = self.config.get("use_lowercase_repo", True)
        self.auto_resolve_links = self.config.get("auto_resolve_links", True)
        self.github_token = self.config.get("github_token", "")
        self.check_interval = self.config.get("check_interval", 30)
        self.enable_webhook = bool(self.config.get("enable_webhook", False))
        self.webhook_host = self.config.get("webhook_host", "0.0.0.0")
        self.webhook_port = int(self.config.get("webhook_port", 6192))
        self.webhook_secret = self.config.get("webhook_secret", "")
        self.webhook_path = self.config.get("webhook_path", "/github/webhook")
        self.webhook_server: Any | None = None
        self.task: asyncio.Task[Any] | None = None

        if self.enable_webhook:
            server = GitHubWebhookServer(
                plugin=self,
                host=self.webhook_host,
                port=self.webhook_port,
                secret=self.webhook_secret,
                path=self.webhook_path,
            )
            self.webhook_server = server
            server.start()
            logger.info("GitHub Cards Plugin 初始化完成，启用 Webhook 模式")
        else:
            # Start background task to check for updates when webhook is disabled
            self.task = asyncio.create_task(self._check_updates_periodically())
            logger.info(
                f"GitHub Cards Plugin初始化完成，检查间隔: {self.check_interval}分钟"
            )

    def _load_subscriptions(self) -> dict[str, list[str]]:
        """Load subscriptions from JSON file"""
        if os.path.exists(SUBSCRIPTION_FILE):
            try:
                with open(SUBSCRIPTION_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载订阅数据失败: {e}")
        return {}

    def _save_subscriptions(self):
        """Save subscriptions to JSON file"""
        try:
            os.makedirs(os.path.dirname(SUBSCRIPTION_FILE), exist_ok=True)
            with open(SUBSCRIPTION_FILE, "w", encoding="utf-8") as f:
                json.dump(self.subscriptions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存订阅数据失败: {e}")

    def _load_default_repos(self) -> dict[str, str]:
        """Load default repo settings from JSON file"""
        if os.path.exists(DEFAULT_REPO_FILE):
            try:
                with open(DEFAULT_REPO_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载默认仓库数据失败: {e}")
        return {}

    def _save_default_repos(self):
        """Save default repo settings to JSON file"""
        try:
            os.makedirs(os.path.dirname(DEFAULT_REPO_FILE), exist_ok=True)
            with open(DEFAULT_REPO_FILE, "w", encoding="utf-8") as f:
                json.dump(self.default_repos, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存默认仓库数据失败: {e}")

    def _load_link_settings(self) -> dict[str, bool]:
        """Load link resolution settings from JSON file"""
        if os.path.exists(LINK_SETTINGS_FILE):
            try:
                with open(LINK_SETTINGS_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载链接解析设置失败: {e}")
        return {}

    def _save_link_settings(self):
        """Save link resolution settings to JSON file"""
        try:
            os.makedirs(os.path.dirname(LINK_SETTINGS_FILE), exist_ok=True)
            with open(LINK_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.link_settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存链接解析设置失败: {e}")

    def _normalize_repo_name(self, repo: str) -> str:
        """Normalize repository name according to configuration"""
        return repo.lower() if self.use_lowercase else repo

    def _resolve_repo_key(self, repo: str) -> str | None:
        """Resolve stored subscription key that matches the provided repo name."""
        if repo in self.subscriptions:
            return repo

        normalized = self._normalize_repo_name(repo)
        for stored_repo in self.subscriptions.keys():
            if self._normalize_repo_name(stored_repo) == normalized:
                return stored_repo

        return None

    def _get_github_headers(self) -> dict[str, str]:
        """Get GitHub API headers with token if available"""
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"
        return headers

    @filter.regex(GITHUB_URL_PATTERN)
    async def github_repo(self, event: AstrMessageEvent):
        """解析 Github 仓库信息"""
        # Check if link resolution is enabled for this conversation
        should_resolve = self.link_settings.get(
            event.unified_msg_origin, self.auto_resolve_links
        )
        if not should_resolve:
            return

        msg = event.message_str
        match = re.search(GITHUB_URL_PATTERN, msg)
        if not match:
            logger.debug("未能在消息中解析到 GitHub 链接")
            return
        repo_url = match.group(0)
        repo_url = repo_url.replace("https://github.com/", "")
        hash_value = uuid.uuid4().hex
        opengraph_url = GITHUB_REPO_OPENGRAPH.format(hash=hash_value, appendix=repo_url)
        logger.info(f"生成的 OpenGraph URL: {opengraph_url}")

        try:
            yield event.image_result(opengraph_url)
        except Exception as e:
            logger.error(f"下载图片失败: {e}")
            yield event.plain_result("下载 GitHub 图片失败: " + str(e))
            return

    @filter.command("ghlink")
    async def set_link_resolution(self, event: AstrMessageEvent, state: str):
        """设置当前会话是否自动解析 GitHub 链接。用法: /ghlink on 或 /ghlink off"""
        state = state.lower()
        if state not in ["on", "off"]:
            yield event.plain_result("无效的参数，请使用 on 或 off")
            return

        enabled = state == "on"
        self.link_settings[event.unified_msg_origin] = enabled
        self._save_link_settings()

        status_text = "开启" if enabled else "关闭"
        yield event.plain_result(f"已在当前会话{status_text} GitHub 链接自动解析")

    @filter.command("ghsub")
    async def subscribe_repo(self, event: AstrMessageEvent, repo: str):
        """订阅 GitHub 仓库的 Issue 和 PR。例如: /ghsub Soulter/AstrBot"""
        if not self._is_valid_repo(repo):
            yield event.plain_result("请提供有效的仓库名，格式为: 用户名/仓库名")
            return

        # Normalize repository name
        normalized_repo = self._normalize_repo_name(repo)

        # Check if the repo exists
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    GITHUB_API_URL.format(repo=repo), headers=self._get_github_headers()
                ) as resp:
                    if resp.status != 200:
                        yield event.plain_result(f"仓库 {repo} 不存在或无法访问")
                        return

                    repo_data = await resp.json()
                    display_name = repo_data.get("full_name", repo)
        except Exception as e:
            logger.error(f"访问 GitHub API 失败: {e}")
            yield event.plain_result(f"检查仓库时出错: {str(e)}")
            return

        # Get the unique identifier for the subscriber
        subscriber_id = event.unified_msg_origin

        repo_key = self._resolve_repo_key(repo)
        if not repo_key:
            repo_key = normalized_repo if self.use_lowercase else display_name

        subscribers = self.subscriptions.setdefault(repo_key, [])

        if subscriber_id not in subscribers:
            subscribers.append(subscriber_id)
            self._save_subscriptions()

            # Fetch initial state for new subscription
            if not self.enable_webhook:
                await self._fetch_new_items(repo_key, None)

            yield event.plain_result(f"成功订阅仓库 {display_name} 的事件更新。")
        else:
            yield event.plain_result(f"你已经订阅了仓库 {display_name}")

        # Set as default repo for this conversation
        self.default_repos[event.unified_msg_origin] = display_name
        self._save_default_repos()

    @filter.command("ghunsub")
    async def unsubscribe_repo(self, event: AstrMessageEvent, repo: str | None = None):
        """取消订阅 GitHub 仓库。例如: /ghunsub Soulter/AstrBot，不提供仓库名则取消所有订阅"""
        subscriber_id = event.unified_msg_origin

        if repo is None:
            # Unsubscribe from all repos
            unsubscribed = []
            for repo_name, subscribers in list(self.subscriptions.items()):
                if subscriber_id in subscribers:
                    subscribers.remove(subscriber_id)
                    unsubscribed.append(repo_name)
                    if not subscribers:
                        del self.subscriptions[repo_name]

            if unsubscribed:
                self._save_subscriptions()
                yield event.plain_result(
                    f"已取消订阅所有仓库: {', '.join(unsubscribed)}"
                )
            else:
                yield event.plain_result("你没有订阅任何仓库")
            return

        if not self._is_valid_repo(repo):
            yield event.plain_result("请提供有效的仓库名，格式为: 用户名/仓库名")
            return

        repo_key = self._resolve_repo_key(repo)
        if repo_key and subscriber_id in self.subscriptions.get(repo_key, []):
            self.subscriptions[repo_key].remove(subscriber_id)
            if not self.subscriptions[repo_key]:
                del self.subscriptions[repo_key]
            self._save_subscriptions()
            self.last_check_time.pop(repo_key, None)
            yield event.plain_result(f"已取消订阅仓库 {repo_key}")
        else:
            yield event.plain_result(f"你没有订阅仓库 {repo}")

    @filter.command("ghlist")
    async def list_subscriptions(self, event: AstrMessageEvent):
        """列出当前订阅的 GitHub 仓库"""
        subscriber_id = event.unified_msg_origin
        subscribed_repos = []

        for repo, subscribers in self.subscriptions.items():
            if subscriber_id in subscribers:
                subscribed_repos.append(repo)

        if subscribed_repos:
            yield event.plain_result(
                f"你当前订阅的仓库有: {', '.join(subscribed_repos)}"
            )
        else:
            yield event.plain_result("你当前没有订阅任何仓库")

    @filter.command("ghdefault", alias={"ghdef"})
    async def set_default_repo(self, event: AstrMessageEvent, repo: str | None = None):
        """设置默认仓库。例如: /ghdefault Soulter/AstrBot"""
        if repo is None:
            # Show current default repo
            default_repo = self.default_repos.get(event.unified_msg_origin)
            if default_repo:
                yield event.plain_result(f"当前默认仓库为: {default_repo}")
            else:
                yield event.plain_result(
                    "当前未设置默认仓库，可使用 /ghdefault 用户名/仓库名 进行设置"
                )
            return

        if not self._is_valid_repo(repo):
            yield event.plain_result("请提供有效的仓库名，格式为: 用户名/仓库名")
            return

        # Check if the repo exists
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    GITHUB_API_URL.format(repo=repo), headers=self._get_github_headers()
                ) as resp:
                    if resp.status != 200:
                        yield event.plain_result(f"仓库 {repo} 不存在或无法访问")
                        return

                    repo_data = await resp.json()
                    display_name = repo_data.get("full_name", repo)
        except Exception as e:
            logger.error(f"访问 GitHub API 失败: {e}")
            yield event.plain_result(f"检查仓库时出错: {str(e)}")
            return

        # Set as default repo for this conversation
        self.default_repos[event.unified_msg_origin] = display_name
        self._save_default_repos()
        yield event.plain_result(f"已将 {display_name} 设为默认仓库")

    def _is_valid_repo(self, repo: str) -> bool:
        """Check if the repository name is valid"""
        return bool(re.match(r"[\w\-]+/[\w\-]+$", repo))

    async def _check_updates_periodically(self):
        """Periodically check for updates in subscribed repositories"""
        if self.enable_webhook:
            logger.debug("Webhook 模式已启用，跳过轮询任务")
            return

        try:
            while True:
                try:
                    await self._check_all_repos()
                except Exception as e:
                    logger.error(f"检查仓库更新时出错: {e}")

                # Use configured check interval
                minutes = max(1, self.check_interval)  # Ensure at least 1 minute
                logger.debug(f"等待 {minutes} 分钟后再次检查仓库更新")
                await asyncio.sleep(minutes * 60)
        except asyncio.CancelledError:
            logger.info("停止检查仓库更新")

    async def _check_all_repos(self):
        """Check all subscribed repositories for updates"""
        if self.enable_webhook:
            return

        for repo in list(self.subscriptions.keys()):
            logger.debug(f"正在检查仓库 {repo} 更新")
            if not self.subscriptions[repo]:  # Skip if no subscribers
                continue

            try:
                # Get the last check time for this repo
                last_check = self.last_check_time.get(repo, None)

                # Fetch new issues and PRs
                new_items = await self._fetch_new_items(repo, last_check)

                if new_items:
                    # Update last check time
                    self.last_check_time[repo] = datetime.now().isoformat()

                    # Notify subscribers about new items
                    await self._notify_subscribers(repo, new_items)
            except Exception as e:
                logger.error(f"检查仓库 {repo} 更新时出错: {e}")

    async def _fetch_new_items(self, repo: str, last_check: str | None):
        """Fetch new issues, PRs, commits, and releases from a repository since last check"""
        if not last_check:
            # If first time checking, just record current time and return empty list
            # Store as UTC timestamp without timezone info to avoid comparison issues
            self.last_check_time[repo] = (
                datetime.utcnow().replace(microsecond=0).isoformat()
            )
            logger.info(f"初始化仓库 {repo} 的时间戳: {self.last_check_time[repo]}")
            return []

        try:
            # Always treat stored timestamps as UTC without timezone info
            last_check_dt = datetime.fromisoformat(last_check)

            # Ensure it's treated as naive datetime
            if hasattr(last_check_dt, "tzinfo") and last_check_dt.tzinfo is not None:
                # If it somehow has timezone info, convert to naive UTC
                last_check_dt = last_check_dt.replace(tzinfo=None)

            logger.debug(f"仓库 {repo} 的上次检查时间: {last_check_dt.isoformat()}")
            new_items = []

            async with aiohttp.ClientSession() as session:
                # 1. Fetch Issues / PRs
                try:
                    params_issues = {
                        "sort": "created",
                        "direction": "desc",
                        "state": "all",
                        "per_page": 10,
                        "since": last_check_dt.isoformat() + "Z",
                    }
                    async with session.get(
                        GITHUB_ISSUES_API_URL.format(repo=repo),
                        params=params_issues,
                        headers=self._get_github_headers(),
                    ) as resp:
                        if resp.status == 200:
                            items = await resp.json()
                            for item in items:
                                github_timestamp = item["created_at"].replace("Z", "")
                                created_at = datetime.fromisoformat(github_timestamp).replace(tzinfo=None)
                                if created_at > last_check_dt:
                                    logger.info(f"发现新的 item #{item.get('number')} in {repo}")
                                    new_items.append(item)
                                else:
                                    break
                        else:
                            text = await resp.text()
                            logger.error(f"获取仓库 {repo} 的 Issue/PR 失败: {resp.status}: {text[:100]}")
                except Exception as e:
                    logger.error(f"获取仓库 {repo} 的 Issue/PR 时出错: {e}")

                # 2. Fetch Commits
                try:
                    params_commits = {
                        "per_page": 10,
                        "since": last_check_dt.isoformat() + "Z",
                    }
                    async with session.get(
                        GITHUB_COMMITS_API_URL.format(repo=repo),
                        params=params_commits,
                        headers=self._get_github_headers(),
                    ) as resp:
                        if resp.status == 200:
                            commits = await resp.json()
                            if isinstance(commits, list):
                                for commit in commits:
                                    commit_date_str = commit.get("commit", {}).get("committer", {}).get("date", "")
                                    if not commit_date_str:
                                        continue
                                    github_timestamp = commit_date_str.replace("Z", "")
                                    created_at = datetime.fromisoformat(github_timestamp).replace(tzinfo=None)
                                    if created_at > last_check_dt:
                                        logger.info(f"发现新的 commit {commit.get('sha')[:7]} in {repo}")
                                        commit["_astrbot_type"] = "commit"
                                        # To pass branch info in notifier we can't easily get it here, but we can just say "代码推送"
                                        new_items.append(commit)
                                    else:
                                        break
                        else:
                            text = await resp.text()
                            logger.error(f"获取仓库 {repo} 的 Commits 失败: {resp.status}: {text[:100]}")
                except Exception as e:
                    logger.error(f"获取仓库 {repo} 的 Commits 时出错: {e}")

                # 3. Fetch Releases
                try:
                    params_releases = {"per_page": 5}
                    # releases API doesn't support 'since', we rely on sorting (default is by created_at desc)
                    async with session.get(
                        GITHUB_RELEASES_API_URL.format(repo=repo),
                        params=params_releases,
                        headers=self._get_github_headers(),
                    ) as resp:
                        if resp.status == 200:
                            releases = await resp.json()
                            if isinstance(releases, list):
                                for release in releases:
                                    release_date_str = release.get("published_at") or release.get("created_at") or ""
                                    if not release_date_str:
                                        continue
                                    github_timestamp = release_date_str.replace("Z", "")
                                    created_at = datetime.fromisoformat(github_timestamp).replace(tzinfo=None)
                                    if created_at > last_check_dt:
                                        logger.info(f"发现新的 release {release.get('tag_name')} in {repo}")
                                        release["_astrbot_type"] = "release"
                                        new_items.append(release)
                                    else:
                                        break
                        else:
                            text = await resp.text()
                            logger.error(f"获取仓库 {repo} 的 Releases 失败: {resp.status}: {text[:100]}")
                except Exception as e:
                    logger.error(f"获取仓库 {repo} 的 Releases 时出错: {e}")

            # Update the last check time to now (UTC without timezone info)
            if new_items:
                logger.info(f"找到 {len(new_items)} 个新的 items 在 {repo}")
            else:
                logger.debug(f"没有找到新的 items 在 {repo}")

            # Always update the timestamp after checking, regardless of whether we found items
            self.last_check_time[repo] = (
                datetime.utcnow().replace(microsecond=0).isoformat()
            )
            logger.debug(f"更新仓库 {repo} 的时间戳为: {self.last_check_time[repo]}")

            return new_items
        except Exception as e:
            logger.error(f"解析时间/获取数据时出错: {e}", exc_info=True)
            self.last_check_time[repo] = (
                datetime.utcnow().replace(microsecond=0).isoformat()
            )
            logger.info(
                f"出错后更新仓库 {repo} 的时间戳为: {self.last_check_time[repo]}"
            )
            return []

    async def _notify_subscribers(self, repo: str, new_items: list[dict[str, Any]]):
        """Notify subscribers about new issues and PRs"""
        if not new_items:
            return

        repo_key = self._resolve_repo_key(repo) or repo

        for subscriber_id in self.subscriptions.get(repo_key, []):
            try:
                # Create notification message
                for item in new_items:
                    if "_astrbot_type" in item:
                        if item["_astrbot_type"] == "commit":
                            sha = item.get("sha", "")[:7]
                            msg = item.get("commit", {}).get("message", "").split("\n")[0]
                            author = item.get("commit", {}).get("author", {}).get("name", "未知")
                            url = item.get("html_url", "")
                            message = (
                                f"[GitHub 更新] 仓库 {repo} 有新的代码推送:\n"
                                f"- {sha} {msg}\n"
                                f"作者: {author}\n"
                                f"链接: {url}"
                            )
                        elif item["_astrbot_type"] == "release":
                            tag_name = item.get("tag_name", "未知版本")
                            name = item.get("name") or tag_name
                            author = item.get("author", {}).get("login", "未知")
                            url = item.get("html_url", "")
                            message = (
                                f"[GitHub 更新] 仓库 {repo} 发布了新版本:\n"
                                f"版本: {name} ({tag_name})\n"
                                f"发布者: {author}\n"
                                f"链接: {url}"
                            )
                        else:
                            # Fallback if unknown type
                            continue
                    else:
                        item_type = "PR" if "pull_request" in item else "Issue"
                        message = (
                            f"[GitHub 更新] 仓库 {repo} 有新的{item_type}:\n"
                            f"#{item['number']} {item['title']}\n"
                            f"作者: {item['user']['login']}\n"
                            f"链接: {item['html_url']}"
                        )

                    # Send message to subscriber
                    await self.context.send_message(
                        subscriber_id, MessageChain(chain=[Comp.Plain(message)])
                    )

                    # Add a small delay between messages to avoid rate limiting
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"向订阅者 {subscriber_id} 发送通知时出错: {e}")

    async def handle_webhook_event(
        self, event_type: str, payload: dict[str, Any]
    ) -> None:
        """Process incoming GitHub webhook events."""
        if event_type == "ping":
            logger.info("收到 GitHub Webhook ping 事件")
            return

        repo_info = payload.get("repository")
        if not isinstance(repo_info, dict):
            logger.warning("GitHub Webhook 事件缺少 repository 信息")
            return

        repo_full_name = repo_info.get("full_name")
        if not repo_full_name:
            logger.warning("GitHub Webhook 事件缺少仓库全名")
            return

        repo_key = self._resolve_repo_key(repo_full_name)
        if not repo_key:
            logger.debug(
                f"忽略仓库 {repo_full_name} 的 Webhook 事件 {event_type}: 未找到对应订阅"
            )
            return

        subscribers = self.subscriptions.get(repo_key, [])
        if not subscribers:
            logger.debug(
                f"仓库 {repo_full_name} 没有订阅者，跳过 Webhook 事件 {event_type}"
            )
            return

        sender = payload.get("sender")
        action = payload.get("action", "")
        message: str | None = None

        if event_type == "issues":
            issue = payload.get("issue")
            if isinstance(issue, dict):
                message = formatters.format_webhook_issue_message(
                    repo_full_name, action, issue, sender
                )
        elif event_type == "pull_request":
            pull_request = payload.get("pull_request")
            if isinstance(pull_request, dict):
                message = formatters.format_webhook_pr_message(
                    repo_full_name, action, pull_request, sender
                )
        elif event_type == "issue_comment":
            issue = payload.get("issue")
            comment = payload.get("comment")
            if isinstance(issue, dict) and isinstance(comment, dict):
                message = formatters.format_webhook_issue_comment_message(
                    repo_full_name, action, issue, comment, sender
                )
        elif event_type == "commit_comment":
            comment = payload.get("comment")
            if isinstance(comment, dict):
                message = formatters.format_webhook_commit_comment_message(
                    repo_full_name, action, comment, sender
                )
        elif event_type == "discussion":
            discussion = payload.get("discussion")
            if isinstance(discussion, dict):
                message = formatters.format_webhook_discussion_message(
                    repo_full_name, action, discussion, sender
                )
        elif event_type == "discussion_comment":
            discussion = payload.get("discussion")
            comment = payload.get("comment")
            if isinstance(discussion, dict) and isinstance(comment, dict):
                message = formatters.format_webhook_discussion_comment_message(
                    repo_full_name, action, discussion, comment, sender
                )
        elif event_type == "fork":
            message = formatters.format_webhook_fork_message(
                repo_full_name, payload.get("forkee"), sender
            )
        elif event_type == "pull_request_review_comment":
            pull_request = payload.get("pull_request")
            comment = payload.get("comment")
            if isinstance(pull_request, dict) and isinstance(comment, dict):
                message = formatters.format_webhook_pr_review_comment_message(
                    repo_full_name, action, pull_request, comment, sender
                )
        elif event_type == "pull_request_review":
            pull_request = payload.get("pull_request")
            review = payload.get("review")
            if isinstance(pull_request, dict) and isinstance(review, dict):
                message = formatters.format_webhook_pr_review_message(
                    repo_full_name, action, pull_request, review, sender
                )
        elif event_type == "pull_request_review_thread":
            pull_request = payload.get("pull_request")
            thread = payload.get("thread")
            if isinstance(pull_request, dict) and isinstance(thread, dict):
                message = formatters.format_webhook_pr_review_thread_message(
                    repo_full_name, action, pull_request, thread, sender
                )
        elif event_type == "star":
            message = formatters.format_webhook_star_message(
                repo_full_name, action, sender
            )
        elif event_type == "create":
            message = formatters.format_webhook_create_message(
                repo_full_name, payload, sender
            )
        elif event_type == "push":
            message = formatters.format_webhook_push_message(
                repo_full_name, payload, sender
            )
        elif event_type == "release":
            release = payload.get("release")
            if isinstance(release, dict):
                message = formatters.format_webhook_release_message(
                    repo_full_name, action, release, sender
                )
        else:
            logger.debug(f"暂不处理的 GitHub Webhook 事件类型: {event_type}")
            return

        if not message:
            logger.debug(f"Webhook 事件 {event_type} 未生成通知，可能是不支持的 action")
            return

        for subscriber_id in subscribers:
            try:
                await self.context.send_message(
                    subscriber_id, MessageChain(chain=[Comp.Plain(message)])
                )
                await asyncio.sleep(1)
            except Exception as exc:
                logger.error(f"向订阅者 {subscriber_id} 发送 Webhook 通知时出错: {exc}")

    @filter.command("ghissue", alias={"ghis"})
    async def get_issue_details(self, event: AstrMessageEvent, issue_ref: str):
        """获取 GitHub Issue 详情。格式：/ghissue 用户名/仓库名#123 或 /ghissue 123 (使用默认仓库)"""
        repo, issue_number = self._parse_issue_reference(
            issue_ref, event.unified_msg_origin
        )
        if not repo or not issue_number:
            yield event.plain_result(
                "请提供有效的 Issue 引用，格式为：用户名/仓库名#123 或纯数字(使用默认仓库)"
            )
            return

        try:
            issue_data = await self._fetch_issue_data(repo, issue_number)
            if not issue_data:
                yield event.plain_result(
                    f"无法获取 Issue {repo}#{issue_number} 的信息，可能不存在或无访问权限"
                )
                return

            # Format and send the issue details
            result = formatters.format_issue_details(repo, issue_data)
            yield event.plain_result(result)

            # Send the issue card image if available
            if issue_data.get("html_url"):
                hash_value = uuid.uuid4().hex
                url_path = issue_data["html_url"].replace("https://github.com/", "")
                card_url = GITHUB_REPO_OPENGRAPH.format(
                    hash=hash_value, appendix=url_path
                )
                try:
                    yield event.image_result(card_url)
                except Exception as e:
                    logger.error(f"下载 Issue 卡片图片失败: {e}")

        except Exception as e:
            logger.error(f"获取 Issue 详情时出错: {e}")
            yield event.plain_result(f"获取 Issue 详情时出错: {str(e)}")

    @filter.command("ghpr")
    async def get_pr_details(self, event: AstrMessageEvent, pr_ref: str):
        """获取 GitHub PR 详情。格式：/ghpr 用户名/仓库名#123 或 /ghpr 123 (使用默认仓库)"""
        repo, pr_number = self._parse_issue_reference(pr_ref, event.unified_msg_origin)
        if not repo or not pr_number:
            yield event.plain_result(
                "请提供有效的 PR 引用，格式为：用户名/仓库名#123 或纯数字(使用默认仓库)"
            )
            return

        try:
            pr_data = await self._fetch_pr_data(repo, pr_number)
            if not pr_data:
                yield event.plain_result(
                    f"无法获取 PR {repo}#{pr_number} 的信息，可能不存在或无访问权限"
                )
                return

            # Format and send the PR details
            result = formatters.format_pr_details(repo, pr_data)
            yield event.plain_result(result)

            # Send the PR card image if available
            if pr_data.get("html_url"):
                hash_value = uuid.uuid4().hex
                url_path = pr_data["html_url"].replace("https://github.com/", "")
                card_url = GITHUB_REPO_OPENGRAPH.format(
                    hash=hash_value, appendix=url_path
                )
                try:
                    yield event.image_result(card_url)
                except Exception as e:
                    logger.error(f"下载 PR 卡片图片失败: {e}")

        except Exception as e:
            logger.error(f"获取 PR 详情时出错: {e}")
            yield event.plain_result(f"获取 PR 详情时出错: {str(e)}")

    def _parse_issue_reference(
        self, reference: str, msg_origin: str | None = None
    ) -> tuple[str | None, str | None]:
        """Parse issue/PR reference string in various formats"""
        # Try format 'owner/repo#number' or 'owner/repo number'
        match = re.match(r"([\w\-]+/[\w\-]+)(?:#|\s+)(\d+)$", reference)
        if match:
            return match.group(1), match.group(2)

        # Try format 'owner/repo/number' (without spaces)
        match = re.match(r"([\w\-]+/[\w\-]+)/(\d+)$", reference)
        if match:
            return match.group(1), match.group(2)

        # If reference is just a number, try to use default repo or a subscribed repo
        if reference.isdigit():
            # First check for default repo for this conversation
            if msg_origin and msg_origin in self.default_repos:
                return self.default_repos[msg_origin], reference

            # Next check if there's exactly one subscription
            if msg_origin:
                user_subscriptions = []
                for repo, subscribers in self.subscriptions.items():
                    if msg_origin in subscribers:
                        user_subscriptions.append(repo)

                if len(user_subscriptions) == 1:
                    return user_subscriptions[0], reference
                elif len(user_subscriptions) > 1:
                    logger.debug(
                        f"Found multiple subscriptions for {msg_origin}, can't determine default repo"
                    )

        return None, None

    def _parse_readme_reference(self, reference: str) -> str | None:
        """Parse readme reference string."""
        # Match 'owner/repo' and optional '#...' or ' ...' part
        match = re.match(r"([\w\-]+/[\w\-]+)", reference)
        if match:
            return match.group(1)
        return None

    @filter.command("ghreadme")
    async def get_readme_details(self, event: AstrMessageEvent, readme_ref: str):
        """查询指定仓库的 README 信息。例如: /ghreadme 用户名/仓库名"""
        repo = self._parse_readme_reference(readme_ref)
        if not repo:
            yield event.plain_result("请提供有效的仓库引用，格式为：用户名/仓库名")
            return

        try:
            readme_data = await self._fetch_readme_data(repo)
            if not readme_data:
                yield event.plain_result(
                    f"无法获取仓库 {repo} 的 README 信息，可能不存在或无访问权限"
                )
                return

            # Decode content from base64
            content_base64 = readme_data.get("content", "")
            try:
                readme_content = base64.b64decode(content_base64).decode("utf-8")
            except Exception as e:
                logger.error(f"解码 README 内容失败: {e}")
                yield event.plain_result(f"解码仓库 {repo} 的 README 内容时出错")
                return

            # **[REMOVED]** Truncation logic is removed.

            header = f"📖 {repo} 的 README\n\n"
            full_text = header + readme_content

            # Render text to image
            try:
                image_url = await self.text_to_image(full_text)
                yield event.image_result(image_url)
            except Exception as e:
                logger.error(f"渲染 README 图片失败: {e}")
                # Fallback to plain text if image rendering fails
                yield event.plain_result(full_text)

        except Exception as e:
            logger.error(f"获取 README 详情时出错: {e}")
            yield event.plain_result(f"获取 README 详情时出错: {str(e)}")

    async def _fetch_readme_data(self, repo: str) -> dict[str, Any] | None:
        """Fetch README data from GitHub API"""
        async with aiohttp.ClientSession() as session:
            try:
                url = GITHUB_README_API_URL.format(repo=repo)
                async with session.get(url, headers=self._get_github_headers()) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"获取 README {repo} 失败: {resp.status}")
                        return None
            except Exception as e:
                logger.error(f"获取 README {repo} 时出错: {e}")
                return None

    async def _fetch_issue_data(
        self, repo: str, issue_number: str
    ) -> dict[str, Any] | None:
        """Fetch issue data from GitHub API"""
        async with aiohttp.ClientSession() as session:
            try:
                url = GITHUB_ISSUE_API_URL.format(repo=repo, issue_number=issue_number)
                async with session.get(url, headers=self._get_github_headers()) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(
                            f"获取 Issue {repo}#{issue_number} 失败: {resp.status}"
                        )
                        return None
            except Exception as e:
                logger.error(f"获取 Issue {repo}#{issue_number} 时出错: {e}")
                return None

    async def _fetch_pr_data(self, repo: str, pr_number: str) -> dict[str, Any] | None:
        """Fetch PR data from GitHub API"""
        async with aiohttp.ClientSession() as session:
            try:
                url = GITHUB_PR_API_URL.format(repo=repo, pr_number=pr_number)
                async with session.get(url, headers=self._get_github_headers()) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"获取 PR {repo}#{pr_number} 失败: {resp.status}")
                        return None
            except Exception as e:
                logger.error(f"获取 PR {repo}#{pr_number} 时出错: {e}")
                return None

    @filter.command("ghlimit", alias={"ghrate"})
    async def check_rate_limit(self, event: AstrMessageEvent):
        """查看 GitHub API 速率限制状态"""
        try:
            rate_limit_data = await self._fetch_rate_limit()
            if not rate_limit_data:
                yield event.plain_result("无法获取 GitHub API 速率限制信息")
                return

            # Format and send the rate limit details
            result = self._format_rate_limit(rate_limit_data)
            yield event.plain_result(result)

        except Exception as e:
            logger.error(f"获取 API 速率限制信息时出错: {e}")
            yield event.plain_result(f"获取 API 速率限制信息时出错: {str(e)}")

    async def _fetch_rate_limit(self) -> dict[str, Any] | None:
        """Fetch rate limit information from GitHub API"""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    GITHUB_RATE_LIMIT_URL, headers=self._get_github_headers()
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"获取 API 速率限制信息失败: {resp.status}")
                        return None
            except Exception as e:
                logger.error(f"获取 API 速率限制信息时出错: {e}")
                return None

    def _format_rate_limit(self, rate_limit_data: dict[str, Any]) -> str:
        """Format rate limit data for display"""
        if not rate_limit_data or "resources" not in rate_limit_data:
            return "获取到的速率限制数据无效"

        resources = rate_limit_data["resources"]
        core = resources.get("core", {})
        search = resources.get("search", {})
        graphql = resources.get("graphql", {})

        # Convert timestamps to datetime objects
        core_reset = datetime.fromtimestamp(core.get("reset", 0))
        search_reset = datetime.fromtimestamp(search.get("reset", 0))
        graphql_reset = datetime.fromtimestamp(graphql.get("reset", 0))

        # Calculate time until reset
        now = datetime.now()
        core_minutes = max(0, (core_reset - now).total_seconds() // 60)
        search_minutes = max(0, (search_reset - now).total_seconds() // 60)
        graphql_minutes = max(0, (graphql_reset - now).total_seconds() // 60)

        # Format the result
        result = (
            "📊 GitHub API 速率限制状态\n\n"
            "💻 核心 API (repositories, issues, etc):\n"
            f"  剩余请求数: {core.get('remaining', 0)}/{core.get('limit', 0)}\n"
            f"  重置时间: {core_reset.strftime('%H:%M:%S')} (约 {int(core_minutes)} 分钟后)\n\n"
            "🔍 搜索 API:\n"
            f"  剩余请求数: {search.get('remaining', 0)}/{search.get('limit', 0)}\n"
            f"  重置时间: {search_reset.strftime('%H:%M:%S')} (约 {int(search_minutes)} 分钟后)\n\n"
            "📈 GraphQL API:\n"
            f"  剩余请求数: {graphql.get('remaining', 0)}/{graphql.get('limit', 0)}\n"
            f"  重置时间: {graphql_reset.strftime('%H:%M:%S')} (约 {int(graphql_minutes)} 分钟后)\n"
        )

        # Add information about authentication status
        if self.github_token:
            result += "\n✅ 已使用 GitHub Token 进行身份验证，速率限制较高"
        else:
            result += (
                "\n⚠️ 未使用 GitHub Token，速率限制较低。可在配置中添加 Token 以提高限制"
            )

        return result

    # TODO: svg2png
    # @filter.command("ghstar")
    # async def ghstar(self, event: AstrMessageEvent, identifier: str):
    #     '''查看 GitHub 仓库的 Star 趋势图。如: /ghstar Soulter/AstrBot'''
    #     url = STAR_HISTORY_URL.format(identifier=identifier)
    #     # download svg
    #     fpath = "data/temp/{identifier}.svg".format(identifier=identifier.replace("/",
    #         "_"))
    #     await download_file(url, fpath)
    #     # convert to png
    #     png_fpath = fpath.replace(".svg", ".png")
    #     cairosvg.svg2png(url=fpath, write_to=png_fpath)
    #     # send image
    #     yield event.image_result(png_fpath)

    async def terminate(self):
        """Cleanup and save data before termination"""
        self._save_subscriptions()
        self._save_default_repos()
        self._save_link_settings()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        if self.webhook_server:
            await self.webhook_server.stop()
        logger.info("GitHub Cards Plugin 已终止")
