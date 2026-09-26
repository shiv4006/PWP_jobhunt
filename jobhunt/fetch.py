"""Fetch jobs from public ATS APIs. No auth, no scraping, no ToS risk."""
from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Iterable

import requests

UA = {"User-Agent": "jobhunt/1.0 (personal job search agent)"}
TIMEOUT = 20

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<\s*(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", text, flags=re.I)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = _NL.sub("\n\n", text)
    return text.strip()


@dataclass
class Job:
    job_id: str          # stable global id for dedupe: "<ats>:<slug>:<id>"
    ats: str
    company: str
    title: str
    location: str
    url: str
    description: str
    posted_at: str | None = None
    salary: str | None = None
    apply_url: str | None = None   # straight to the application form, when the ATS has one
    # filled in later by the pipeline
    score: float | None = None
    reason: str | None = None
    draft: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Adapters. Each takes the raw JSON body and returns list[Job].
# Keeping parse separate from HTTP is what makes offline testing possible.
# --------------------------------------------------------------------------

def parse_greenhouse(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    for j in (body or {}).get("jobs", []):
        loc = (j.get("location") or {}).get("name") or ""
        out.append(Job(
            job_id=f"greenhouse:{slug}:{j.get('id')}",
            ats="greenhouse",
            company=company,
            title=(j.get("title") or "").strip(),
            location=loc.strip(),
            url=j.get("absolute_url") or "",
            description=strip_html(j.get("content")),
            posted_at=j.get("updated_at") or j.get("first_published"),
            # absolute_url is often the company's own careers page; the embed
            # form is the application itself, whatever site the board lives on.
            apply_url=f"https://job-boards.greenhouse.io/embed/job_app?for={slug}&token={j.get('id')}",
        ))
    return out


def parse_lever(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    for j in (body or []):
        cats = j.get("categories") or {}
        # Lever splits the JD across descriptionPlain + a `lists` array.
        chunks = [j.get("descriptionPlain") or strip_html(j.get("description"))]
        for lst in (j.get("lists") or []):
            chunks.append(str(lst.get("text") or ""))
            chunks.append(strip_html(lst.get("content")))
        chunks.append(j.get("additionalPlain") or strip_html(j.get("additional")))
        ts = j.get("createdAt")
        posted = None
        if isinstance(ts, (int, float)):
            posted = time.strftime("%Y-%m-%d", time.gmtime(ts / 1000))
        out.append(Job(
            job_id=f"lever:{slug}:{j.get('id')}",
            ats="lever",
            company=company,
            title=(j.get("text") or "").strip(),
            location=(cats.get("location") or "").strip(),
            url=j.get("hostedUrl") or j.get("applyUrl") or "",
            description="\n\n".join(c for c in chunks if c).strip(),
            posted_at=posted,
            salary=cats.get("commitment"),
            apply_url=j.get("applyUrl") or (f"{j['hostedUrl']}/apply" if j.get("hostedUrl") else None),
        ))
    return out


def parse_ashby(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    for j in (body or {}).get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = j.get("compensation") or {}
        salary = None
        summary = comp.get("compensationTierSummary") or comp.get("summaryComponents")
        if isinstance(summary, str):
            salary = summary
        out.append(Job(
            job_id=f"ashby:{slug}:{j.get('id')}",
            ats="ashby",
            company=company,
            title=(j.get("title") or "").strip(),
            location=(j.get("location") or "").strip(),
            url=j.get("jobUrl") or j.get("applyUrl") or "",
            description=(j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")) or "").strip(),
            posted_at=j.get("publishedAt"),
            salary=salary,
            apply_url=j.get("applyUrl") or (f"{j['jobUrl']}/application" if j.get("jobUrl") else None),
        ))
    return out


def _sr_location(loc: dict) -> str:
    # fullLocation leaves a hole when there is no region: "Hyderabad, , India"
    where = loc.get("fullLocation") or ", ".join(
        p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p)
    where = re.sub(r"(\s*,)+\s*", ", ", where).strip(" ,")
    return f"{where} (Remote)" if loc.get("remote") else where


def smartrecruiters_description(body: Any) -> str:
    """The JD lives only on the per-posting detail endpoint, split into sections."""
    sections = ((body or {}).get("jobAd") or {}).get("sections") or {}
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        sec = sections.get(key) or {}
        text = strip_html(sec.get("text"))
        if text:
            parts.append(f"{sec.get('title') or key}\n{text}")
    return "\n\n".join(parts)


def parse_smartrecruiters(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    for j in (body or {}).get("content", []):
        if j.get("visibility", "PUBLIC") != "PUBLIC":
            continue
        ident = (j.get("company") or {}).get("identifier") or slug
        out.append(Job(
            job_id=f"smartrecruiters:{slug}:{j.get('id')}",
            ats="smartrecruiters",
            company=company,
            title=(j.get("name") or "").strip(),
            location=_sr_location(j.get("location") or {}),
            url=j.get("postingUrl") or f"https://jobs.smartrecruiters.com/{ident}/{j.get('id')}",
            # Absent from the list payload; hydrate() fills it for the few jobs
            # that survive the prefilter instead of one request per posting.
            description=smartrecruiters_description(j),
            posted_at=j.get("releasedDate"),
            # hydrate() swaps in the detail payload's own applyUrl
            apply_url=f"https://jobs.smartrecruiters.com/{ident}/{j.get('id')}?oga=true",
        ))
    return out


ENDPOINTS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", parse_greenhouse),
    "lever":      ("https://api.lever.co/v0/postings/{slug}?mode=json", parse_lever),
    "ashby":      ("https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", parse_ashby),
    "smartrecruiters": ("https://api.smartrecruiters.com/v1/companies/{slug}/postings", parse_smartrecruiters),
}

def _fill_smartrecruiters(j: Job, body: Any) -> None:
    j.description = smartrecruiters_description(body)
    j.url = (body or {}).get("postingUrl") or j.url
    j.apply_url = (body or {}).get("applyUrl") or j.apply_url


# Boards whose list endpoint omits the JD: ats -> (detail url, fill(job, body))
DETAILS = {
    "smartrecruiters": ("https://api.smartrecruiters.com/v1/companies/{slug}/postings/{id}",
                        _fill_smartrecruiters),
}

SR_PAGE = 100   # SmartRecruiters' max page size


class BoardError(Exception):
    pass


def _get_json(sess, url: str, params: dict | None = None) -> Any:
    r = sess.get(url, params=params, headers=UA, timeout=TIMEOUT)
    if r.status_code != 200:
        raise BoardError(f"HTTP {r.status_code}")
    return r.json()


def _get_smartrecruiters(sess, url: str, country: str | None) -> dict:
    """Pages through the list. Note an unknown company is a 200 with zero
    results, not a 404 — a typo'd slug just reports nothing."""
    content: list = []
    while True:
        params = {"limit": SR_PAGE, "offset": len(content)}
        if country:
            params["country"] = country
        page = _get_json(sess, url, params)
        batch = page.get("content") or []
        content.extend(batch)
        if not batch or len(content) >= int(page.get("totalFound") or 0):
            return {"content": content}


def fetch_board(ats: str, slug: str, company: str | None = None,
                session: requests.Session | None = None,
                country: str | None = None) -> list[Job]:
    """Hit one company's public board. Returns [] on any failure (never raises).

    `country` (ISO code, e.g. "in") narrows SmartRecruiters boards server-side;
    worth it for giants like Bosch with thousands of global postings.
    """
    if ats not in ENDPOINTS:
        print(f"  ! {ats}/{slug} -> unsupported ATS, skipping")
        return []
    url_tpl, parser = ENDPOINTS[ats]
    sess = session or requests
    url = url_tpl.format(slug=slug)
    try:
        if ats == "smartrecruiters":
            body = _get_smartrecruiters(sess, url, country)
        else:
            body = _get_json(sess, url)
        return parser(slug, company or slug, body)
    except BoardError as e:
        print(f"  ! {ats}/{slug} -> {e}")
        return []
    except Exception as e:  # dead slug, rate limit, network blip
        print(f"  ! {ats}/{slug} -> {type(e).__name__}: {e}")
        return []


def hydrate(jobs: Iterable[Job], session: requests.Session | None = None,
            sleep: float = 0.2) -> int:
    """Fetch the JD for jobs whose board list omitted it. Run this AFTER the
    prefilter so only the handful of survivors cost a request. Returns the
    number filled; a failure leaves that description empty, never raises."""
    sess = session or requests.Session()
    filled = 0
    for j in jobs:
        if j.description or j.ats not in DETAILS:
            continue
        url_tpl, fill = DETAILS[j.ats]
        _, slug, pid = j.job_id.split(":", 2)
        try:
            fill(j, _get_json(sess, url_tpl.format(slug=slug, id=pid)))
            filled += bool(j.description)
        except Exception as e:
            print(f"  ! JD for {j.job_id} -> {type(e).__name__}: {e}")
        time.sleep(sleep)
    return filled


def fetch_all(companies: Iterable[dict], sleep: float = 0.25) -> list[Job]:
    jobs: list[Job] = []
    session = requests.Session()
    for c in companies:
        got = fetch_board(c["ats"], c["slug"], c.get("name"), session=session,
                          country=c.get("country"))
        if got:
            print(f"  {c.get('name') or c['slug']:<28} {len(got):>4} jobs  ({c['ats']})")
        jobs.extend(got)
        time.sleep(sleep)
    return jobs
