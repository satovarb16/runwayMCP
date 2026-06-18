from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse, parse_qs

import requests
from requests.exceptions import RequestException
from pydantic import BaseModel
from bs4 import BeautifulSoup

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_BOT_CHALLENGE_FINGERPRINTS = (
    "checking your browser",
    "cf-browser-verification",
    "cf_clearance",
    "hcaptcha",
    "enable javascript and cookies",
)

try:
    from playwright.sync_api import sync_playwright

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    sync_playwright = None  # type: ignore[assignment]
    _PLAYWRIGHT_AVAILABLE = False


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class JobPostingResult(BaseModel):
    title: str
    company: str
    country: str | None = None
    location: str | None = None
    description: str | None = None
    posted_date: str | None = None
    source_url: str | None = None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _normalize_country(token: str) -> str:
    """Normalize a country token to a canonical string.

    US/USA/United States/United States of America → "USA"
    UK/United Kingdom/England/GB → "UK"
    Remote (any case) → "Remote"
    Everything else → returned as-is.
    """
    normalized = token.strip()
    upper = normalized.upper()

    if upper in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
        return "USA"
    if upper in {"UK", "UNITED KINGDOM", "ENGLAND", "GB"}:
        return "UK"
    if upper == "REMOTE":
        return "Remote"
    return normalized


# Two-letter US state codes — used by _country_from_freetext to map city/state
# location strings (e.g. "San Francisco, CA") to "USA".
_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}


def _country_from_freetext(text: str | None) -> str | None:
    """Extract and normalize country from a free-text location string.

    Splits on comma and uses the last token. Recognises two-letter US state
    codes (e.g. "CA", "NY") as "USA" so that city/state formats like
    "San Francisco, CA" produce the correct country rather than a raw state
    abbreviation.

    Returns None if text is empty or None.
    """
    if not text:
        return None
    parts = [p.strip() for p in text.split(",")]
    if not parts:
        return None
    last = parts[-1].strip()
    if not last:
        return None
    if last.upper() in _US_STATE_CODES:
        return "USA"
    return _normalize_country(last)


def _slug_to_company(slug: str) -> str:
    """Convert a URL slug to a title-cased company name."""
    return slug.replace("-", " ").replace("_", " ").title()


# ---------------------------------------------------------------------------
# Generic fallback extractors (pure — no I/O)
# ---------------------------------------------------------------------------


def _is_bot_challenge(html: str) -> bool:
    """Return True if the HTML looks like a bot-protection interstitial page."""
    lowered = html.lower()
    return any(fp in lowered for fp in _BOT_CHALLENGE_FINGERPRINTS)


def _extract_jsonld(html: str, url: str) -> JobPostingResult | None:
    """Try to extract a JobPosting from a JSON-LD script block.

    Returns None when no JobPosting block is found or JSON is malformed.
    """
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    for raw in blocks:
        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        # JSON-LD may be a list or a @graph container
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            candidates = data.get("@graph", [data])
        else:
            candidates = []

        for node in candidates:
            if not isinstance(node, dict):
                continue
            if "JobPosting" not in str(node.get("@type", "")):
                continue
            try:
                title = node.get("title")
                if not title:
                    continue

                org = node.get("hiringOrganization")
                if isinstance(org, dict):
                    company = org.get("name") or ""
                elif isinstance(org, str):
                    company = org
                else:
                    company = ""

                loc = node.get("jobLocation")
                if isinstance(loc, list):
                    loc = loc[0] if loc else None
                address = (loc or {}).get("address") if isinstance(loc, dict) else None
                country_raw = (
                    (address or {}).get("addressCountry")
                    if isinstance(address, dict)
                    else None
                )
                if isinstance(country_raw, dict):
                    country_raw = country_raw.get("name")
                country = _normalize_country(country_raw) if country_raw else None
                location = (
                    (address or {}).get("addressLocality")
                    if isinstance(address, dict)
                    else None
                )

                desc_raw = node.get("description")
                description = (
                    BeautifulSoup(desc_raw, "html.parser")
                    .get_text(separator="\n")
                    .strip()
                    if desc_raw
                    else None
                )

                posted = node.get("datePosted")
                posted_date = (
                    posted[:10] if isinstance(posted, str) and posted else None
                )

                return JobPostingResult(
                    title=title,
                    company=company,
                    country=country,
                    location=location,
                    description=description,
                    posted_date=posted_date,
                    source_url=url,
                )
            except (KeyError, TypeError):
                continue
    return None


