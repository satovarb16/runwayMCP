import pytest
import responses as responses_lib
from pydantic import ValidationError
from unittest.mock import MagicMock, patch

import tools.jobs as jobs_module
from tools.jobs import (
    JobPostingResult,
    fetch_job_posting,
    _normalize_country,
    _country_from_freetext,
    _slug_to_company,
    _route,
    _fetch_ashby,
    _fetch_greenhouse,
    _fetch_lever,
    _match_host,
    _extract_gh_token_from_html,
    _render_and_extract_gh_token,
    _is_bot_challenge,
    _extract_jsonld,
    _extract_microdata,
    _extract_next_data,
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
        _route("https://unknownboard.example.org/company/job-123")


def test_match_host_known():
    assert _match_host("jobs.ashbyhq.com") is _fetch_ashby
    assert _match_host("boards.greenhouse.io") is _fetch_greenhouse


def test_match_host_unknown_returns_none():
    assert _match_host("unknownboard.example.org") is None
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
    unsupported_url = "https://unknownboard.example.org/company/job-123"
    responses_lib.add(
        responses_lib.HEAD,
        CUSTOM_DOMAIN_URL,
        status=301,
        headers={"Location": unsupported_url},
    )
    responses_lib.add(responses_lib.HEAD, unsupported_url, status=200)
    responses_lib.add(responses_lib.GET, unsupported_url, body="", status=200)

    with patch.object(jobs_module, "_PLAYWRIGHT_AVAILABLE", False):
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
    assert (
        _extract_gh_token_from_html("<html><body>no greenhouse here</body></html>")
        is None
    )


# ---------------------------------------------------------------------------
# Integration: Greenhouse custom domain via gh_jid query param
# ---------------------------------------------------------------------------

CUSTOM_GH_DOMAIN_URL = "https://www.toyotaconnected.com/job?gh_jid=8577877002"
CUSTOM_GH_TOKEN = "toyotaconnected"
CUSTOM_GH_JOB_ID = "8577877002"
CUSTOM_GH_JOB_API = f"https://boards-api.greenhouse.io/v1/boards/{CUSTOM_GH_TOKEN}/jobs/{CUSTOM_GH_JOB_ID}?content=true"
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


# ---------------------------------------------------------------------------
# T3: Lever URL routing via _match_host
# ---------------------------------------------------------------------------


def test_match_host_lever_jobs_lever_co():
    """jobs.lever.co must route to _fetch_lever."""
    assert _match_host("jobs.lever.co") is _fetch_lever


def test_match_host_lever_co():
    """lever.co must route to _fetch_lever."""
    assert _match_host("lever.co") is _fetch_lever


def test_match_host_non_lever_not_fetch_lever():
    """boards.greenhouse.io must not route to _fetch_lever."""
    assert _match_host("boards.greenhouse.io") is not _fetch_lever


def test_route_lever_returns_fetch_lever():
    """_route with a jobs.lever.co URL must return _fetch_lever."""
    fn = _route("https://jobs.lever.co/acmecorp/abc-123")
    assert fn is _fetch_lever


# ---------------------------------------------------------------------------
# T4: Lever API URL construction
# ---------------------------------------------------------------------------

LEVER_JOB_URL = "https://jobs.lever.co/acmecorp/abc-123"
LEVER_API_URL = "https://api.lever.co/v0/postings/acmecorp/abc-123"

LEVER_FULL_PAYLOAD = {
    "text": "Senior Software Engineer",
    "categories": {"location": "San Francisco, CA"},
    "country": "US",
    "descriptionPlain": "Build great software.",
    "createdAt": 1748736000000,  # 2025-06-01 00:00:00 UTC in ms
}


@responses_lib.activate
def test_fetch_lever_api_url_constructed_correctly():
    """_fetch_lever must call the correct api.lever.co endpoint."""
    responses_lib.add(
        responses_lib.GET,
        LEVER_API_URL,
        json=LEVER_FULL_PAYLOAD,
        status=200,
    )
    _fetch_lever(LEVER_JOB_URL)
    assert len(responses_lib.calls) == 1
    assert responses_lib.calls[0].request.url == LEVER_API_URL


# ---------------------------------------------------------------------------
# T5: Lever field mapping
# ---------------------------------------------------------------------------


@responses_lib.activate
def test_fetch_lever_all_fields_present():
    """All JobPostingResult fields are mapped correctly from a full Lever response."""
    responses_lib.add(
        responses_lib.GET,
        LEVER_API_URL,
        json=LEVER_FULL_PAYLOAD,
        status=200,
    )
    result = _fetch_lever(LEVER_JOB_URL)
    assert result.title == "Senior Software Engineer"
    assert result.company == "Acmecorp"
    assert result.location == "San Francisco, CA"
    assert result.country == "USA"
    assert result.description == "Build great software."
    assert result.posted_date == "2025-06-01"
    assert result.source_url == LEVER_JOB_URL


@responses_lib.activate
def test_fetch_lever_created_at_absent_posted_date_is_none():
    """When createdAt is absent, posted_date must be None without raising."""
    payload = {k: v for k, v in LEVER_FULL_PAYLOAD.items() if k != "createdAt"}
    responses_lib.add(responses_lib.GET, LEVER_API_URL, json=payload, status=200)
    result = _fetch_lever(LEVER_JOB_URL)
    assert result.posted_date is None


@responses_lib.activate
def test_fetch_lever_country_absent_is_none():
    """When country is absent from the response, result.country must be None."""
    payload = {k: v for k, v in LEVER_FULL_PAYLOAD.items() if k != "country"}
    responses_lib.add(responses_lib.GET, LEVER_API_URL, json=payload, status=200)
    result = _fetch_lever(LEVER_JOB_URL)
    assert result.country is None


@responses_lib.activate
def test_fetch_lever_categories_no_location_is_none():
    """When categories has no location key, result.location must be None."""
    payload = {**LEVER_FULL_PAYLOAD, "categories": {}}
    responses_lib.add(responses_lib.GET, LEVER_API_URL, json=payload, status=200)
    result = _fetch_lever(LEVER_JOB_URL)
    assert result.location is None


@responses_lib.activate
def test_fetch_lever_company_from_url_slug():
    """company is derived from the URL slug, not the API payload."""
    responses_lib.add(
        responses_lib.GET, LEVER_API_URL, json=LEVER_FULL_PAYLOAD, status=200
    )
    result = _fetch_lever(LEVER_JOB_URL)
    # _slug_to_company("acmecorp") == "Acmecorp"
    assert result.company == "Acmecorp"


# ---------------------------------------------------------------------------
# T6: Lever error handling
# ---------------------------------------------------------------------------


@responses_lib.activate
def test_fetch_lever_404_raises_value_error():
    """HTTP 404 from Lever API must raise ValueError with the standard message."""
    responses_lib.add(responses_lib.GET, LEVER_API_URL, status=404)
    with pytest.raises(ValueError, match="Failed to fetch job posting"):
        _fetch_lever(LEVER_JOB_URL)


def test_fetch_lever_request_exception_raises_value_error():
    """A network error must be wrapped in ValueError."""
    from requests.exceptions import ConnectionError as ReqConnectionError

    with patch("tools.jobs.requests.get", side_effect=ReqConnectionError("timeout")):
        with pytest.raises(ValueError, match="Failed to fetch job posting"):
            _fetch_lever(LEVER_JOB_URL)


def test_fetch_lever_invalid_url_path_raises():
    """A Lever URL with fewer than 2 path parts must raise ValueError."""
    with pytest.raises(ValueError, match="Failed to fetch job posting"):
        _fetch_lever("https://jobs.lever.co/acmecorp")


@responses_lib.activate
def test_fetch_lever_malformed_json_raises_value_error():
    """A non-JSON response from the Lever API must raise ValueError, not JSONDecodeError."""
    responses_lib.add(
        responses_lib.GET,
        LEVER_API_URL,
        body="not json at all",
        content_type="text/html",
        status=200,
    )
    with pytest.raises(ValueError, match="Failed to fetch job posting"):
        _fetch_lever(LEVER_JOB_URL)


# ---------------------------------------------------------------------------
# T9: _PLAYWRIGHT_AVAILABLE flag
# ---------------------------------------------------------------------------


def test_playwright_available_flag_reflects_module_state():
    """_PLAYWRIGHT_AVAILABLE must be a bool set at import time."""
    assert isinstance(jobs_module._PLAYWRIGHT_AVAILABLE, bool)


def test_playwright_available_false_when_patched():
    """Monkeypatching _PLAYWRIGHT_AVAILABLE to False must be visible to callers."""
    original = jobs_module._PLAYWRIGHT_AVAILABLE
    jobs_module._PLAYWRIGHT_AVAILABLE = False
    assert jobs_module._PLAYWRIGHT_AVAILABLE is False
    jobs_module._PLAYWRIGHT_AVAILABLE = original


# ---------------------------------------------------------------------------
# T10: Static fast path preserved — Playwright NOT called when token in HTML
# ---------------------------------------------------------------------------


@responses_lib.activate
def test_greenhouse_custom_domain_static_path_playwright_not_called():
    """When static HTML yields a token, _render_and_extract_gh_token must not be called."""
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
            "title": "Engineer",
            "content": "<p>Work here.</p>",
            "location": {"name": "Austin, TX, United States"},
            "first_published": "2026-06-01T00:00:00Z",
        },
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        CUSTOM_GH_META_API,
        json={"name": "Toyota Connected"},
        status=200,
    )

    with patch.object(jobs_module, "_render_and_extract_gh_token") as mock_render:
        result = fetch_job_posting(CUSTOM_GH_DOMAIN_URL)
        mock_render.assert_not_called()
    assert result.title == "Engineer"


