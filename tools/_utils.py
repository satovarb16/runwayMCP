import re


def normalize_company(name: str) -> str:
    """Lowercase, strip legal suffixes and punctuation, collapse whitespace."""
    name = name.lower().strip()
    suffixes = r"\b(llc|inc|corp|ltd|co|lp|na|plc)\b"
    name = re.sub(suffixes, "", name)
    name = re.sub(r"[^a-z0-9 ]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name
