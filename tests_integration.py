"""Интеграционный тест: три источника MCP подряд через единый интерфейс."""
import asyncio
import sys

sys.path.insert(0, r"C:\Users\79677\Desktop\Универ\3 курс\медицина\Приложение для ПИМ\mcp-clinic")

from mcp_clinic.clients.stdio_client import StdioMCPClient, ToolError
from mcp_clinic.clients.sse_client import SSEMCPClient
from mcp_clinic.clients.external_client import ExternalMCPClient


async def main():
    print("=== 1) STDIO ===")
    c = StdioMCPClient()
    await c.connect()
    tools = await c.list_tools()
    print("tools:", [t["name"] for t in tools])
    r = await c.call_tool("ckd_epi", {"age": 72, "sex": "female",
                                      "creatinine_mg_dl": 1.4})
    print("ckd_epi:", r["value"], r["unit"], "|", r["band"], r["risk_color"])
    meta = await c.call_tool("list_calculators", {})
    print("list_calculators count:", meta["count"])
    try:
        await c.call_tool("bmi", {"weight_kg": -5, "height_m": 1.7})
        print("ОШИБКА: валидация не сработала!")
    except ToolError as e:
        print("ToolError OK:", str(e)[:80])
    await c.aclose()
    print("stdio packets:", len(c.log.dump()))

    print("\n=== 2) SSE :8000 ===")
    s = SSEMCPClient()
    try:
        await s.connect()
    except ConnectionError as e:
        print("SSE недоступен (сервер не запущен?):", str(e)[:80])
        print("→ пропускаем, запустите sse_server.py")
        s = None
    if s:
        tools = await s.list_tools()
        print("tools:", [t["name"] for t in tools])
        # проверка санитайзера: строки вместо чисел
        r = await s.call_tool("gcs", {"eye": "3", "verbal": "4", "motor": "6"})
        print("gcs:", r["value"], "|", r["band"], "| sanitize notes:",
              s.sanitize_notes)
        # проверка границ: выход за физиологические пределы
        r = await s.call_tool("bmi", {"weight_kg": 900, "height_m": 0.2})
        print("bmi clamps:", r["value"], "| warnings:", r.get("warnings"))
        cat = await s.call_tool("list_by_category", {"category": "кардиология"})
        print("кардиология:", cat.get("count"), list(cat.get("calculators", {})))
        await s.aclose()
        print("sse packets:", len(s.log.dump()))

    print("\n=== 3) EXTERNAL pubmed-mcp ===")
    try:
        e = ExternalMCPClient()
        await e.connect()
        tools = await e.list_tools()
        print("tools (%d):" % len(tools), [t["name"] for t in tools][:8], "...")
        r = await e.call_tool("pubmed_search",
                              {"query": "warfarin aspirin interaction",
                               "max_results": 3})
        txt = str(r)[:200].replace("\n", " ")
        print("pubmed_search:", txt)
        # кэш: повторный вызов
        r2 = await e.call_tool("pubmed_search",
                               {"query": "warfarin aspirin interaction",
                                "max_results": 3})
        print("cached:", r2.get("_cached"), "| cache hits:", e.cache_hits)
        await e.aclose()
        print("external packets:", len(e.log.dump()))
    except Exception as ex:
        print("external FAIL:", type(ex).__name__, str(ex)[:150])


asyncio.run(main())
