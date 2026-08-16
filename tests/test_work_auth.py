"""Tests for tools/work_auth.py — set_work_authorization + the live
country-comparison warning (design D7, spec R12-R14, tasks 3b.1a-3b.1n).

The warning is a COMPARISON between the job's country and the countries the
user has declared, never a job-intrinsic property — recomputed fresh on
every analyze_job call, never persisted (design D7's rejection of storing a
derived value, the same mistake visa_verdict made).
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# R12/SC-24-27: declaration state — never-set vs set-with-zero-countries
# ---------------------------------------------------------------------------


class TestDeclarationState:
    def test_declared_authorizations_returns_none_when_never_set(self, db_path):
        """SC-24's storage-level precondition: nothing declared yet."""
        from tools.work_auth import _declared_authorizations

        assert _declared_authorizations() is None

    def test_sc25_set_writes_the_setting(self, db_path):
        from tools.work_auth import _declared_authorizations, set_work_authorization

        result = set_work_authorization(countries=["USA", "Canada"])

        assert result.success is True
        declared = _declared_authorizations()
        assert declared is not None
        canonicals = {d.canonical for d in declared}
        assert canonicals == {"UNITED STATES", "CANADA"}

    def test_sc26_replaces_not_accumulates(self, db_path):
        """A naive accumulating implementation would keep USA authorized
        after declaring Germany — this proves the full-set replace."""
        from tools.work_auth import _declared_authorizations, set_work_authorization

        set_work_authorization(countries=["USA"])
        set_work_authorization(countries=["Germany"])

        declared = _declared_authorizations()
        canonicals = {d.canonical for d in declared}
        assert canonicals == {"GERMANY"}

    def test_sc27_explicit_empty_list_is_declared_not_unset(self, db_path):
        """A zero-country declaration is a DISTINCT valid state from never
        having called the tool at all — None vs [] must not collapse."""
        from tools.work_auth import _declared_authorizations, set_work_authorization

        result = set_work_authorization(countries=[])

        assert result.success is True
        declared = _declared_authorizations()
        assert declared == []
        assert declared is not None

    def test_set_echoes_raw_and_canonical_forms(self, db_path):
        """Design D7: echoes back what it stored, raw AND canonical form,
        so a misread is visible at the moment of declaration."""
        from tools.work_auth import set_work_authorization

        result = set_work_authorization(countries=["U.S."])

        assert result.countries_raw == ["U.S."]
        assert result.countries_canonical == ["UNITED STATES"]

    def test_set_deduplicates_by_canonical_form(self, db_path):
        """Two raw spellings of the same country must not collide as two
        rows sharing one PRIMARY KEY."""
        from tools.work_auth import _declared_authorizations, set_work_authorization

        result = set_work_authorization(countries=["USA", "United States"])

        assert result.success is True
        declared = _declared_authorizations()
        assert len(declared) == 1
        assert declared[0].canonical == "UNITED STATES"


# ---------------------------------------------------------------------------
# R13/SC-28-31, D7's three outcomes: authorized / warned / undetermined
# ---------------------------------------------------------------------------


class TestCheckWorkAuthorization:
    def test_sc28_declared_country_no_warning(self, db_path):
        from tools.work_auth import DeclaredCountry, _check_work_authorization

        declared = [DeclaredCountry(canonical="UNITED STATES", raw="USA")]
        check = _check_work_authorization("USA", declared)

        assert check.status == "authorized"
        assert check.warning is None

    def test_sc29_undeclared_country_produces_warning(self, db_path):
        from tools.work_auth import DeclaredCountry, _check_work_authorization

        declared = [DeclaredCountry(canonical="UNITED STATES", raw="USA")]
        check = _check_work_authorization("Germany", declared)

        assert check.status == "warned"
        assert check.warning is not None
        assert "Germany" in check.warning
        assert "USA" in check.warning

    def test_sc30_comparison_is_normalized_not_exact_string(self, db_path):
        """A naive exact-string-match implementation would wrongly warn
        here — "USA" and "United States" must canonicalize to the same
        declared country."""
        from tools.work_auth import DeclaredCountry, _check_work_authorization

        declared = [DeclaredCountry(canonical="UNITED STATES", raw="United States")]
        check = _check_work_authorization("USA", declared)

        assert check.status == "authorized"

    def test_undetermined_when_job_country_canonicalizes_to_empty(self, db_path):
        """3b.1j: an uninterpretable country (nothing left after stripping
        whitespace/punctuation) must be its own outcome, not silently
        treated as authorized (a false negative warning) or warned (a false
        positive one)."""
        from tools.work_auth import DeclaredCountry, _check_work_authorization

        declared = [DeclaredCountry(canonical="UNITED STATES", raw="USA")]
        check = _check_work_authorization("...", declared)

        assert check.status == "undetermined"
        assert check.warning is None

    def test_three_outcomes_are_pairwise_distinguishable(self, db_path):
        """3b.1j: "no warning because authorized" and "no warning because
        undetermined" must NOT look identical — status is what makes them
        distinguishable, not merely the presence/absence of `warning`."""
        from tools.work_auth import DeclaredCountry, _check_work_authorization

        declared = [DeclaredCountry(canonical="UNITED STATES", raw="USA")]

        authorized = _check_work_authorization("USA", declared)
        warned = _check_work_authorization("Germany", declared)
        undetermined = _check_work_authorization("...", declared)

        assert {authorized.status, warned.status, undetermined.status} == {
            "authorized",
            "warned",
            "undetermined",
        }
        assert authorized.warning is None
        assert warned.warning is not None
        assert undetermined.warning is None

    def test_no_declared_countries_at_all_produces_warning_not_crash(self, db_path):
        """SC-27's zero-country declaration still must produce a coherent
        comparison result, not an empty-collection crash."""
        from tools.work_auth import _check_work_authorization

        check = _check_work_authorization("USA", [])

        assert check.status == "warned"
        assert check.warning is not None


