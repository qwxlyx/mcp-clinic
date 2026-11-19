"""
Базовый клиент MCP: жизненный цикл сессии + журнал протокольных пакетов.

Инспектор JSON-RPC в веб-интерфейсе показывает именно эти записи:
запрос tools/call с сериализованными параметрами и ответ сервера
в канонической обёртке content: [{type: "text", ...}].

Также здесь живёт «actor» — единый фоновый event loop в отдельном потоке,
в котором исполняются ВСЕ MCP-сессии. Это обязательно:

• Streamlit пересоздаёт глобальные переменные скрипта при каждом rerun,
  поэтому loop нельзя держать в app.py — сессии развалились бы пополам;
  модуль пакета mcp_clinic импорт-кэшем защищён от перезапуска скрипта.
• Ручное управление контекстными менеджерами stdio_client/sse_client
  (self._cm.__aenter__() → ... → __aexit__()) требует, чтобы вход и выход
  происходили в одном task и одном anyio cancel scope — иначе SDK падает
  с «Attempted to exit cancel scope in a different task». Поэтому каждая
  корутина клиента ставится в очередь и исполняется в одном drain-task.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field


@dataclass
class PacketLog:
    """Журнал протокольного обмена для MCP Packet Inspector."""

    entries: list[dict] = field(default_factory=list)
    _next_id: int = 1

    def request(self, method: str, params: dict) -> int:
        rid = self._next_id
        self._next_id += 1
        self.entries.append({
            "direction": "→ запрос",
            "jsonrpc": {
                "jsonrpc": "2.0",
                "id": rid,
                "method": method,
                "params": params,
            },
        })
        return rid

    def response(self, rid: int, result, is_error: bool = False) -> None:
        if is_error:
            payload = {"jsonrpc": "2.0", "id": rid,
                       "error": {"code": -32000, "message": str(result)}}
        else:
            payload = {"jsonrpc": "2.0", "id": rid,
                       "result": {"content": [
                           {"type": "text",
                            "text": json.dumps(result, ensure_ascii=False,
                                               default=str)}]}}
        self.entries.append({"direction": "← ответ", "jsonrpc": payload})

    def dump(self) -> list[dict]:
        return self.entries


# ---------------------------------------------------------------------------
# Actor: единый фоновый loop + drain-task (см. docstring модуля)
# ---------------------------------------------------------------------------
_ACTOR: dict = {}


def _ensure_loop() -> asyncio.AbstractEventLoop:
    if "loop" not in _ACTOR:
        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        threading.Thread(target=_run, daemon=True).start()
        _ACTOR["loop"] = loop
    return _ACTOR["loop"]


async def _drain() -> None:
    queue: asyncio.Queue = asyncio.Queue()
    _ACTOR["queue"] = queue
    while True:
        coro, box, event = await queue.get()
        try:
            box["result"] = await coro
        except BaseException as e:  # noqa: BLE001 — ошибка уходит вызывающему
            box["error"] = e
        finally:
            event.set()


def run_async(coro, timeout: float = 60.0):
    """Выполнить корутину MCP-клиента в общем фоновом loop.

    Потокобезопасно: подходит для Streamlit (script thread), тестов и CLI.
    """
    loop = _ensure_loop()
    if "queue" not in _ACTOR:
        _ACTOR["drain"] = asyncio.run_coroutine_threadsafe(_drain(), loop)
        while "queue" not in _ACTOR:
            time.sleep(0.01)
    box: dict = {}
    event = threading.Event()
    loop.call_soon_threadsafe(_ACTOR["queue"].put_nowait, (coro, box, event))
    if not event.wait(timeout):
        raise TimeoutError(f"Превышен таймаут {timeout:.0f} с")
    if "error" in box:
        raise box["error"]
    return box.get("result")


class BaseMCPClient:
    """Общий интерфейс трёх клиентов (stdio / SSE / external)."""

    source_name = "base"

    def __init__(self):
        self.log = PacketLog()

    async def list_tools(self) -> list[dict]:
        raise NotImplementedError

    async def call_tool(self, name: str, arguments: dict) -> dict:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass
