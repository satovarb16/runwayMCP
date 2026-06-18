import pytest
from unittest.mock import patch
from pydantic import ValidationError

from tools.visa import VisaResult, Verdict, check_visa_sponsorship
from tools.uscis_cache import CACHE_URL


@pytest.mark.contract
def test_valid_company_accepted():
    """Valid company string must not raise any exception."""
    result = check_visa_sponsorship(company="Acme Corp")
    assert result is not None


@pytest.mark.contract
def test_missing_company_raises():
    """Instantiating the input model without company must raise ValidationError."""
    with pytest.raises(ValidationError):
        VisaResult.model_validate({})


@pytest.mark.contract
def test_output_is_pydantic_model():
    """Handler must return a VisaResult instance, not a plain dict."""
    result = check_visa_sponsorship(company="Acme Corp")
    assert isinstance(result, VisaResult)


@pytest.mark.contract
def test_verdict_is_valid_enum():
    """verdict field must be a valid Verdict enum member (green | yellow | red)."""
    result = check_visa_sponsorship(company="Acme Corp")
    assert isinstance(result.verdict, Verdict)
    assert result.verdict in (Verdict.GREEN, Verdict.YELLOW, Verdict.RED)


@pytest.mark.contract
def test_approval_rate_bounds():
    """approval_rate must be a float in the closed interval [0.0, 1.0]."""
    result = check_visa_sponsorship(company="Acme Corp")
    assert isinstance(result.approval_rate, float)
    assert 0.0 <= result.approval_rate <= 1.0


@pytest.mark.contract
def test_filing_count_non_negative():
    """total_filings must be a non-negative integer."""
    result = check_visa_sponsorship(company="Acme Corp")
    assert isinstance(result.total_filings, int)
    assert result.total_filings >= 0


# ---------------------------------------------------------------------------
# T-03 — compute_verdict threshold tests
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize(
    "filings,rate,expected",
    [
        (15, 0.95, Verdict.GREEN),  # active sponsor
        (5, 0.85, Verdict.GREEN),  # exactly at GREEN threshold
        (5, 0.75, Verdict.YELLOW),  # filings ok but rate below 0.80
        (1, 0.60, Verdict.YELLOW),  # has history, rate ok
        (0, 0.0, Verdict.RED),  # no filings
        (3, 0.40, Verdict.RED),  # low rate below 0.50
    ],
)
def test_compute_verdict(filings, rate, expected):
    """compute_verdict must map (filings, rate) to the correct Verdict."""
    from tools.visa import compute_verdict

    assert compute_verdict(filings, rate) == expected


# ---------------------------------------------------------------------------
# T-04 — _normalize tests
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_normalize_strips_suffix():
    """_normalize must strip legal suffixes like 'LLC'."""
    from tools.visa import _normalize

    assert _normalize("Google LLC") == "google"


@pytest.mark.contract
def test_normalize_idempotent():
    """_normalize must be idempotent — applying it twice gives the same result."""
    from tools.visa import _normalize

    assert _normalize("google") == "google"


# ---------------------------------------------------------------------------
# T-09 — check_visa_sponsorship integration tests (mocked index)
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_known_company_match():
    """A company present in the index must return a GREEN VisaResult."""
    mock_index = {"google": {"approvals": 50, "denials": 0}}
    with patch("tools.visa.get_employer_index", return_value=mock_index):
        result = check_visa_sponsorship("Google LLC")
    assert isinstance(result, VisaResult)
    assert result.total_filings > 0
    assert result.verdict == Verdict.GREEN
    assert "USCIS H-1B" in result.source


@pytest.mark.contract
def test_unknown_company_no_match():
    """An unknown company must return RED with zero filings."""
    with patch("tools.visa.get_employer_index", return_value={}):
        result = check_visa_sponsorship("Acme Corp")
    assert isinstance(result, VisaResult)
    assert result.total_filings == 0
    assert result.approval_rate == 0.0
    assert result.verdict == Verdict.RED


@pytest.mark.contract
def test_cache_unavailable():
    """When get_employer_index raises, result must be RED with no exception."""
    with patch("tools.visa.get_employer_index", side_effect=Exception("network error")):
        result = check_visa_sponsorship("Google")
    assert isinstance(result, VisaResult)
    assert result.verdict == Verdict.RED
    assert "unavailable" in result.source


# ---------------------------------------------------------------------------
# T-10 — USCIS FY alignment: CACHE_URL year matches VisaResult.source label
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_cache_url_points_to_fy2024():
    """CACHE_URL must reference the FY2024 USCIS export file."""
    assert "h1b_datahubexport-2024.csv" in CACHE_URL


@pytest.mark.contract
def test_visa_result_source_default_matches_fy2024():
    """VisaResult.source default must equal 'USCIS H-1B Employer Hub FY2024'."""
    assert VisaResult.model_fields["source"].default == "USCIS H-1B Employer Hub FY2024"


@pytest.mark.contract
def test_source_label_matches_cache_url_year():
    """The FY year in CACHE_URL must match the FY year in VisaResult.source default."""
    import re
    url_match = re.search(r"datahubexport-(\d{4})\.csv", CACHE_URL)
    source_match = re.search(r"FY(\d{4})", VisaResult.model_fields["source"].default)
    assert url_match is not None, "CACHE_URL must contain a 4-digit year"
    assert source_match is not None, "VisaResult.source default must contain FY<year>"
    assert url_match.group(1) == source_match.group(1), (
        f"CACHE_URL year {url_match.group(1)} must match source label year {source_match.group(1)}"
    )
