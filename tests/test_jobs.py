import pytest
from pydantic import ValidationError

from tools.jobs import JobPostingResult, fetch_job_posting


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
