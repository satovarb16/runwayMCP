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

    with patch(
        "tools.uscis_cache.requests.get", return_value=mock_response
    ) as mock_get:
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

    with patch(
        "tools.uscis_cache.requests.get", return_value=mock_response
    ) as mock_get:
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


# ---------------------------------------------------------------------------
# SC-04: CACHE_URL does not match FY pattern — no HTTP, WARNING to stderr
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_sc04_no_fy_pattern_in_cache_url(tmp_path, monkeypatch, capsys):
    """SC-04: When CACHE_URL has no h1b_datahubexport-<year>.csv pattern,
    refresh_to_latest_fy() must print a WARNING to stderr, never call
    requests.get, and leave CACHE_PATH and _INDEX unchanged."""
    import tools.uscis_cache as uc
    from tools.uscis_cache import refresh_to_latest_fy

    cache_file = tmp_path / "uscis_h1b.csv"
    sentinel_index = {"fake": "index"}
    monkeypatch.setattr(uc, "CACHE_URL", "https://example.com/no-year-here.csv")
    monkeypatch.setattr(uc, "CACHE_PATH", cache_file)
    monkeypatch.setattr(uc, "_INDEX", sentinel_index)

    with patch("tools.uscis_cache.requests.get") as mock_get:
        refresh_to_latest_fy()

    mock_get.assert_not_called()
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert not cache_file.exists(), "CACHE_PATH must not be created"
    assert uc._INDEX is sentinel_index, "_INDEX must be left unchanged"


# ---------------------------------------------------------------------------
# SC-01: Newer FY available (HTTP 200) — cache overwritten, _INDEX reset
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_sc01_newer_fy_available(tmp_path, monkeypatch, capsys):
    """SC-01: HTTP 200 for FY+1 candidate overwrites CACHE_PATH, resets
    _INDEX to None, and prints the success message to stderr."""
    import tools.uscis_cache as uc
    from tools.uscis_cache import refresh_to_latest_fy

    cache_file = tmp_path / "uscis_h1b.csv"
    cache_file.write_bytes(b"old data")
    fake_new_bytes = b"new fy2025 csv data"

    sentinel_index = {"old": "index"}
    monkeypatch.setattr(
        uc,
        "CACHE_URL",
        "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-2024.csv",
    )
    monkeypatch.setattr(uc, "CACHE_PATH", cache_file)
    monkeypatch.setattr(uc, "_INDEX", sentinel_index)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = fake_new_bytes

    with patch("tools.uscis_cache.requests.get", return_value=mock_response):
        refresh_to_latest_fy()

    assert cache_file.read_bytes() == fake_new_bytes, "CACHE_PATH must be overwritten"
    assert uc._INDEX is None, "_INDEX must be reset to None"
    captured = capsys.readouterr()
    assert "FY2025" in captured.err
    assert "Updated" in captured.err


# ---------------------------------------------------------------------------
# SC-05: CACHE_PATH does not yet exist — created on successful probe
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_sc05_cache_path_absent(tmp_path, monkeypatch, capsys):
    """SC-05: When CACHE_PATH does not exist and probe returns 200,
    the parent dirs and the file must be created."""
    import tools.uscis_cache as uc
    from tools.uscis_cache import refresh_to_latest_fy

    # Point to a path that does NOT exist yet (nested, no pre-created dir)
    cache_file = tmp_path / "sub" / "uscis_h1b.csv"
    assert not cache_file.exists()
    fake_bytes = b"brand new csv"

    monkeypatch.setattr(
        uc,
        "CACHE_URL",
        "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-2024.csv",
    )
    monkeypatch.setattr(uc, "CACHE_PATH", cache_file)
    monkeypatch.setattr(uc, "_INDEX", None)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = fake_bytes

    with patch("tools.uscis_cache.requests.get", return_value=mock_response):
        refresh_to_latest_fy()

    assert cache_file.exists(), "CACHE_PATH must be created"
    assert cache_file.read_bytes() == fake_bytes
    assert uc._INDEX is None
    captured = capsys.readouterr()
    assert "FY2025" in captured.err


