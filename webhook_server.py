import asyncio
import hashlib
import hmac
from typing import Any

from quart import Quart, Response, request

from astrbot.api import logger


class GitHubWebhookServer:
    """Run a Quart server to receive GitHub webhook callbacks."""

    def __init__(
        self,
        plugin,
        host: str,
        port: int,
        secret: str | None,
        path: str,
    ) -> None:
        self.plugin = plugin
        self.host = host
        self.port = port
        self.secret: bytes | None = secret.encode("utf-8") if secret else None
        self.path = path if path.startswith("/") else f"/{path}"
        self.app = Quart(__name__)
        self._shutdown: asyncio.Event | None = None
        self._runner: asyncio.Task[Any] | None = None
        self._configure_routes()

    def _configure_routes(self) -> None:
        @self.app.post(self.path)
        async def github_webhook():
            signature = request.headers.get("X-Hub-Signature-256")
            payload = await request.get_data()
            if isinstance(payload, (bytes, bytearray)):
                payload_bytes = bytes(payload)
            else:
                payload_bytes = str(payload).encode("utf-8")

            if self.secret:
                expected = "sha256=" + hmac.new(
                    self.secret, payload_bytes, hashlib.sha256
                ).hexdigest()
                if not signature or not hmac.compare_digest(signature, expected):
                    logger.warning("收到无效的 GitHub Webhook 签名")
                    return Response("invalid signature", status=401)

            event_type = request.headers.get("X-GitHub-Event", "")
            if not event_type:
                return Response("missing event", status=400)

            try:
                data = await request.get_json()
            except Exception:
                logger.warning("GitHub Webhook JSON 解析失败")
                return Response("invalid payload", status=400)

            async def dispatch() -> None:
                try:
                    await self.plugin.handle_webhook_event(event_type, data)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        f"处理 GitHub Webhook 事件时出错: {exc}", exc_info=True
                    )

            asyncio.create_task(dispatch())
            return Response("ok", status=200)

        @self.app.get(self.path)
        async def github_webhook_health():
            return Response("github webhook ok", status=200)

    def start(self) -> None:
        if self._runner:
            return
        self._shutdown = asyncio.Event()
        logger.info(
            f"启动 GitHub Webhook 服务: http://{self.host}:{self.port}{self.path}"
        )
        if not self.secret:
            logger.warning("GitHub Webhook 未设置 secret，建议在配置中设置以验证请求")
        self._runner = asyncio.create_task(
            self.app.run_task(
                host=self.host,
                port=self.port,
                shutdown_trigger=self._wait_for_shutdown,
            )
        )

    async def _wait_for_shutdown(self) -> None:
        if self._shutdown is None:
            return
        await self._shutdown.wait()

    async def stop(self) -> None:
        if not self._runner:
            return
        if self._shutdown:
            self._shutdown.set()
        try:
            await self._runner
        finally:
            self._runner = None
            self._shutdown = None