# ---------------------------------------------------------------------------
# T11: Greenhouse Playwright fallback scenarios
# ---------------------------------------------------------------------------


@responses_lib.activate
def test_greenhouse_custom_domain_playwright_fallback_succeeds():
    """When static returns None and Playwright is available, fallback token is used."""
    responses_lib.add(
        responses_lib.GET,
        CUSTOM_GH_DOMAIN_URL,
        body="<html><body>no token here</body></html>",
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        CUSTOM_GH_JOB_API,
        json={
            "title": "SPA Engineer",
            "content": "<p>SPA job.</p>",
            "location": {"name": "Remote"},
            "first_published": "2026-06-01T00:00:00Z",
        },
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        CUSTOM_GH_META_API,
        json={"name": "Toyota Connected"},
        status=200,
    )

    with patch.object(jobs_module, "_PLAYWRIGHT_AVAILABLE", True):
        with patch.object(
            jobs_module, "_render_and_extract_gh_token", return_value=CUSTOM_GH_TOKEN
        ):
            result = fetch_job_posting(CUSTOM_GH_DOMAIN_URL)
    assert result.title == "SPA Engineer"


@responses_lib.activate
def test_greenhouse_custom_domain_playwright_both_fail_raises():
    """When static and Playwright both return None, ValueError with 'board token' is raised."""
    responses_lib.add(
        responses_lib.GET,
        CUSTOM_GH_DOMAIN_URL,
        body="<html><body>no token here</body></html>",
        status=200,
    )

    with patch.object(jobs_module, "_PLAYWRIGHT_AVAILABLE", True):
        with patch.object(
            jobs_module, "_render_and_extract_gh_token", return_value=None
        ):
            with pytest.raises(ValueError, match="board token"):
                fetch_job_posting(CUSTOM_GH_DOMAIN_URL)


