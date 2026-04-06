"""
latex_fixer.py — авто-коррекция OCR-артефактов → LaTeX-синтаксис.

Все паттерны компилируются один раз при импорте модуля (не на каждый вызов).
Вызывается из blocks_to_markdown() при генерации export.md.

Примечание: функция не пропускает уже существующие $...$ блоки —
предполагается однократный вызов на сырой OCR-вывод.
"""
import re

# ─── Unicode superscript/subscript → LaTeX ────────────────────────────────────
_UNICODE_SUP = str.maketrans(
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ",
    "0123456789+-n"
)
_UNICODE_SUB = str.maketrans(
    "₀₁₂₃₄₅₆₇₈₉₊₋",
    "0123456789+-"
)


def _fix_unicode(text: str) -> str:
    """Заменяет уцелевшие unicode sup/sub символы → LaTeX: m² → m$^{2}$"""
    # Superscripts: символ(ы) после буквы/цифры
    text = re.sub(
        r'([A-Za-z0-9])([\u2070-\u2079\u00B2\u00B3\u00B9]+)',
        lambda m: m.group(1) + "$^{" + m.group(2).translate(_UNICODE_SUP) + "}$",
        text
    )
    # Subscripts
    text = re.sub(
        r'([A-Za-z0-9])([\u2080-\u2089]+)',
        lambda m: m.group(1) + "$_{" + m.group(2).translate(_UNICODE_SUB) + "}$",
        text
    )
    return text


# ─── Все паттерны компилируются ОДИН РАЗ при импорте ─────────────────────────
_PATTERNS: list[tuple[re.Pattern, str]] = [

    # Нефтегазовые единицы объёма — наиболее частые OCR-ошибки
    # Порядок важен: сначала составные (10^3 ft^3), потом одиночные единицы
    (re.compile(r'\b10\^?3\s*ft\^?3\b',  re.I), r'$10^3\\,\\text{ft}^3$'),
    (re.compile(r'\b10\^?6\s*ft\^?3\b',  re.I), r'$10^6\\,\\text{ft}^3$'),
    (re.compile(r'\b10\^?3\s*m\^?3\b',   re.I), r'$10^3\\,\\text{m}^3$'),
    (re.compile(r'\b10\^?6\s*m\^?3\b',   re.I), r'$10^6\\,\\text{m}^3$'),
    (re.compile(r'\b10\^?3\s*bbl\b',     re.I), r'$10^3\\,\\text{bbl}$'),
    (re.compile(r'\b10\^?6\s*bbl\b',     re.I), r'$10^6\\,\\text{bbl}$'),
    (re.compile(r'\b10\^?9\s*bbl\b',     re.I), r'$10^9\\,\\text{bbl}$'),

    # Аббревиатуры единиц (отдельные слова)
    (re.compile(r'\bMMscf\b'),  r'$\\text{MMscf}$'),
    (re.compile(r'\bMscf\b'),   r'$\\text{Mscf}$'),
    (re.compile(r'\bMMbbl\b'),  r'$\\text{MMbbl}$'),
    (re.compile(r'\bMbbl\b'),   r'$\\text{Mbbl}$'),
    (re.compile(r'\bstb\b'),    r'$\\text{stb}$'),
    (re.compile(r'\bMstb\b'),   r'$\\text{Mstb}$'),
    (re.compile(r'\bMMstb\b'),  r'$\\text{MMstb}$'),

    # Химические формулы
    (re.compile(r'\bCO2\b'),   r'CO$_{2}$'),
    (re.compile(r'\bH2S\b'),   r'H$_{2}$S'),
    (re.compile(r'\bCH4\b'),   r'CH$_{4}$'),
    (re.compile(r'\bC2H6\b'),  r'C$_{2}$H$_{6}$'),
    (re.compile(r'\bSO2\b'),   r'SO$_{2}$'),
    (re.compile(r'\bNO2\b'),   r'NO$_{2}$'),

    # Общие степени после единиц измерения: m2, km2, ft3 и т.д.
    # Срабатывает только при наличии word boundary (т.е. не внутри числа 1000m2)
    (re.compile(r'\b(m|km|ft|mi|ha|cm)\^?2\b'), r'$\1^2$'),
    (re.compile(r'\b(m|km|ft|mi|cm)\^?3\b'),    r'$\1^3$'),
]


def fix_latex(text: str) -> str:
    """
    Основная функция авто-коррекции OCR-артефактов → LaTeX.
    Предназначена для однократного вызова на сырой OCR-вывод.
    """
    if not text:
        return text

    # 1. Unicode символы (простой translate, не regex)
    text = _fix_unicode(text)

    # 2. Скомпилированные regex-паттерны
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)

    return text
