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

POLL_EVENTS = {"issues", "prs", "commits", "releases"}
WEBHOOK_EVENTS = {
    "issues",
    "issue_comment",
    "prs",
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "pull_request_review_thread",
    "commit_comment",
    "discussion",
    "discussion_comment",
    "fork",
    "star",
    "create",
    "push",
    "commits",
    "release",
    "releases",
}
SUBSCRIPTION_EVENTS = POLL_EVENTS | WEBHOOK_EVENTS
EVENT_ALIASES = {
    "issue": "issues",
    "pr": "prs",
    "pulls": "prs",
    "pull_request": "prs",
    "commit": "commits",
    "push": "commits",
    "release": "releases",
}

# Path for storing subscription data
SUBSCRIPTION_FILE = "data/github_subscriptions.json"
# Path for storing default repo data
DEFAULT_REPO_FILE = "data/github_default_repos.json"
# Path for storing link resolution settings
LINK_SETTINGS_FILE = "data/github_link_settings.json"


@register(
    "astrbot_plugin_github_cards",
    "根据群聊中 GitHub 相关链接自动发送 GitHub OpenGraph 图片，支持订阅仓库的 Issue 和 PR",
    "1.1.0",
    "https://github.com AstrBotDev/astrbot_plugin_github_cards",
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
    async def subscribe_repo(
        self,
        event: AstrMessageEvent,
        repo: str,
        branch: str | None = None,
        events: str | None = None,
    ):
        """订阅 GitHub 仓库事件。例如: /ghsub AstrBotDev/AstrBot main issues,commits"""
        parsed = self._parse_subscribe_target(repo, branch, events)
        if not parsed:
            yield event.plain_result(
                "请提供有效的仓库名，格式为: 用户名/仓库名 或 用户名/仓库名 分支名"
            )
            return

        base_repo, branch, event_set = parsed

        # Check if the repo exists
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    GITHUB_API_URL.format(repo=base_repo),
                    headers=self._get_github_headers(),
                ) as resp:
                    if resp.status != 200:
                        yield event.plain_result(f"仓库 {base_repo} 不存在或无法访问")
                        return

                    repo_data = await resp.json()
                    display_name = repo_data.get("full_name", base_repo)
        except Exception as e:
            logger.error(f"访问 GitHub API 失败: {e}")
            yield event.plain_result(f"检查仓库时出错: {str(e)}")
            return

        # Build the subscription key including optional branch/events
        repo_key = self._format_repo_key(base_repo, branch, event_set)
        display_suffix = f" ({branch} 分支)" if branch else ""
        if event_set:
            display_suffix += f" [{', '.join(sorted(event_set))}]"

        # Get the unique identifier for the subscriber
        subscriber_id = event.unified_msg_origin

        subscribers = self.subscriptions.setdefault(repo_key, [])

        if subscriber_id not in subscribers:
            subscribers.append(subscriber_id)
            self._save_subscriptions()

            # Fetch initial state for new subscription.
            # Repo-level polling uses base_repo as timestamp key to avoid duplicate API calls.
            if not self.enable_webhook:
                if self._subscription_allows(repo_key, "issues") or self._subscription_allows(repo_key, "prs") or self._subscription_allows(repo_key, "releases"):
                    await self._fetch_new_items(base_repo, None, fetch_commits=False)
                if self._subscription_allows(repo_key, "commits"):
                    await self._fetch_new_items(repo_key, None, fetch_repo_level=False)

            yield event.plain_result(
                f"成功订阅仓库 {display_name}{display_suffix} 的事件更新。"
            )
        else:
            yield event.plain_result(
                f"你已经订阅了仓库 {display_name}{display_suffix}"
            )

        # Set as default repo for this conversation (always store base repo)
        self.default_repos[event.unified_msg_origin] = display_name
        self._save_default_repos()

    @filter.command("ghunsub")
    async def unsubscribe_repo(
        self,
        event: AstrMessageEvent,
        repo: str | None = None,
        branch: str | None = None,
        events: str | None = None,
    ):
        """取消订阅 GitHub 仓库。例如: /ghunsub AstrBotDev/AstrBot，不提供仓库名则取消所有订阅"""
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

        parsed = self._parse_subscribe_target(repo, branch, events)
        if not parsed:
            yield event.plain_result(
                "请提供有效的仓库名，格式为: 用户名/仓库名 或 用户名/仓库名 分支名"
            )
            return

        base_repo, branch, event_set = parsed
        repo_key = self._format_repo_key(base_repo, branch, event_set)
        display_suffix = f" ({branch} 分支)" if branch else ""
        if event_set:
            display_suffix += f" [{', '.join(sorted(event_set))}]"

        if repo_key and subscriber_id in self.subscriptions.get(repo_key, []):
            self.subscriptions[repo_key].remove(subscriber_id)
            if not self.subscriptions[repo_key]:
                del self.subscriptions[repo_key]
            self._save_subscriptions()
            self.last_check_time.pop(repo_key, None)
            yield event.plain_result(f"已取消订阅仓库 {repo_key}{display_suffix}")
        else:
            yield event.plain_result(f"你没有订阅仓库 {base_repo}{display_suffix}")

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
        """设置默认仓库。例如: /ghdefault AstrBotDev/AstrBot"""
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
        """Check if the repository name is valid (user/repo format)"""
        return bool(re.match(r"^[\w\-]+/[\w\-]+$", repo))

    def _parse_event_list(self, events: str | None) -> set[str] | None:
        """Parse comma-separated event list. None means all events."""
        if not events:
            return None
        parsed = {
            EVENT_ALIASES.get(item.strip().lower(), item.strip().lower())
            for item in events.split(",")
            if item.strip()
        }
        if not parsed or not parsed <= SUBSCRIPTION_EVENTS:
            return None
        return parsed

    def _events_to_suffix(self, events: set[str] | None) -> str:
        return "" if events is None else ":" + ",".join(sorted(events))

    def _parse_repo_key(self, repo_key: str) -> tuple[str, str | None]:
        """Parse subscription key into (base_repo, branch), ignoring event suffix."""
        key_without_events = repo_key.split(":", 1)[0]
        parts = key_without_events.split("/")
        if len(parts) == 2:
            return key_without_events, None
        if len(parts) >= 3:
            return f"{parts[0]}/{parts[1]}", "/".join(parts[2:])
        return key_without_events, None

    def _parse_subscription_key(
        self, repo_key: str
    ) -> tuple[str, str | None, set[str] | None]:
        """Parse subscription key into (base_repo, branch, events)."""
        key, _, events_part = repo_key.partition(":")
        base_repo, branch = self._parse_repo_key(key)
        events = self._parse_event_list(events_part) if events_part else None
        return base_repo, branch, events

    def _format_repo_key(
        self,
        base_repo: str,
        branch: str | None = None,
        events: set[str] | None = None,
    ) -> str:
        """Format base_repo, optional branch and optional events into key."""
        normalized_base = self._normalize_repo_name(base_repo)
        if branch:
            branch_norm = branch.lower() if self.use_lowercase else branch
            return f"{normalized_base}/{branch_norm}{self._events_to_suffix(events)}"
        return f"{normalized_base}{self._events_to_suffix(events)}"

    def _parse_subscribe_target(
        self,
        repo: str | None,
        branch: str | None = None,
        events: str | None = None,
    ) -> tuple[str, str | None, set[str] | None] | None:
        """Parse command args into (base_repo, branch, events).

        Supported:
        - /ghsub user/repo
        - /ghsub user/repo main
        - /ghsub user/repo issues
        - /ghsub user/repo main issues,commits
        - /ghsub user/repo/main issues,commits
        """
        if not repo:
            return None

        branch_value = branch
        events_value = events
        if branch_value and events_value is None:
            maybe_events = self._parse_event_list(branch_value)
            if maybe_events is not None:
                branch_value = None
                events_value = branch

        parsed_events = self._parse_event_list(events_value) if events_value else None
        if events_value and parsed_events is None:
            return None

        parts = repo.split("/", 2)
        if len(parts) == 2 and self._is_valid_repo(repo):
            return repo, branch_value, parsed_events
        if len(parts) >= 3:
            if branch_value is not None:
                return None
            return f"{parts[0]}/{parts[1]}", parts[2], parsed_events

        return None

    def _subscription_allows(self, repo_key: str, event_name: str) -> bool:
        """Return whether a subscription key allows an event."""
        _, _, events = self._parse_subscription_key(repo_key)
        event_name = EVENT_ALIASES.get(event_name, event_name)
        return events is None or event_name in events

    def _item_event_name(self, item: dict[str, Any]) -> str:
        if item.get("_astrbot_type") == "commit":
            return "commits"
        if item.get("_astrbot_type") == "release":
            return "releases"
        return "prs" if "pull_request" in item else "issues"

    def _webhook_event_name(self, event_type: str) -> str:
        return EVENT_ALIASES.get(event_type, event_type)

    def _extract_webhook_branch(self, event_type: str, payload: dict[str, Any]) -> str | None:
        ref = payload.get("ref")
        if isinstance(ref, str) and ref.startswith("refs/heads/"):
            return ref.removeprefix("refs/heads/")
        return None

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
        """Check all subscribed repositories for updates.

        Repository-level items (issues/PRs/releases) are checked once per base
        repository, while commits are checked per branch subscription to avoid
        duplicate notifications.
        """
        if self.enable_webhook:
            return

        # Group subscription keys by base repository
        base_to_keys: dict[str, list[str]] = {}
        for repo_key in list(self.subscriptions.keys()):
            if not self.subscriptions[repo_key]:
                continue
            base_repo, _, _ = self._parse_subscription_key(repo_key)
            base_to_keys.setdefault(base_repo, []).append(repo_key)

        for base_repo, repo_keys in base_to_keys.items():
            logger.debug(f"正在检查仓库 {base_repo} 更新")

            try:
                need_repo_level = any(
                    self._subscription_allows(k, "issues")
                    or self._subscription_allows(k, "prs")
                    or self._subscription_allows(k, "releases")
                    for k in repo_keys
                )
                if need_repo_level:
                    # Repo-level events use one timestamp per base repository to avoid duplicate API calls.
                    last_check = self.last_check_time.get(base_repo, None)
                    repo_items = await self._fetch_new_items(
                        base_repo, last_check, fetch_commits=False
                    )
                    if repo_items:
                        self.last_check_time[base_repo] = datetime.now().isoformat()
                        for repo_key in repo_keys:
                            await self._notify_subscribers(repo_key, repo_items)

                # Check commits individually for each branch subscription that allows commits
                for repo_key in repo_keys:
                    if not self._subscription_allows(repo_key, "commits"):
                        continue
                    last_check = self.last_check_time.get(repo_key, None)
                    branch_items = await self._fetch_new_items(
                        repo_key, last_check, fetch_repo_level=False
                    )
                    if branch_items:
                        self.last_check_time[repo_key] = datetime.now().isoformat()
                        await self._notify_subscribers(repo_key, branch_items)
            except Exception as e:
                logger.error(f"检查仓库 {base_repo} 更新时出错: {e}")

    async def _fetch_new_items(
        self,
        repo: str,
        last_check: str | None,
        *,
        fetch_repo_level: bool = True,
        fetch_commits: bool = True,
    ):
        """Fetch new issues, PRs, commits, and releases from a repository since last check.

        The ``repo`` argument may include an optional branch suffix, e.g.
        ``user/repo`` or ``user/repo/main``. When a branch is specified,
        only commits on that branch are monitored.

        ``fetch_repo_level`` controls whether issues/PRs/releases are checked
        (these are repository-level). ``fetch_commits`` controls whether
        commits are checked (branch-scoped when a branch is present).
        """
        base_repo, branch = self._parse_repo_key(repo)
        branch_suffix = f" ({branch} 分支)" if branch else ""

        if not last_check:
            self.last_check_time[repo] = (
                datetime.utcnow().replace(microsecond=0).isoformat()
            )
            logger.info(f"初始化仓库 {repo}{branch_suffix} 的时间戳: {self.last_check_time[repo]}")
            return []

        try:
            last_check_dt = datetime.fromisoformat(last_check)
            if hasattr(last_check_dt, "tzinfo") and last_check_dt.tzinfo is not None:
                last_check_dt = last_check_dt.replace(tzinfo=None)

            logger.debug(f"仓库 {repo}{branch_suffix} 的上次检查时间: {last_check_dt.isoformat()}")
            new_items = []

            async with aiohttp.ClientSession() as session:
                if fetch_repo_level:
                    # 1. Fetch Issues / PRs (repository-level)
                    try:
                        params_issues = {
                            "sort": "created",
                            "direction": "desc",
                            "state": "all",
                            "per_page": 10,
                            "since": last_check_dt.isoformat() + "Z",
                        }
                        async with session.get(
                            GITHUB_ISSUES_API_URL.format(repo=base_repo),
                            params=params_issues,
                            headers=self._get_github_headers(),
                        ) as resp:
                            if resp.status == 200:
                                items = await resp.json()
                                for item in items:
                                    github_timestamp = item["created_at"].replace("Z", "")
                                    created_at = datetime.fromisoformat(github_timestamp).replace(tzinfo=None)
                                    if created_at > last_check_dt:
                                        logger.info(f"发现新的 item #{item.get('number')} in {base_repo}")
                                        new_items.append(item)
                                    else:
                                        break
                            else:
                                text = await resp.text()
                                logger.error(f"获取仓库 {base_repo} 的 Issue/PR 失败: {resp.status}: {text[:100]}")
                    except Exception as e:
                        logger.error(f"获取仓库 {base_repo} 的 Issue/PR 时出错: {e}")

                if fetch_commits:
                    # 2. Fetch Commits (optionally scoped to a branch)
                    try:
                        params_commits: dict[str, Any] = {
                            "per_page": 100,
                            "since": last_check_dt.isoformat() + "Z",
                        }
                        if branch:
                            params_commits["sha"] = branch
                        async with session.get(
                            GITHUB_COMMITS_API_URL.format(repo=base_repo),
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
                                            logger.info(f"发现新的 commit {commit.get('sha')[:7]} in {repo}{branch_suffix}")
                                            commit["_astrbot_type"] = "commit"
                                            commit["_astrbot_branch"] = branch
                                            new_items.append(commit)
                                        else:
                                            break
                            else:
                                text = await resp.text()
                                logger.error(f"获取仓库 {repo}{branch_suffix} 的 Commits 失败: {resp.status}: {text[:100]}")
                    except Exception as e:
                        logger.error(f"获取仓库 {repo}{branch_suffix} 的 Commits 时出错: {e}")

                if fetch_repo_level:
                    # 3. Fetch Releases (repository-level)
                    try:
                        params_releases = {"per_page": 5}
                        async with session.get(
                            GITHUB_RELEASES_API_URL.format(repo=base_repo),
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
                                            logger.info(f"发现新的 release {release.get('tag_name')} in {base_repo}")
                                            release["_astrbot_type"] = "release"
                                            new_items.append(release)
                                        else:
                                            break
                            else:
                                text = await resp.text()
                                logger.error(f"获取仓库 {base_repo} 的 Releases 失败: {resp.status}: {text[:100]}")
                    except Exception as e:
                        logger.error(f"获取仓库 {base_repo} 的 Releases 时出错: {e}")

            if new_items:
                logger.info(f"找到 {len(new_items)} 个新的 items 在 {repo}{branch_suffix}")
            else:
                logger.debug(f"没有找到新的 items 在 {repo}{branch_suffix}")

            self.last_check_time[repo] = (
                datetime.utcnow().replace(microsecond=0).isoformat()
            )
            logger.debug(f"更新仓库 {repo}{branch_suffix} 的时间戳为: {self.last_check_time[repo]}")

            return new_items
        except Exception as e:
            logger.error(f"解析时间/获取数据时出错: {e}", exc_info=True)
            self.last_check_time[repo] = (
                datetime.utcnow().replace(microsecond=0).isoformat()
            )
            logger.info(
                f"出错后更新仓库 {repo}{branch_suffix} 的时间戳为: {self.last_check_time[repo]}"
            )
            return []

    async def _notify_subscribers(self, repo: str, new_items: list[dict[str, Any]]):
        """Notify subscribers about new issues and PRs"""
        if not new_items:
            return

        repo_key = self._resolve_repo_key(repo) or repo
        base_repo, branch, _ = self._parse_subscription_key(repo_key)
        branch_suffix = f" ({branch} 分支)" if branch else ""

        for subscriber_id in self.subscriptions.get(repo_key, []):
            try:
                # Create notification message
                for item in new_items:
                    if not self._subscription_allows(repo_key, self._item_event_name(item)):
                        continue
                    if "_astrbot_type" in item:
                        if item["_astrbot_type"] == "commit":
                            sha = item.get("sha", "")[:7]
                            msg = item.get("commit", {}).get("message", "").split("\n")[0]
                            author = item.get("commit", {}).get("author", {}).get("name", "未知")
                            url = item.get("html_url", "")
                            branch = item.get("_astrbot_branch")
                            branch_info = f" ({branch} 分支)" if branch else ""
                            message = (
                                f"[GitHub 更新] 仓库 {base_repo}{branch_info} 有新的代码推送:\n"
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
                                f"[GitHub 更新] 仓库 {base_repo}{branch_suffix} 发布了新版本:\n"
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
                            f"[GitHub 更新] 仓库 {base_repo}{branch_suffix} 有新的{item_type}:\n"
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

        event_name = self._webhook_event_name(event_type)
        event_branch = self._extract_webhook_branch(event_type, payload)
        normalized_repo = self._normalize_repo_name(repo_full_name)
        matching_keys = []
        for key, subscribers in self.subscriptions.items():
            if not subscribers:
                continue
            base_repo, branch, _ = self._parse_subscription_key(key)
            if self._normalize_repo_name(base_repo) != normalized_repo:
                continue
            if branch and event_branch and self._normalize_repo_name(branch) != self._normalize_repo_name(event_branch):
                continue
            if branch and event_branch is None and event_type in {"push", "create"}:
                continue
            if self._subscription_allows(key, event_name):
                matching_keys.append(key)

        if not matching_keys:
            logger.debug(
                f"忽略仓库 {repo_full_name} 的 Webhook 事件 {event_type}: 未找到匹配订阅"
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

        sent_to: set[str] = set()
        for repo_key in matching_keys:
            for subscriber_id in self.subscriptions.get(repo_key, []):
                if subscriber_id in sent_to:
                    continue
                sent_to.add(subscriber_id)
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
    #     '''查看 GitHub 仓库的 Star 趋势图。如: /ghstar AstrBotDev/AstrBot'''
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