@responses_lib.activate
def test_greenhouse_custom_domain_playwright_not_installed_raises_install_hint():
    """When static fails and Playwright is NOT installed, error mentions pip install runwayMCP[browser]."""
    responses_lib.add(
        responses_lib.GET,
        CUSTOM_GH_DOMAIN_URL,
        body="<html><body>no token here</body></html>",
        status=200,
    )

    with patch.object(jobs_module, "_PLAYWRIGHT_AVAILABLE", False):
        with pytest.raises(ValueError, match=r"pip install runwayMCP\[browser\]"):
            fetch_job_posting(CUSTOM_GH_DOMAIN_URL)


# ---------------------------------------------------------------------------
# T12: _render_and_extract_gh_token
# ---------------------------------------------------------------------------


def test_render_and_extract_gh_token_calls_goto_networkidle():
    """_render_and_extract_gh_token must call goto with wait_until='networkidle'."""
    mock_page = MagicMock()
    mock_page.content.return_value = GH_EMBED_HTML
    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_p = MagicMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_p)
    mock_cm.__exit__ = MagicMock(return_value=False)

    with patch.object(jobs_module, "sync_playwright", return_value=mock_cm):
        result = _render_and_extract_gh_token(
            "https://www.toyotaconnected.com/job?gh_jid=1"
        )

    mock_page.goto.assert_called_once_with(
        "https://www.toyotaconnected.com/job?gh_jid=1",
        wait_until="networkidle",
        timeout=30000,
    )
    assert result == "toyotaconnected"


