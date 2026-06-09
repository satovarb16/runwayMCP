import pytest
import responses as responses_lib
from pydantic import ValidationError

from tools.jobs import (
    JobPostingResult,
    fetch_job_posting,
    _normalize_country,
    _country_from_freetext,
    _slug_to_company,
    _route,
    _fetch_ashby,
    _fetch_greenhouse,
    _match_host,
    _extract_gh_token_from_html,
    _fetch_greenhouse_custom_domain,
)


# ---------------------------------------------------------------------------
# Existing contract tests (must remain green)
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_valid_url_accepted():
    """Valid URL string must not raise any exception."""
    result = fetch_job_posting(url="https://example.com/jobs/123")
    assert result is not None


@pytest.mark.contract
def test_missing_url_raises():
    """Instantiating the output model without required url must raise ValidationError."""
    with pytest.raises(ValidationError):
        JobPostingResult.model_validate({})


@pytest.mark.contract
def test_output_is_pydantic_model():
    """Handler must return a JobPostingResult instance, not a plain dict."""
    result = fetch_job_posting(url="https://example.com/jobs/123")
    assert isinstance(result, JobPostingResult)


@pytest.mark.contract
def test_country_present_and_non_empty():
    """country field must be present and a non-empty string."""
    result = fetch_job_posting(url="https://example.com/jobs/123")
    assert hasattr(result, "country")
    assert isinstance(result.country, str)
    assert result.country != ""


@pytest.mark.contract
def test_required_string_fields_non_empty():
    """title, company, and description must all be non-empty strings."""
    result = fetch_job_posting(url="https://example.com/jobs/123")
    assert isinstance(result.title, str) and result.title != ""
    assert isinstance(result.company, str) and result.company != ""
    assert isinstance(result.description, str) and result.description != ""


# ---------------------------------------------------------------------------
# Unit: _normalize_country
# ---------------------------------------------------------------------------


def test_normalize_country_us_aliases():
    assert _normalize_country("US") == "USA"
    assert _normalize_country("USA") == "USA"
    assert _normalize_country("United States") == "USA"
    assert _normalize_country("United States of America") == "USA"


def test_normalize_country_uk_aliases():
    assert _normalize_country("UK") == "UK"
    assert _normalize_country("United Kingdom") == "UK"
    assert _normalize_country("England") == "UK"
    assert _normalize_country("GB") == "UK"


def test_normalize_country_remote():
    assert _normalize_country("Remote") == "Remote"
    assert _normalize_country("remote") == "Remote"
    assert _normalize_country("REMOTE") == "Remote"


def test_normalize_country_unknown_passthrough():
    assert _normalize_country("Germany") == "Germany"
    assert _normalize_country("Canada") == "Canada"
    assert _normalize_country("CA") == "CA"


# ---------------------------------------------------------------------------
# Unit: _country_from_freetext
# ---------------------------------------------------------------------------


def test_country_from_freetext_standard():
    # "New York, NY, United States" → last token "United States" → "USA"
    assert _country_from_freetext("New York, NY, United States") == "USA"


def test_country_from_freetext_simple():
    # "London, United Kingdom" → last token "United Kingdom" → "UK"
    assert _country_from_freetext("London, United Kingdom") == "UK"


def test_country_from_freetext_no_location_none():
    assert _country_from_freetext(None) is None
    assert _country_from_freetext("") is None


def test_country_from_freetext_remote():
    assert _country_from_freetext("Remote") == "Remote"


# ---------------------------------------------------------------------------
# Unit: _slug_to_company
# ---------------------------------------------------------------------------


def test_slug_to_company_dashes():
    assert _slug_to_company("acme-corp") == "Acme Corp"


def test_slug_to_company_underscores():
    assert _slug_to_company("acme_corp") == "Acme Corp"


def test_slug_to_company_single_word():
    assert _slug_to_company("acme") == "Acme"


# ---------------------------------------------------------------------------
# Unit: _route
# ---------------------------------------------------------------------------


def test_route_ashby():
    fn = _route("https://jobs.ashbyhq.com/company/abc-123")
    assert fn is _fetch_ashby


