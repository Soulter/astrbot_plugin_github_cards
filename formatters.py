from datetime import datetime
from typing import Any


def truncate_text(text: str, limit: int = 200) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def format_webhook_issue_message(
    repo: str,
    action: str,
    issue: dict[str, Any],
    sender: dict[str, Any] | None,
) -> str | None:
    action_labels = {
        "opened": "新建",
        "closed": "关闭",
        "reopened": "重新打开",
    }

    if action == "closed" and issue.get("state") == "closed":
        action_labels["closed"] = "关闭"

    if action not in action_labels:
        return None

    actor = (sender or {}).get("login") or issue.get("user", {}).get("login")
    actor = actor or "未知"

    message_lines = [
        f"[GitHub Webhook] 仓库 {repo} 的 Issue 更新",
        f"#{issue['number']} {issue['title']}",
        f"事件: {action_labels[action]}",
        f"触发人: {actor}",
    ]

    if issue.get("html_url"):
        message_lines.append(f"链接: {issue['html_url']}")

    return "\n".join(message_lines)


def format_webhook_pr_message(
    repo: str,
    action: str,
    pull_request: dict[str, Any],
    sender: dict[str, Any] | None,
) -> str | None:
    action_labels = {
        "opened": "新建",
        "closed": "关闭",
        "reopened": "重新打开",
    }

    if action == "closed" and pull_request.get("merged"):
        action_labels["closed"] = "合并"

    if action not in action_labels:
        return None

    actor = (sender or {}).get("login") or pull_request.get("user", {}).get("login")
    actor = actor or "未知"

    base_label = pull_request.get("base", {}).get("label", "?")
    head_label = pull_request.get("head", {}).get("label", "?")

    message_lines = [
        f"[GitHub Webhook] 仓库 {repo} 的 PR 更新",
        f"#{pull_request['number']} {pull_request['title']}",
        f"事件: {action_labels[action]}",
        f"触发人: {actor}",
        f"分支: {head_label} → {base_label}",
    ]

    if pull_request.get("html_url"):
        message_lines.append(f"链接: {pull_request['html_url']}")

    return "\n".join(message_lines)


def format_webhook_issue_comment_message(
    repo: str,
    action: str,
    issue: dict[str, Any],
    comment: dict[str, Any],
    sender: dict[str, Any] | None,
) -> str | None:
    action_labels = {
        "created": "新增评论",
        "edited": "编辑评论",
        "deleted": "删除评论",
    }

    label = action_labels.get(action)
    if not label:
        return None

    actor = (sender or {}).get("login")
    actor = actor or comment.get("user", {}).get("login") or "未知"

    message_lines = [
        f"[GitHub Webhook] 仓库 {repo} 的 Issue 评论更新",
        f"Issue #{issue.get('number', '?')} {issue.get('title', '')}",
        f"事件: {label}",
        f"触发人: {actor}",
    ]

    if action != "deleted":
        body = comment.get("body", "")
        if body:
            message_lines.append("评论内容:")
            message_lines.append(truncate_text(body))

    url = comment.get("html_url") or issue.get("html_url")
    if url:
        message_lines.append(f"链接: {url}")

    return "\n".join(line for line in message_lines if line)


def format_webhook_commit_comment_message(
    repo: str,
    action: str,
    comment: dict[str, Any],
    sender: dict[str, Any] | None,
) -> str | None:
    if action and action != "created":
        return None

    actor = (sender or {}).get("login")
    actor = actor or comment.get("user", {}).get("login") or "未知"
    commit_id = comment.get("commit_id", "")
    short_commit = commit_id[:7] if commit_id else "未知"

    message_lines = [
        f"[GitHub Webhook] 仓库 {repo} 的提交评论",
        f"提交: {short_commit}",
        f"触发人: {actor}",
    ]

    body = comment.get("body", "")
    if body:
        message_lines.append("评论内容:")
        message_lines.append(truncate_text(body))

    if comment.get("html_url"):
        message_lines.append(f"链接: {comment['html_url']}")

    return "\n".join(line for line in message_lines if line)


def format_webhook_discussion_message(
    repo: str,
    action: str,
    discussion: dict[str, Any],
    sender: dict[str, Any] | None,
) -> str | None:
    action_labels = {
        "created": "新建讨论",
        "edited": "更新讨论",
        "answered": "标记为已回答",
        "unanswered": "取消回答",
        "labeled": "添加标签",
        "unlabeled": "移除标签",
    }

    label = action_labels.get(action)
    if not label:
        return None

    actor = (sender or {}).get("login") or discussion.get("user", {}).get("login")
    actor = actor or "未知"

    message_lines = [
        f"[GitHub Webhook] 仓库 {repo} 的 Discussion 更新",
        f"Discussion #{discussion.get('number', '?')} {discussion.get('title', '')}",
        f"事件: {label}",
        f"触发人: {actor}",
    ]

    if discussion.get("html_url"):
        message_lines.append(f"链接: {discussion['html_url']}")

    return "\n".join(line for line in message_lines if line)


