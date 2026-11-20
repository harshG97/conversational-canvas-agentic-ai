"""Dispatches tool calls from the LLM to the Canvas API client."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .canvas_client import CanvasClient
from .dates import resolve_range

log = logging.getLogger(__name__)

# Friendly labels for tool names shown to the user
_TOOL_LABELS = {
    "get_courses": "Fetching your courses",
    "get_upcoming_assignments": "Checking upcoming assignments",
    "get_upcoming_quizzes": "Checking upcoming quizzes",
    "get_all_upcoming_deadlines": "Scanning all courses for deadlines",
    #"get_assignment_detail": "Getting assignment details",
    "force_refresh_data": "Clearing cache",
    "add_deadlines_to_calendar": "Adding deadlines to Google Calendar",
}

# Cache TTL in seconds per tool
_CACHE_TTL = {
    "get_courses": 86400,            # 1 day
    "get_upcoming_assignments": 3600, # 1 hour
    "get_upcoming_quizzes": 3600,     # 1 hour
    "get_all_upcoming_deadlines": 3600,  # 1 hour
    #"get_assignment_detail": 3600,  # 1 hour
}

import re
from typing import Any

def parse_days_ahead(value: Any, default: int = 30, min_days: int = 1, max_days: int = 150) -> int:
    """
    Parse a 'days ahead' input into an int with clamping.

    Accepts:
      - int/float/str
      - strings like: "30", "30 days", "4 weeks", "2 months", "in 7"
    Notes:
      - "month" is approximated as 30 days.
    """
    try:
        # Fast path: already numeric-ish
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            n = int(value)
        else:
            s = str(value).strip().lower()
            m = re.search(r"-?\d+", s)
            if not m:
                n = default
            else:
                n = int(m.group())

            if "week" in s:
                n *= 7
            elif "month" in s:
                n *= 30
    except Exception:
        n = default

    # Clamp
    if n < min_days:
        return min_days
    if n > max_days:
        return max_days
    return n


def resolve_tool_range(inp: dict):
    """Resolve a tool's start_date / end_date (or legacy days_ahead) input."""
    days = inp.get("days_ahead")
    return resolve_range(
        inp.get("start_date"),
        inp.get("end_date"),
        days_ahead=parse_days_ahead(days) if days is not None else None,
    )