def test_route_greenhouse_boards():
    fn = _route("https://boards.greenhouse.io/acme/jobs/123")
    assert fn is _fetch_greenhouse


def test_route_greenhouse_job_boards():
    fn = _route("https://job-boards.greenhouse.io/acme/jobs/123")
    assert fn is _fetch_greenhouse


def test_route_unsupported_raises():
    with pytest.raises(ValueError, match="Unsupported job board"):
        _route("https://lever.co/company/job-123")


def test_match_host_known():
    assert _match_host("jobs.ashbyhq.com") is _fetch_ashby
    assert _match_host("boards.greenhouse.io") is _fetch_greenhouse


def test_match_host_unknown_returns_none():
    assert _match_host("lever.co") is None
    assert _match_host("") is None


# ---------------------------------------------------------------------------
# Integration: fetch_job_posting — redirect following for custom domains
# ---------------------------------------------------------------------------

CUSTOM_DOMAIN_URL = "https://careers.custom-company.com/jobs/999"


@responses_lib.activate
def test_fetch_job_posting_follows_redirect_to_greenhouse():
    responses_lib.add(
        responses_lib.HEAD,
        CUSTOM_DOMAIN_URL,
        status=301,
        headers={"Location": GH_JOB_URL_BOARDS},
    )
    responses_lib.add(responses_lib.HEAD, GH_JOB_URL_BOARDS, status=200)
    responses_lib.add(responses_lib.GET, GH_JOB_API, json=GH_JOB_PAYLOAD_US, status=200)
    responses_lib.add(responses_lib.GET, GH_META_API, json=GH_META_PAYLOAD, status=200)

    result = fetch_job_posting(CUSTOM_DOMAIN_URL)
    assert result.title == "Staff Engineer"
    assert result.company == "Acme Corp"
    assert result.country == "USA"


@responses_lib.activate
def test_fetch_job_posting_redirect_to_unsupported_raises():
    responses_lib.add(
        responses_lib.HEAD,
        CUSTOM_DOMAIN_URL,
        status=301,
        headers={"Location": "https://lever.co/company/job-123"},
    )
    responses_lib.add(responses_lib.HEAD, "https://lever.co/company/job-123", status=200)

    with pytest.raises(ValueError, match="Unsupported job board"):
        fetch_job_posting(CUSTOM_DOMAIN_URL)


# ---------------------------------------------------------------------------
# Integration: Ashby — happy path with addressCountry
# ---------------------------------------------------------------------------

ASHBY_JOB_UUID = "abc12345-0000-0000-0000-000000000001"
ASHBY_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/my-company"
ASHBY_JOB_URL = f"https://jobs.ashbyhq.com/my-company/{ASHBY_JOB_UUID}"

ASHBY_HAPPY_PAYLOAD = {
    "jobs": [
        {
            "title": "Backend Engineer",
            "jobUrl": f"https://jobs.ashbyhq.com/my-company/{ASHBY_JOB_UUID}",
            "location": "San Francisco, CA",
            "descriptionPlain": "Build cool things.",
            "publishedAt": "2026-06-01T00:00:00Z",
            "address": {"postalAddress": {"addressCountry": "US"}},
        }
    ]
}


@responses_lib.activate
def test_ashby_happy_path_with_address_country():
    responses_lib.add(
        responses_lib.GET,
        ASHBY_BOARD_URL,
        json=ASHBY_HAPPY_PAYLOAD,
        status=200,
    )
    result = _fetch_ashby(ASHBY_JOB_URL)
    assert result.title == "Backend Engineer"
    assert result.company == "My Company"
    assert result.country == "USA"
    assert result.location == "San Francisco, CA"
    assert result.posted_date == "2026-06-01"


# ---------------------------------------------------------------------------
# Integration: Ashby — location fallback (no addressCountry)
# ---------------------------------------------------------------------------

ASHBY_NO_COUNTRY_PAYLOAD = {
    "jobs": [
        {
            "title": "Frontend Engineer",
            "jobUrl": f"https://jobs.ashbyhq.com/my-company/{ASHBY_JOB_UUID}",
            "location": "San Francisco, CA",
            "descriptionPlain": "Build UIs.",
            "publishedAt": "2026-06-01T00:00:00Z",
            "address": {"postalAddress": {"addressCountry": ""}},
        }
    ]
}


