"""seen.json doubles as the dedupe index AND the application tracker."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .fetch import Job


class Store:
    def __init__(self, path: str | Path = "seen.json"):
        self.path = Path(path)
        self.data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                print(f"  ! {self.path} corrupt, starting fresh")

    def unseen(self, jobs: list[Job]) -> list[Job]:
        return [j for j in jobs if j.job_id not in self.data]

    def record(self, jobs: list[Job], emailed: bool) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for j in jobs:
            self.data.setdefault(j.job_id, {
                "first_seen": now,
                "company": j.company,
                "title": j.title,
                "location": j.location,
                "url": j.url,
                "apply_url": j.apply_url,
                "score": j.score,
                "reason": j.reason,
                "emailed": emailed,
                "applied": False,
                "applied_on": None,
            })
        self.save()

    def unsent_matches(self, threshold: float, max_age_days: int | None = 30,
                       exclude: set[str] | frozenset = frozenset()) -> list[Job]:
        """Matches from earlier runs that never reached your inbox — the send
        failed, or the run had no --send — rebuilt as Jobs so the next digest
        can carry them. Skips applied ones and anything older than max_age_days
        (it has probably closed)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)
                  if max_age_days else None)
        out = []
        for jid, row in self.data.items():
            if jid in exclude or row.get("emailed") or row.get("applied"):
                continue
            # None = never screened (e.g. dropped by the JD checks), not a 0
            if row.get("score") is None or row["score"] < threshold:
                continue
            try:
                seen = datetime.fromisoformat(row.get("first_seen") or "")
            except ValueError:
                seen = None
            if cutoff and seen and seen < cutoff:
                continue
            out.append(Job(job_id=jid, ats=jid.split(":", 1)[0],
                           company=row.get("company") or "", title=row.get("title") or "",
                           location=row.get("location") or "", url=row.get("url") or "",
                           description="", score=row.get("score"), reason=row.get("reason"),
                           apply_url=row.get("apply_url")))
        return sorted(out, key=lambda j: j.score or 0, reverse=True)

    def mark_emailed(self, job_ids) -> None:
        for jid in job_ids:
            if jid in self.data:
                self.data[jid]["emailed"] = True
        self.save()

    def mark_applied(self, job_id: str) -> bool:
        if job_id not in self.data:
            return False
        self.data[job_id]["applied"] = True
        self.data[job_id]["applied_on"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.save()
        return True

    def stats(self) -> dict:
        return {
            "tracked": len(self.data),
            "emailed": sum(1 for v in self.data.values() if v.get("emailed")),
            "applied": sum(1 for v in self.data.values() if v.get("applied")),
        }

    def export_csv(self, path: str | Path = "out/tracker.csv") -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        cols = ["first_seen", "company", "title", "location", "score",
                "reason", "applied", "applied_on", "url", "apply_url"]
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["job_id"] + cols, extrasaction="ignore")
            w.writeheader()
            for jid, row in sorted(self.data.items(),
                                   key=lambda kv: kv[1].get("first_seen", ""), reverse=True):
                w.writerow({"job_id": jid, **row})
        return path

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))