def format_webhook_discussion_comment_message(
    repo: str,
    action: str,
    discussion: dict[str, Any],
    comment: dict[str, Any],
    sender: dict[str, Any] | None,
) -> str | None:
    action_labels = {
        "created": "新增讨论评论",
        "edited": "编辑讨论评论",
        "deleted": "删除讨论评论",
    }

    label = action_labels.get(action)
    if not label:
        return None

    actor = (sender or {}).get("login")
    actor = actor or comment.get("user", {}).get("login") or "未知"

    message_lines = [
        f"[GitHub Webhook] 仓库 {repo} 的 Discussion 评论更新",
        f"Discussion #{discussion.get('number', '?')} {discussion.get('title', '')}",
        f"事件: {label}",
        f"触发人: {actor}",
    ]

    if action != "deleted":
        body = comment.get("body", "")
        if body:
            message_lines.append("评论内容:")
            message_lines.append(truncate_text(body))

    url = comment.get("html_url") or discussion.get("html_url")
    if url:
        message_lines.append(f"链接: {url}")

    return "\n".join(line for line in message_lines if line)


def format_webhook_fork_message(
    repo: str,
    forkee: Any,
    sender: dict[str, Any] | None,
) -> str | None:
    if not isinstance(forkee, dict):
        return None

    actor = (sender or {}).get("login") or "未知"
    new_repo = forkee.get("full_name") or forkee.get("name") or "未知"
    html_url = forkee.get("html_url")

    message_lines = [
        f"[GitHub Webhook] 仓库 {repo} 被 Fork",
        f"新仓库: {new_repo}",
        f"触发人: {actor}",
    ]

    if html_url:
        message_lines.append(f"链接: {html_url}")

    return "\n".join(message_lines)


def format_webhook_pr_review_comment_message(
    repo: str,
    action: str,
    pull_request: dict[str, Any],
    comment: dict[str, Any],
    sender: dict[str, Any] | None,
) -> str | None:
    action_labels = {
        "created": "新增审查评论",
        "edited": "编辑审查评论",
        "deleted": "删除审查评论",
    }

    label = action_labels.get(action)
    if not label:
        return None

    actor = (sender or {}).get("login")
    actor = actor or comment.get("user", {}).get("login") or "未知"

    message_lines = [
        f"[GitHub Webhook] 仓库 {repo} 的 PR 审查评论",
        f"PR #{pull_request.get('number', '?')} {pull_request.get('title', '')}",
        f"事件: {label}",
        f"触发人: {actor}",
    ]

    if action != "deleted":
        body = comment.get("body", "")
        if body:
            message_lines.append("评论内容:")
            message_lines.append(truncate_text(body))

    url = comment.get("html_url") or pull_request.get("html_url")
    if url:
        message_lines.append(f"链接: {url}")

    return "\n".join(line for line in message_lines if line)


def format_webhook_pr_review_message(
    repo: str,
    action: str,
    pull_request: dict[str, Any],
    review: dict[str, Any],
    sender: dict[str, Any] | None,
) -> str | None:
    action_labels = {
        "submitted": "提交审查",
        "edited": "编辑审查",
        "dismissed": "撤销审查",
    }

    label = action_labels.get(action)
    if not label:
        return None

    actor = (sender or {}).get("login")
    actor = actor or review.get("user", {}).get("login") or "未知"
    review_state = review.get("state", "").upper()

    message_lines = [
        f"[GitHub Webhook] 仓库 {repo} 的 PR 审查",
        f"PR #{pull_request.get('number', '?')} {pull_request.get('title', '')}",
        f"事件: {label}",
        f"审查状态: {review_state or 'N/A'}",
        f"触发人: {actor}",
    ]

    body = review.get("body", "")
    if body:
        message_lines.append("审查内容:")
        message_lines.append(truncate_text(body))

    url = review.get("html_url") or pull_request.get("html_url")
    if url:
        message_lines.append(f"链接: {url}")

    return "\n".join(line for line in message_lines if line)


def format_webhook_pr_review_thread_message(
    repo: str,
    action: str,
    pull_request: dict[str, Any],
    thread: dict[str, Any],
    sender: dict[str, Any] | None,
) -> str | None:
    action_labels = {
        "created": "创建审查线程",
        "resolved": "已解决审查线程",
        "unresolved": "重新打开审查线程",
    }

    label = action_labels.get(action)
    if not label:
        return None

    actor = (sender or {}).get("login") or "未知"
    comments = thread.get("comments")
    first_comment = comments[0] if isinstance(comments, list) and comments else {}
    body = first_comment.get("body", "")

    message_lines = [
        f"[GitHub Webhook] 仓库 {repo} 的 PR 审查线程",
        f"PR #{pull_request.get('number', '?')} {pull_request.get('title', '')}",
        f"事件: {label}",
        f"触发人: {actor}",
    ]

    if body:
        message_lines.append("讨论内容:")
        message_lines.append(truncate_text(body))

    url = thread.get("html_url") or pull_request.get("html_url")
    if url:
        message_lines.append(f"链接: {url}")

    return "\n".join(line for line in message_lines if line)