# ---------------------------------------------------------------------------
# R14/SC-32: corrupt settings store fails distinctly from "never set"
# ---------------------------------------------------------------------------


class TestCorruptSettingsStore:
    def test_sc32_corrupt_store_propagates_valueerror_not_none(
        self, db_path, monkeypatch
    ):
        """_declared_authorizations must RAISE on corruption, not return
        None — returning None would make a broken database indistinguishable
        from "never declared", sending the caller to set_work_authorization,
        which reads (and fails on) the exact same broken store."""
        import tools.work_auth as wa

        def _boom(path=None, *, write=False):
            raise ValueError("Database is corrupt or could not be read")

        monkeypatch.setattr(wa, "connect", _boom)

        with pytest.raises(ValueError):
            wa._declared_authorizations()


class TestUninterpretableDeclarations:
    """The declaration side must refuse garbage the same way the job side does.

    _check_work_authorization already returns "undetermined" rather than
    guessing at a country it cannot interpret. Without the same guard on the
    way in, a blank entry became a stored declaration: it satisfied
    analyze_job's precondition as though the user had declared something, and
    every later mismatch rendered the empty raw text back at them.
    """

    def test_blank_only_declaration_is_rejected(self, db_path):
        from tools.work_auth import set_work_authorization, _declared_authorizations

        result = set_work_authorization(countries=["   "])

        assert result.success is False
        assert result.error == "invalid_input"
        # ...and nothing was stored, so the precondition still reports unset
        assert _declared_authorizations() is None

    def test_blank_entries_dropped_but_real_ones_kept(self, db_path):
        from tools.work_auth import set_work_authorization, _declared_authorizations

        result = set_work_authorization(countries=["", "United States", "  "])

        assert result.success is True
        assert result.countries_raw == ["United States"]
        declared = _declared_authorizations()
        assert [d.canonical for d in declared] == ["UNITED STATES"]

    def test_empty_list_is_still_a_valid_declaration(self, db_path):
        """SC-27: "I may work nowhere" differs from "I never said"."""
        from tools.work_auth import set_work_authorization, _declared_authorizations

        result = set_work_authorization(countries=[])

        assert result.success is True
        assert _declared_authorizations() == []

    def test_warning_never_renders_an_empty_country_name(self, db_path):
        from tools.resumes import save_resume_version
        from tools.work_auth import set_work_authorization
        from tools.analyze import analyze_job

        save_resume_version(content="cv", label="Base", parent_id=None)
        set_work_authorization(countries=["United States"])

        check = analyze_job(
            title="T", company="C", country="Germany"
        ).work_authorization

        assert check.status == "warned"
        assert "(   )" not in (check.warning or "")
        assert "United States" in check.warning


class TestWriteFailuresStayEnvelopes:
    def test_sqlite_error_from_the_insert_returns_an_envelope(self, db_path):
        """connect() translates errors from OPENING the database, but not ones
        raised by statements inside the yielded block — so this module's own
        INSERT can still throw past a bare `except ValueError`."""
        from tools.work_auth import set_work_authorization, _MARKER_CANONICAL

        result = set_work_authorization(countries=[_MARKER_CANONICAL])

        assert result.success is False
        assert result.error in {"write_error", "invalid_input"}
        assert result.message
