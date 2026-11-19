"""
mcp-clinic: единый веб-интерфейс трёх MCP-источников (Задание 4).

Запуск:
  python -m mcp_clinic.servers.sse_server          # в терминале 1 (порт 8000)
  streamlit run app.py                              # в терминале 2

Интерфейс: селектор источника → поиск инструмента → динамическая форма
из JSON-схемы → карточка результата с цветовой стратификацией →
инспектор JSON-RPC пакетов.
"""

from __future__ import annotations

import json

import streamlit as st

from mcp_clinic.calculators import CALCULATOR_META as CALC_META
from mcp_clinic.clients.base import BaseMCPClient, run_async
from mcp_clinic.clients.external_client import ExternalMCPClient
from mcp_clinic.clients.sse_client import SSEMCPClient
from mcp_clinic.clients.stdio_client import StdioMCPClient, ToolError

st.set_page_config(page_title="mcp-clinic", page_icon="🩺", layout="wide")

RISK_STYLE = {
    "red": ("🔴", "Высокий риск", "#A94438"),
    "yellow": ("🟡", "Внимание", "#A98F2F"),
    "green": ("🟢", "Норма / низкий риск", "#71855B"),
}

st.markdown("""
<style>
  :root { --brand: #684765; --cream: #F7F3E8; --ink: #3A3340; }
  .stApp { background: var(--cream); }
  h1, h2, h3 { color: var(--brand) !important; }
  .hero {
    background: linear-gradient(135deg, #684765 0%, #8A6B87 100%);
    border-radius: 14px; padding: 20px 26px; margin-bottom: 16px; color: #F7F3E8;
  }
  .hero h1 { color: #F7F3E8 !important; font-size: 1.6rem; margin: 0; }
  .hero p { color: #E5D9E4; margin: 5px 0 0 0; font-size: 0.9rem; }
  .result-card {
    background: #fff; border-radius: 12px; padding: 18px 22px;
    box-shadow: 0 1px 3px rgba(60,45,60,0.12); margin-top: 10px;
  }
  .big-value { font-size: 2.6rem; font-weight: 800; }
  .rpc-block {
    background: #2B2730; color: #D9E2EC; border-radius: 10px;
    padding: 12px 16px; font-family: Consolas, monospace; font-size: 0.78rem;
    margin: 6px 0; white-space: pre-wrap; word-break: break-all;
  }
</style>
<div class="hero">
  <h1>🩺 mcp-clinic</h1>
  <p>Три паттерна интеграции по протоколу MCP: stdio · SSE :8000 ·
  внешний medical-mcp (PubMed/FDA) · инспектор JSON-RPC</p>
</div>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------
# Фабрика клиентов + кэш сессий Streamlit
# ------------------------------------------------------------------
def make_client(source: str) -> BaseMCPClient:
    if source.startswith("stdio"):
        return StdioMCPClient()
    if source.startswith("sse"):
        return SSEMCPClient()
    return ExternalMCPClient()


@st.cache_resource(show_spinner=False)
def connected_client(source: str):
    """Сессия живёт между rerun'ами Streamlit; пересоздаётся при смене источника."""
    client = make_client(source)
    try:
        run_async(client.connect(), timeout=30.0)
        client.tools_cache = run_async(client.list_tools(), timeout=30.0)
        return client
    except Exception as e:  # noqa: BLE001 — показываем ошибку, не падаем
        client.error = str(e)
        return client


def render_field(name: str, prop: dict, key_prefix: str):
    """Динамическая форма: тип поля из JSON-схемы инструмента."""
    ptype = prop.get("type", "string")
    description = prop.get("description", "")
    default = prop.get("default")
    if ptype == "boolean":
        return st.checkbox(name, value=bool(default or False),
                           help=description, key=f"{key_prefix}{name}")
    if ptype in ("number", "integer"):
        lo, hi = 0.0, 100.0
        if isinstance(prop.get("minimum"), (int, float)):
            lo = float(prop["minimum"])
        if isinstance(prop.get("maximum"), (int, float)):
            hi = float(prop["maximum"])
        # границы из каталога методик (физиологические пределы)
        meta_fields = CALC_META.get(
            st.session_state.get("active_tool", ""), {}).get("params", {})
        fm = meta_fields.get(name) or {}
        unit = fm.get("unit", "")
        if isinstance(fm.get("min"), (int, float)):
            lo = float(fm["min"])
        if isinstance(fm.get("max"), (int, float)):
            hi = float(fm["max"])
        step = 1 if ptype == "integer" else (hi - lo) / 200 or 0.1
        if ptype == "integer":
            lo, hi = int(lo), int(hi)
        default_val = float(default) if default is not None else (lo + hi) / 2
        if ptype == "integer":
            default_val = int(default_val)
        val = st.slider(f"{name}{f' ({unit})' if unit else ''} ({lo:g}–{hi:g})",
                        min_value=lo, max_value=hi,
                        value=default_val,
                        step=step, help=description, key=f"{key_prefix}{name}")
        return int(val) if ptype == "integer" else val
    if "enum" in prop:
        return st.selectbox(name, options=prop["enum"],
                            help=description, key=f"{key_prefix}{name}")
    return st.text_input(name, value=str(default or ""),
                         help=description, key=f"{key_prefix}{name}")


def render_result(data: dict):
    """Карточка результата: главное значение крупно + цвет + разбор + источник."""
    if not isinstance(data, dict):
        st.json(data)
        return
    color_key = data.get("risk_color")
    icon, label, hexcol = RISK_STYLE.get(color_key, ("ℹ️", "", "#684765"))
    value = data.get("value")
    top = st.container()
    with top:
        c1, c2 = st.columns([1, 2])
        with c1:
            if value is not None:
                st.markdown(
                    f'<div class="result-card" style="text-align:center">'
                    f'<div class="big-value" style="color:{hexcol}">{value}'
                    f'<span style="font-size:1rem;color:#8C7F8A"> '
                    f'{data.get("unit", "")}</span></div>'
                    f'<div style="color:{hexcol};font-weight:700">{icon} '
                    f'{data.get("band", label)}</div></div>',
                    unsafe_allow_html=True)
        with c2:
            lines = []
            if data.get("formula"):
                lines.append(f"**Формула:** {data['formula']}")
            if data.get("components"):
                lines.append("**Разбор:** " + "; ".join(data["components"]))
            if data.get("warnings"):
                lines.append("⚠️ " + " / ".join(data["warnings"]))
            if data.get("recommendation"):
                lines.append(f"**Рекомендация:** {data['recommendation']}")
            if data.get("ckd_stage"):
                lines.append(f"**Стадия ХБП:** {data['ckd_stage']}")
            for ln in lines:
                st.markdown(ln)
    meta = CALC_META.get(st.session_state.get("active_tool", ""), {})
    if meta.get("source"):
        st.caption(f"Методика: {meta['source']} · "
                   f"[первоисточник]({meta.get('source_url', '')})")


# ------------------------------------------------------------------
# Sidebar: селектор источника
# ------------------------------------------------------------------
with st.sidebar:
    st.header("Источник MCP")
    source = st.radio(
        "MCP Source Selector",
        ["stdio — локальный процесс",
         "sse — локальный сетевой :8000",
         "external — medical-mcp (PubMed/FDA)"],
        index=0)
    st.divider()
    st.caption("stdio: подпроцесс с калькуляторами · sse: демон на порту 8000 · "
               "external: медицинские базы из каталога Glama.ai")
    st.caption("Учебный проект. Не медицинская рекомендация.")

client = connected_client(source)

if getattr(client, "error", None):
    st.error(f"Не удалось подключиться к источнику «{client.source_name}»:\n\n"
             f"{client.error}")
    st.info("Для SSE-источника сначала запустите: "
            "`python -m mcp_clinic.servers.sse_server`")
    st.stop()

tools: list[dict] = client.tools_cache

# ------------------------------------------------------------------
# Поиск и фильтрация инструментов
# ------------------------------------------------------------------
st.subheader(f"🔌 {client.source_name} — {len(tools)} инструментов")
query = st.text_input(
    "Поиск по названию и описанию (рус / лат)",
    placeholder="например: скф, bmi, глазго, pubmed…")
q = query.strip().lower()

# бытовые русские слова → медицинские термины из описаний
SEARCH_SYNONYMS = {
    "почки": "нефрология ckd скф креатинин",
    "почка": "нефрология ckd скф",
    "сердце": "кардиология инсульт кровотечения",
    "сердца": "кардиология инсульт",
    "инсульт": "stroke cha2ds2 vasc",
    "кровотеч": "has_bled bleeding",
    "кома": "глазго gcs",
    "сознание": "глазго gcs",
    "вес": "bmi масса тела",
    "масс": "bmi вес ожирение",
    "ожирени": "bmi вес масса тела",
    "аритми": "cha2ds2 фибрилляция предсердий",
    "хбп": "ckd скф креатинин нефрология",
}
for key, syn in SEARCH_SYNONYMS.items():
    if key in q:
        q = f"{q} {syn}"


def _search_text(t: dict) -> str:
    """Имя + описание + русское название и категория из каталога методик."""
    meta = CALC_META.get(t["name"], {})
    return " ".join([t["name"], t.get("description", ""),
                     meta.get("name_ru", ""), meta.get("category", "")]).lower()


tokens = q.split()
filtered = [t for t in tools
            if not tokens or any(tok in _search_text(t) for tok in tokens)]
if not filtered:
    st.warning("Ничего не найдено — попробуйте другой запрос.")
    st.stop()

names = [t["name"] for t in filtered]
tool_name = st.selectbox("Инструмент", names,
                         format_func=lambda n: next(
                             (t["description"].split(".")[0]
                              for t in filtered if t["name"] == n), n))
st.session_state["active_tool"] = tool_name
tool = next(t for t in filtered if t["name"] == tool_name)

# ------------------------------------------------------------------
# Динамическая форма параметров из JSON-схемы
# ------------------------------------------------------------------
schema_props = tool.get("inputSchema", {}).get("properties", {})
required = set(tool.get("inputSchema", {}).get("required", []))

st.markdown(f"#### ⚙️ Параметры: `{tool_name}`")
if tool.get("description"):
    st.caption(tool["description"])
args = {}
with st.form("tool_form"):
    for pname, prop in schema_props.items():
        args[pname] = render_field(pname, prop, key_prefix="f_")
    submitted = st.form_submit_button("Выполнить расчёт / запрос",
                                      type="primary",
                                      use_container_width=True)

# пустые необязательные поля не отправляем: серверы валидируют '' как ошибку
args = {k: v for k, v in args.items() if v not in ("", None)}

# ------------------------------------------------------------------
# Вызов + карточка результата + инспектор JSON-RPC
# ------------------------------------------------------------------
if submitted:
    with st.spinner(f"Вызов {tool_name} по протоколу MCP…"):
        try:
            data = run_async(client.call_tool(tool_name, args), timeout=60.0)
        except ToolError as e:
            st.error(f"**ToolError:** {e}")
            data = None
        except Exception as e:  # noqa: BLE001 — приложение не падает никогда
            st.error(f"Ошибка вызова: {type(e).__name__}: {e}")
            data = None

    if data is not None:
        if getattr(client, "sanitize_notes", None):
            for n in client.sanitize_notes:
                st.info(f"Sanitizer: {n}")
        if data.get("_cached"):
            st.info("📦 Результат из кэша (внешний запрос не выполнялся — "
                    "бережём rate limits)")
        render_result(data)

st.divider()
with st.expander("🔍 MCP Packet Inspector — сырые JSON-RPC пакеты",
                 expanded=False):
    entries = client.log.dump()
    if not entries:
        st.caption("Пакетов пока нет — выполните вызов инструмента.")
    for e in entries:
        st.markdown(f"**{e['direction']}**")
        st.markdown("```json\n" + json.dumps(e["jsonrpc"], ensure_ascii=False,
                                             indent=2) + "\n```")
    if isinstance(client, ExternalMCPClient):
        st.caption(f"Кэш: {len(client.cache._store)} записей · "
                   f"попаданий в кэш: {client.cache_hits}")