class ToolHandler:
    """Executes tool calls requested by the agent and returns JSON results."""

    def __init__(
        self,
        client: CanvasClient,
        *,
        google_creds=None,
        on_tool_start: Callable[[str], None] | None = None,
        on_tool_detail: Callable[[str], None] | None = None,
    ) -> None:
        self._client = client
        self._google_creds = google_creds
        self._on_tool_start = on_tool_start
        self._on_tool_detail = on_tool_detail
        self._cache: dict[str, tuple[float, object]] = {}

    def _emit_start(self, tool_name: str) -> None:
        label = _TOOL_LABELS.get(tool_name, tool_name)
        if self._on_tool_start:
            try:
                self._on_tool_start(label)
            except Exception:
                pass

    def _emit_detail(self, msg: str) -> None:
        if self._on_tool_detail:
            try:
                self._on_tool_detail(msg)
            except Exception:
                pass

    def _cache_key(self, tool_name: str, tool_input: dict) -> str:
        """Build a cache key from tool name and its input args."""
        sorted_input = json.dumps(tool_input, sort_keys=True)
        return f"{tool_name}:{sorted_input}"

    def _get_cached(self, tool_name: str, tool_input: dict) -> object | None:
        """Return cached result if valid, else None."""
        key = self._cache_key(tool_name, tool_input)
        entry = self._cache.get(key)
        if entry is None:
            return None
        cached_at, data = entry
        ttl = _CACHE_TTL.get(tool_name, 300)
        if time.time() - cached_at > ttl:
            del self._cache[key]
            return None
        return data

    def _set_cached(self, tool_name: str, tool_input: dict, data: object) -> None:
        key = self._cache_key(tool_name, tool_input)
        self._cache[key] = (time.time(), data)

    def clear_cache(self) -> None:
        """Clear all cached results."""
        log.info("Cache cleared (%d entries removed)", len(self._cache))
        self._cache.clear()

    def execute(self, tool_name: str, tool_input: dict) -> str:
        """Run *tool_name* with *tool_input* and return a JSON string."""
        log.info("execute tool=%s  input=%s", tool_name, tool_input)
        self._emit_start(tool_name)

        # Handle force refresh
        if tool_name == "force_refresh_data":
            self.clear_cache()
            return json.dumps({"status": "Cache cleared. Next queries will fetch fresh data from Canvas."})

        # Handle calendar — not cached, needs special flow
        if tool_name == "add_deadlines_to_calendar":
            return self._add_to_calendar(tool_input)

        # Check cache
        cached = self._get_cached(tool_name, tool_input)
        if cached is not None:
            log.info("CACHE HIT for %s", tool_name)
            self._emit_detail("Using cached data")
            result_json = json.dumps(cached, default=str)
            log.debug("  cached result: %d chars", len(result_json))
            return result_json

        log.info("CACHE MISS for %s — calling Canvas API", tool_name)
        try:
            result = self._dispatch(tool_name, tool_input)
            self._set_cached(tool_name, tool_input, result)
            result_json = json.dumps(result, default=str)
            log.info("Tool %s completed — result: %d chars", tool_name, len(result_json))
            return result_json
        except Exception as exc:
            log.error("Tool %s FAILED: %s", tool_name, exc)
            return json.dumps({"error": str(exc)})

    def _dispatch(self, name: str, inp: dict) -> object:
        if name == "get_courses":
            return self._client.get_courses()

        if name == "get_upcoming_assignments":
            start, end = resolve_tool_range(inp)
            return self._client.get_assignments(
                inp["course_id"], upcoming_only=True, start=start, end=end,
            )

        if name == "get_upcoming_quizzes":
            start, end = resolve_tool_range(inp)
            return self._client.get_quizzes(
                inp["course_id"], upcoming_only=True, start=start, end=end,
            )

        if name == "get_all_upcoming_deadlines":
            return self._get_all_upcoming(inp)

        # if name == "get_assignment_detail":
        #     return self._client.get_assignment_detail(
        #         inp["course_id"], inp["assignment_id"]
        #     )

        raise ValueError(f"Unknown tool: {name}")

    def _get_all_upcoming(self, inp: dict) -> dict:
        start, end = resolve_tool_range(inp)
        log.info("_get_all_upcoming: fetching courses (window %s → %s)", start, end)
        courses = self._client.get_courses()
        self._emit_detail(f"Found {len(courses)} active courses")
        log.info("_get_all_upcoming: scanning %d courses concurrently", len(courses))
        deadlines: dict[str, list] = {}

        def _fetch_course(course: dict) -> tuple[str, list]:
            cid = course["id"]
            cname = course["name"]
            log.debug("  fetching deadlines for: %s (id=%s)", cname, cid)
            self._emit_detail(f"Checking: {cname}")
            try:
                assignments = self._client.get_assignments(
                    cid, upcoming_only=True, start=start, end=end
                )
            except Exception as exc:
                log.warning("  assignments failed for %s: %s", cname, exc)
                assignments = []
            try:
                quizzes = self._client.get_quizzes(
                    cid, upcoming_only=True, start=start, end=end
                )
            except Exception as exc:
                log.warning("  quizzes failed for %s: %s", cname, exc)
                quizzes = []
            # Drop assignment copies of classic quizzes we already have,
            # and label New Quizzes (assignment-only) as quizzes.
            quiz_ids = {q.get("id") for q in quizzes}
            dupes = [a for a in assignments if a.get("quiz_id") in quiz_ids]
            if dupes:
                log.debug("  %s: dropping %d quiz duplicates", cname, len(dupes))
            items = [
                {**a, "type": "quiz" if a.get("is_new_quiz") else "assignment"}
                for a in assignments
                if a.get("quiz_id") not in quiz_ids
            ] + [
                {**q, "type": "quiz"} for q in quizzes
            ]
            log.debug("  %s: %d assignments, %d quizzes", cname, len(assignments), len(quizzes))
            return cname, items

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(_fetch_course, c) for c in courses]
            for future in as_completed(futures):
                try:
                    cname, items = future.result()
                except Exception as exc:
                    log.warning("  course future failed: %s", exc)
                    continue
                if items:
                    deadlines[cname] = sorted(
                        items, key=lambda x: x.get("due_at") or "9999"
                    )

        total = sum(len(v) for v in deadlines.values())
        log.info("_get_all_upcoming: %d total deadlines across %d courses", total, len(deadlines))
        return deadlines

    def _add_to_calendar(self, inp: dict) -> str:
        """Fetch deadlines and add them to Google Calendar."""
        from .google_calendar import GoogleCalendarClient

        course_filter = inp.get("course_name")
        range_inp = {
            k: inp[k] for k in ("start_date", "end_date", "days_ahead") if k in inp
        }
        log.info("_add_to_calendar: course_filter=%s, range=%s", course_filter, range_inp)

        # Fetch deadlines for the requested window
        self._emit_detail("Fetching upcoming deadlines...")
        try:
            deadlines = self._dispatch("get_all_upcoming_deadlines", range_inp)
        except Exception as exc:
            log.error("Failed to fetch deadlines for calendar: %s", exc)
            return json.dumps({"error": f"Failed to fetch deadlines: {exc}"})

        if not deadlines:
            return json.dumps({"status": "No upcoming deadlines to add."})

        # Create Google Calendar events
        self._emit_detail("Connecting to Google Calendar...")
        try:
            gcal = GoogleCalendarClient(creds=self._google_creds)
        except Exception as exc:
            log.error("Google Calendar auth failed: %s", exc)
            return json.dumps({"error": str(exc)})

        self._emit_detail("Creating calendar events...")
        try:
            result = gcal.add_deadlines(deadlines, course_filter=course_filter)
            log.info("Calendar result: %s", result)
            return json.dumps(result, default=str)
        except Exception as exc:
            log.error("Failed to add events: %s", exc)
            return json.dumps({"error": f"Failed to create calendar events: {exc}"})
