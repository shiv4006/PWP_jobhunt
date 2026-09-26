"""Parsers + prefilter, run against the fixtures in their native ATS shapes.

No network, no API key. This is the suite that catches the two bugs that cost
me an evening each: Lever's epoch-milliseconds timestamps, and a bare `sde`
regex that silently matches nothing.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobhunt import mock
from jobhunt.fetch import (Job, hydrate, parse_ashby, parse_greenhouse, parse_lever,
                           parse_smartrecruiters, smartrecruiters_description, strip_html)
from jobhunt import fetch
from jobhunt.mock import fetch_all_mock
from jobhunt.prefilter import prefilter

CONFIG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml")
                        .read_text(encoding="utf-8"))
FILTERS = CONFIG["filters"]


# ------------------------------------------------------------- strip_html ---

def test_strip_html_unescapes_twice():
    """Greenhouse ships HTML-entity-escaped HTML: unescape, strip, unescape."""
    raw = "&lt;p&gt;Go &amp;amp; Java&lt;/p&gt;"
    assert strip_html(raw) == "Go & Java"


def test_strip_html_turns_block_tags_into_newlines():
    out = strip_html("<p>One</p><p>Two</p><ul><li>a</li><li>b</li></ul>")
    assert "One" in out and "Two" in out and "a" in out and "b" in out
    assert "<" not in out


def test_strip_html_handles_none_and_empty():
    assert strip_html(None) == ""
    assert strip_html("") == ""


# ---------------------------------------------------------------- parsers ---

def test_greenhouse_maps_every_field():
    jobs = parse_greenhouse("acme-edge", "Acme Edge", mock.GREENHOUSE["acme-edge"])
    j = next(j for j in jobs if j.title.startswith("Software Engineer II"))
    assert j.job_id == "greenhouse:acme-edge:5501001"
    assert j.ats == "greenhouse"
    assert j.company == "Acme Edge"
    assert j.location == "Bangalore, India"
    assert j.url.startswith("https://boards.greenhouse.io/")
    assert "distributed services" in j.description


def test_lever_concatenates_description_lists_and_additional():
    """The requirements live in lists[], not descriptionPlain. Drop the
    concatenation and every Lever job looks unqualified."""
    jobs = parse_lever("quantstack", "QuantStack", mock.LEVER["quantstack"])
    j = next(j for j in jobs if j.title == "Backend Engineer (Go)")
    assert "market data pipeline" in j.description      # descriptionPlain
    assert "Requirements" in j.description              # lists[].text
    assert "2-5 years backend experience" in j.description  # lists[].content
    assert "No take-home" in j.description              # additionalPlain


def test_lever_createdAt_is_epoch_milliseconds():
    """1.7e12 is milliseconds. Reading it as seconds dates the post to 1970
    and the freshness filter eats the whole board without a word."""
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).date()
    jobs = parse_lever("quantstack", "QuantStack", mock.LEVER["quantstack"])
    j = next(j for j in jobs if j.title == "Backend Engineer (Go)")
    assert j.posted_at == two_days_ago.isoformat()


def test_ashby_skips_unlisted_drafts():
    jobs = parse_ashby("helioscale", "Helioscale", mock.ASHBY["helioscale"])
    assert all("unlisted" not in j.url for j in jobs)
    assert len(jobs) == 2   # 3 postings, one isListed: false


def test_ashby_reads_compensation_and_html_fallback():
    jobs = parse_ashby("helioscale", "Helioscale", mock.ASHBY["helioscale"])
    networking = next(j for j in jobs if j.title == "Software Engineer, Networking")
    assert networking.salary == "₹32L – ₹48L"
    ds = next(j for j in jobs if j.title == "Data Scientist, Growth")
    assert "Causal inference" in ds.description   # descriptionHtml fallback


def test_smartrecruiters_maps_fields_and_cleans_location():
    jobs = parse_smartrecruiters("kitepay", "Kitepay", mock.SMARTRECRUITERS["kitepay"])
    j = next(j for j in jobs if j.title == "Full Stack Developer")
    assert j.job_id == "smartrecruiters:kitepay:744000100000001"
    assert j.ats == "smartrecruiters"
    assert j.location == "Hyderabad, India"          # not "Hyderabad, , India"
    assert j.url == "https://jobs.smartrecruiters.com/Kitepay/744000100000001"
    assert j.description == ""                        # list payload has no JD
    sap = next(j for j in jobs if j.title == "SAP ABAP Developer")
    assert sap.location == "Pune, MH, India (Remote)"


def test_smartrecruiters_skips_non_public_postings():
    body = {"content": [dict(mock.SMARTRECRUITERS["kitepay"]["content"][0],
                             visibility="INTERNAL")]}
    assert parse_smartrecruiters("kitepay", "Kitepay", body) == []


def test_smartrecruiters_description_joins_sections_but_not_the_company_blurb():
    text = smartrecruiters_description(mock.SMARTRECRUITERS_DETAIL["744000100000001"])
    assert "Next.js" in text and "0-2 years" in text and "Hybrid" in text
    assert "UPI rails" not in text
    assert smartrecruiters_description(None) == ""


class _FakeResp:
    def __init__(self, body, status=200):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class _FakeSession:
    """Answers from a {url: body} map and records every call."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, **_):
        self.calls.append((url, dict(params or {})))
        body = self.routes(url, params or {}) if callable(self.routes) else self.routes.get(url)
        return _FakeResp(body) if body is not None else _FakeResp({}, 404)


