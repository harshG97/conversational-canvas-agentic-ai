"""Tool definitions exposed to the Claude agent for Canvas LMS queries."""

from __future__ import annotations

# Shared date-window inputs. The system prompt lists ready-made ranges
# for common phrases ("this week", "next week", ...).
_DATE_RANGE_PROPS = {
    "start_date": {
        "type": "string",
        "description": (
            "Optional: first day to include, YYYY-MM-DD (US Eastern). "
            "Defaults to today. Items already past due are never returned."
        ),
    },
    "end_date": {
        "type": "string",
        "description": (
            "Optional: last day to include, YYYY-MM-DD (US Eastern, inclusive). "
            "Defaults to 30 days from today."
        ),
    },
}

TOOLS = [
    {
        "name": "get_courses",
        "description": (
            "List all courses the student is currently enrolled in. "
            "Returns course id, name, code, and term."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_upcoming_assignments",
        "description": (
            "Get upcoming assignments (with due dates in the future) for a "
            "specific course. Returns assignment name, due date, points, "
            "submission status, and link. Filter by start_date / end_date."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "course_id": {
                    "type": "integer",
                    "description": "The Canvas course ID.",
                },
                **_DATE_RANGE_PROPS,
            },
            "required": ["course_id"],
        },
    },
    {
        "name": "get_upcoming_quizzes",
        "description": (
            "Get upcoming quizzes (with due dates in the future) for a "
            "specific course. Returns quiz title, due date, points, "
            "and link. Filter by start_date / end_date."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "course_id": {
                    "type": "integer",
                    "description": "The Canvas course ID.",
                },
                **_DATE_RANGE_PROPS,
            },
            "required": ["course_id"],
        },
    },
    {
        "name": "get_all_upcoming_deadlines",
        "description": (
            "Get ALL upcoming assignments and quizzes across ALL enrolled "
            "courses. Use this when the user asks about upcoming deadlines "
            "without specifying a particular course. Filter by "
            "start_date / end_date."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_DATE_RANGE_PROPS,
            },
            "required": [],
        },
    },
    {
        "name": "force_refresh_data",
        "description": (
            "Clear cached Canvas data so the next query fetches fresh "
            "results from the Canvas API. Use this when the user says "
            "the data looks stale or asks to refresh/reload."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "add_deadlines_to_calendar",
        "description": (
            "Add upcoming assignment and quiz deadlines to the user's "
            "Google Calendar. Fetches all upcoming deadlines and creates "
            "calendar events with reminders. Skips duplicates already in "
            "the calendar. Optionally filter by course name and/or "
            "start_date / end_date."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "course_name": {
                    "type": "string",
                    "description": (
                        "Optional: only add deadlines for this course. "
                        "Matches partially (e.g. 'CS 6601' matches "
                        "'CS 6601 - Artificial Intelligence')."
                    ),
                },
                **_DATE_RANGE_PROPS,
            },
            "required": [],
        },
    },
]