@responses_lib.activate
def test_ashby_location_fallback_country():
    """When addressCountry is absent/empty, fall back to last whitespace token of location."""
    responses_lib.add(
        responses_lib.GET,
        ASHBY_BOARD_URL,
        json=ASHBY_NO_COUNTRY_PAYLOAD,
        status=200,
    )
    result = _fetch_ashby(ASHBY_JOB_URL)
    # "San Francisco, CA" → last whitespace token is "CA"
    assert result.country == "CA"


# ---------------------------------------------------------------------------
# Integration: Ashby — 404 raises ValueError
# ---------------------------------------------------------------------------


@responses_lib.activate
def test_ashby_404_raises_value_error():
    responses_lib.add(
        responses_lib.GET,
        ASHBY_BOARD_URL,
        status=404,
    )
    with pytest.raises(ValueError, match="Failed to fetch job posting"):
        _fetch_ashby(ASHBY_JOB_URL)


# ---------------------------------------------------------------------------
# Integration: Greenhouse boards.greenhouse.io — US location
# ---------------------------------------------------------------------------

GH_TOKEN = "acme-corp"
GH_JOB_ID = "999"
GH_JOB_URL_BOARDS = f"https://boards.greenhouse.io/{GH_TOKEN}/jobs/{GH_JOB_ID}"
GH_JOB_API = f"https://boards-api.greenhouse.io/v1/boards/{GH_TOKEN}/jobs/{GH_JOB_ID}?content=true"
GH_META_API = f"https://boards-api.greenhouse.io/v1/boards/{GH_TOKEN}"

GH_JOB_PAYLOAD_US = {
    "title": "Staff Engineer",
    "content": "<p>Lead our platform team.</p>",
    "location": {"name": "New York, NY, United States"},
    "first_published": "2026-05-15T00:00:00Z",
}

GH_META_PAYLOAD = {
    "name": "Acme Corp",
}


@responses_lib.activate
def test_greenhouse_boards_host_us_country():
    responses_lib.add(responses_lib.GET, GH_JOB_API, json=GH_JOB_PAYLOAD_US, status=200)
    responses_lib.add(responses_lib.GET, GH_META_API, json=GH_META_PAYLOAD, status=200)
    result = _fetch_greenhouse(GH_JOB_URL_BOARDS)
    assert result.title == "Staff Engineer"
    assert result.company == "Acme Corp"
    assert result.country == "USA"
    assert result.posted_date == "2026-05-15"


# ---------------------------------------------------------------------------
# Integration: Greenhouse job-boards.greenhouse.io — UK location
# ---------------------------------------------------------------------------

GH_JOB_URL_JB = f"https://job-boards.greenhouse.io/{GH_TOKEN}/jobs/{GH_JOB_ID}"

GH_JOB_PAYLOAD_UK = {
    "title": "Senior Engineer",
    "content": "<p>Join our London office.</p>",
    "location": {"name": "London, United Kingdom"},
    "first_published": "2026-05-20T00:00:00Z",
}


@responses_lib.activate
def test_greenhouse_job_boards_host_uk_country():
    responses_lib.add(responses_lib.GET, GH_JOB_API, json=GH_JOB_PAYLOAD_UK, status=200)
    responses_lib.add(responses_lib.GET, GH_META_API, json=GH_META_PAYLOAD, status=200)
    result = _fetch_greenhouse(GH_JOB_URL_JB)
    assert result.country == "UK"


# ---------------------------------------------------------------------------
# Integration: Greenhouse — Remote location
# ---------------------------------------------------------------------------

GH_JOB_PAYLOAD_REMOTE = {
    "title": "Remote Engineer",
    "content": "<p>Work from anywhere.</p>",
    "location": {"name": "Remote"},
    "updated_at": "2026-04-01T00:00:00Z",
}


@responses_lib.activate
def test_greenhouse_remote_country():
    responses_lib.add(
        responses_lib.GET, GH_JOB_API, json=GH_JOB_PAYLOAD_REMOTE, status=200
    )
    responses_lib.add(responses_lib.GET, GH_META_API, json=GH_META_PAYLOAD, status=200)
    result = _fetch_greenhouse(GH_JOB_URL_BOARDS)
    assert result.country == "Remote"


