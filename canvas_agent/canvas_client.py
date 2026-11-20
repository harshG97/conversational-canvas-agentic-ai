"""Client for the Canvas LMS REST API."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)


class CanvasClient:
    """Thin wrapper around the Canvas LMS REST API.

    Requires two environment variables:
      - CANVAS_API_URL  (e.g. https://myschool.instructure.com)
      - CANVAS_API_TOKEN
    """

    def __init__(self, *, canvas_token: str | None = None) -> None:
        base_url = os.environ["CANVAS_API_URL"].rstrip("/")
        token = canvas_token or os.environ["CANVAS_API_TOKEN"]
        self._client = httpx.Client(
            base_url=f"{base_url}/api/v1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        )
        log.info("CanvasClient initialised — base_url=%s/api/v1", base_url)

    # ── helpers ──────────────────────────────────────────────────────

    def _get_paginated(self, url: str, params: dict | None = None) -> list[dict]:
        """Follow Canvas pagination links and return all results."""
        results: list[dict] = []
        params = params or {}
        params.setdefault("per_page", 100)
        page = 1

        while url:
            log.debug("API GET (paginated p%d) %s  params=%s", page, url, params)
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            batch = resp.json()
            log.debug("  → received %d items", len(batch))
            results.extend(batch)
            # After the first request, params are embedded in the next link
            params = {}
            url = _next_link(resp)
            page += 1

        log.info("API paginated %s → %d total items", url, len(results))
        return results

    # ── public methods ───────────────────────────────────────────────

    def get_courses(self, *, enrollment_state: str = "active") -> list[dict]:
        """Return the user's courses (active enrolments by default)."""
        log.info("get_courses(enrollment_state=%s)", enrollment_state)
        items = self._get_paginated(
            "/courses",
            {
                "enrollment_state": enrollment_state,
                "include[]": "term",
                "state[]": "available",
            },
        )
        courses = [
            {
                "id": c["id"],
                "name": c.get("name", ""),
                "code": c.get("course_code", ""),
                #"term": (c.get("term") or {}).get("name", ""),
            }
            for c in items
        ]
        log.info("get_courses → %d courses", len(courses))
        for c in courses:
            log.debug("  course: %s (id=%s)", c["name"], c["id"])
        return courses

    def _get_single_page(self, url: str, params: dict | None = None) -> list[dict]:
        """Fetch a single page of results (no pagination)."""
        params = params or {}
        params.setdefault("per_page", 100)
        log.debug("API GET (single page) %s  params=%s", url, params)
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        log.debug("  → received %d items", len(data))
        return data

    def get_assignments(
        self,
        course_id: int,
        *,
        upcoming_only: bool = False,
        start: datetime | None = None,
        end: datetime | None = None,
        order_by: str = "due_at",
    ) -> list[dict]:
        """Return assignments for a course, optionally only upcoming ones.

        Args:
            start: With ``upcoming_only``, skip items due before this
                   (aware datetime). Defaults to now.
            end:   With ``upcoming_only``, skip items due after this
                   (aware datetime). ``None`` means no upper bound.
        """
        log.info("get_assignments(course_id=%s, upcoming_only=%s, start=%s, end=%s)",
                 course_id, upcoming_only, start, end)
        params: dict = {"order_by": order_by, "include[]": "submission"}

        # When we only need upcoming items, skip pagination — one page is enough.
        if upcoming_only:
            items = self._get_single_page(
                f"/courses/{course_id}/assignments", params
            )
        else:
            items = self._get_paginated(
                f"/courses/{course_id}/assignments", params
            )

        window_start = start or datetime.now(timezone.utc)
        cutoff = end
        results = []
        for a in items:
            due_raw = a.get("due_at")
            locked = a.get("locked_for_user", False)
            if locked:
                log.debug("  skipping locked assignment: %s", a.get("name"))
                continue
            if upcoming_only and due_raw:
                due_dt = _parse_due(due_raw)
                if due_dt is None:
                    continue
                if due_dt < window_start:
                    log.debug("  skipping past-due assignment: %s (due %s)", a.get("name"), due_raw)
                    continue
                if cutoff and due_dt > cutoff:
                    log.debug("  skipping beyond window end: %s (due %s)", a.get("name"), due_raw)
                    continue

            # Convert to Eastern for LLM display
            due_et = _parse_due(due_raw) if due_raw else None
            results.append(
                {
                    "id": a["id"],
                    "name": a.get("name", ""),
                    "due_at": _format_due(due_et) if due_et else due_raw,
                    "points_possible": a.get("points_possible"),
                    "html_url": a.get("html_url", ""),
                    "has_submitted": _has_submitted(a),
                }
            )
        log.info("get_assignments → %d results (from %d raw)", len(results), len(items))
        return results

    def get_quizzes(
        self,
        course_id: int,
        *,
        upcoming_only: bool = False,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict]:
        """Return quizzes for a course, optionally only upcoming ones.

        Args:
            start: With ``upcoming_only``, skip items due before this
                   (aware datetime). Defaults to now.
            end:   With ``upcoming_only``, skip items due after this
                   (aware datetime). ``None`` means no upper bound.
        """
        log.info("get_quizzes(course_id=%s, upcoming_only=%s, start=%s, end=%s)",
                 course_id, upcoming_only, start, end)
        if upcoming_only:
            items = self._get_single_page(f"/courses/{course_id}/quizzes")
        else:
            items = self._get_paginated(f"/courses/{course_id}/quizzes")

        window_start = start or datetime.now(timezone.utc)
        cutoff = end
        results = []
        for q in items:
            due_raw = q.get("due_at")
            locked = q.get("locked_for_user", False)
            if locked:
                log.debug("  skipping locked quiz: %s", q.get("title"))
                continue
            if upcoming_only and due_raw:
                due_dt = _parse_due(due_raw)
                if due_dt is None:
                    continue
                if due_dt < window_start:
                    log.debug("  skipping past-due quiz: %s (due %s)", q.get("title"), due_raw)
                    continue
                if cutoff and due_dt > cutoff:
                    log.debug("  skipping beyond window end: %s (due %s)", q.get("title"), due_raw)
                    continue

            # Convert to Eastern for LLM display
            due_et = _parse_due(due_raw) if due_raw else None
            results.append(
                {
                    "id": q["id"],
                    "title": q.get("title", ""),
                    "due_at": _format_due(due_et) if due_et else due_raw,
                    "points_possible": q.get("points_possible"),
                    "html_url": q.get("html_url", ""),
                    #"time_limit": q.get("time_limit"),
                    #"question_count": q.get("question_count"),
                }
            )
        log.info("get_quizzes → %d results (from %d raw)", len(results), len(items))
        return results

    def get_assignment_detail(self, course_id: int, assignment_id: int) -> dict:
        """Return full detail for a single assignment."""
        log.info("get_assignment_detail(course_id=%s, assignment_id=%s)", course_id, assignment_id)
        log.debug("API GET /courses/%s/assignments/%s", course_id, assignment_id)
        resp = self._client.get(
            f"/courses/{course_id}/assignments/{assignment_id}",
            params={"include[]": "submission"},
        )
        resp.raise_for_status()
        a = resp.json()
        log.debug("  → assignment: %s", a.get("name", "?"))
        due_raw = a.get("due_at")
        due_et = _parse_due(due_raw) if due_raw else None
        return {
            "id": a["id"],
            "name": a.get("name", ""),
            #"description_text": _strip_html(a.get("description", "") or ""),
            "due_at": _format_due(due_et) if due_et else due_raw,
            "points_possible": a.get("points_possible"),
            "html_url": a.get("html_url", ""),
            #"submission_types": a.get("submission_types", []),
            "has_submitted": _has_submitted(a),
        }

    def close(self) -> None:
        self._client.close()


# ── private helpers ──────────────────────────────────────────────────

_ET = ZoneInfo("America/New_York")


def _parse_due(due_str: str) -> datetime | None:
    """Parse a Canvas UTC date string and return an Eastern-time datetime."""
    try:
        dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        return dt.astimezone(_ET)
    except (ValueError, TypeError):
        return None


def _format_due(dt: datetime) -> str:
    """Format an Eastern-time datetime as a human-readable ISO string."""
    return dt.isoformat()


def _next_link(response: httpx.Response) -> str | None:
    """Parse the ``Link`` header for the next page URL."""
    link_header = response.headers.get("link", "")
    for part in link_header.split(","):
        if 'rel="next"' in part:
            url = part.split(";")[0].strip().strip("<>")
            return url
    return None


def _has_submitted(assignment: dict) -> bool:
    sub = assignment.get("submission") or {}
    return sub.get("workflow_state", "unsubmitted") != "unsubmitted"


def _strip_html(html: str) -> str:
    """Very basic HTML tag stripper (no external dependency)."""
    import re

    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()
