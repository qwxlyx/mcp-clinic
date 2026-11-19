"""
Клиент к stdio MCP-серверу (Задание 1, клиентская часть).

Жизненный цикл:
  subprocess.Popen(python -m mcp_clinic.servers.stdio_server)
  → initialize → tools/list → tools/call → закрытие каналов.
Серверные ValueError превращаются в структурированные ToolError.
"""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters  # в mcp 2.x параметры здесь

from mcp_clinic.clients.base import BaseMCPClient

MODULE = "mcp_clinic.servers.stdio_server"


class ToolError(Exception):
    """Структурированная ошибка инструмента, понятная пользователю."""

    def __init__(self, tool: str, message: str):
        self.tool = tool
        super().__init__(f"[{tool}] {message}")


class StdioMCPClient(BaseMCPClient):
    source_name = "Локальный stdio"

    def __init__(self, python_exe: str | None = None, startup_timeout: float = 20.0):
        super().__init__()
        self._python = python_exe or sys.executable
        self._params = StdioServerParameters(
            command=self._python,
            args=["-m", MODULE],
            env={"PYTHONUNBUFFERED": "1"},
        )
        self._timeout = startup_timeout
        self._cm = None
        self._session_cm = None
        self.session: ClientSession | None = None

    async def connect(self) -> None:
        """Запуск подпроцесса + initialize по протоколу MCP."""
        self._cm = stdio_client(self._params)
        read, write = await asyncio.wait_for(self._cm.__aenter__(),
                                             timeout=self._timeout)
        self._session_cm = ClientSession(read, write)
        self.session = await self._session_cm.__aenter__()
        rid = self.log.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "mcp-clinic-web", "version": "1.0.0"},
        })
        init = await asyncio.wait_for(self.session.initialize(),
                                      timeout=self._timeout)
        self.log.response(rid, {
            "protocolVersion": init.protocol_version,
            "serverInfo": init.server_info.model_dump(),
            "capabilities": init.capabilities.model_dump(),
        })

    async def list_tools(self) -> list[dict]:
        rid = self.log.request("tools/list", {})
        result = await asyncio.wait_for(self.session.list_tools(),
                                        timeout=self._timeout)
        tools = [t.model_dump(by_alias=True) for t in result.tools]
        self.log.response(rid, {"tools": [t["name"] for t in tools]})
        return tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        rid = self.log.request("tools/call", {"name": name,
                                              "arguments": arguments})
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(name, arguments),
                timeout=self._timeout)
        except ValueError as e:
            self.log.response(rid, str(e), is_error=True)
            raise ToolError(name, str(e)) from e
        if result.is_error:
            text = result.content[0].text if result.content else "unknown"
            self.log.response(rid, text, is_error=True)
            raise ToolError(name, text)
        payload = result.content[0].text
        import json
        data = json.loads(payload)
        self.log.response(rid, data)
        return data

    async def aclose(self) -> None:
        """Корректное закрытие: сессия, каналы, завершение подпроцесса."""
        try:
            if self._session_cm:
                await self._session_cm.__aexit__(None, None, None)
            if self._cm:
                await self._cm.__aexit__(None, None, None)
        finally:
            self.session = None