def test_smartrecruiters_pages_until_totalFound():
    postings = [{"id": str(i), "name": "Software Engineer", "visibility": "PUBLIC",
                 "location": {"fullLocation": "Bengaluru, KA, India"}} for i in range(230)]

    def routes(url, params):
        off, lim = params["offset"], params["limit"]
        return {"totalFound": len(postings), "content": postings[off:off + lim]}

    sess = _FakeSession(routes)
    jobs = fetch.fetch_board("smartrecruiters", "bigco", "BigCo", session=sess, country="in")
    assert len(jobs) == 230
    assert [p["offset"] for _, p in sess.calls] == [0, 100, 200]
    assert all(p["country"] == "in" for _, p in sess.calls)


def test_hydrate_fetches_only_missing_descriptions(monkeypatch):
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    detail = "https://api.smartrecruiters.com/v1/companies/kitepay/postings/744000100000001"
    sess = _FakeSession({detail: mock.SMARTRECRUITERS_DETAIL["744000100000001"]})
    sr = parse_smartrecruiters("kitepay", "Kitepay", mock.SMARTRECRUITERS["kitepay"])[:1]
    gh = parse_greenhouse("acme-edge", "Acme Edge", mock.GREENHOUSE["acme-edge"])[:1]

    assert hydrate(sr + gh, session=sess) == 1
    assert "Next.js" in sr[0].description
    assert [u for u, _ in sess.calls] == [detail]      # greenhouse already had its JD


def test_hydrate_survives_a_dead_detail_endpoint(monkeypatch):
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    sr = parse_smartrecruiters("kitepay", "Kitepay", mock.SMARTRECRUITERS["kitepay"])[:1]
    assert hydrate(sr, session=_FakeSession({})) == 0
    assert sr[0].description == ""


def test_every_parser_sets_a_direct_apply_link():
    gh = parse_greenhouse("acme-edge", "Acme Edge", mock.GREENHOUSE["acme-edge"])[0]
    assert gh.apply_url == "https://job-boards.greenhouse.io/embed/job_app?for=acme-edge&token=5501001"
    lv = parse_lever("quantstack", "QuantStack", mock.LEVER["quantstack"])
    assert lv[0].apply_url.endswith("/apply")            # the API's own applyUrl
    assert lv[1].apply_url == lv[1].url + "/apply"       # no applyUrl -> hostedUrl + /apply
    ab = parse_ashby("helioscale", "Helioscale", mock.ASHBY["helioscale"])[0]
    assert ab.apply_url == ab.url + "/application"
    sr = parse_smartrecruiters("kitepay", "Kitepay", mock.SMARTRECRUITERS["kitepay"])[0]
    assert sr.apply_url == "https://jobs.smartrecruiters.com/Kitepay/744000100000001?oga=true"


def test_hydrate_takes_the_detail_payloads_own_links(monkeypatch):
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    detail = "https://api.smartrecruiters.com/v1/companies/kitepay/postings/744000100000001"
    body = dict(mock.SMARTRECRUITERS_DETAIL["744000100000001"],
                postingUrl="https://jobs.smartrecruiters.com/Kitepay/744000100000001-full-stack",
                applyUrl="https://jobs.smartrecruiters.com/Kitepay/744000100000001-full-stack?oga=true")
    sr = parse_smartrecruiters("kitepay", "Kitepay", mock.SMARTRECRUITERS["kitepay"])[:1]
    hydrate(sr, session=_FakeSession({detail: body}))
    assert sr[0].url == body["postingUrl"]
    assert sr[0].apply_url == body["applyUrl"]


def test_job_ids_are_globally_unique_and_namespaced():
    jobs = fetch_all_mock()
    ids = [j.job_id for j in jobs]
    assert len(ids) == len(set(ids))
    assert all(re.match(r"^(greenhouse|lever|ashby|smartrecruiters):[^:]+:.+$", i) for i in ids)


