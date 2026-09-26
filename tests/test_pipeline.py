"""Location + JD gates, the all-matches digest, and the never-lose-a-match
store. No network, no API key: the CLI tests run --mock --scorer keyword with
the mailer stubbed out.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobhunt import cli, digest, mailer
from jobhunt.fetch import Job
from jobhunt.prefilter import (classify_location, jd_filter, offers_sponsorship,
                               prefilter, years_required)
from jobhunt.store import Store

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
FILTERS = CONFIG["filters"]


def job(title="Software Engineer", location="Bengaluru, India", description="Go, Postgres.",
        jid="greenhouse:x:1", **kw) -> Job:
    return Job(job_id=jid, ats=jid.split(":")[0], company="X", title=title,
               location=location, url="https://example.com/j/1", description=description, **kw)


# ------------------------------------------------------------- location ---

@pytest.mark.parametrize("location,title,expected", [
    ("Bengaluru, India", "Software Engineer", "home"),
    ("Remote (India)", "Software Engineer", "home"),
    ("Remote", "Software Engineer, Infrastructure (Bengaluru)", "home"),   # city in title
    ("Remote - Worldwide", "Software Engineer", "remote"),
    ("Remote, APAC", "Software Engineer", "remote"),
    ("Remote", "Software Engineer", "remote"),                            # no region named
    ("Remote - US", "Software Engineer", "restricted"),
    ("Remote, Canada; Remote, United States", "Backend Engineer", "restricted"),
    ("Ankara, Türkiye - Remote", "Software Engineer", "restricted"),
    ("Pakistan", "Cloud Engineer (Remote, Full-Time)", "restricted"),     # remote in title only
    ("Belgrade, Serbia", "Software Engineer - Distributed Data Systems", "abroad"),
    ("Indianapolis, IN", "Software Engineer", "abroad"),                   # not "india"
    ("London, UK", "Backend Engineer", "abroad"),
])
def test_classify_location(location, title, expected):
    assert classify_location(job(title=title, location=location), FILTERS) == expected


def test_distributed_in_the_title_is_not_a_remote_role():
    """The old bug: "distributed" was a remote hint and the title was searched,
    so every Distributed Systems role anywhere on earth passed the gate."""
    j = job(title="Software Engineer - Distributed Data Systems", location="Belgrade, Serbia")
    assert prefilter([j], FILTERS) == []


def test_remote_pinned_to_another_country_is_dropped():
    assert prefilter([job(location="Remote - US")], FILTERS) == []


def test_bare_remote_pinned_by_the_jd_is_dropped_but_open_one_is_kept():
    pinned = job(location="Remote", description="You must be authorized to work in the United States.")
    open_ = job(location="Remote", jid="greenhouse:x:2",
                description="Async team across 30 countries. Go and Postgres.")
    assert prefilter([pinned, open_], FILTERS) == [open_]


def test_abroad_needs_sponsorship_in_the_jd():
    yes = job(location="London, UK", description="Visa sponsorship and relocation support available.")
    no = job(location="London, UK", jid="greenhouse:x:2",
             description="Great team. We are unable to sponsor visas for this role.")
    silent = job(location="Berlin, Germany", jid="greenhouse:x:3", description="Great team.")
    assert prefilter([yes, no, silent], FILTERS) == [yes]


def test_abroad_check_can_be_switched_off():
    silent = job(location="Berlin, Germany", description="Great team.")
    assert prefilter([silent], dict(FILTERS, abroad_needs_sponsorship=False)) == [silent]


@pytest.mark.parametrize("text,expected", [
    ("We offer visa sponsorship for this role.", True),
    ("Relocation assistance is provided.", True),
    ("We will sponsor work visas for the right candidate.", True),
    ("We do not offer visa sponsorship.", False),
    ("Relocation assistance available, but we cannot sponsor a visa.", False),  # no wins
    ("Candidates must be authorized to work in Canada.", False),
    ("Nothing about it either way.", False),
    ("", False),
])
def test_offers_sponsorship(text, expected):
    assert offers_sponsorship(text) is expected


# ------------------------------------------------------------ experience ---

@pytest.mark.parametrize("text,expected", [
    ("5+ years of experience building APIs", 5),
    ("3-5 years of professional software experience", 3),
    ("Experience: 6+ years in backend", 6),
    ("2+ years of React experience and 4+ years of backend experience", 4),
    ("Minimum 2 yrs of hands-on experience", 2),
    ("Founded 12 years ago, we serve 3M merchants.", None),   # no "experience"
    ("Since 2015 years have flown by", None),
    ("", None),
])
def test_years_required(text, expected):
    assert years_required(text) == expected


def test_jd_asking_for_too_many_years_is_dropped():
    heavy = job(description="6+ years of professional experience with Go.")
    fits = job(jid="greenhouse:x:2", description="2+ years of experience with Node.js.")
    assert prefilter([heavy, fits], FILTERS) == [fits]
    assert prefilter([heavy], dict(FILTERS, max_years_required=None)) == [heavy]


def test_jobs_without_a_jd_wait_for_jd_filter():
    """SmartRecruiters lists carry no JD, so prefilter cannot prove sponsorship
    yet. It keeps the job; jd_filter decides once hydrate has filled it."""
    abroad = job(location="London, UK", description="", jid="smartrecruiters:x:1")
    assert prefilter([abroad], FILTERS) == [abroad]

    kept, dropped = jd_filter([abroad], FILTERS)            # hydrate failed: still empty
    assert kept == [] and dropped == [abroad]
    assert abroad.reason.startswith("filtered: abroad")

    abroad.description = "We offer visa sponsorship and relocation assistance."
    abroad.reason = None
    assert jd_filter([abroad], FILTERS) == ([abroad], [])


# ---------------------------------------------------------------- digest ---

def _matches(n: int, drafted: int) -> list[Job]:
    out = []
    for i in range(n):
        j = job(title=f"Engineer {i}", jid=f"lever:x:{i}", score=9 - i * 0.1,
                reason=f"reason {i}", apply_url=f"https://jobs.lever.co/x/{i}/apply")
        if i < drafted:
            j.draft = {"fit_summary": "fits", "tailored_bullets": ["b"], "gaps": [],
                       "cover_note": "note", "questions_to_ask": []}
        out.append(j)
    return out


def test_digest_lists_every_match_with_its_apply_link_not_just_the_drafted_ones():
    jobs = _matches(12, drafted=5)
    subject, doc = digest.build(jobs, scanned=500, candidates=40, stats={})
    assert subject.startswith("12 jobs worth your time")
    for j in jobs:
        assert j.apply_url in doc
    assert doc.count("Cover note") == 5                     # kits only for the top 5
    assert "More matches (7)" in doc


def test_digest_falls_back_to_the_posting_url_and_shows_both_links():
    j = job(score=8.0, apply_url=None)
    _, doc = digest.build([j], 1, 1, {})
    assert 'href="https://example.com/j/1"' in doc and "View posting" not in doc
    j.apply_url = "https://example.com/j/1/apply"
    _, doc = digest.build([j], 1, 1, {})
    assert "https://example.com/j/1/apply" in doc and "View posting" in doc


def test_digest_carries_earlier_matches_in_their_own_section():
    earlier = [job(title="Old match", jid="lever:x:old", score=8.0)]
    subject, doc = digest.build([], 10, 0, {}, earlier=earlier)
    assert subject.startswith("1 job worth your time")
    assert "Still open from earlier runs (1)" in doc and "Old match" in doc


# ----------------------------------------------------------------- store ---

def test_unsent_matches_skips_emailed_applied_weak_and_old(tmp_path):
    store = Store(tmp_path / "seen.json")
    good, weak, applied = (job(jid=f"lever:x:{i}", score=s, apply_url=f"https://a/{i}")
                           for i, s in ((1, 8.5), (2, 5.0), (3, 9.0)))
    store.record([good, weak, applied], emailed=False)
    store.mark_applied(applied.job_id)
    old = job(jid="lever:x:4", score=9.5)
    store.record([old], emailed=False)
    store.data[old.job_id]["first_seen"] = (
        datetime.now(timezone.utc) - timedelta(days=45)).isoformat(timespec="seconds")

    carried = store.unsent_matches(7.0, max_age_days=30)
    assert [j.job_id for j in carried] == [good.job_id]
    assert carried[0].apply_url == "https://a/1"
    assert store.unsent_matches(7.0, 30, exclude={good.job_id}) == []

    store.mark_emailed([good.job_id])
    assert store.unsent_matches(7.0, 30) == []


def test_jobs_dropped_by_jd_checks_are_never_carried_as_matches(tmp_path):
    """They are stored unscored so their JD is not refetched. Even at a 0
    threshold, "never screened" must not read as a score of 0."""
    store = Store(tmp_path / "seen.json")
    dropped = job(jid="smartrecruiters:x:1", reason="filtered: abroad, no visa/relocation")
    store.record([dropped], emailed=False)
    assert store.unsent_matches(0.0, 30) == []


# ------------------------------------------------------------------- cli ---

@pytest.fixture
def run_cli(tmp_path, monkeypatch):
    """Runs `jobhunt run --mock --scorer keyword --send` against tmp files.
    Threshold 0 so every screened job is a match: this is testing plumbing,
    not the stub scorer."""
    cfg = dict(CONFIG, score_threshold=0.0, max_drafts=2,
               profile_file=str(tmp_path / "missing-profile.json"),
               seen_file=str(tmp_path / "seen.json"),
               digest_file=str(tmp_path / "out" / "digest.html"),
               tracker_csv=str(tmp_path / "out" / "tracker.csv"))
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    sent: list[tuple[str, str]] = []
    state = {"fail": False}

    def fake_send(subject, doc):
        if state["fail"]:
            raise OSError("smtp down")
        sent.append((subject, doc))

    monkeypatch.setattr(mailer, "send", fake_send)

    def run(fail=False):
        state["fail"] = fail
        assert cli.main(["--config", str(path), "run", "--mock", "--scorer", "keyword",
                         "--send"]) == 0
        return json.loads((tmp_path / "seen.json").read_text())

    run.sent = sent
    return run


def test_every_match_is_emailed_with_an_apply_link(run_cli):
    seen = run_cli()
    subject, doc = run_cli.sent[-1]
    matches = [r for r in seen.values() if r.get("score") is not None]
    assert len(matches) > 2                                  # more than max_drafts
    assert subject.startswith(f"{len(matches)} jobs")
    for r in matches:
        assert (r["apply_url"] or r["url"]).replace("&", "&amp;") in doc
    assert all(r["emailed"] for r in matches)


def test_a_failed_send_carries_matches_into_the_next_digest(run_cli):
    seen = run_cli(fail=True)
    assert run_cli.sent == []
    assert not any(r["emailed"] for r in seen.values())

    seen = run_cli()                          # nothing new; the backlog goes out
    subject, doc = run_cli.sent[-1]
    assert "Still open from earlier runs" in doc
    assert all(r["emailed"] for r in seen.values() if r.get("score") is not None)

    run_cli()                                 # and it goes out exactly once
    assert len(run_cli.sent) == 1