def test_render_and_extract_gh_token_exception_returns_none():
    """Any exception from sync_playwright must be swallowed and None returned."""
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(side_effect=Exception("Chromium not found"))

    with patch.object(jobs_module, "sync_playwright", return_value=mock_cm):
        result = _render_and_extract_gh_token(
            "https://www.toyotaconnected.com/job?gh_jid=1"
        )

    assert result is None


# ---------------------------------------------------------------------------
# 2.1 Unit: _is_bot_challenge
# ---------------------------------------------------------------------------


def test_is_bot_challenge_cloudflare_returns_true():
    """Cloudflare interstitial text must trigger bot-challenge detection."""
    html = "<html><body>Checking your browser before accessing the site.</body></html>"
    assert _is_bot_challenge(html) is True


def test_is_bot_challenge_hcaptcha_returns_true():
    """hCaptcha widget presence must trigger bot-challenge detection."""
    html = '<html><body><div class="hcaptcha">solve me</div></body></html>'
    assert _is_bot_challenge(html) is True


def test_is_bot_challenge_cf_clearance_cookie_returns_true():
    """cf_clearance cookie reference must trigger bot-challenge detection."""
    html = (
        "<html><head><script>document.cookie='cf_clearance=abc'</script></head></html>"
    )
    assert _is_bot_challenge(html) is True


def test_is_bot_challenge_clean_page_returns_false():
    """A normal job posting page with no WAF markers must return False."""
    html = "<html><body><h1>Software Engineer</h1><p>We are hiring.</p></body></html>"
    assert _is_bot_challenge(html) is False


def test_is_bot_challenge_case_insensitive():
    """Detection must work regardless of character case."""
    html = "<html><body>CHECKING YOUR BROWSER please wait</body></html>"
    assert _is_bot_challenge(html) is True


# ---------------------------------------------------------------------------
# 2.2 Unit: _extract_jsonld
# ---------------------------------------------------------------------------

JSONLD_FULL_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Staff Engineer",
  "hiringOrganization": {"@type": "Organization", "name": "Acme Corp"},
  "jobLocation": {
    "@type": "Place",
    "address": {
      "@type": "PostalAddress",
      "addressCountry": "US",
      "addressLocality": "New York"
    }
  },
  "description": "<p>Build great things.</p>",
  "datePosted": "2026-06-01T00:00:00Z"
}
</script>
</head><body></body></html>
"""

JSONLD_PARTIAL_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Engineer",
  "hiringOrganization": {"@type": "Organization", "name": "Beta Co"}
}
</script>
</head><body></body></html>
"""

JSONLD_MULTIPLE_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "First Job", "hiringOrganization": {"name": "First Co"}}
</script>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Second Job", "hiringOrganization": {"name": "Second Co"}}
</script>
</head><body></body></html>
"""

JSONLD_NO_JOB_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "Organization", "name": "Acme"}
</script>
</head><body></body></html>
"""

JSONLD_MALFORMED_HTML = """
<html><head>
<script type="application/ld+json">
{ this is not valid json !!
</script>
</head><body></body></html>
"""


def test_extract_jsonld_full_block_all_fields():
    """Full JSON-LD JobPosting block must produce a populated JobPostingResult."""
    result = _extract_jsonld(JSONLD_FULL_HTML, "https://example.org/job/1")
    assert result is not None
    assert result.title == "Staff Engineer"
    assert result.company == "Acme Corp"
    assert result.country == "USA"
    assert result.location == "New York"
    assert result.posted_date == "2026-06-01"
    assert "<p>" not in result.description  # HTML stripped