def test_parsers_take_decoded_json_not_a_response():
    """Parsers are pure: body in, list[Job] out. That is what makes --mock
    exercise the real code path instead of a second implementation."""
    assert parse_greenhouse("x", "X", {}) == []
    assert parse_lever("x", "X", []) == []
    assert parse_ashby("x", "X", {}) == []
    assert parse_smartrecruiters("x", "X", {}) == []


# -------------------------------------------------------------- prefilter ---

@pytest.mark.parametrize("title", [
    "Software Engineer II, Distributed Systems",
    "Software Development Engineer, Core Infra",
    "Backend Engineer (Go)",
    "Site Reliability Engineer",
    "SDE II",
    # "Developer" wording and the profile's own target titles
    "Backend Developer",
    "Full Stack Developer",
    "Full-Stack Engineer",
    "Frontend Engineer",
    "Node.js Developer",
    "Software Developer - Java",
    "SDE-1",
    "Associate Software Development Engineer (SDE)",
])
def test_include_titles_match_real_titles(title):
    inc = FILTERS["include_titles"]
    assert any(re.search(p, title, re.I) for p in inc), title


def test_bare_sde_regex_does_not_match_the_spelled_out_title():
    """The bug: `sde` looks like it covers "Software Development Engineer".
    It does not — they share no substring. \\bsde\\b plus the spelled-out
    variant is why both titles survive the filter."""
    assert not re.search(r"\bsde\b", "Software Development Engineer", re.I)
    assert re.search(r"\bsde\b", "SDE II", re.I)
    inc = FILTERS["include_titles"]
    assert any(re.search(p, "Software Development Engineer, Core Infra", re.I) for p in inc)


@pytest.mark.parametrize("title", [
    "Staff Software Engineer, Storage",       # too senior
    "Engineering Manager, Platform",          # management track
    "Enterprise Account Executive",           # wrong function
    "Data Scientist, Growth",                 # wrong discipline
    "Sr. Software Engineer",                  # too senior, abbreviated
    "Lead Site Reliability Engineer",         # too senior
    "SDE III - Data Engineering",             # level 3+
    "Software Engineer 4",                    # level 3+
    "SAP ABAP Developer",                     # enterprise-platform role
    "Software Development Engineer in Test",  # QA track
    "iOS Developer",                          # wrong discipline
    "Associate Solutions Engineer, Okta",     # presales
    "Application Security Engineer",          # security track
    "Network Engineer II",                    # network ops, not software
    "IN_RBIC_Senior Engineer_Application Engineer",  # Bosch-style underscores
])
def test_junk_titles_are_rejected(title):
    inc, exc = FILTERS["include_titles"], FILTERS["exclude_titles"]
    included = any(re.search(p, title, re.I) for p in inc)
    excluded = any(re.search(p, title, re.I) for p in exc)
    assert excluded or not included, f"{title!r} would have survived"


def test_full_mock_funnel_keeps_only_the_real_matches():
    kept = prefilter(fetch_all_mock(), FILTERS)
    titles = sorted(j.title for j in kept)
    assert titles == [
        "Backend Engineer (Go)",
        "Backend Engineer, Cards",                # London, but sponsors a visa
        "Frontend Engineer, Design Systems",
        "Full Stack Developer",
        "Site Reliability Engineer",
        "Software Development Engineer, Core Infra",
        "Software Engineer II, Distributed Systems",
        "Software Engineer, Networking",
    ]


def test_stale_posting_is_dropped_by_freshness_gate():
    kept = prefilter(fetch_all_mock(), FILTERS)
    assert not any("Senior Software Engineer, Platform" == j.title for j in kept)


def test_wrong_city_dropped_but_remote_kept():
    kept = prefilter(fetch_all_mock(), FILTERS)
    assert not any("San Francisco" in (j.location or "") for j in kept)
    assert any("Remote" in (j.location or "") for j in kept)


def test_allow_remote_is_what_lets_an_out_of_region_remote_role_through():
    """"Remote (India)" already matches the `india` location, so it is the
    wrong fixture for this. Use a remote role that names no allowed city."""
    remote = Job(job_id="lever:x:1", ats="lever", company="X",
                 title="Backend Engineer", location="Remote - Global",
                 url="https://example.com", description="Go")

    kept_on = prefilter([remote], dict(FILTERS, allow_remote=True))
    kept_off = prefilter([remote], dict(FILTERS, allow_remote=False))

    assert len(kept_on) == 1
    assert kept_off == []


def test_empty_filters_keep_everything():
    jobs = fetch_all_mock()
    assert len(prefilter(jobs, {})) == len(jobs)
