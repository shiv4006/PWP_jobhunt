"""Build the daily HTML digest. Inline CSS only — Gmail strips <style> blocks."""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from .fetch import Job

BG = "#0f1115"
CARD = "#171a21"
LINE = "#262b36"
TEXT = "#e6e8ec"
MUTED = "#8b93a3"
ACCENT = "#7c9cff"


def _badge(score: float | None) -> str:
    s = score or 0
    color = "#3fb950" if s >= 8.5 else "#d29922" if s >= 7 else "#8b949e"
    return (f'<span style="background:{color};color:#0f1115;font-weight:700;'
            f'padding:3px 9px;border-radius:999px;font-size:13px;">{s:.1f}</span>')


def _bullets(items: list[str]) -> str:
    if not items:
        return ""
    lis = "".join(
        f'<li style="margin:0 0 6px 0;color:{TEXT};font-size:14px;line-height:1.5;">'
        f'{html.escape(str(i))}</li>' for i in items)
    return f'<ul style="margin:8px 0 0 0;padding-left:18px;">{lis}</ul>'


def _section(label: str, body: str) -> str:
    if not body:
        return ""
    return (f'<div style="margin-top:14px;">'
            f'<div style="color:{MUTED};font-size:11px;letter-spacing:.09em;'
            f'text-transform:uppercase;font-weight:700;">{label}</div>{body}</div>')


def _links(j: Job) -> str:
    """Apply goes straight to the application form; the posting link is there
    for reading the JD first. Every card gets both, drafted or not."""
    apply_href = j.apply_url or j.url
    view = ""
    if j.url and j.url != apply_href:
        view = (f'<a href="{html.escape(j.url)}" style="color:{ACCENT};font-size:13px;'
                f'margin-left:12px;text-decoration:none;">View posting</a>')
    return f"""<div style="margin-top:14px;">
    <a href="{html.escape(apply_href)}" style="display:inline-block;background:{ACCENT};
       color:#0f1115;font-weight:700;font-size:14px;text-decoration:none;
       padding:10px 18px;border-radius:8px;">Apply →</a>{view}
    <div style="color:{MUTED};font-size:11px;margin-top:6px;">{html.escape(j.job_id)}</div>
  </div>"""


def _has_kit(j: Job) -> bool:
    return any((j.draft or {}).values())


def _compact(j: Job) -> str:
    """A match without a drafted kit: title, score, one-line reason, apply."""
    meta = " · ".join(x for x in [j.company, j.location or "—"] if x)
    reason = (f'<div style="color:{TEXT};font-size:13px;line-height:1.5;margin-top:6px;">'
              f'{html.escape(j.reason)}</div>') if j.reason else ""
    return f"""
<div style="background:{CARD};border:1px solid {LINE};border-radius:12px;padding:14px 18px;margin-bottom:10px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;">
    <div style="font-size:15px;font-weight:700;color:{TEXT};">{html.escape(j.title)}</div>
    <div style="padding-left:12px;">{_badge(j.score)}</div>
  </div>
  <div style="color:{MUTED};font-size:13px;margin-top:4px;">{html.escape(meta)}</div>
  {reason}
  {_links(j)}
</div>"""


def _heading(text: str) -> str:
    return (f'<div style="color:{MUTED};font-size:12px;letter-spacing:.09em;text-transform:uppercase;'
            f'font-weight:700;margin:22px 0 10px 0;">{html.escape(text)}</div>')


def _card(j: Job) -> str:
    if not _has_kit(j):
        return _compact(j)
    d = j.draft or {}
    meta = " · ".join(x for x in [j.company, j.location or "—", j.ats] if x)

    para = lambda t: (f'<p style="margin:8px 0 0 0;color:{TEXT};font-size:14px;'
                      f'line-height:1.6;">{html.escape(t)}</p>') if t else ""

    cover = d.get("cover_note")
    cover_html = ""
    if cover:
        cover_html = _section("Cover note (edit before sending)",
            f'<div style="margin-top:8px;padding:12px;background:#0d1017;'
            f'border:1px solid {LINE};border-radius:8px;color:{TEXT};font-size:14px;'
            f'line-height:1.6;white-space:pre-wrap;">{html.escape(cover)}</div>')

    return f"""
<div style="background:{CARD};border:1px solid {LINE};border-radius:12px;padding:18px;margin-bottom:14px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;">
    <div style="font-size:17px;font-weight:700;color:{TEXT};">{html.escape(j.title)}</div>
    <div style="padding-left:12px;">{_badge(j.score)}</div>
  </div>
  <div style="color:{MUTED};font-size:13px;margin-top:5px;">{html.escape(meta)}</div>
  {para(j.reason or "")}
  {_section("Why it fits", para(d.get("fit_summary", "")))}
  {_section("Resume bullets for this role", _bullets(d.get("tailored_bullets", [])))}
  {_section("Honest gaps", _bullets(d.get("gaps", [])))}
  {cover_html}
  {_section("Ask them", _bullets(d.get("questions_to_ask", [])))}
  {_links(j)}
</div>"""


def build(jobs: list[Job], scanned: int, candidates: int, stats: dict,
          earlier: list[Job] | None = None) -> tuple[str, str]:
    """`jobs` is EVERY match from this run, best first — drafted ones render as
    full kits, the rest as compact cards, all with an apply link. `earlier` is
    matches from past runs that never got emailed."""
    earlier = earlier or []
    today = datetime.now().strftime("%d %b %Y")
    total = len(jobs) + len(earlier)
    subject = (f"{total} job{'s' if total != 1 else ''} worth your time — {today}"
               if total else f"No new matches today — {today}")

    if total:
        kits = [j for j in jobs if _has_kit(j)]
        rest = [j for j in jobs if not _has_kit(j)]
        body = "".join(_card(j) for j in kits)
        if rest:
            if kits:
                body += _heading(f"More matches ({len(rest)})")
            body += "".join(_compact(j) for j in rest)
        if earlier:
            body += _heading(f"Still open from earlier runs ({len(earlier)})")
            body += "".join(_compact(j) for j in earlier)
    else:
        body = (f'<div style="background:{CARD};border:1px solid {LINE};border-radius:12px;'
                f'padding:24px;color:{MUTED};font-size:14px;">Scanned {scanned} postings, '
                f'nothing cleared the bar today. Boards are quiet on weekends.</div>')

    html_doc = f"""<!doctype html><html><body style="margin:0;padding:20px;background:{BG};
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:640px;margin:0 auto;">
  <div style="color:{TEXT};font-size:22px;font-weight:800;">Your job digest</div>
  <div style="color:{MUTED};font-size:13px;margin:6px 0 20px 0;">
    {today} · scanned {scanned} postings · {candidates} passed filters ·
    {len(jobs)} new match{'es' if len(jobs) != 1 else ''}<br>
    tracker: {stats.get('tracked', 0)} seen · {stats.get('applied', 0)} applied
  </div>
  {body}
  <div style="color:{MUTED};font-size:11px;line-height:1.6;margin-top:18px;
       border-top:1px solid {LINE};padding-top:14px;">
    Drafts are starting points, not send-ready. Read the JD, edit the note,
    then submit it yourself.
  </div>
</div></body></html>"""
    return subject, html_doc


def write(html_doc: str, path: str | Path = "out/digest.html") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_doc, encoding="utf-8")
    return path
