import csv
import requests
from pathlib import Path

from tools._utils import normalize_company

# FY2024 is the latest with a known stable direct URL.
# For newer data, drop the file at CACHE_PATH manually (downloaded from
# https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
# via Crosstab View → Download CSV).
# NOTE: Changing CACHE_URL does NOT auto-invalidate an existing on-disk cache.
# If an older FY CSV is cached at CACHE_PATH (~/.cache/runway-mcp/uscis_h1b.csv),
# delete it manually before running so the updated FY dataset is downloaded.
CACHE_URL = (
    "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-2024.csv"
)
CACHE_PATH = Path.home() / ".cache" / "runway-mcp" / "uscis_h1b.csv"
_INDEX: dict | None = None

# Column name variants across FY formats
_EMPLOYER_COLS = ["Employer (Petitioner) Name", "Employer"]
_APPROVAL_COLS = ["New Employment Approval", "Initial Approval"]
_DENIAL_COLS = ["New Employment Denial", "Initial Denial"]
MIN_FILINGS = 2  # companies with a single filing are excluded (likely one-off, not systematic sponsors)


def _detect_encoding(path: Path) -> str:
    with path.open("rb") as fh:
        raw = fh.read(2)
    return "utf-16" if raw in (b"\xff\xfe", b"\xfe\xff") else "utf-8"


def _detect_delimiter(path: Path, encoding: str) -> str:
    with path.open(encoding=encoding) as fh:
        first_line = fh.readline()
    return "\t" if "\t" in first_line else ","


def _get_col(row: dict, candidates: list[str]) -> str:
    for col in candidates:
        if col in row:
            return row[col]
    return ""


def get_employer_index() -> dict:
    """Return a normalized-name → {approvals, denials} dict.

    Builds the index once from the USCIS H-1B CSV (downloading if needed),
    then caches it as a module-level singleton. Always returns a dict — never raises.
    Handles both old (FY2023, comma/UTF-8) and new (FY2026, tab/UTF-16) formats.
    """
    global _INDEX

    if _INDEX is not None:
        return _INDEX

    if not CACHE_PATH.exists():
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            response = requests.get(CACHE_URL)
            response.raise_for_status()
            CACHE_PATH.write_bytes(response.content)
        except requests.RequestException:
            return {}

    index: dict = {}
    try:
        encoding = _detect_encoding(CACHE_PATH)
        delimiter = _detect_delimiter(CACHE_PATH, encoding)
        with CACHE_PATH.open(encoding=encoding, newline="") as fh:
            reader = csv.DictReader(fh, delimiter=delimiter)
            for row in reader:
                employer = _get_col(row, _EMPLOYER_COLS)
                if not employer:
                    continue
                try:
                    approvals = int(_get_col(row, _APPROVAL_COLS) or 0)
                    denials = int(_get_col(row, _DENIAL_COLS) or 0)
                except ValueError:
                    continue
                key = normalize_company(employer)
                if not key:
                    continue
                if key in index:
                    index[key]["approvals"] += approvals
                    index[key]["denials"] += denials
                else:
                    index[key] = {"approvals": approvals, "denials": denials}
    except Exception:
        return {}

    _INDEX = {
        k: v for k, v in index.items() if v["approvals"] + v["denials"] >= MIN_FILINGS
    }
    return _INDEX
