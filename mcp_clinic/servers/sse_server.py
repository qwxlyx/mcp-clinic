"""
MCP-сервер «Клинические шкалы» — сетевой транспорт SSE на порту 8000 (Задание 2).

Запуск:  python -m mcp_clinic.servers.sse_server
Эндпоинты:
  GET  /sse                — поток событий (JSON-RPC от сервера к клиенту)
  POST /messages?session_id=... — приём JSON-RPC запросов от клиента

Здесь же реализованы:
  • валидация границ параметров с информативными предупреждениями
    (сервер НЕ падает при выходе за физиологические границы);
  • «умные» smart-defaults для пропущенных параметров.
"""

from __future__ import annotations

import logging
import os

from mcp.server.mcpserver import MCPServer as FastMCP  # FastMCP → MCPServer в mcp 2.x

from mcp_clinic.calculators import (
    calc_bmi,
    calc_cha2ds2_vasc,
    calc_ckd_epi,
    calc_gcs,
    calc_has_bled,
    list_calculators as _list_calculators,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] sse-server: %(message)s",
)
log = logging.getLogger("mcp_clinic.sse")

HOST = os.environ.get("MCP_SSE_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_SSE_PORT", "8000"))

mcp = FastMCP(
    name="clinic-scales-sse",
    instructions="Сетевой каталог клинических шкал (транспорт SSE, порт 8000).",
)


def _clamp_warn(value: float, lo: float, hi: float, name: str,
                unit: str) -> tuple[float, list[str]]:
    """Граничная валидация: поджимаем к физиологическим пределам и
    возвращаем предупреждение — сервер не падает, а объясняет."""
    warnings = []
    if value < lo:
        warnings.append(f"{name}={value} {unit} ниже границы {lo} — "
                        f"подставлено физиологически безопасное значение")
        return lo, warnings
    if value > hi:
        warnings.append(f"{name}={value} {unit} выше границы {hi} — "
                        f"подставлено физиологически безопасное значение")
        return hi, warnings
    return value, warnings


@mcp.tool()
def bmi(weight_kg: float = 70.0, height_m: float = 1.7) -> dict:
    """Индекс массы тела (WHO). Границы: вес 2–500 кг, рост 0.5–2.5 м.
    Пропущенные параметры подставляются безопасными значениями по умолчанию."""
    weight_kg, w1 = _clamp_warn(weight_kg, 2.0, 500.0, "вес", "кг")
    height_m, w2 = _clamp_warn(height_m, 0.5, 2.5, "рост", "м")
    out = calc_bmi(weight_kg=weight_kg, height_m=height_m)
    out["warnings"] = w1 + w2
    return out


@mcp.tool()
def ckd_epi(age: int = 50, sex: str = "female",
            creatinine_mg_dl: float = 0.9) -> dict:
    """СКФ по CKD-EPI 2021. Возраст 18–120, креатинин 0.1–20 мг/дл.
    Выход за границы → предупреждение + безопасное значение."""
    age_i, w1 = _clamp_warn(float(age), 18, 120, "возраст", "лет")
    crea, w2 = _clamp_warn(creatinine_mg_dl, 0.1, 20.0, "креатинин", "мг/дл")
    out = calc_ckd_epi(age=int(age_i), sex=sex, creatinine_mg_dl=crea)
    out["warnings"] = w1 + w2
    return out


@mcp.tool()
def cha2ds2_vasc(age: int = 65, sex: str = "male", chf: bool = False,
                 hypertension: bool = False, stroke_tia: bool = False,
                 vascular_disease: bool = False,
                 diabetes: bool = False) -> dict:
    """CHA₂DS₂-VASc (кардиология/аритмология)."""
    age_i, w1 = _clamp_warn(float(age), 18, 120, "возраст", "лет")
    out = calc_cha2ds2_vasc(age=int(age_i), sex=sex, chf=chf,
                            hypertension=hypertension, stroke_tia=stroke_tia,
                            vascular_disease=vascular_disease, diabetes=diabetes)
    out["warnings"] = w1
    return out


@mcp.tool()
def has_bled(hypertension: bool = False, renal_disease: bool = False,
             liver_disease: bool = False, stroke_history: bool = False,
             bleeding_history: bool = False, age_over_65: bool = False,
             drugs_alcohol: bool = False) -> dict:
    """HAS-BLED (кардиология/геморрагический риск)."""
    return calc_has_bled(hypertension=hypertension,
                         renal_disease=renal_disease,
                         liver_disease=liver_disease,
                         stroke_history=stroke_history,
                         bleeding_history=bleeding_history,
                         age_over_65=age_over_65, drugs_alcohol=drugs_alcohol)


@mcp.tool()
def gcs(eye: int = 4, verbal: int = 5, motor: int = 6) -> dict:
    """Шкала комы Глазго (реаниматология/неврология).
    Границы: E 1–4, V 1–5, M 1–6."""
    e, w1 = _clamp_warn(float(eye), 1, 4, "Eye", "")
    v, w2 = _clamp_warn(float(verbal), 1, 5, "Verbal", "")
    mo, w3 = _clamp_warn(float(motor), 1, 6, "Motor", "")
    out = calc_gcs(eye=int(e), verbal=int(v), motor=int(mo))
    out["warnings"] = w1 + w2 + w3
    return out


@mcp.tool()
def list_calculators() -> dict:
    """Каталог всех методик с границами параметров и первоисточниками."""
    return _list_calculators()


@mcp.tool()
def list_by_category(category: str) -> dict:
    """Фильтрация методик по нозологии: кардиология, нефрология,
    реаниматология, терапия."""
    aliases = {
        "кардиология": "кардиология",
        "cardiology": "кардиология",
        "нефрология": "нефрология",
        "nephrology": "нефрология",
        "реаниматология": "реаниматология",
        "неврология": "реаниматология",
        "icu": "реаниматология",
        "терапия": "терапия",
        "therapy": "терапия",
    }
    key = aliases.get(category.strip().lower())
    if not key:
        return {"error": f"Неизвестная нозология '{category}'. "
                         f"Доступны: {sorted(set(aliases.values()))}"}
    found = {k: v for k, v in _list_calculators()["calculators"].items()
             if key in v["category"]}
    return {"category": key, "count": len(found), "calculators": found}


if __name__ == "__main__":
    log.info("старт SSE-сервера на http://%s:%s/sse", HOST, PORT)
    # в mcp 2.x host/port передаются в run(), а не в конструктор
    mcp.run(transport="sse", host=HOST, port=PORT)