# ---------------------------------------------------------------------------
# SC-06: _INDEX is not None before success — reset after write
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_sc06_index_reset_after_refresh(tmp_path, monkeypatch, capsys):
    """SC-06: A pre-built _INDEX must be set to None after a successful probe,
    and a subsequent get_employer_index() call must rebuild from the new file."""
    import tools.uscis_cache as uc
    from tools.uscis_cache import refresh_to_latest_fy, get_employer_index

    cache_file = tmp_path / "uscis_h1b.csv"
    cache_file.write_bytes(b"old")
    new_csv = _MINIMAL_CSV.encode("utf-8")

    monkeypatch.setattr(
        uc,
        "CACHE_URL",
        "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-2024.csv",
    )
    monkeypatch.setattr(uc, "CACHE_PATH", cache_file)
    # Pre-populate _INDEX with a stale object
    monkeypatch.setattr(uc, "_INDEX", {"stale": "data"})

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = new_csv

    with patch("tools.uscis_cache.requests.get", return_value=mock_response):
        refresh_to_latest_fy()

    assert uc._INDEX is None, "_INDEX must be None after successful refresh"

    # Now call get_employer_index() — it should rebuild from the new file
    rebuilt = get_employer_index()
    assert isinstance(rebuilt, dict)
    assert len(rebuilt) > 0, "Rebuilt index must not be empty"


# ---------------------------------------------------------------------------
# SC-02: Non-200 HTTP response — cache and index untouched, WARNING to stderr
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_sc02_non_200_response(tmp_path, monkeypatch, capsys):
    """SC-02: HTTP 404 must leave CACHE_PATH unmodified, leave _INDEX
    unchanged, and print a WARNING to stderr naming FY2024 and FY2025."""
    import tools.uscis_cache as uc
    from tools.uscis_cache import refresh_to_latest_fy

    cache_file = tmp_path / "uscis_h1b.csv"
    original_bytes = b"untouched old cache"
    cache_file.write_bytes(original_bytes)
    sentinel_index = {"keep": "me"}

    monkeypatch.setattr(
        uc,
        "CACHE_URL",
        "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-2024.csv",
    )
    monkeypatch.setattr(uc, "CACHE_PATH", cache_file)
    monkeypatch.setattr(uc, "_INDEX", sentinel_index)

    mock_response = MagicMock()
    mock_response.status_code = 404

    with patch("tools.uscis_cache.requests.get", return_value=mock_response):
        refresh_to_latest_fy()

    assert cache_file.read_bytes() == original_bytes, "CACHE_PATH must be unchanged"
    assert uc._INDEX is sentinel_index, "_INDEX must be unchanged"
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "FY2024" in captured.err
    assert "FY2025" in captured.err


# ---------------------------------------------------------------------------
# SC-03: Network exception during probe — cache and index untouched, WARNING
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_sc03_network_error(tmp_path, monkeypatch, capsys):
    """SC-03: A requests.RequestException during the probe must leave
    CACHE_PATH and _INDEX unchanged and print a WARNING to stderr with
    FY2024 and the error reason."""
    import tools.uscis_cache as uc
    from tools.uscis_cache import refresh_to_latest_fy

    cache_file = tmp_path / "uscis_h1b.csv"
    original_bytes = b"existing cache"
    cache_file.write_bytes(original_bytes)
    sentinel_index = {"stable": "index"}

    monkeypatch.setattr(
        uc,
        "CACHE_URL",
        "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-2024.csv",
    )
    monkeypatch.setattr(uc, "CACHE_PATH", cache_file)
    monkeypatch.setattr(uc, "_INDEX", sentinel_index)

    with patch(
        "tools.uscis_cache.requests.get",
        side_effect=requests.RequestException("timeout"),
    ):
        refresh_to_latest_fy()

    assert cache_file.read_bytes() == original_bytes, "CACHE_PATH must be unchanged"
    assert uc._INDEX is sentinel_index, "_INDEX must be unchanged"
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "FY2024" in captured.err
    assert "timeout" in captured.err
