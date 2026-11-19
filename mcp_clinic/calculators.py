"""
Клинические калькуляторы — чистые функции с Pydantic-валидацией.

Используются обоими MCP-серверами (stdio и SSE), поэтому вынесены
в общий модуль: формула описывается один раз, транспортов может быть много.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CalcError(ValueError):
    """Ошибка валидации входных данных калькулятора."""


# ---------------------------------------------------------------------------
# Метаданные методик (для мета-инструмента list_calculators)
# ---------------------------------------------------------------------------
CALCULATOR_META = {
    "bmi": {
        "name_ru": "Индекс массы тела (BMI)",
        "name_en": "Body Mass Index",
        "category": "терапия / оценка массы тела",
        "source": "WHO. Obesity: preventing and managing the global epidemic. 2000",
        "source_url": "https://www.who.int/publications/i/item/who-trs-894",
        "params": {
            "weight_kg": {"unit": "кг", "min": 2.0, "max": 500.0},
            "height_m": {"unit": "м", "min": 0.5, "max": 2.5},
        },
    },
    "ckd_epi": {
        "name_ru": "СКФ по CKD-EPI 2021",
        "name_en": "eGFR (CKD-EPI 2021, race-free)",
        "category": "нефрология",
        "source": "Inker LA et al. N Engl J Med 2021;385:1737-1749",
        "source_url": "https://pubmed.ncbi.nlm.nih.gov/34554658/",
        "params": {
            "age": {"unit": "лет", "min": 18, "max": 120},
            "sex": {"unit": "male/female", "enum": ["male", "female"]},
            "creatinine_mg_dl": {"unit": "мг/дл", "min": 0.1, "max": 20.0},
        },
    },
    "cha2ds2_vasc": {
        "name_ru": "CHA₂DS₂-VASc (инсульт при ФП)",
        "name_en": "CHA2DS2-VASc score",
        "category": "кардиология / аритмология",
        "source": "Lip GYH et al. Chest 2010;137:263-272",
        "source_url": "https://pubmed.ncbi.nlm.nih.gov/19762550/",
        "params": {
            "age": {"unit": "лет", "min": 18, "max": 120},
            "sex": {"unit": "male/female", "enum": ["male", "female"]},
            "chf": {"unit": "bool", "hint": "сердечная недостаточность"},
            "hypertension": {"unit": "bool", "hint": "артериальная гипертензия"},
            "stroke_tia": {"unit": "bool", "hint": "инсульт/ТИА в анамнезе"},
            "vascular_disease": {"unit": "bool", "hint": "ишемическая болезнь/ИМ/ПАКС"},
            "diabetes": {"unit": "bool"},
        },
    },
    "has_bled": {
        "name_ru": "HAS-BLED (риск кровотечений)",
        "name_en": "HAS-BLED score",
        "category": "кардиология / геморрагический риск",
        "source": "Pisters R et al. Chest 2010;138:1093-1100",
        "source_url": "https://pubmed.ncbi.nlm.nih.gov/20299626/",
        "params": {
            "hypertension": {"unit": "bool", "hint": "САД >160 мм рт.ст."},
            "renal_disease": {"unit": "bool"},
            "liver_disease": {"unit": "bool"},
            "stroke_history": {"unit": "bool"},
            "bleeding_history": {"unit": "bool", "hint": "кровотечения в анамнезе"},
            "age_over_65": {"unit": "bool"},
            "drugs_alcohol": {"unit": "bool", "hint": "НПВС/антиагреганты или алкоголь"},
        },
    },
    "gcs": {
        "name_ru": "Шкала комы Глазго (GCS)",
        "name_en": "Glasgow Coma Scale",
        "category": "реаниматология / неврология",
        "source": "Teasdale G, Jennett B. Lancet 1974;2:81-84",
        "source_url": "https://pubmed.ncbi.nlm.nih.gov/4136544/",
        "params": {
            "eye": {"unit": "1-4", "min": 1, "max": 4},
            "verbal": {"unit": "1-5", "min": 1, "max": 5},
            "motor": {"unit": "1-6", "min": 1, "max": 6},
        },
    },
}


def list_calculators() -> dict:
    """Мета-инструмент: каталог методик с границами и единицами."""
    return {
        "count": len(CALCULATOR_META),
        "calculators": CALCULATOR_META,
    }


# ---------------------------------------------------------------------------
# BMI
# ---------------------------------------------------------------------------
class BMIInput(BaseModel):
    weight_kg: float = Field(ge=2.0, le=500.0)
    height_m: float = Field(ge=0.5, le=2.5)


def calc_bmi(weight_kg: float, height_m: float) -> dict:
    inp = BMIInput(weight_kg=weight_kg, height_m=height_m)
    bmi = inp.weight_kg / (inp.height_m ** 2)
    if bmi < 18.5:
        band, color, advice = "Недостаточная масса тела", "yellow", \
            "Оценить питание, исключить дефицитные состояния"
    elif bmi < 25:
        band, color, advice = "Норма", "green", "Поддерживать текущий образ жизни"
    elif bmi < 30:
        band, color, advice = "Избыточная масса тела", "yellow", \
            "Рекомендованы диетотерапия и регулярная аэробная нагрузка"
    else:
        band, color, advice = "Ожирение", "red", \
            "Консультация врача, программа снижения массы тела"
    return {
        "value": round(bmi, 1),
        "unit": "кг/м²",
        "band": band,
        "risk_color": color,
        "recommendation": advice,
        "formula": "BMI = вес (кг) / рост² (м²)",
    }


# ---------------------------------------------------------------------------
# CKD-EPI 2021 (race-free), СКФ
# ---------------------------------------------------------------------------
class CKDEPIInput(BaseModel):
    age: int = Field(ge=18, le=120)
    sex: str
    creatinine_mg_dl: float = Field(ge=0.1, le=20.0)

    @field_validator("sex")
    @classmethod
    def _sex(cls, v: str) -> str:
        if v not in ("male", "female"):
            raise ValueError("sex должен быть 'male' или 'female'")
        return v


def calc_ckd_epi(age: int, sex: str, creatinine_mg_dl: float) -> dict:
    inp = CKDEPIInput(age=age, sex=sex, creatinine_mg_dl=creatinine_mg_dl)
    female = inp.sex == "female"
    k, alpha = (0.7, -0.241) if female else (0.9, -0.302)
    ratio = inp.creatinine_mg_dl / k
    factor = 142 * (ratio ** alpha if ratio < 1 else ratio ** -1.200)
    egfr = factor * (0.9938 ** inp.age)
    if female:
        egfr *= 1.012
    if egfr >= 90:
        band, color, gfr_stage = "Норма / незначительное снижение", "green", "G1–G2"
    elif egfr >= 60:
        band, color, gfr_stage = "Лёгкое снижение СКФ", "green", "G2"
    elif egfr >= 45:
        band, color, gfr_stage = "Умеренное снижение", "yellow", "G3a"
    elif egfr >= 30:
        band, color, gfr_stage = "Выраженное снижение", "yellow", "G3b"
    elif egfr >= 15:
        band, color, gfr_stage = "Тяжёлое снижение", "red", "G4"
    else:
        band, color, gfr_stage = "Терминальная стадия", "red", "G5"
    return {
        "value": round(egfr, 1),
        "unit": "мл/мин/1.73 м²",
        "band": band,
        "ckd_stage": gfr_stage,
        "risk_color": color,
        "recommendation": "Коррекция дозировок препаратов по СКФ; "
                          "нефролог при G3a и ниже" if egfr < 90
        else "Динамическое наблюдение",
        "formula": "CKD-EPI 2021: 142 × min(Scr/k,1)^α × max(Scr/k,1)^−1.200 × 0.9938^возраст"
                   + (" × 1.012 (женщины)" if female else ""),
    }


# ---------------------------------------------------------------------------
# CHA2DS2-VASc
# ---------------------------------------------------------------------------
class CHADSInput(BaseModel):
    age: int = Field(ge=18, le=120)
    sex: str
    chf: bool = False
    hypertension: bool = False
    stroke_tia: bool = False
    vascular_disease: bool = False
    diabetes: bool = False

    @field_validator("sex")
    @classmethod
    def _sex(cls, v: str) -> str:
        if v not in ("male", "female"):
            raise ValueError("sex должен быть 'male' или 'female'")
        return v


def calc_cha2ds2_vasc(age: int, sex: str, chf: bool, hypertension: bool,
                      stroke_tia: bool, vascular_disease: bool,
                      diabetes: bool) -> dict:
    inp = CHADSInput(age=age, sex=sex, chf=chf, hypertension=hypertension,
                     stroke_tia=stroke_tia, vascular_disease=vascular_disease,
                     diabetes=diabetes)
    score = 0
    components = []
    if inp.chf:
        score += 1
        components.append("ХСН (+1)")
    if inp.hypertension:
        score += 1
        components.append("АГ (+1)")
    if inp.stroke_tia:
        score += 2
        components.append("Инсульт/ТИА (+2)")
    if inp.vascular_disease:
        score += 1
        components.append("Сосудистое заболевание (+1)")
    if inp.diabetes:
        score += 1
        components.append("СД (+1)")
    if 65 <= inp.age <= 74:
        score += 1
        components.append("Возраст 65–74 (+1)")
    elif inp.age >= 75:
        score += 2
        components.append("Возраст ≥75 (+2)")
    if inp.sex == "female":
        score += 1
        components.append("Женский пол (+1)")

    if score == 0:
        band, color = "Низкий риск инсульта", "green"
    elif score == 1:
        band, color = "Умеренный риск (низкий у мужчин)", "yellow"
    else:
        band, color = "Высокий риск инсульта", "red"
    return {
        "value": score,
        "unit": "баллов",
        "band": band,
        "risk_color": color,
        "components": components,
        "recommendation": "Рассмотреть пероральную антикоагулянтную терапию "
                          "при балле ≥2 (женщины) / ≥1 (мужчины) по ESC 2024"
        if score >= 1 else "Антикоагулянты, как правило, не показаны",
        "formula": "C+HF=1, HTN=1, Age≥75=2, 65–74=1, Diabetes=1, "
                   "Stroke/TIA=2, Vascular=1, Sex (female)=1",
    }


# ---------------------------------------------------------------------------
# HAS-BLED
# ---------------------------------------------------------------------------
class HASBLEDInput(BaseModel):
    hypertension: bool = False
    renal_disease: bool = False
    liver_disease: bool = False
    stroke_history: bool = False
    bleeding_history: bool = False
    age_over_65: bool = False
    drugs_alcohol: bool = False


def calc_has_bled(hypertension: bool, renal_disease: bool, liver_disease: bool,
                  stroke_history: bool, bleeding_history: bool,
                  age_over_65: bool, drugs_alcohol: bool) -> dict:
    inp = HASBLEDInput(hypertension=hypertension, renal_disease=renal_disease,
                       liver_disease=liver_disease, stroke_history=stroke_history,
                       bleeding_history=bleeding_history,
                       age_over_65=age_over_65, drugs_alcohol=drugs_alcohol)
    labels = [
        ("hypertension", "Неконтролируемая АГ", "+1"),
        ("renal_disease", "Болезнь почек", "+1"),
        ("liver_disease", "Болезнь печени", "+1"),
        ("stroke_history", "Инсульт в анамнезе", "+1"),
        ("bleeding_history", "Кровотечения в анамнезе", "+1"),
        ("age_over_65", "Возраст >65 лет", "+1"),
        ("drugs_alcohol", "НПВС/антиагреганты/алкоголь", "+1"),
    ]
    score = 0
    components = []
    for attr, label, pts in labels:
        if getattr(inp, attr):
            score += 1
            components.append(f"{label} ({pts})")
    if score <= 1:
        band, color = "Низкий риск кровотечений", "green"
    elif score == 2:
        band, color = "Умеренный риск", "yellow"
    else:
        band, color = "Высокий риск кровотечений", "red"
    return {
        "value": score,
        "unit": "баллов",
        "band": band,
        "risk_color": color,
        "components": components,
        "recommendation": "HAS-BLED >3 — не отмена антикоагулянтов, а повод "
                          "корректировать корректируемые факторы риска",
        "formula": "1 балл за каждый фактор: HTN, почки, печень, инсульт, "
                   "кровотечения, возраст >65, лекарства/алкоголь",
    }


# ---------------------------------------------------------------------------
# GCS
# ---------------------------------------------------------------------------
class GCSInput(BaseModel):
    eye: int = Field(ge=1, le=4)
    verbal: int = Field(ge=1, le=5)
    motor: int = Field(ge=1, le=6)


def calc_gcs(eye: int, verbal: int, motor: int) -> dict:
    inp = GCSInput(eye=eye, verbal=verbal, motor=motor)
    score = inp.eye + inp.verbal + inp.motor
    if score >= 13:
        band, color = "Лёгкое нарушение сознания", "green"
    elif score >= 9:
        band, color = "Умеренное нарушение сознания", "yellow"
    else:
        band, color = "Тяжёлое нарушение сознания", "red"
    return {
        "value": score,
        "unit": "баллов (E{0} V{1} M{2})".format(inp.eye, inp.verbal, inp.motor),
        "band": band,
        "risk_color": color,
        "components": [
            f"Открытие глаз: {inp.eye}/4",
            f"Речевая реакция: {inp.verbal}/5",
            f"Двигательная реакция: {inp.motor}/6",
        ],
        "recommendation": "Оценка каждые 15–30 мин при тяжёлой ЧМТ; "
                          "GCS ≤8 — критерий интубации",
        "formula": "GCS = E(1–4) + V(1–5) + M(1–6)",
    }


CALC_FUNCS = {
    "bmi": calc_bmi,
    "ckd_epi": calc_ckd_epi,
    "cha2ds2_vasc": calc_cha2ds2_vasc,
    "has_bled": calc_has_bled,
    "gcs": calc_gcs,
}
