"""
MCP-сервер «Клинические калькуляторы» — транспорт stdio (Задание 1).

Запуск:  python -m mcp_clinic.servers.stdio_server

Протокольный обмен идёт строго через sys.stdin / sys.stdout (JSON-RPC 2.0).
Логирование — ТОЛЬКО в sys.stderr: любой print() в stdout сломает протокол,
т.к. хост читает stdout как поток сообщений.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer as FastMCP  # FastMCP → MCPServer в mcp 2.x

from mcp_clinic.calculators import (
    CalcError,
    calc_bmi,
    calc_cha2ds2_vasc,
    calc_ckd_epi,
    calc_gcs,
    calc_has_bled,
    list_calculators as _list_calculators,
)

# ---------------------------------------------------------------------------
# Логирование строго в stderr — иначе мусор попадёт в протокольный поток
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] stdio-server: %(message)s",
)
log = logging.getLogger("mcp_clinic.stdio")


def _guard(fn, *args, **kwargs):
    """Превращаем ValueError/Pydantic-ошибки в читаемый ToolError."""
    try:
        return fn(*args, **kwargs)
    except (ValueError, CalcError) as e:
        log.warning("ToolError: %s", e)
        raise ValueError(f"Ошибка параметров: {e}") from e


mcp = FastMCP(
    name="clinic-calc-stdio",
    instructions="Локальные клинические калькуляторы (транспорт stdio). "
                 "Сначала вызовите list_calculators для каталога методик.",
)


@mcp.tool()
def bmi(weight_kg: float, height_m: float) -> dict:
    """Индекс массы тела (WHO). weight_kg: 2–500 кг, height_m: 0.5–2.5 м."""
    log.info("tool call: bmi(%s, %s)", weight_kg, height_m)
    return _guard(calc_bmi, weight_kg=weight_kg, height_m=height_m)


@mcp.tool()
def ckd_epi(age: int, sex: str, creatinine_mg_dl: float) -> dict:
    """СКФ по CKD-EPI 2021 (race-free). age 18–120, sex: male/female,
    creatinine_mg_dl 0.1–20."""
    log.info("tool call: ckd_epi(%s, %s, %s)", age, sex, creatinine_mg_dl)
    return _guard(calc_ckd_epi, age=age, sex=sex,
                  creatinine_mg_dl=creatinine_mg_dl)


@mcp.tool()
def cha2ds2_vasc(age: int, sex: str, chf: bool = False,
                 hypertension: bool = False, stroke_tia: bool = False,
                 vascular_disease: bool = False,
                 diabetes: bool = False) -> dict:
    """CHA₂DS₂-VASc — риск инсульта при фибрилляции предсердий.
    Факторы риска — флажки True/False."""
    log.info("tool call: cha2ds2_vasc(age=%s)", age)
    return _guard(calc_cha2ds2_vasc, age=age, sex=sex, chf=chf,
                  hypertension=hypertension, stroke_tia=stroke_tia,
                  vascular_disease=vascular_disease, diabetes=diabetes)


@mcp.tool()
def has_bled(hypertension: bool = False, renal_disease: bool = False,
             liver_disease: bool = False, stroke_history: bool = False,
             bleeding_history: bool = False, age_over_65: bool = False,
             drugs_alcohol: bool = False) -> dict:
    """HAS-BLED — риск больших кровотечений на антикоагулянтах.
    1 балл за каждый фактор."""
    log.info("tool call: has_bled()")
    return _guard(calc_has_bled, hypertension=hypertension,
                  renal_disease=renal_disease, liver_disease=liver_disease,
                  stroke_history=stroke_history,
                  bleeding_history=bleeding_history,
                  age_over_65=age_over_65, drugs_alcohol=drugs_alcohol)


@mcp.tool()
def gcs(eye: int, verbal: int, motor: int) -> dict:
    """Шкала комы Глазго. eye 1–4, verbal 1–5, motor 1–6."""
    log.info("tool call: gcs(%s, %s, %s)", eye, verbal, motor)
    return _guard(calc_gcs, eye=eye, verbal=verbal, motor=motor)


@mcp.tool()
def list_calculators() -> dict:
    """Каталог методик: описание, границы параметров, единицы, первоисточник."""
    log.info("tool call: list_calculators()")
    return _list_calculators()


if __name__ == "__main__":
    log.info("старт stdio-сервера, PID=%s", __import__("os").getpid())
    mcp.run(transport="stdio")  # только stdin/stdout, логи уже в stderr
