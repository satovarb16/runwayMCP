from enum import Enum

from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process as fuzz_process

from tools._utils import normalize_company
from tools.uscis_cache import get_employer_index


class Verdict(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class VisaResult(BaseModel):
    company: str
    total_filings: int = Field(ge=0)
    approval_rate: float = Field(ge=0, le=1)
    verdict: Verdict
    source: str = "USCIS H-1B Employer Hub FY2024"


_normalize = normalize_company


def compute_verdict(total_filings: int, approval_rate: float) -> Verdict:
    """Return GREEN / YELLOW / RED based on filing volume and approval rate.

    GREEN  : total_filings >= 10 AND approval_rate >= 0.90
    YELLOW : total_filings >= 1  AND approval_rate >= 0.70
    RED    : all other cases
    """
    if total_filings >= 5 and approval_rate >= 0.80:
        return Verdict.GREEN
    if total_filings >= 1 and approval_rate >= 0.50:
        return Verdict.YELLOW
    return Verdict.RED


def check_visa_sponsorship(company: str) -> VisaResult:
    """Check H-1B visa sponsorship history for a company via USCIS Employer Hub.

    Pass the full employer name exactly as it appears in the job posting
    (e.g. 'Google LLC', 'Amazon Web Services', 'Microsoft Corporation').
    Abbreviated names like 'Meta' or 'Amazon' may match subsidiaries instead
    of the primary entity.
    """
    try:
        index = get_employer_index()
    except Exception:
        return VisaResult(
            company=company,
            total_filings=0,
            approval_rate=0.0,
            verdict=Verdict.RED,
            source="USCIS unavailable",
        )

    if not index:
        return VisaResult(
            company=company,
            total_filings=0,
            approval_rate=0.0,
            verdict=Verdict.RED,
            source="USCIS unavailable",
        )

    norm = _normalize(company)
    match = fuzz_process.extractOne(norm, index.keys(), scorer=fuzz.token_set_ratio, score_cutoff=85)
    if match is None:
        return VisaResult(
            company=company,
            total_filings=0,
            approval_rate=0.0,
            verdict=Verdict.RED,
            source="USCIS H-1B Employer Hub FY2024",
        )

    data = index[match[0]]
    total = data["approvals"] + data["denials"]
    rate = data["approvals"] / total if total > 0 else 0.0
    return VisaResult(
        company=company,
        total_filings=total,
        approval_rate=round(rate, 4),
        verdict=compute_verdict(total, rate),
        source="USCIS H-1B Employer Hub FY2024",
    )
