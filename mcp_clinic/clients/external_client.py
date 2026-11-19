"""
Клиент к внешнему распределённому MCP-серверу (Задание 3).

Источник: пакет pubmed-mcp (каталог Glama.ai, healthcare; GitHub BerkayCelk/pubmed-mcp)
— инструменты PubMed, Europe PMC, OpenFDA. Подключение — через сам протокол MCP:
сервер запускается как stdio-подпроцесс, весь обмен идёт по JSON-RPC.

Требования задания:
  • таймаут вызова ≤ 15 секунд (защита от зависания интерфейса);
  • кэширование результатов — от исчерпания rate limits внешних API.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
import time

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters  # в mcp 2.x параметры здесь

from mcp_clinic.clients.base import BaseMCPClient
from mcp_clinic.clients.stdio_client import ToolError

CALL_TIMEOUT = 12.0       # сек, жёсткий таймаут внешнего вызова
CACHE_TTL = 600           # сек, время жизни кэша
MAX_CACHE_ENTRIES = 200


class TTLCache:
    """Простой in-memory кэш с временем жизни — бережём rate limits PubMed/FDA."""

    def __init__(self, ttl: int = CACHE_TTL):
        self.ttl = ttl
        self._store: dict[str, tuple[float, dict]] = {}

    def key(self, tool: str, arguments: dict) -> str:
        raw = json.dumps({"t": tool, "a": arguments}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get(self, key: str) -> dict | None:
        hit = self._store.get(key)
        if not hit:
            return None
        ts, value = hit
        if time.time() - ts > self.ttl:
            del self._store[key]
            return None
        return value

    def put(self, key: str, value: dict) -> None:
        if len(self._store) >= MAX_CACHE_ENTRIES:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest]
        self._store[key] = (time.time(), value)


def _resolve_external_command() -> StdioServerParameters | None:
    """Ищем способ запустить внешний сервер: pubmed-mcp (Python, Glama.ai).
    Проверяем PATH и каталог Scripts текущего venv."""
    import pathlib
    scripts = pathlib.Path(sys.executable).parent
    for exe in ("pubmed-mcp", "medical-mcp"):
        for cand in (shutil.which(exe), scripts / (exe + ".exe"),
                     scripts / exe):
            if cand and pathlib.Path(str(cand)).exists():
                return StdioServerParameters(command=str(cand), args=[])
    return StdioServerParameters(command=sys.executable, args=["-m", "server"],
                                 env={"PYTHONUNBUFFERED": "1"})


class ExternalMCPClient(BaseMCPClient):
    source_name = "Внешний pubmed-mcp (PubMed/FDA)"

    def __init__(self):
        super().__init__()
        self.cache = TTLCache()
        self._cm = None
        self._session_cm = None
        self.session: ClientSession | None = None
        self.cache_hits = 0

    async def connect(self) -> None:
        params = _resolve_external_command()
        self._cm = stdio_client(params)
        read, write = await asyncio.wait_for(self._cm.__aenter__(),
                                             timeout=CALL_TIMEOUT)
        self._session_cm = ClientSession(read, write)
        self.session = await self._session_cm.__aenter__()
        rid = self.log.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "mcp-clinic-web", "version": "1.0.0"},
            "_meta": {"upstream": "pubmed-mcp (Glama.ai)"},
        })
        init = await asyncio.wait_for(self.session.initialize(),
                                      timeout=CALL_TIMEOUT)
        self.log.response(rid, {
            "protocolVersion": init.protocol_version,
            "serverInfo": init.server_info.model_dump(),
        })

    async def list_tools(self) -> list[dict]:
        rid = self.log.request("tools/list", {})
        result = await asyncio.wait_for(self.session.list_tools(),
                                        timeout=CALL_TIMEOUT)
        tools = [t.model_dump(by_alias=True) for t in result.tools]
        self.log.response(rid, {"tools": [t["name"] for t in tools]})
        return tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        # 1) кэш: повторный запрос не уходит во внешнюю сеть
        ckey = self.cache.key(name, arguments)
        cached = self.cache.get(ckey)
        if cached is not None:
            self.cache_hits += 1
            rid = self.log.request("tools/call", {"name": name,
                                                  "arguments": arguments,
                                                  "_meta": {"cached": True}})
            self.log.response(rid, {"_cached": True, **cached})
            return {"_cached": True, **cached}

        # 2) реальный вызов с жёстким таймаутом
        rid = self.log.request("tools/call", {"name": name,
                                              "arguments": arguments})
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(name, arguments),
                timeout=CALL_TIMEOUT)
        except asyncio.TimeoutError as e:
            self.log.response(rid, f"таймаут {CALL_TIMEOUT:.0f} с", is_error=True)
            raise ToolError(name, f"Внешний сервис не ответил за "
                                  f"{CALL_TIMEOUT:.0f} с — повторите позже") from e
        except Exception as e:  # noqa: BLE001
            self.log.response(rid, str(e), is_error=True)
            raise ToolError(name, str(e)) from e
        if result.is_error:
            text = result.content[0].text if result.content else "unknown"
            self.log.response(rid, text, is_error=True)
            raise ToolError(name, text)
        text = result.content[0].text
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"text": text}   # сервер вернул не-JSON — показываем как есть
        self.cache.put(ckey, data)
        self.log.response(rid, data)
        return data

    async def aclose(self) -> None:
        try:
            if self._session_cm:
                await self._session_cm.__aexit__(None, None, None)
            if self._cm:
                await self._cm.__aexit__(None, None, None)
        finally:
            self.session = None
