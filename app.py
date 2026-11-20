"""
mcp-clinic: единый веб-интерфейс трёх MCP-источников (Задание 4).

Запуск:
  python -m mcp_clinic.servers.sse_server          # в терминале 1 (порт 8000)
  streamlit run app.py                              # в терминале 2

Интерфейс: селектор источника → выбор инструмента → динамическая форма
из JSON-схемы → карточка результата с цветовой стратификацией →
инспектор JSON-RPC пакетов.
"""

from __future__ import annotations

import json
import re

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

# ------------------------------------------------------------------
# Русские подписи параметров: «Перевод (оригинал)»
# ------------------------------------------------------------------
PARAM_LABELS = {
    # общие
    "age": "Возраст, лет",
    "sex": "Пол",
    "category": "Категория",
    # антропометрия
    "weight_kg": "Вес",
    "height_m": "Рост",
    # почки
    "creatinine": "Креатинин сыворотки",
    # CHA2DS2-VASc
    "chf": "ХСН (застойная сердечная недостаточность)",
    "hypertension": "Артериальная гипертензия",
    "stroke": "Инсульт / ТИА в анамнезе",
    "vascular_disease": "Сосудистые заболевания",
    "diabetes": "Сахарный диабет",
    # HAS-BLED
    "renal_disease": "Заболевания почек",
    "liver_disease": "Заболевания печени",
    "bleeding": "Кровотечения в анамнезе",
    "labile_inr": "Нестабильное МНО",
    "drugs": "Лекарства, повышающие риск кровотечения",
    "alcohol": "Приём алкоголя",
    # Глазго
    "eye": "Открывание глаз",
    "verbal": "Речевой ответ",
    "motor": "Двигательный ответ",
    # PubMed
    "query": "Поисковый запрос",
    "pmid": "Идентификатор статьи (PMID)",
    "max_results": "Максимум результатов",
    "min_year": "Год не раньше",
    "max_year": "Год не позже",
    "sort": "Сортировка",
    "only_free_fulltext": "Только бесплатный полный текст",
    "only_with_abstract": "Только со аннотацией",
    # дополнительные (SSE + FDA-инструменты external)
    "age_over_65": "Возраст старше 65 лет",
    "author": "Автор",
    "bleeding_history": "Кровотечения в анамнезе",
    "condition": "Заболевание / состояние",
    "creatinine_mg_dl": "Креатинин, мг/дл",
    "drugs_alcohol": "Лекарства и алкоголь",
    "ids": "Идентификаторы",
    "include_mesh": "Включать рубрики MeSH",
    "input_type": "Тип ввода",
    "intervention": "Вмешательство / лечение",
    "journal": "Журнал",
    "limit": "Лимит результатов",
    "max_sections": "Максимум разделов",
    "page": "Страница результатов",
    "paper_id": "Идентификатор статьи",
    "pmids": "Список идентификаторов (PMID)",
    "relationship": "Тип взаимодействия",
    "search_mode": "Режим поиска",
    "sections_filter": "Фильтр по разделам",
    "sources": "Источники данных",
    "status": "Статус",
    "stroke_history": "Инсульт в анамнезе",
    "stroke_tia": "Инсульт / ТИА",
    "style": "Стиль изложения",
    "term": "Термин",
    "volume": "Том журнала",
    "year": "Год",
}

ENUM_LABELS = {
    "male": "мужской",
    "female": "женский",
    "relevance": "по релевантности",
    "date": "по дате (свежие первыми)",
    "pub_date": "по дате публикации",
}


def param_label(name: str) -> str:
    """«Перевод (оригинал)»; если перевода нет — оригинал как есть."""
    ru = PARAM_LABELS.get(name)
    return f"{ru} ({name})" if ru else name


