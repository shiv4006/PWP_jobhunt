"""Mock ATS payloads in each provider's exact native JSON shape.

These exercise the real parsers, so `--mock` tests everything except the
HTTP hop. Includes deliberate junk (wrong seniority, wrong city, wrong
function, stale posting, an unlisted Ashby draft) so the filters have
something to actually reject.

Dates are computed relative to *now*, never hardcoded. A fixture with a
hardcoded date silently ages past `max_age_days` and one day your demo
returns zero jobs for no visible reason. `_STALE` is the only old one, and
it is old on purpose.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .fetch import (parse_greenhouse, parse_lever, parse_ashby, parse_smartrecruiters,
                    smartrecruiters_description, Job)


def _ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _gh(days: int) -> str:
    """Greenhouse: ISO 8601 with an offset."""
    return _ago(days).strftime("%Y-%m-%dT%H:%M:%S-04:00")


def _lever(days: int) -> int:
    """Lever: epoch MILLISECONDS. Seconds here silently dates every posting
    to 1970 and the freshness filter eats the entire board."""
    return int(_ago(days).timestamp() * 1000)


def _ashby(days: int) -> str:
    return _ago(days).strftime("%Y-%m-%dT%H:%M:%S.000Z")


_sr = _ashby   # SmartRecruiters releasedDate uses the same ISO-with-millis shape


STALE_DAYS = 280   # comfortably past any sane max_age_days

_BACKEND_JD = """<p>We are building the control plane for our edge platform.</p>
<p><strong>What you'll do</strong></p><ul>
<li>Design and operate low-latency distributed services handling millions of RPS</li>
<li>Work in Go and Java across caching, routing and traffic-steering systems</li>
<li>Own reliability: on-call, incident response, capacity planning</li></ul>
<p><strong>What we look for</strong></p><ul>
<li>3+ years building backend systems at scale</li>
<li>Strong fundamentals in data structures, algorithms and networking (TCP/IP, HTTP, DNS)</li>
<li>Experience with Kubernetes and observability tooling</li></ul>"""

_STAFF_JD = """<p>As a Staff Engineer you will set multi-year technical direction
across three teams, mentor senior engineers, and own architecture for our
petabyte-scale storage layer.</p><ul><li>10+ years of experience required</li>
<li>Proven track record leading org-wide migrations</li></ul>"""

_FRONTEND_JD = """<p>Build delightful UI in React and TypeScript. Own our design
system, animations and accessibility work.</p>"""

GREENHOUSE = {
    "acme-edge": {"jobs": [
        # keeper: right level, right city, fresh
        {"id": 5501001, "title": "Software Engineer II, Distributed Systems",
         "absolute_url": "https://boards.greenhouse.io/acme-edge/jobs/5501001",
         "location": {"name": "Bangalore, India"},
         "updated_at": _gh(2), "content": _BACKEND_JD},
        # junk: wrong seniority
        {"id": 5501002, "title": "Staff Software Engineer, Storage",
         "absolute_url": "https://boards.greenhouse.io/acme-edge/jobs/5501002",
         "location": {"name": "Bengaluru, KA"},
         "updated_at": _gh(3), "content": _STAFF_JD},
        # junk: wrong function
        {"id": 5501003, "title": "Enterprise Account Executive",
         "absolute_url": "https://boards.greenhouse.io/acme-edge/jobs/5501003",
         "location": {"name": "Mumbai, India"},
         "updated_at": _gh(4),
         "content": "<p>Own a $3M quota selling to CIOs.</p>"},
        # junk: wrong city, and not remote
        {"id": 5501004, "title": "Backend Engineer, Payments",
         "absolute_url": "https://boards.greenhouse.io/acme-edge/jobs/5501004",
         "location": {"name": "San Francisco, CA"},
         "updated_at": _gh(1), "content": _BACKEND_JD},
        # junk: would pass every other gate, but it is ancient
        {"id": 5501005, "title": "Senior Software Engineer, Platform",
         "absolute_url": "https://boards.greenhouse.io/acme-edge/jobs/5501005",
         "location": {"name": "Remote - India"},
         "updated_at": _gh(STALE_DAYS), "content": _BACKEND_JD},
    ]},
    "novapay": {"jobs": [
        # keeper: "SDE" spelled out — the bare regex "sde" would miss this
        {"id": 7702001, "title": "Software Development Engineer, Core Infra",
         "absolute_url": "https://boards.greenhouse.io/novapay/jobs/7702001",
         "location": {"name": "Bengaluru, India"},
         "updated_at": _gh(1),
         "content": _BACKEND_JD + "<p>Java, Kafka, Postgres. Hybrid, 3 days in office.</p>"},
        # keeper: frontend is one of the target titles for a full-stack profile
        {"id": 7702002, "title": "Frontend Engineer, Design Systems",
         "absolute_url": "https://boards.greenhouse.io/novapay/jobs/7702002",
         "location": {"name": "Bengaluru, India"},
         "updated_at": _gh(2), "content": _FRONTEND_JD},
        # junk: remote, but pinned to another country
        {"id": 7702003, "title": "Software Engineer, Ledger",
         "absolute_url": "https://boards.greenhouse.io/novapay/jobs/7702003",
         "location": {"name": "Remote - US"},
         "updated_at": _gh(1), "content": _BACKEND_JD},
        # keeper: abroad, but the JD offers a visa and relocation
        {"id": 7702004, "title": "Backend Engineer, Cards",
         "absolute_url": "https://boards.greenhouse.io/novapay/jobs/7702004",
         "location": {"name": "London, UK"},
         "updated_at": _gh(1),
         "content": _BACKEND_JD + "<p>We offer visa sponsorship and a relocation "
                                  "package for candidates moving to London.</p>"},
        # junk: right title and city, but the JD asks for far more experience
        {"id": 7702005, "title": "Software Engineer, Risk",
         "absolute_url": "https://boards.greenhouse.io/novapay/jobs/7702005",
         "location": {"name": "Bengaluru, India"},
         "updated_at": _gh(1),
         "content": "<p>Build our fraud rules engine.</p><ul><li>6+ years of "
                    "professional software engineering experience</li></ul>"},
    ]},
}

LEVER = {
    "quantstack": [
        {"id": "a1b2c3d4-1111-4aaa-9999-000000000001",
         "text": "Backend Engineer (Go)",
         "hostedUrl": "https://jobs.lever.co/quantstack/a1b2c3d4-1111-4aaa-9999-000000000001",
         "applyUrl": "https://jobs.lever.co/quantstack/a1b2c3d4-1111-4aaa-9999-000000000001/apply",
         "categories": {"location": "Bangalore", "team": "Infrastructure",
                        "commitment": "Full-time"},
         "createdAt": _lever(2),
         "descriptionPlain": "We run a real-time market data pipeline in Go. "
                             "You will own ingestion, fan-out and the storage layer.",
         "lists": [{"text": "Requirements",
                    "content": "<li>2-5 years backend experience</li>"
                               "<li>Go or Java, strong CS fundamentals</li>"
                               "<li>Comfort with Kubernetes, gRPC, Kafka</li>"}],
         "additionalPlain": "We interview with one system design round and one "
                            "pair-programming round. No take-home."},
        {"id": "a1b2c3d4-1111-4aaa-9999-000000000002",
         "text": "Engineering Manager, Platform",
         "hostedUrl": "https://jobs.lever.co/quantstack/a1b2c3d4-1111-4aaa-9999-000000000002",
         "categories": {"location": "Bangalore", "team": "Platform",
                        "commitment": "Full-time"},
         "createdAt": _lever(3),
         "descriptionPlain": "Lead a team of 8 engineers. 5+ years of people management required.",
         "lists": []},
        {"id": "a1b2c3d4-1111-4aaa-9999-000000000003",
         "text": "Site Reliability Engineer",
         "hostedUrl": "https://jobs.lever.co/quantstack/a1b2c3d4-1111-4aaa-9999-000000000003",
         "categories": {"location": "Remote (India)", "team": "SRE",
                        "commitment": "Full-time"},
         "createdAt": _lever(1),
         "descriptionPlain": "Own SLOs, on-call and incident response for a "
                             "multi-region Kubernetes fleet. Terraform, Prometheus, Go.",
         "lists": [{"text": "Nice to have",
                    "content": "<li>CDN or edge networking background</li>"}]},
    ],
}

ASHBY = {
    "helioscale": {"jobs": [
        {"id": "9f8e7d6c-2222-4bbb-8888-000000000001",
         "title": "Software Engineer, Networking",
         "location": "Bengaluru, India", "isListed": True,
         "jobUrl": "https://jobs.ashbyhq.com/helioscale/9f8e7d6c-2222-4bbb-8888-000000000001",
         "publishedAt": _ashby(1),
         "compensation": {"compensationTierSummary": "₹32L – ₹48L"},
         "descriptionPlain": "Work on our anycast network and HTTP proxy layer. "
                             "You will tune TCP congestion control, build DNS "
                             "steering logic and reduce p99 latency across POPs. "
                             "We use Rust and Go. 2+ years experience."},
        {"id": "9f8e7d6c-2222-4bbb-8888-000000000002",
         "title": "Software Engineer, Networking",
         "location": "Bengaluru, India", "isListed": False,
         "jobUrl": "https://jobs.ashbyhq.com/helioscale/unlisted",
         "publishedAt": _ashby(1),
         "descriptionPlain": "Draft posting that should never surface."},
        {"id": "9f8e7d6c-2222-4bbb-8888-000000000003",
         "title": "Data Scientist, Growth",
         "location": "Bengaluru, India", "isListed": True,
         "jobUrl": "https://jobs.ashbyhq.com/helioscale/9f8e7d6c-2222-4bbb-8888-000000000003",
         "publishedAt": _ashby(2),
         "descriptionHtml": "<p>Causal inference, experimentation, SQL &amp; Python.</p>"},
    ]},
}


# SmartRecruiters: the list endpoint carries no JD. It lives on the per-posting
# detail endpoint, keyed here by posting id (see fetch.hydrate).
SMARTRECRUITERS = {
    "kitepay": {"offset": 0, "limit": 100, "totalFound": 3, "content": [
        # keeper: "Developer", not "Engineer" — the wording Indian boards use.
        # fullLocation with no region really does ship as "City, , Country".
        {"id": "744000100000001", "name": "Full Stack Developer",
         "company": {"identifier": "Kitepay", "name": "Kitepay"},
         "releasedDate": _sr(1), "visibility": "PUBLIC",
         "location": {"city": "Hyderabad", "country": "in", "remote": False,
                      "fullLocation": "Hyderabad, , India"}},
        # junk: level III at a new-grad profile
        {"id": "744000100000002", "name": "SDE III - Backend",
         "company": {"identifier": "Kitepay", "name": "Kitepay"},
         "releasedDate": _sr(2), "visibility": "PUBLIC",
         "location": {"city": "Bengaluru", "region": "KA", "country": "in",
                      "remote": False, "fullLocation": "Bengaluru, KA, India"}},
        # junk: enterprise-platform "developer", not software engineering
        {"id": "744000100000003", "name": "SAP ABAP Developer",
         "company": {"identifier": "Kitepay", "name": "Kitepay"},
         "releasedDate": _sr(1), "visibility": "PUBLIC",
         "location": {"city": "Pune", "region": "MH", "country": "in",
                      "remote": True, "fullLocation": "Pune, MH, India"}},
    ]},
}

SMARTRECRUITERS_DETAIL = {
    "744000100000001": {"id": "744000100000001", "name": "Full Stack Developer",
        "jobAd": {"sections": {
            "companyDescription": {"title": "Company Description",
                                   "text": "<p>Kitepay runs UPI rails for 3M merchants.</p>"},
            "jobDescription": {"title": "Job Description",
                               "text": "<p>Ship merchant dashboards in Next.js and the "
                                       "Node.js/Postgres APIs behind them.</p>"},
            "qualifications": {"title": "Qualifications",
                               "text": "<ul><li>0-2 years with TypeScript and React</li>"
                                       "<li>Comfort with SQL and Redis</li></ul>"},
            "additionalInformation": {"title": "Additional Information",
                                      "text": "<p>Hybrid, 3 days a week in Hyderabad.</p>"},
        }}},
}


def fetch_all_mock(companies=None) -> list[Job]:
    jobs: list[Job] = []
    for slug, body in GREENHOUSE.items():
        jobs += parse_greenhouse(slug, slug.replace("-", " ").title(), body)
    for slug, body in LEVER.items():
        jobs += parse_lever(slug, slug.title(), body)
    for slug, body in ASHBY.items():
        jobs += parse_ashby(slug, slug.title(), body)
    for slug, body in SMARTRECRUITERS.items():
        for j in parse_smartrecruiters(slug, slug.title(), body):
            # what fetch.hydrate does over HTTP for the real boards
            j.description = smartrecruiters_description(
                SMARTRECRUITERS_DETAIL.get(j.job_id.split(":", 2)[2]))
            jobs.append(j)
    boards = len(GREENHOUSE) + len(LEVER) + len(ASHBY) + len(SMARTRECRUITERS)
    print(f"  [mock] {len(jobs)} postings from {boards} boards")
    return jobs
