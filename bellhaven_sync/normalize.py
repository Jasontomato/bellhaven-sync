"""Normalization helpers.

The goal is to compare *meaning*, not formatting: "1250 Northwest Franklin St"
and "1250 NW Franklin Street" are the same address; "Wilmington Pk" is
"Wilmington Pike".
"""
import re
from difflib import SequenceMatcher

_SUFFIX = {
    "street": "st", "st": "st", "str": "st",
    "avenue": "ave", "ave": "ave", "av": "ave",
    "road": "rd", "rd": "rd",
    "drive": "dr", "dr": "dr",
    "boulevard": "blvd", "blvd": "blvd",
    "lane": "ln", "ln": "ln",
    "court": "ct", "ct": "ct",
    "pike": "pike", "pk": "pike",
    "place": "pl", "pl": "pl",
    "parkway": "pkwy", "pkwy": "pkwy",
    "circle": "cir", "cir": "cir",
    "highway": "hwy", "hwy": "hwy",
    "terrace": "ter", "ter": "ter",
    "square": "sq", "sq": "sq",
    "trail": "trl", "trl": "trl",
}
_DIR = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
}
_UNIT = re.compile(r"(?:\b(?:suite|ste|unit|apt)\b|#)\s*\w+")
_PO_BOX = re.compile(r"^\s*p\.?\s*o\.?\s*box\b", re.I)


def norm_street(s: str) -> str:
    s = (s or "").lower().replace(".", " ")
    s = _UNIT.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    toks = [_DIR.get(t, t) for t in s.split()]
    toks = [_SUFFIX.get(t, t) for t in toks]
    return " ".join(toks)


def street_number(s: str) -> str:
    m = re.match(r"^\s*(\d+[a-z]?)\b", (s or "").lower())
    return m.group(1) if m else ""


def is_po_box(s: str) -> bool:
    return bool(_PO_BOX.match(s or ""))


def zip5(z) -> str:
    m = re.match(r"\s*(\d{5})", str(z or ""))
    return m.group(1) if m else ""


def phone_digits(p: str) -> str:
    d = re.sub(r"\D", "", p or "")
    return d[-10:] if len(d) >= 10 else ""


def squash(s: str) -> str:
    """Whitespace-normalized string, for exact display comparisons."""
    return " ".join((s or "").split())


def norm_name(s: str) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\brehab\b", "rehabilitation", s)
    s = re.sub(r"\bcentre\b", "center", s)
    s = re.sub(r"\bhealth\s+care\b", "healthcare", s)
    return " ".join(t for t in s.split() if t != "the")


def ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def token_set_ratio(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    inter = " ".join(sorted(ta & tb))
    da = " ".join(sorted(ta - tb))
    db = " ".join(sorted(tb - ta))
    s2 = f"{inter} {da}".strip()
    s3 = f"{inter} {db}".strip()
    cands = [ratio(s2, s3)]
    if inter:
        cands += [ratio(inter, s2), ratio(inter, s3)]
    return max(cands)


def name_similarity(a: str, b: str) -> float:
    """Blend of plain and token-set similarity on normalized names (0..1).

    Token-set alone scores "Bellhaven of Carlisle" vs "Bellhaven of New Carlisle"
    as identical, so it is averaged with a plain sequence ratio.
    """
    na, nb = norm_name(a), norm_name(b)
    return round((ratio(na, nb) + token_set_ratio(na, nb)) / 2, 3)


# Website care offerings -> CRM care_type picklist values.
CARE_MAP = {
    "short-term rehabilitation & nursing": "Skilled Nursing",
    "short-term rehabilitation and nursing": "Skilled Nursing",
    "skilled nursing": "Skilled Nursing",
    "memory support": "Memory Care",
    "memory care": "Memory Care",
    "assisted living": "Assisted Living",
    "independent living": "Independent Living",
}


def to_crm_care_types(offerings) -> list:
    out = []
    for o in offerings or []:
        v = CARE_MAP.get(squash(o).lower())
        if v and v not in out:
            out.append(v)
    return out


def money(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0