st.markdown("""
<style>
  :root { --brand: #6B5B95; --ink: #3A3340; }
  .stApp { background: #FBFAF7; }
  h1, h2, h3 { color: var(--brand) !important; }
  .hero {
    background: linear-gradient(135deg, #6B5B95 0%, #8B7BB8 100%);
    border-radius: 14px; padding: 14px 22px; margin-bottom: 14px;
    color: #FFFFFF;
  }
  .hero h1 { color: #FFFFFF !important; font-size: 1.35rem; margin: 0; }
  .hero p { color: #E9E4F5; margin: 3px 0 0 0; font-size: 0.85rem; }
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
  [data-testid="stFormSubmitButton"] button,
  [data-testid="baseButton-primary"] {
    background-color: #6B5B95 !important;
    border: none !important;
    border-radius: 10px !important;
  }
</style>
<div class="hero">
  <h1>🩺 mcp-clinic</h1>
  <p>Мультиагентный сбор медицинской информации: калькуляторы (stdio, SSE) ·
  PubMed / FDA (external) · инспектор JSON-RPC</p>
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
    label = param_label(name)
    if ptype == "boolean":
        return st.checkbox(label, value=bool(default or False),
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
        val = st.slider(f"{label}{f' ({unit})' if unit else ''} · {lo:g}–{hi:g}",
                        min_value=lo, max_value=hi,
                        value=default_val,
                        step=step, help=description, key=f"{key_prefix}{name}")
        return int(val) if ptype == "integer" else val
    if "enum" in prop:
        return st.selectbox(label, options=prop["enum"],
                            format_func=lambda v: ENUM_LABELS.get(v, v),
                            help=description, key=f"{key_prefix}{name}")
    return st.text_input(label, value=str(default or ""),
                         help=description, key=f"{key_prefix}{name}")


def _render_pubmed_search(data: dict):
    """Человекочитаемая карточка результата pubmed_search (список PMID)."""
    q_applied = data.get("query_applied") or data.get("original_query") or "—"
    total = data.get("total_count", 0)
    returned = data.get("returned_count", len(data.get("pmids", [])))
    pmids = [str(p) for p in data.get("pmids", [])]
    st.success("✅ Поиск в PubMed выполнен")
    st.markdown(
        f"**Ваш запрос:** `{q_applied}`  \n"
        f"**Найдено статей всего:** {total:,}".replace(",", " ")
        + f"  \n**Показано идентификаторов (PMID):** {returned}")
    if pmids:
        st.markdown("**PMID первых статей** — скопируйте любой:")
        st.code("  ".join(pmids), language=None)
        st.info(
            "**Что дальше?** Выберите в списке инструментов выше **pubmed_fetch**, "
            "вставьте любой PMID в его поле «Идентификатор статьи» и нажмите "
            "кнопку — получите название, авторов и аннотацию статьи.")
    with st.expander("Сырой ответ сервера (JSON)", expanded=False):
        st.json(data)


def _render_pubmed_fetch(data: dict):
    """Человекочитаемая карточка результата pubmed_fetch (список статей)."""
    articles = data.get("articles", [])
    st.success(f"✅ Получено статей: {data.get('count', len(articles))}")
    for i, art in enumerate(articles, 1):
        authors = ", ".join(a.get("full", "") for a in art.get("authors", []))
        journal = art.get("journal_iso") or art.get("journal", "")
        year = art.get("pub_year", "")
        doi = art.get("doi", "")
        st.markdown(f"**{i}. {art.get('title', '—')}**")
        st.caption(f"{authors} · {journal}, {year} · DOI: {doi} · "
                   f"PMID: {art.get('pmid', '')}")
        abstract = art.get("abstract", "")
        if abstract:
            with st.expander("Аннотация", expanded=False):
                st.write(abstract)
        if i < len(articles):
            st.divider()
    with st.expander("Сырой ответ сервера (JSON)", expanded=False):
        st.json(data)


def render_result(data: dict):
    """Карточка результата: главное значение крупно + цвет + разбор + источник."""
    if not isinstance(data, dict):
        st.json(data)
        return
    color_key = data.get("risk_color")
    icon, label, hexcol = RISK_STYLE.get(color_key, ("ℹ️", "", "#6B5B95"))
    value = data.get("value")
    if value is None:
        # результат не похож на карточку риска (например, список статей
        # PubMed) — рисуем специализированную карточку или полезную нагрузку
        if isinstance(data.get("pmids"), list):
            _render_pubmed_search(data)
            return
        if isinstance(data.get("articles"), list):
            _render_pubmed_fetch(data)
            return
        st.json(data)
        return
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
    st.caption("🧮 stdio — калькуляторы в локальном процессе")
    st.caption("🌐 sse — наш сервер на порту 8000")
    st.caption("📚 external — живой поиск PubMed и FDA")
    st.divider()
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
# Выбор инструмента (списки короткие — 6–17 позиций, фильтр не нужен)
# ------------------------------------------------------------------
st.subheader("Инструменты")
st.caption(f"{client.source_name} · {len(tools)} доступно")

names = [t["name"] for t in tools]
tool_name = st.selectbox("Выберите функцию", names,
                         format_func=lambda n: next(
                             (t["description"].split(".")[0]
                              for t in tools if t["name"] == n), n))
st.session_state["active_tool"] = tool_name
tool = next(t for t in tools if t["name"] == tool_name)

# ------------------------------------------------------------------
# Динамическая форма параметров из JSON-схемы
# ------------------------------------------------------------------
schema_props = tool.get("inputSchema", {}).get("properties", {})
required = set(tool.get("inputSchema", {}).get("required", []))

st.markdown(f"#### Параметры")
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

# параметры-массивы: серверы ждут JSON-list, а текстовое поле даёт строку
# (например, pubmed_fetch.pmids — список PMID, разделитель: запятая/пробел)
for pname, prop in schema_props.items():
    if pname not in args or not isinstance(args[pname], str):
        continue
    variants = prop.get("anyOf") if isinstance(prop.get("anyOf"), list) else []
    is_array = prop.get("type") == "array" or any(
        isinstance(o, dict) and o.get("type") == "array" for o in variants)
    if is_array:
        args[pname] = [s for s in re.split(r"[,\s]+", args[pname].strip()) if s]

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
