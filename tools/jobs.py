from pydantic import BaseModel


class JobPostingResult(BaseModel):
    title: str
    company: str
    country: str
    location: str | None = None
    description: str
    posted_date: str | None = None
    source_url: str


def fetch_job_posting(url: str) -> JobPostingResult:
    """Fetch and parse a job posting from a URL (stub)."""
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