def _extract_microdata(html: str, url: str) -> JobPostingResult | None:
    """Try to extract a JobPosting from HTML microdata (itemprop attributes).

    Returns None when no JobPosting itemtype element is found.
    """
    soup = BeautifulSoup(html, "html.parser")
    item = soup.find(attrs={"itemtype": re.compile("JobPosting", re.IGNORECASE)})
    if item is None:
        return None

    def prop(scope, *names):
        for name in names:
            tag = scope.find(attrs={"itemprop": name})
            if tag is not None:
                value = tag.get("content") or tag.get_text(separator="\n")
                stripped = value.strip() if value else None
                return stripped or None
        return None

    title = prop(item, "title", "jobTitle")
    if not title:
        return None

    org = item.find(attrs={"itemtype": re.compile("Organization", re.IGNORECASE)})
    if org is not None:
        company = prop(org, "name", "legalName") or ""
    else:
        company = prop(item, "hiringOrganization") or ""

    country_raw = prop(item, "addressCountry")
    country = _normalize_country(country_raw) if country_raw else None
    location = prop(item, "addressLocality")
    description = prop(item, "description")
    posted = prop(item, "datePosted")
    posted_date = posted[:10] if posted else None

    return JobPostingResult(
        title=title,
        company=company,
        country=country,
        location=location,
        description=description,
        posted_date=posted_date,
        source_url=url,
    )


def _extract_next_data(html: str, url: str) -> JobPostingResult | None:
    """Try to extract a JobPosting from a Rippling-style __NEXT_DATA__ script block.

    Confined to the path: props.pageProps.apiData.jobPost.
    Returns None on any path miss or malformed JSON.
    """
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return None
    try:
        data = json.loads(m.group(1).strip())
        api = data["props"]["pageProps"]["apiData"]
        job = api["jobPost"]
        title = job["name"]
        company = job.get("companyName") or ""
        created = job.get("createdOn")
        posted_date = created[:10] if isinstance(created, str) and created else None
        work_locations = api.get("workLocations") or []
        location = None
        if work_locations:
            first = work_locations[0]
            if isinstance(first, dict):
                location = first.get("name") or first.get("city")
            else:
                location = str(first)
    except (KeyError, TypeError, IndexError, json.JSONDecodeError):
        return None

    country = _country_from_freetext(location)
    return JobPostingResult(
        title=title,
        company=company,
        country=country,
        location=location,
        description=None,
        posted_date=posted_date,
        source_url=url,
    )


def _fetch_generic(url: str, html: str) -> JobPostingResult | None:
    """Run the ordered extractor cascade on pre-fetched HTML.

    Returns the first non-None result from JSON-LD → microdata → __NEXT_DATA__.
    Pure: no I/O — callers are responsible for fetching/rendering HTML.
    """
    for extractor in (_extract_jsonld, _extract_microdata, _extract_next_data):
        result = extractor(html, url)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Ashby parser
# ---------------------------------------------------------------------------


def _fetch_ashby(url: str) -> JobPostingResult:
    """Fetch a job posting from Ashby (jobs.ashbyhq.com)."""
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    # Expected: /{slug}/{uuid}[/application]
    if len(path_parts) < 2:
        raise ValueError(f"Failed to fetch job posting: {url}: unexpected URL path")

    slug = path_parts[0]
    uuid = path_parts[1]

    api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        jobs = data["jobs"]
    except RequestException as e:
        raise ValueError(f"Failed to fetch job posting: {url}: {e}") from e
    except (KeyError, IndexError) as e:
        raise ValueError(f"Failed to fetch job posting: {url}: {e}") from e

    # Find the job whose jobUrl contains the UUID; fallback to first if only one
    job = None
    for j in jobs:
        if uuid in j.get("jobUrl", ""):
            job = j
            break
    if job is None and len(jobs) == 1:
        job = jobs[0]
    if job is None:
        raise ValueError(f"Failed to fetch job posting: {url}: job not found in board")

    title = job.get("title", "")
    company = _slug_to_company(slug)
    location = job.get("location") or None
    description = job.get("descriptionPlain") or job.get("descriptionHtml") or None

    published_at = job.get("publishedAt")
    posted_date = published_at[:10] if published_at else None

    # Country: structured field first, then last whitespace token of location
    address_country = None
    try:
        address_country = job["address"]["postalAddress"]["addressCountry"]
    except (KeyError, TypeError):
        pass

    if address_country:
        country = _normalize_country(address_country)
    else:
        country = _country_from_freetext(location)

    return JobPostingResult(
        title=title,
        company=company,
        country=country,
        location=location,
        description=description,
        posted_date=posted_date,
        source_url=url,
    )