def test_extract_jsonld_partial_block_missing_field_is_none():
    """When datePosted is absent the result must have posted_date=None and no exception."""
    result = _extract_jsonld(JSONLD_PARTIAL_HTML, "https://example.org/job/2")
    assert result is not None
    assert result.title == "Engineer"
    assert result.posted_date is None


def test_extract_jsonld_multiple_blocks_first_wins():
    """When multiple JobPosting blocks exist the first one must be used."""
    result = _extract_jsonld(JSONLD_MULTIPLE_HTML, "https://example.org/job/3")
    assert result is not None
    assert result.title == "First Job"
    assert result.company == "First Co"


def test_extract_jsonld_no_job_posting_returns_none():
    """When no JobPosting @type exists the function must return None."""
    result = _extract_jsonld(JSONLD_NO_JOB_HTML, "https://example.org/job/4")
    assert result is None


def test_extract_jsonld_malformed_json_returns_none():
    """Malformed JSON in the script block must return None without raising."""
    result = _extract_jsonld(JSONLD_MALFORMED_HTML, "https://example.org/job/5")
    assert result is None


# ---------------------------------------------------------------------------
# 2.3 Unit: _extract_microdata
# ---------------------------------------------------------------------------

MICRODATA_FULL_HTML = """
<html><body>
<div itemscope itemtype="https://schema.org/JobPosting">
  <span itemprop="title">Backend Engineer</span>
  <div itemscope itemtype="https://schema.org/Organization">
    <span itemprop="name">Gamma Inc</span>
  </div>
  <span itemprop="addressCountry">US</span>
  <span itemprop="addressLocality">Austin</span>
  <div itemprop="description">Build APIs.</div>
  <span itemprop="datePosted">2026-05-01</span>
</div>
</body></html>
"""

MICRODATA_PARTIAL_HTML = """
<html><body>
<div itemscope itemtype="https://schema.org/JobPosting">
  <span itemprop="title">Frontend Dev</span>
</div>
</body></html>
"""

MICRODATA_NO_JOBPOSTING_HTML = """
<html><body>
<div itemscope itemtype="https://schema.org/Organization">
  <span itemprop="name">Some Company</span>
</div>
</body></html>
"""


def test_extract_microdata_full_block_all_fields():
    """Full microdata JobPosting block must produce a populated JobPostingResult."""
    result = _extract_microdata(MICRODATA_FULL_HTML, "https://example.org/job/10")
    assert result is not None
    assert result.title == "Backend Engineer"
    assert result.company == "Gamma Inc"
    assert result.country == "USA"
    assert result.location == "Austin"
    assert result.posted_date == "2026-05-01"


def test_extract_microdata_partial_block_location_none():
    """When addressLocality is absent the result must have location=None and no exception."""
    result = _extract_microdata(MICRODATA_PARTIAL_HTML, "https://example.org/job/11")
    assert result is not None
    assert result.title == "Frontend Dev"
    assert result.location is None


def test_extract_microdata_no_jobposting_returns_none():
    """When no element with JobPosting itemtype exists the function must return None."""
    result = _extract_microdata(
        MICRODATA_NO_JOBPOSTING_HTML, "https://example.org/job/12"
    )
    assert result is None


# ---------------------------------------------------------------------------
# 2.4 Unit: _extract_next_data
# ---------------------------------------------------------------------------

NEXT_DATA_FULL_HTML = """
<html><head>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "apiData": {
        "jobPost": {
          "name": "Product Manager",
          "companyName": "Rippling Inc",
          "createdOn": "2026-04-15T00:00:00Z"
        },
        "workLocations": [{"name": "San Francisco, CA"}]
      }
    }
  }
}
</script>
</head><body></body></html>
"""

NEXT_DATA_PATH_ABSENT_HTML = """
<html><head>
<script id="__NEXT_DATA__" type="application/json">
{"props": {"pageProps": {}}}
</script>
</head><body></body></html>
"""