# ---------------------------------------------------------------------------
# Integration: Greenhouse — metadata 500 → company fallback, no exception
# ---------------------------------------------------------------------------


@responses_lib.activate
def test_greenhouse_metadata_500_company_fallback():
    responses_lib.add(responses_lib.GET, GH_JOB_API, json=GH_JOB_PAYLOAD_US, status=200)
    responses_lib.add(responses_lib.GET, GH_META_API, status=500)
    result = _fetch_greenhouse(GH_JOB_URL_BOARDS)
    # Falls back to _slug_to_company("acme-corp") = "Acme Corp"
    assert result.company == "Acme Corp"


# ---------------------------------------------------------------------------
# Integration: Greenhouse — job 404 raises ValueError
# ---------------------------------------------------------------------------


@responses_lib.activate
def test_greenhouse_job_404_raises_value_error():
    responses_lib.add(responses_lib.GET, GH_JOB_API, status=404)
    with pytest.raises(ValueError, match="Failed to fetch job posting"):
        _fetch_greenhouse(GH_JOB_URL_BOARDS)


# ---------------------------------------------------------------------------
# Unit: _extract_gh_token_from_html
# ---------------------------------------------------------------------------

GH_EMBED_HTML = """
<html><head>
<script src="https://boards.greenhouse.io/embed/job_board/js?for=toyotaconnected"></script>
</head></html>
"""

GH_EMBED_HTML_JOB_BOARDS = """
<html><head>
<script src="https://job-boards.greenhouse.io/embed/job_board/js?for=acme-corp"></script>
</head></html>
"""


def test_extract_gh_token_boards_domain():
    assert _extract_gh_token_from_html(GH_EMBED_HTML) == "toyotaconnected"


def test_extract_gh_token_job_boards_domain():
    assert _extract_gh_token_from_html(GH_EMBED_HTML_JOB_BOARDS) == "acme-corp"


def test_extract_gh_token_not_found_returns_none():
    assert _extract_gh_token_from_html("<html><body>no greenhouse here</body></html>") is None


# ---------------------------------------------------------------------------
# Integration: Greenhouse custom domain via gh_jid query param
# ---------------------------------------------------------------------------

CUSTOM_GH_DOMAIN_URL = "https://www.toyotaconnected.com/job?gh_jid=8577877002"
CUSTOM_GH_TOKEN = "toyotaconnected"
CUSTOM_GH_JOB_ID = "8577877002"
CUSTOM_GH_JOB_API = (
    f"https://boards-api.greenhouse.io/v1/boards/{CUSTOM_GH_TOKEN}/jobs/{CUSTOM_GH_JOB_ID}?content=true"
)
CUSTOM_GH_META_API = f"https://boards-api.greenhouse.io/v1/boards/{CUSTOM_GH_TOKEN}"


@responses_lib.activate
def test_fetch_job_posting_greenhouse_custom_domain_gh_jid():
    responses_lib.add(
        responses_lib.GET,
        CUSTOM_GH_DOMAIN_URL,
        body=GH_EMBED_HTML,
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        CUSTOM_GH_JOB_API,
        json={
            "title": "Software Engineer, Entry Level",
            "content": "<p>Build things.</p>",
            "location": {"name": "Plano, TX, United States"},
            "first_published": "2026-06-01T00:00:00Z",
        },
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        CUSTOM_GH_META_API,
        json={"name": "Toyota Connected North America"},
        status=200,
    )

    result = fetch_job_posting(CUSTOM_GH_DOMAIN_URL)
    assert result.title == "Software Engineer, Entry Level"
    assert result.company == "Toyota Connected North America"
    assert result.country == "USA"


@responses_lib.activate
def test_fetch_job_posting_greenhouse_custom_domain_missing_token_raises():
    responses_lib.add(
        responses_lib.GET,
        CUSTOM_GH_DOMAIN_URL,
        body="<html><body>no greenhouse embed</body></html>",
        status=200,
    )
    with pytest.raises(ValueError, match="Failed to fetch job posting"):
        fetch_job_posting(CUSTOM_GH_DOMAIN_URL)