# ---------------------------------------------------------------------------
# Greenhouse parser
# ---------------------------------------------------------------------------


def _fetch_greenhouse(url: str) -> JobPostingResult:
    """Fetch a job posting from Greenhouse (boards.greenhouse.io or job-boards.greenhouse.io)."""
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    # Expected: /{token}/jobs/{id}
    if len(path_parts) < 3 or path_parts[1] != "jobs":
        raise ValueError(f"Failed to fetch job posting: {url}: unexpected URL path")

    token = path_parts[0]
    job_id = path_parts[2]

    job_api = (
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?content=true"
    )
    try:
        response = requests.get(job_api, timeout=10)
        response.raise_for_status()
        job = response.json()
    except RequestException as e:
        raise ValueError(f"Failed to fetch job posting: {url}: {e}") from e
    except (KeyError, IndexError) as e:
        raise ValueError(f"Failed to fetch job posting: {url}: {e}") from e

    title = job.get("title", "")
    description = job.get("content") or None
    location_name = (job.get("location") or {}).get("name") or None
    location = location_name

    first_published = job.get("first_published")
    updated_at = job.get("updated_at")
    raw_date = first_published or updated_at
    posted_date = raw_date[:10] if raw_date else None

    # Company: secondary metadata endpoint, fallback to slug
    meta_api = f"https://boards-api.greenhouse.io/v1/boards/{token}"
    try:
        meta_response = requests.get(meta_api, timeout=10)
        meta_response.raise_for_status()
        company = meta_response.json().get("name") or _slug_to_company(token)
    except Exception:
        company = _slug_to_company(token)

    country = _country_from_freetext(location_name)

    return JobPostingResult(
        title=title,
        company=company,
        country=country,
        location=location,
        description=description,
        posted_date=posted_date,
        source_url=url,
    )


# ---------------------------------------------------------------------------
# Greenhouse custom-domain helper
# ---------------------------------------------------------------------------


def _extract_gh_token_from_html(html: str) -> str | None:
    """Extract the Greenhouse board token from an embedded Greenhouse job page.

    Greenhouse injects a script tag of the form:
      <script src="https://boards.greenhouse.io/embed/job_board/js?for=TOKEN">
    """
    match = re.search(r"greenhouse\.io/embed/job_board/js\?for=([^\"&\s]+)", html)
    return match.group(1) if match else None


def _render_and_extract_gh_token(url: str) -> str | None:
    """Render a JS page with headless Chromium and re-run the GH token regex.

    Used as a fallback for React/Next.js SPA custom domains where the
    Greenhouse embed script is injected client-side and absent from static HTML.
    Returns None if rendering fails or the token is still not present.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                rendered_html = page.content()
            finally:
                browser.close()
    except Exception:
        return None

    return _extract_gh_token_from_html(rendered_html)


def _render_page_html(url: str) -> str | None:
    """Render a page with headless Chromium and return the full HTML content.

    UA-spoofed via new_page(user_agent=...) to avoid HeadlessChrome fingerprinting.
    Used as a last-resort rendering step in the generic fallback cascade.
    Returns None if rendering fails for any reason.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=_BROWSER_HEADERS["User-Agent"])
                page.goto(url, wait_until="networkidle", timeout=30000)
                return page.content()
            finally:
                browser.close()
    except Exception:
        return None


def _fetch_greenhouse_custom_domain(url: str) -> JobPostingResult:
    """Fetch a Greenhouse job posted on a company's custom domain.

    Detects the board token from the embedded Greenhouse script tag, then
    delegates to _fetch_greenhouse with the canonical boards.greenhouse.io URL.
    Falls back to a Playwright-rendered DOM scan when the token is absent from
    static HTML and the `browser` optional dependency is installed.
    """
    params = parse_qs(urlparse(url).query)
    job_id = params["gh_jid"][0]

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        html = response.text
    except RequestException as e:
        raise ValueError(f"Failed to fetch job posting: {url}: {e}") from e

    token = _extract_gh_token_from_html(html)

    if token is None and _PLAYWRIGHT_AVAILABLE:
        token = _render_and_extract_gh_token(url)

    if token is None:
        if not _PLAYWRIGHT_AVAILABLE:
            raise ValueError(
                f"Failed to fetch job posting: {url}: could not find Greenhouse "
                f"board token in static HTML; install the 'browser' extra and run "
                f"'playwright install chromium' to support JavaScript-rendered pages "
                f"(pip install runwayMCP[browser])"
            )
        raise ValueError(
            f"Failed to fetch job posting: {url}: could not find Greenhouse board token"
        )

    canonical = f"https://boards.greenhouse.io/{token}/jobs/{job_id}"
    return _fetch_greenhouse(canonical)


