import pytest


@pytest.fixture
def visa_stub_payload() -> dict:
    """Stub payload matching the VisaResult output contract.

    Fields: company, total_filings, approval_rate, verdict, source
    verdict is one of: green | yellow | red
    approval_rate is a float in [0.0, 1.0]
    total_filings is a non-negative integer
    """
    return {
        "company": "Google",
        "total_filings": 4821,
        "approval_rate": 0.97,
        "verdict": "green",
        "source": "DOL LCA (stub)",
    }


@pytest.fixture
def job_stub_payload() -> dict:
    """Stub payload matching the JobPostingResult output contract.

    Fields: title, company, country, location, description, posted_date, source_url
    country is load-bearing: drives Claude's optional visa-check decision.
    """
    return {
        "title": "Senior Software Engineer",
        "company": "Google",
        "country": "USA",
        "location": "Mountain View, CA",
        "description": (
            "Join the Google infrastructure team to design and build "
            "large-scale distributed systems. You will work on core "
            "platform services used by billions of users worldwide. "
            "H-1B sponsorship available for qualified candidates."
        ),
        "posted_date": "2026-06-01",
        "source_url": "https://careers.google.com/jobs/results/123456789",
    }