def format_webhook_star_message(
    repo: str,
    action: str,
    sender: dict[str, Any] | None,
) -> str | None:
    action_labels = {
        "created": "收藏了仓库",
        "deleted": "取消收藏仓库",
    }

    label = action_labels.get(action)
    if not label:
        return None

    actor = (sender or {}).get("login") or "未知"

    message_lines = [
        "[GitHub Webhook] Star 事件",
        f"仓库: {repo}",
        f"触发人: {actor}",
        f"事件: {label}",
    ]

    return "\n".join(message_lines)


def format_webhook_create_message(
    repo: str,
    payload: dict[str, Any],
    sender: dict[str, Any] | None,
) -> str | None:
    ref_type = payload.get("ref_type")
    if not ref_type:
        return None

    ref = payload.get("ref") or ""
    actor = (sender or {}).get("login") or "未知"

    message_lines = [
        "[GitHub Webhook] 创建事件",
        f"仓库: {repo}",
        f"触发人: {actor}",
    ]

    if ref_type == "repository":
        message_lines.append("创建了新的仓库版本")
    elif ref_type == "branch":
        message_lines.append(f"创建分支: {ref}")
    elif ref_type == "tag":
        message_lines.append(f"创建标签: {ref}")
    else:
        message_lines.append(f"创建 {ref_type}: {ref}")

    return "\n".join(message_lines)


def format_issue_details(repo: str, issue_data: dict[str, Any]) -> str:
    if "pull_request" in issue_data:
        return f"#{issue_data['number']} 是一个 PR，请使用 /ghpr 命令查看详情"

    created_str = issue_data["created_at"].replace("Z", "+00:00")
    updated_str = issue_data["updated_at"].replace("Z", "+00:00")

    created_at = datetime.fromisoformat(created_str)
    updated_at = datetime.fromisoformat(updated_str)

    status = "开启" if issue_data["state"] == "open" else "已关闭"
    labels = ", ".join([label["name"] for label in issue_data.get("labels", [])])

    result = (
        f"🔍 Issue 详情 | {repo}#{issue_data['number']}\n"
        f"标题: {issue_data['title']}\n"
        f"状态: {status}\n"
        f"创建者: {issue_data['user']['login']}\n"
        f"创建时间: {created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"更新时间: {updated_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    if labels:
        result += f"标签: {labels}\n"

    if issue_data.get("assignees") and len(issue_data["assignees"]) > 0:
        assignees = ", ".join(
            [assignee["login"] for assignee in issue_data["assignees"]]
        )
        result += f"指派给: {assignees}\n"

    if issue_data.get("body"):
        body = issue_data["body"]
        if len(body) > 200:
            body = body[:197] + "..."
        result += f"\n内容概要:\n{body}\n"

    result += f"\n链接: {issue_data['html_url']}"
    return result


def format_pr_details(repo: str, pr_data: dict[str, Any]) -> str:
    created_str = pr_data["created_at"].replace("Z", "+00:00")
    updated_str = pr_data["updated_at"].replace("Z", "+00:00")

    created_at = datetime.fromisoformat(created_str)
    updated_at = datetime.fromisoformat(updated_str)

    status = pr_data["state"]
    if status == "open":
        status = "开启"
    elif status == "closed":
        status = "已关闭" if not pr_data.get("merged") else "已合并"

    labels = ", ".join([label["name"] for label in pr_data.get("labels", [])])

    result = (
        f"🔀 PR 详情 | {repo}#{pr_data['number']}\n"
        f"标题: {pr_data['title']}\n"
        f"状态: {status}\n"
        f"创建者: {pr_data['user']['login']}\n"
        f"创建时间: {created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"更新时间: {updated_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"分支: {pr_data['head']['label']} → {pr_data['base']['label']}\n"
    )

    if labels:
        result += f"标签: {labels}\n"

    if pr_data.get("requested_reviewers") and len(pr_data["requested_reviewers"]) > 0:
        reviewers = ", ".join(
            [reviewer["login"] for reviewer in pr_data["requested_reviewers"]]
        )
        result += f"审阅者: {reviewers}\n"

    if pr_data.get("assignees") and len(pr_data["assignees"]) > 0:
        assignees = ", ".join(
            [assignee["login"] for assignee in pr_data["assignees"]]
        )
        result += f"指派给: {assignees}\n"

    result += (
        f"增加: +{pr_data.get('additions', 0)} 行\n"
        f"删除: -{pr_data.get('deletions', 0)} 行\n"
        f"文件变更: {pr_data.get('changed_files', 0)} 个\n"
    )

    if pr_data.get("body"):
        body = pr_data["body"]
        if len(body) > 200:
            body = body[:197] + "..."
        result += f"\n内容概要:\n{body}\n"

    result += f"\n链接: {pr_data['html_url']}"
    return result