NEXT_DATA_MALFORMED_HTML = """
<html><head>
<script id="__NEXT_DATA__" type="application/json">
not json at all
</script>
</head><body></body></html>
"""


def test_extract_next_data_full_path_present():
    """Full Rippling __NEXT_DATA__ path must produce a populated JobPostingResult."""
    result = _extract_next_data(NEXT_DATA_FULL_HTML, "https://rippling.com/job/1")
    assert result is not None
    assert result.title == "Product Manager"
    assert result.company == "Rippling Inc"
    assert result.posted_date == "2026-04-15"
    assert result.location == "San Francisco, CA"


def test_extract_next_data_path_absent_returns_none():
    """When the Rippling path is absent the function must return None without raising."""
    result = _extract_next_data(
        NEXT_DATA_PATH_ABSENT_HTML, "https://rippling.com/job/2"
    )
    assert result is None


def test_extract_next_data_malformed_json_returns_none():
    """Malformed __NEXT_DATA__ JSON must return None without raising."""
    result = _extract_next_data(NEXT_DATA_MALFORMED_HTML, "https://rippling.com/job/3")
    assert result is None


# ---------------------------------------------------------------------------
# 2.5 Integration: fetch_job_posting — generic fallback
# ---------------------------------------------------------------------------

GENERIC_URL = "https://unknownats.io/jobs/42"


@responses_lib.activate
def test_fetch_job_posting_generic_jsonld_succeeds():
    """When unknown host returns JSON-LD, fetch_job_posting must return the extracted result."""
    responses_lib.add(
        responses_lib.HEAD,
        GENERIC_URL,
        status=200,
        headers={"Location": GENERIC_URL},
    )
    responses_lib.add(
        responses_lib.GET,
        GENERIC_URL,
        body=JSONLD_FULL_HTML,
        status=200,
    )
    with patch.object(jobs_module, "_PLAYWRIGHT_AVAILABLE", False):
        result = fetch_job_posting(GENERIC_URL)
    assert result.title == "Staff Engineer"
    assert result.company == "Acme Corp"


@responses_lib.activate
def test_fetch_job_posting_generic_microdata_fallback():
    """When JSON-LD fails but microdata succeeds, microdata result must be returned."""
    responses_lib.add(
        responses_lib.HEAD,
        GENERIC_URL,
        status=200,
        headers={"Location": GENERIC_URL},
    )
    responses_lib.add(
        responses_lib.GET,
        GENERIC_URL,
        body=MICRODATA_FULL_HTML,
        status=200,
    )
    with patch.object(jobs_module, "_PLAYWRIGHT_AVAILABLE", False):
        result = fetch_job_posting(GENERIC_URL)
    assert result.title == "Backend Engineer"


@responses_lib.activate
def test_fetch_job_posting_generic_bot_challenge_raises():
    """When the HTML is a bot-challenge page, ValueError containing 'blocked by bot protection' must be raised."""
    bot_html = (
        "<html><body>Checking your browser before accessing the site.</body></html>"
    )
    responses_lib.add(
        responses_lib.HEAD,
        GENERIC_URL,
        status=200,
        headers={"Location": GENERIC_URL},
    )
    responses_lib.add(
        responses_lib.GET,
        GENERIC_URL,
        body=bot_html,
        status=200,
    )
    with patch.object(jobs_module, "_PLAYWRIGHT_AVAILABLE", False):
        with pytest.raises(ValueError, match="blocked by bot protection"):
            fetch_job_posting(GENERIC_URL)


@responses_lib.activate
def test_fetch_job_posting_generic_all_fail_raises_unsupported():
    """When all extractors fail and Playwright is unavailable, 'Unsupported job board' must be raised."""
    responses_lib.add(
        responses_lib.HEAD,
        GENERIC_URL,
        status=200,
        headers={"Location": GENERIC_URL},
    )
    responses_lib.add(
        responses_lib.GET,
        GENERIC_URL,
        body="<html><body>No structured data here at all.</body></html>",
        status=200,
    )
    with patch.object(jobs_module, "_PLAYWRIGHT_AVAILABLE", False):
        with pytest.raises(ValueError, match="Unsupported job board"):
            fetch_job_posting(GENERIC_URL)
