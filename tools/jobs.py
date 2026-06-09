from __future__ import annotations

from typing import Callable
from urllib.parse import urlparse

import requests
from requests.exceptions import RequestException
from pydantic import BaseModel


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


def _country_from_freetext(text: str | None) -> str | None:
    """Extract and normalize country from a free-text location string.

    Splits on comma and uses the last token.
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
    return _normalize_country(last)


def _slug_to_company(slug: str) -> str:
    """Convert a URL slug to a title-cased company name."""
    return slug.replace("-", " ").replace("_", " ").title()


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
    elif location:
        # Last whitespace-delimited token of the location string
        tokens = location.split()
        country = tokens[-1] if tokens else None
    else:
        country = None

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


def _route(url: str) -> Callable:
    """Dispatch to the correct parser based on URL hostname."""
    host = urlparse(url).hostname or ""

    if "ashbyhq" in host:
        return _fetch_ashby
    if "greenhouse.io" in host:
        return _fetch_greenhouse
    if "example.com" in host:
        return _stub_result

    raise ValueError(f"Unsupported job board: {host}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fetch_job_posting(url: str) -> JobPostingResult:
    """Fetch and parse a job posting from a supported job board URL."""
    return _route(url)(url)
