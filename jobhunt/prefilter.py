"""Deterministic filter that runs BEFORE any LLM call.

This is the whole cost story: ~2000 raw jobs -> ~40 candidates for ~0 rupees,
so Claude only ever reads jobs that already passed title + location + freshness
+ the JD checks (experience ask, visa sponsorship for roles abroad).

Location is classified from the job's location string, never its title's
adjectives: "Distributed Systems Engineer" in Belgrade is not a remote role.

  home       matches `locations` (on-site, hybrid or remote — all fine)
  remote     remote and open to you: names an allowed region, or no region
  restricted remote but pinned elsewhere ("Remote - US")      -> dropped
  abroad     on-site/hybrid anywhere else -> kept only if the JD offers
             visa sponsorship or relocation

The JD checks need a description. Boards whose list omits it (SmartRecruiters)
skip them in `prefilter`; `jd_filter` re-runs them after `fetch.hydrate`.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from .fetch import Job

REMOTE = re.compile(r"\b(remote|anywhere|work from home|wfh|telecommut\w*)\b", re.I)
DEFAULT_REMOTE_REGIONS = ("worldwide", "global", "anywhere", "apac", "asia",
                          "asia pacific", "asia-pacific")
# Words that say HOW you work, not WHERE. Whatever is left after stripping
# these from a remote job's location is the region it is pinned to.
_MODE_WORDS = re.compile(
    r"\b(remote|fully|full[\s-]?time|part[\s-]?time|hybrid|work from home|wfh|"
    r"telecommut\w*|first|friendly|optional|only|based|position|role|contract|"
    r"permanent|office|flexible|or|and|in|of|the)\b", re.I)

# --- JD signals -------------------------------------------------------------
_SPONSOR_NO = [re.compile(p, re.I) for p in (
    r"\b(no|not|unable to|cannot|can't|can ?not|won't|will not|do not|does not|don't|"
    r"doesn't|are not able to|is not able to)\b[^.\n]{0,40}\b(sponsor\w*|relocation)\b",
    r"\bwithout\b[^.\n]{0,25}\bsponsorship\b",
    r"\b(sponsorship|relocation)\b[^.\n]{0,30}\b(not|unavailable)\b",
    r"\bmust\b[^.\n]{0,40}\b(authori[sz]ed|eligible|permitted|right) to work\b",
    r"\b(citizens?|citizenship|permanent residents?)\s+only\b",
    r"\bsecurity clearance\b",
)]
_SPONSOR_YES = [re.compile(p, re.I) for p in (
    r"\b(visa|work permit|immigration)\s+(sponsorship|support|assistance)\b",
    r"\bsponsor(s|ing|ship)?\b[^.\n]{0,25}\b(visas?|work permits?|work authori[sz]ation)\b",
    r"\brelocation\s+(assistance|support|package|allowance|bonus|stipend|benefits?)\b",
    r"\brelocation\b[^.\n]{0,20}\b(is |are )?(offered|provided|available|covered|supported)\b",
    r"\b(help|support|assist)\w*\b[^.\n]{0,20}\byou\b[^.\n]{0,10}\brelocat",
)]
# A remote role with no region in its location can still be pinned in the JD.
_REMOTE_PINNED = [re.compile(p, re.I) for p in (
    r"\bmust\b[^.\n]{0,40}\b(authori[sz]ed|eligible|permitted|right) to work\b",
    r"\b(must|need to|should|required to)\b[^.\n]{0,25}\b(reside|live|be based|be located)\b",
    r"\b(us|u\.s\.|united states|canada|uk|europe|eu|emea|latam)[\s-]based\b",
)]
# "5+ years of experience", "3-5 yrs experience", "minimum 4 years ... experience"
_YEARS_BEFORE = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b"
    r"(?=[^.\n]{0,60}\b(?:experience|exp)\b)", re.I)
# "Experience: 5+ years", "experience of 3-5 years"
_YEARS_AFTER = re.compile(
    r"\b(?:experience|exp)\b[^.\n\d]{0,30}(\d{1,2})\s*(?:\+|plus)?\s*"
    r"(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:years?|yrs?)\b", re.I)


def _any_match(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _terms(words) -> re.Pattern | None:
    return _compile_terms(tuple(w.strip() for w in (words or []) if w and w.strip()))


@lru_cache(maxsize=32)
def _compile_terms(words: tuple[str, ...]) -> re.Pattern | None:
    if not words:
        return None
    # word-bounded, so "india" does not match "Indiana"
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.fromisoformat(v) if fmt is None else datetime.strptime(v, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def classify_location(j: Job, cfg: dict) -> str:
    """-> 'home' | 'remote' | 'restricted' | 'abroad' (see module docstring)."""
    home = _terms(cfg.get("locations"))
    if home is None or home.search(f"{j.location} {j.title}"):
        return "home"
    if cfg.get("allow_remote", True) and REMOTE.search(f"{j.location} {j.title}"):
        open_regions = _terms(cfg.get("remote_regions", DEFAULT_REMOTE_REGIONS))
        if open_regions and open_regions.search(j.location):
            return "remote"
        # "Remote" / "Remote, Full-time" -> no region left -> open to you.
        # "Remote - US" / "Canada; Remote" -> something left -> pinned there.
        leftover = re.sub(r"[\W_\d]+", " ", _MODE_WORDS.sub(" ", j.location)).strip()
        return "restricted" if leftover else "remote"
    return "abroad"


def years_required(description: str) -> int | None:
    """Largest lower bound among the JD's "N years ... experience" asks. The
    headline requirement is usually the biggest one; per-tool asks ("2+ years
    of React") sit below it."""
    found = [int(m.group(1)) for rx in (_YEARS_BEFORE, _YEARS_AFTER)
             for m in rx.finditer(description or "")]
    found = [n for n in found if n <= 20]
    return max(found) if found else None


def offers_sponsorship(description: str) -> bool:
    text = description or ""
    if any(rx.search(text) for rx in _SPONSOR_NO):
        return False
    return any(rx.search(text) for rx in _SPONSOR_YES)


def jd_verdict(j: Job, cfg: dict) -> str | None:
    """None if the JD is fine, else a short reason it was dropped. A job with
    no description passes the experience check (unknown is not a no) but
    cannot prove sponsorship, so it fails the abroad check."""
    max_years = cfg.get("max_years_required")
    if max_years is not None and j.description:
        need = years_required(j.description)
        if need is not None and need > int(max_years):
            return f"asks {need}+ years"

    where = classify_location(j, cfg)
    if where == "abroad":
        if not cfg.get("abroad_needs_sponsorship", True):
            return None
        return None if offers_sponsorship(j.description) else "abroad, no visa/relocation"
    if where == "remote" and j.description and not re.search(r"\bindia\b", j.description, re.I):
        if any(rx.search(j.description) for rx in _REMOTE_PINNED):
            return "remote, but pinned to another country"
    return None


def prefilter(jobs: list[Job], cfg: dict) -> list[Job]:
    inc = cfg.get("include_titles") or [r"."]
    exc = cfg.get("exclude_titles") or []
    max_age = cfg.get("max_age_days")
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age) if max_age else None

    kept = []
    stats = {"title": 0, "location": 0, "age": 0, "jd": 0}
    for j in jobs:
        if not _any_match(inc, j.title) or (exc and _any_match(exc, j.title)):
            stats["title"] += 1
            continue

        if classify_location(j, cfg) == "restricted":
            stats["location"] += 1
            continue

        if cutoff:
            posted = _parse_date(j.posted_at)
            if posted and posted < cutoff:
                stats["age"] += 1
                continue

        # No JD yet (SmartRecruiters) -> jd_filter decides after hydrate.
        if j.description and jd_verdict(j, cfg):
            stats["jd"] += 1
            continue

        kept.append(j)

    print(f"  prefilter: {len(jobs)} -> {len(kept)} "
          f"(dropped title={stats['title']} location={stats['location']} "
          f"stale={stats['age']} jd={stats['jd']})")
    return kept


def jd_filter(jobs: list[Job], cfg: dict) -> tuple[list[Job], list[Job]]:
    """Second pass once every JD is in hand. Returns (kept, dropped); each
    dropped job carries its reason in `reason`."""
    kept, dropped = [], []
    for j in jobs:
        why = jd_verdict(j, cfg)
        if why:
            j.reason = f"filtered: {why}"
            dropped.append(j)
        else:
            kept.append(j)
    if dropped:
        print(f"  jd checks: dropped {len(dropped)} after fetching descriptions")
    return kept, dropped
