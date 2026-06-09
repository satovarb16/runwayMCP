import pytest
import requests
from unittest.mock import patch, MagicMock


# Minimal CSV with the columns the parser expects
_MINIMAL_CSV = (
    "Employer,Initial Approval,Initial Denial\n"
    "Google LLC,50,0\n"
    "Meta Platforms Inc,30,2\n"
)


@pytest.mark.contract
def test_first_call_downloads(tmp_path, monkeypatch):
    """When cache file is absent, get_employer_index() must call requests.get once
    and write the CSV to the cache path, returning a non-empty dict."""
    from tools.uscis_cache import get_employer_index
    import tools.uscis_cache as uc

    cache_file = tmp_path / "uscis_h1b.csv"
    monkeypatch.setattr(uc, "CACHE_PATH", cache_file)
    monkeypatch.setattr(uc, "_INDEX", None)

    mock_response = MagicMock()
    mock_response.content = _MINIMAL_CSV.encode("utf-8")
    mock_response.raise_for_status = MagicMock()

    with patch("tools.uscis_cache.requests.get", return_value=mock_response) as mock_get:
        result = get_employer_index()

    mock_get.assert_called_once()
    assert cache_file.exists()
    assert isinstance(result, dict)
    assert len(result) > 0


@pytest.mark.contract
def test_second_call_no_http(tmp_path, monkeypatch):
    """Calling get_employer_index() twice must issue only one HTTP request
    (singleton caching)."""
    from tools.uscis_cache import get_employer_index
    import tools.uscis_cache as uc

    cache_file = tmp_path / "uscis_h1b.csv"
    monkeypatch.setattr(uc, "CACHE_PATH", cache_file)
    monkeypatch.setattr(uc, "_INDEX", None)

    mock_response = MagicMock()
    mock_response.content = _MINIMAL_CSV.encode("utf-8")
    mock_response.raise_for_status = MagicMock()

    with patch("tools.uscis_cache.requests.get", return_value=mock_response) as mock_get:
        get_employer_index()
        get_employer_index()

    mock_get.assert_called_once()


@pytest.mark.contract
def test_disk_cache_skips_download(tmp_path, monkeypatch):
    """When the cache file already exists on disk, no HTTP request must be made."""
    from tools.uscis_cache import get_employer_index
    import tools.uscis_cache as uc

    cache_file = tmp_path / "uscis_h1b.csv"
    cache_file.write_text(_MINIMAL_CSV, encoding="utf-8")
    monkeypatch.setattr(uc, "CACHE_PATH", cache_file)
    monkeypatch.setattr(uc, "_INDEX", None)

    with patch("tools.uscis_cache.requests.get") as mock_get:
        result = get_employer_index()

    mock_get.assert_not_called()
    assert isinstance(result, dict)
    assert len(result) > 0


@pytest.mark.contract
def test_download_failure_returns_empty(tmp_path, monkeypatch):
    """When requests.get raises RequestException, get_employer_index() must
    return {} without propagating an exception."""
    from tools.uscis_cache import get_employer_index
    import tools.uscis_cache as uc

    cache_file = tmp_path / "uscis_h1b.csv"
    monkeypatch.setattr(uc, "CACHE_PATH", cache_file)
    monkeypatch.setattr(uc, "_INDEX", None)

    with patch(
        "tools.uscis_cache.requests.get",
        side_effect=requests.RequestException("timeout"),
    ):
        result = get_employer_index()

    assert result == {}