def _fetch_lever(url: str) -> JobPostingResult:
    """Fetch a job posting from Lever (jobs.lever.co or lever.co)."""
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    # Expected path: /{company}/{uuid}
    if len(path_parts) < 2:
        raise ValueError(f"Failed to fetch job posting: {url}: unexpected URL path")

    company_slug = path_parts[0]
    uuid = path_parts[1]

    api_url = f"https://api.lever.co/v0/postings/{company_slug}/{uuid}"
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except RequestException as e:
        raise ValueError(f"Failed to fetch job posting: {url}: {e}") from e
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(f"Failed to fetch job posting: {url}: {e}") from e

    title = data.get("text", "")
    company = _slug_to_company(company_slug)
    location = (data.get("categories") or {}).get("location") or None
    country_raw = data.get("country")
    country = _normalize_country(country_raw) if country_raw else None
    description = data.get("descriptionPlain") or None

    created = data.get("createdAt")
    posted_date = (
        datetime.fromtimestamp(int(created) / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )
        if created is not None
        else None
    )

    return JobPostingResult(
        title=title,
        company=company,
        country=country,
        location=location,
        description=description,
        posted_date=posted_date,
        source_url=url,
    )


# ---------------------------------------------------------------------------
# Stub (for example.com contract tests)
# ---------------------------------------------------------------------------


def _stub_result(url: str) -> JobPostingResult:
    """Return a minimal valid result for contract tests using example.com URLs."""
    return JobPostingResult(
        title="Senior Software Engineer",
        company="Acme Corp",
        country="USA",
        location="San Francisco, CA",
        description=(
            "Join our team to build scalable backend services. "
            "You will design and implement high-throughput APIs "
            "used by customers across multiple countries. "
            "Visa sponsorship available for qualified candidates."
        ),
        posted_date="2026-06-01",
        source_url=url,
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def _match_host(host: str) -> Callable | None:
    """Return the parser for a known job board hostname, or None if unsupported."""
    if "ashbyhq" in host:
        return _fetch_ashby
    if "greenhouse.io" in host:
        return _fetch_greenhouse
    if "lever.co" in host:
        return _fetch_lever
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fetch_job_posting(url: str) -> JobPostingResult:
    """Fetch and parse a job posting from a supported job board URL."""
    host = urlparse(url).hostname or ""
    parser = _match_host(host)

    if parser is None:
        # Greenhouse custom domain: gh_jid in query params signals a Greenhouse embed
        if "gh_jid" in parse_qs(urlparse(url).query):
            return _fetch_greenhouse_custom_domain(url)

        # Unknown host — follow HTTP redirects to discover the canonical job board URL
        try:
            response = requests.head(url, timeout=10, allow_redirects=True)
            resolved_url = response.url
            resolved_parser = _match_host(urlparse(resolved_url).hostname or "")
            if resolved_parser is not None:
                return resolved_parser(resolved_url)
            url = resolved_url  # still unknown — attempt generic extraction below
        except RequestException as e:
            raise ValueError(f"Unsupported job board: {host}") from e

        # Generic standards-based fallback on the still-unknown resolved URL
        try:
            get_resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=15)
            get_resp.raise_for_status()
            html = get_resp.text
        except RequestException as e:
            raise ValueError(f"Unsupported job board: {host}") from e

        if _is_bot_challenge(html):
            raise ValueError(
                f"Failed to fetch job posting: {url}: blocked by bot protection"
            )

        result = _fetch_generic(url, html)
        if result is not None:
            return result

        if _PLAYWRIGHT_AVAILABLE:
            rendered = _render_page_html(url)
            if rendered is not None:
                if _is_bot_challenge(rendered):
                    raise ValueError(
                        f"Failed to fetch job posting: {url}: blocked by bot protection"
                    )
                result = _fetch_generic(url, rendered)
                if result is not None:
                    return result

        raise ValueError(f"Unsupported job board: {host}")

    return parser(url)
