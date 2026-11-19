"""
Клиент к локальному SSE MCP-серверу на порту 8000 (Задание 2, клиентская часть).

Включены требуемые механизмы:
  • Sanitizer — автоматическое приведение типов (строки → числа/булевы)
    и подстановка физиологически безопасных значений по умолчанию;
  • пул повторных попыток (retries) при кратковременной недоступности порта.
"""

from __future__ import annotations

import asyncio
import json

from mcp import ClientSession
from mcp.client.sse import sse_client

from mcp_clinic.clients.base import BaseMCPClient
from mcp_clinic.clients.stdio_client import ToolError

DEFAULT_URL = "http://127.0.0.1:8000/sse"
MAX_RETRIES = 3
RETRY_DELAY = 1.0


class Sanitizer:
    """Автосанитизация входных данных перед отправкой на сетевой сервер."""

    # умолчания, безопасные физиологически (подставляются при пропуске)
    SMART_DEFAULTS = {
        "age": 50, "weight_kg": 70.0, "height_m": 1.7,
        "creatinine_mg_dl": 0.9, "sex": "female",
        "eye": 4, "verbal": 5, "motor": 6,
    }
    BOOL_FIELDS = {"chf", "hypertension", "stroke_tia", "vascular_disease",
                   "diabetes", "renal_disease", "liver_disease",
                   "stroke_history", "bleeding_history", "age_over_65",
                   "drugs_alcohol"}
    INT_FIELDS = {"age", "eye", "verbal", "motor"}
    FLOAT_FIELDS = {"weight_kg", "height_m", "creatinine_mg_dl"}

    def clean(self, arguments: dict) -> tuple[dict, list[str]]:
        notes = []
        out = {}
        for key, raw in arguments.items():
            if raw is None or raw == "":
                if key in self.SMART_DEFAULTS:
                    out[key] = self.SMART_DEFAULTS[key]
                    notes.append(f"{key}: пусто → умолчание "
                                 f"{self.SMART_DEFAULTS[key]}")
                continue
            if key in self.BOOL_FIELDS:
                out[key] = self._to_bool(raw, key, notes)
            elif key in self.INT_FIELDS:
                out[key] = self._to_number(raw, key, notes, int)
            elif key in self.FLOAT_FIELDS:
                out[key] = self._to_number(raw, key, notes, float)
            else:
                out[key] = raw
        return out, notes

    @staticmethod
    def _to_bool(raw, key, notes) -> bool:
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("true", "1", "да", "yes", "on"):
            return True
        if s in ("false", "0", "нет", "no", "off"):
            return False
        notes.append(f"{key}: '{raw}' не булево → False")
        return False

    @staticmethod
    def _to_number(raw, key, notes, cast):
        try:
            return cast(float(str(raw).replace(",", ".")))
        except (TypeError, ValueError):
            notes.append(f"{key}: '{raw}' не число → пропущено")
            return None


class SSEMCPClient(BaseMCPClient):
    source_name = "Локальный сетевой (SSE :8000)"

    def __init__(self, url: str = DEFAULT_URL):
        super().__init__()
        self.url = url
        self.sanitizer = Sanitizer()
        self._cm = None
        self._session_cm = None
        self.session: ClientSession | None = None
        self.sanitize_notes: list[str] = []

    async def connect(self) -> None:
        """Подключение с ретраями — порт может быть ещё не поднят."""
        last_err = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._cm = sse_client(self.url)
                read, write = await asyncio.wait_for(self._cm.__aenter__(),
                                                     timeout=10.0)
                self._session_cm = ClientSession(read, write)
                self.session = await self._session_cm.__aenter__()
                rid = self.log.request("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "mcp-clinic-web",
                                   "version": "1.0.0"},
                    "_meta": {"transport": "sse", "url": self.url},
                })
                init = await asyncio.wait_for(self.session.initialize(),
                                              timeout=10.0)
                self.log.response(rid, {
                    "protocolVersion": init.protocol_version,
                    "serverInfo": init.server_info.model_dump(),
                })
                return
            except Exception as e:  # noqa: BLE001 — ретрай любой сетевой ошибки
                last_err = e
                await self._cleanup_partial()
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY * attempt)
        raise ConnectionError(
            f"SSE-сервер недоступен после {MAX_RETRIES} попыток: {last_err}. "
            f"Запустите: python -m mcp_clinic.servers.sse_server")

    async def _cleanup_partial(self) -> None:
        try:
            if self._session_cm:
                await self._session_cm.__aexit__(None, None, None)
            if self._cm:
                await self._cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        self._session_cm = None
        self._cm = None
        self.session = None

    async def list_tools(self) -> list[dict]:
        rid = self.log.request("tools/list", {})
        result = await asyncio.wait_for(self.session.list_tools(), timeout=15.0)
        tools = [t.model_dump(by_alias=True) for t in result.tools]
        self.log.response(rid, {"tools": [t["name"] for t in tools]})
        return tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        # санитизация ДО отправки: типы приводимся, пустые → smart defaults
        cleaned, notes = self.sanitizer.clean(arguments)
        cleaned = {k: v for k, v in cleaned.items() if v is not None}
        self.sanitize_notes = notes
        rid = self.log.request("tools/call", {"name": name,
                                              "arguments": cleaned})
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(name, cleaned), timeout=15.0)
        except Exception as e:  # noqa: BLE001
            self.log.response(rid, str(e), is_error=True)
            raise ToolError(name, str(e)) from e
        if result.is_error:
            text = result.content[0].text if result.content else "unknown"
            self.log.response(rid, text, is_error=True)
            raise ToolError(name, text)
        data = json.loads(result.content[0].text)
        self.log.response(rid, data)
        return data

    async def aclose(self) -> None:
        await self._cleanup_partial()
