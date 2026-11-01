"""上游 HTTP 客户端。

统一封装三类接口，命令处理器不直接使用 aiohttp：

- Sub2API 管理 API：登录、精确搜索用户、查询单个用户；
- 签到服务的机器人接口：``/api/bot/checkin``、``/api/bot/status``；
- 抽奖服务的机器人接口：``/api/bot/draw``、``/api/bot/status``。

异常分层：``UserError`` 可直接展示给用户；``UpstreamError`` 带 HTTP 状态与
业务错误码，由上层决定文案；写请求（签到/抽奖）绝不自动重试。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

try:  # pragma: no cover - AstrBot 运行时一定能导入
    import aiohttp
except ImportError:  # pragma: no cover - 便于在缺少依赖的环境中做单元测试
    aiohttp = None  # type: ignore[assignment]

from . import formatting as fmt
from .log import logger

DEFAULT_CONNECT_TIMEOUT = 5.0
LOGIN_TIMEOUT = 10.0
MAX_SEARCH_PAGES = 20
SEARCH_PAGE_SIZE = 100


# --------------------------------------------------------------------- 异常类型


class BotError(Exception):
    """插件内部异常基类。"""


class UserError(BotError):
    """可以直接回复给用户的错误（文案即 ``str(exc)``）。"""


class ServiceNotConfigured(UserError):
    """服务地址或机器人密钥未配置。"""


class UpstreamError(BotError):
    """上游返回的错误，带 HTTP 状态与业务错误码。"""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.payload: dict[str, Any] = dict(payload or {})


class NetworkError(UpstreamError):
    """网络不可达、连接被拒绝等。"""

    def __init__(self, message: str = "网络请求失败") -> None:
        super().__init__(message, code="network_error")


class UpstreamTimeout(UpstreamError):
    """请求超时（结果未知，写请求不能重试）。"""

    def __init__(self, message: str = fmt.MSG_TIMEOUT_UPSTREAM) -> None:
        super().__init__(message, code="request_timeout")


# --------------------------------------------------------------------- 传输层


@dataclass
class HttpResult:
    status: int
    data: Any
    text: str


class Transport(Protocol):
    """最小 HTTP 传输协议，便于单元测试注入假实现。"""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> HttpResult: ...

    async def close(self) -> None: ...


def _parse_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


class AiohttpTransport:
    """基于 aiohttp 的传输实现（连接超时 5 秒，总超时由配置决定）。"""

    def __init__(
        self,
        total_timeout: float = 30.0,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        self._total_timeout = float(total_timeout)
        self._connect_timeout = float(connect_timeout)
        self._session: Any = None

    async def _ensure_session(self) -> Any:
        if aiohttp is None:  # pragma: no cover - 依赖缺失时
            raise ServiceNotConfigured("运行环境缺少 aiohttp，无法发起 HTTP 请求")
        session = self._session
        if session is None or getattr(session, "closed", False):
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=self._total_timeout,
                    connect=self._connect_timeout,
                )
            )
            self._session = session
        return session

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> HttpResult:
        session = await self._ensure_session()
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = aiohttp.ClientTimeout(
                total=float(timeout),
                connect=min(self._connect_timeout, float(timeout)),
            )
        try:
            async with session.request(
                method,
                url,
                headers=dict(headers or {}),
                params=dict(params) if params else None,
                json=json_body,
                **kwargs,
            ) as response:
                text = await response.text()
                return HttpResult(
                    status=response.status,
                    data=_parse_json(text),
                    text=text,
                )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise UpstreamTimeout() from exc
        except Exception as exc:
            if aiohttp is not None and isinstance(exc, aiohttp.ClientError):
                raise NetworkError(f"网络请求失败：{exc}") from exc
            raise

    async def close(self) -> None:
        session = self._session
        self._session = None
        if session is None:
            return
        try:
            await session.close()
        except Exception:  # pragma: no cover - 关闭失败无需影响插件卸载
            logger.warning("关闭 HTTP 会话失败", exc_info=True)


# ------------------------------------------------------------- Sub2API 管理 API


class Sub2ApiClient:
    """Sub2API 管理 API 客户端（只使用登录、搜索、查询三个接口）。"""

    def __init__(
        self,
        transport: Transport,
        *,
        base_url: str,
        email: str = "",
        password: str = "",
        timeout: float | None = None,
        login_timeout: float = LOGIN_TIMEOUT,
    ) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._email = (email or "").strip()
        self._password = password or ""
        self._timeout = timeout
        self._login_timeout = login_timeout
        self._token: str | None = None
        self._token_deadline = 0.0

    @property
    def credentials_configured(self) -> bool:
        return bool(self._email and self._password)

    # -------------------------------------------------------------- Token 管理

    async def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_deadline:
            return self._token
        return await self._login()

    async def _login(self) -> str:
        if not self.credentials_configured:
            raise UserError(fmt.MSG_ADMIN_CREDS_MISSING)
        result = await self._transport.request(
            "POST",
            f"{self._base_url}/auth/login",
            json_body={"email": self._email, "password": self._password},
            timeout=self._login_timeout,
        )
        if result.status >= 400:
            raise self._map_http_error(result, login=True)
        body = result.data if isinstance(result.data, dict) else {}
        node = body.get("data") if isinstance(body.get("data"), dict) else body
        token = node.get("access_token") or node.get("token")
        if not isinstance(token, str) or not token:
            raise UpstreamError(
                "Sub2API 登录响应缺少 access_token",
                status=result.status,
                code="invalid_login_response",
                payload=body if isinstance(body, dict) else None,
            )
        expires_in = _to_int(node.get("expires_in"), 86400) or 86400
        self._token = token
        self._token_deadline = time.monotonic() + max(expires_in - 60, 30)
        logger.debug("Sub2API 管理员登录成功，Token 有效期 %s 秒", expires_in)
        return token

    def _invalidate_token(self) -> None:
        self._token = None
        self._token_deadline = 0.0

    # ---------------------------------------------------------------- GET 请求

    async def _get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        retry_auth: bool = True,
    ) -> Any:
        token = await self._ensure_token()
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        result = await self._request_read(
            "GET", url, headers=headers, params=params, timeout=self._timeout
        )
        if result.status == 401 and retry_auth:
            logger.warning("Sub2API 返回 401，清除 Token 后重试一次")
            self._invalidate_token()
            return await self._get(path, params, retry_auth=False)
        if result.status >= 400:
            raise self._map_http_error(result)

        body = result.data
        if isinstance(body, dict):
            code = body.get("code")
            if code not in (0, None, "0"):
                raise UpstreamError(
                    fmt.sanitize(body.get("message") or body.get("msg"))
                    or fmt.MSG_UPSTREAM_ERROR,
                    status=result.status,
                    code=str(code),
                    payload=body,
                )
            data = body.get("data", body)
        else:
            data = body
        return data

    async def _request_read(self, method: str, url: str, **kwargs: Any) -> HttpResult:
        """读请求：网络失败/超时后重试一次（不含 4xx/5xx）。"""
        try:
            return await self._transport.request(method, url, **kwargs)
        except (NetworkError, UpstreamTimeout) as exc:
            logger.warning("Sub2API 请求失败，重试一次：%s", exc)
            return await self._transport.request(method, url, **kwargs)

    # ---------------------------------------------------------------- 业务接口

    async def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        """按邮箱精确匹配用户，兼容分页与字符串/数字 id。"""
        target = (email or "").strip().lower()
        if not target:
            return None
        for page in range(1, MAX_SEARCH_PAGES + 1):
            data = await self._get(
                "/admin/users",
                {
                    "page": page,
                    "page_size": SEARCH_PAGE_SIZE,
                    "search": target,
                },
            )
            items = _extract_items(data)
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("email") or "").strip().lower() == target:
                    return item
            total = _to_int(_field(data, "total"), 0) or 0
            if not items or len(items) < SEARCH_PAGE_SIZE:
                return None
            if total and page * SEARCH_PAGE_SIZE >= total:
                return None
        raise UpstreamError(
            "搜索结果过多，请确认填写的注册邮箱",
            code="too_many_results",
        )

    async def get_user(self, uid: str | int) -> dict[str, Any]:
        """查询单个用户，用于绑定提示与 ``/查询`` 的余额。"""
        data = await self._get(f"/admin/users/{_quote(uid)}")
        if not isinstance(data, dict):
            raise UpstreamError(
                "Sub2API 返回的用户数据格式不正确",
                code="invalid_user_payload",
            )
        return data

    # ---------------------------------------------------------------- 错误映射

    def _map_http_error(self, result: HttpResult, *, login: bool = False) -> BotError:
        body = result.data if isinstance(result.data, dict) else {}
        code = str(body.get("error") or body.get("code") or "").strip()
        message = fmt.sanitize(body.get("message") or body.get("msg"))
        status = result.status
        if status == 400 and code == "invalid_user_id":
            return UpstreamError(
                fmt.MSG_INVALID_USER_ID, status=status, code=code, payload=body
            )
        if status == 401:
            return UserError("管理员凭据配置错误，请联系站点管理员")
        if status == 403:
            return UserError("专用管理员权限不足，请检查账号角色")
        if status == 423:
            return UserError(fmt.MSG_COMPLIANCE)
        if status == 404:
            return UserError(fmt.MSG_USER_GONE)
        if status == 504:
            return UpstreamError(
                fmt.MSG_TIMEOUT_UPSTREAM,
                status=status,
                code=code or "upstream_timeout",
                payload=body,
            )
        if 500 <= status < 600:
            return UpstreamError(
                message or fmt.MSG_UPSTREAM_ERROR,
                status=status,
                code=code or "upstream_error",
                payload=body,
            )
        if message:
            return UpstreamError(message, status=status, code=code or None, payload=body)
        verb = "登录" if login else "请求"
        return UpstreamError(
            f"Sub2API {verb}失败（HTTP {status}）",
            status=status,
            code=code or None,
            payload=body,
        )


# ------------------------------------------------------- 签到 / 抽奖机器人接口


class BotServiceClient:
    """签到或抽奖服务的机器人接口客户端。"""

    def __init__(
        self,
        transport: Transport,
        *,
        label: str,
        base_url: str,
        secret: str,
        timeout: float | None = None,
    ) -> None:
        self._transport = transport
        self._label = label
        self._base_url = (base_url or "").rstrip("/")
        self._secret = (secret or "").strip()
        self._timeout = timeout

    @property
    def label(self) -> str:
        return self._label

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._secret)

    # ---------------------------------------------------------------- 业务接口

    async def checkin(self, user_id: str | int, user_name: str = "") -> dict[str, Any]:
        """签到（写请求，不重试）。"""
        return await self._request(
            "POST",
            "/api/bot/checkin",
            body={"userId": str(user_id), "userName": user_name or ""},
            retry=False,
        )

    async def draw(self, user_id: str | int, user_name: str = "") -> dict[str, Any]:
        """抽奖（写请求，不重试）。"""
        return await self._request(
            "POST",
            "/api/bot/draw",
            body={"userId": str(user_id), "userName": user_name or ""},
            retry=False,
        )

    async def status(self, user_id: str | int) -> dict[str, Any]:
        """当日状态（读请求，可重试一次）。"""
        return await self._request(
            "GET",
            "/api/bot/status",
            params={"userId": str(user_id)},
            retry=True,
        )

    # ---------------------------------------------------------------- 内部实现

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        retry: bool = False,
    ) -> dict[str, Any]:
        if not self.configured:
            raise ServiceNotConfigured(fmt.MSG_BOT_SECRET_MISSING)
        url = f"{self._base_url}{path}"
        headers = {
            "X-Bot-Secret": self._secret,
            "Accept": "application/json",
        }
        attempts = 2 if retry else 1
        result: HttpResult | None = None
        for index in range(attempts):
            try:
                result = await self._transport.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json_body=body,
                    timeout=self._timeout,
                )
                break
            except (NetworkError, UpstreamTimeout) as exc:
                if index + 1 >= attempts:
                    raise
                logger.warning("%s 请求失败，重试一次：%s", self._label, exc)
        assert result is not None  # 上面的循环要么 break 要么 raise

        payload = result.data if isinstance(result.data, dict) else None
        if result.status < 400 and payload is not None and payload.get("ok") is not False:
            return payload
        if result.status >= 400 or payload is not None:
            raise self._error(result, payload)
        raise UpstreamError(
            f"{self._label}返回了无法解析的响应",
            status=result.status,
            code="invalid_response",
        )

    def _error(self, result: HttpResult, payload: Mapping[str, Any] | None) -> UpstreamError:
        data = dict(payload or {})
        code = str(data.get("error") or data.get("code") or "").strip() or None
        message = fmt.sanitize(data.get("message") or data.get("msg"))
        status = result.status
        if status == 504:
            return UpstreamError(
                message or fmt.MSG_TIMEOUT_UPSTREAM,
                status=status,
                code=code or "upstream_timeout",
                payload=data,
            )
        if status >= 500:
            return UpstreamError(
                message or fmt.MSG_UPSTREAM_ERROR,
                status=status,
                code=code or "upstream_error",
                payload=data,
            )
        return UpstreamError(
            message or fmt.MSG_UPSTREAM_ERROR,
            status=status,
            code=code,
            payload=data,
        )


# --------------------------------------------------------------------- 小工具


def _to_int(value: Any, default: int | None = None) -> int | None:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _field(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        return data.get(key)
    return None


def _extract_items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "users", "list", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _quote(value: str | int) -> str:
    from urllib.parse import quote

    return quote(str(value).strip(), safe="")


__all__ = [
    "AiohttpTransport",
    "BotError",
    "BotServiceClient",
    "HttpResult",
    "NetworkError",
    "ServiceNotConfigured",
    "Sub2ApiClient",
    "Transport",
    "UpstreamError",
    "UpstreamTimeout",
    "UserError",
]
