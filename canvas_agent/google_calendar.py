"""Google Calendar client — OAuth2 auth + event creation."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

# If modifying scopes, delete token.json so the user re-authorises.
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# Where we persist the OAuth refresh token between runs.
_TOKEN_PATH = Path(__file__).resolve().parent.parent / "token.json"


def _get_credentials() -> Credentials:
    """Return valid Google OAuth2 credentials, prompting if needed.

    Credential sources (checked in order):
    1. Cached ``token.json`` file (from a previous local OAuth flow).
    2. ``GOOGLE_REFRESH_TOKEN`` env var — useful for deployed environments
       (e.g. Streamlit Cloud) where a browser-based OAuth flow is impossible.
    3. Interactive ``InstalledAppFlow`` (opens browser — local only).
    """
    creds: Credentials | None = None

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    # 1. Load previously saved token file
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)
        log.debug("Loaded cached Google credentials from %s", _TOKEN_PATH)

    # 2. Build credentials from GOOGLE_REFRESH_TOKEN env var
    if (not creds or not creds.valid) and not (creds and creds.expired and creds.refresh_token):
        refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
        if refresh_token and client_id and client_secret:
            log.info("Building Google credentials from GOOGLE_REFRESH_TOKEN env var")
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=_SCOPES,
            )

    # Refresh expired credentials
    if creds and creds.expired and creds.refresh_token:
        log.info("Google credentials expired — refreshing")
        creds.refresh(Request())

    # 3. Fall back to interactive browser OAuth flow (local only)
    if not creds or not creds.valid:
        log.info("No valid Google credentials — starting OAuth flow")
        if not client_id or not client_secret:
            raise RuntimeError(
                "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env "
                "to use Google Calendar integration."
            )

        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:8501"],
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, _SCOPES)
        creds = flow.run_local_server(port=0)
        log.info("OAuth flow completed successfully")

    # Save for next time (skip in deployed envs where filesystem may be read-only)
    try:
        _TOKEN_PATH.write_text(creds.to_json())
        log.debug("Saved Google credentials to %s", _TOKEN_PATH)
    except OSError:
        log.debug("Could not save token.json (read-only filesystem?), skipping")
    return creds


def _require_client_env() -> tuple[str, str]:
    """Return (client_id, client_secret) or raise RuntimeError."""
    cid = os.environ.get("GOOGLE_CLIENT_ID", "")
    secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not cid or not secret:
        raise RuntimeError(
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to use "
            "Google Calendar integration."
        )
    return cid, secret


def get_auth_url(redirect_uri: str) -> tuple[str, str]:
    """Generate a Google OAuth authorization URL for the web redirect flow.

    Returns:
        (auth_url, state) — the caller should store *state* and verify it
        when the redirect comes back.
    """
    cid, secret = _require_client_env()
    client_config = {
        "web": {
            "client_id": cid,
            "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, _SCOPES, redirect_uri=redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    log.info("Generated web OAuth auth URL (state=%s)", state)
    return auth_url, state


def exchange_code(code: str, redirect_uri: str) -> Credentials:
    """Exchange an authorization *code* (from Google redirect) for credentials."""
    cid, secret = _require_client_env()
    client_config = {
        "web": {
            "client_id": cid,
            "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, _SCOPES, redirect_uri=redirect_uri)
    flow.fetch_token(code=code)
    log.info("Exchanged auth code for Google credentials")
    return flow.credentials


class GoogleCalendarClient:
    """Creates events on the user's primary Google Calendar."""

    def __init__(self, creds: Credentials | None = None) -> None:
        if creds is None:
            creds = _get_credentials()
        # Refresh if expired
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        self._service = build("calendar", "v3", credentials=creds)
        log.info("GoogleCalendarClient initialised")

    def _event_exists(self, summary: str, start_dt: datetime) -> bool:
        """Check if an event with the same title and start time already exists."""
        time_min = start_dt.isoformat()
        time_max = (start_dt + timedelta(minutes=1)).isoformat()

        result = (
            self._service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                q=summary,
                singleEvents=True,
                maxResults=5,
            )
            .execute()
        )
        for event in result.get("items", []):
            if event.get("summary", "").strip() == summary.strip():
                log.debug("  duplicate found: %s at %s", summary, start_dt)
                return True
        return False

    def create_event(
        self,
        summary: str,
        start_dt: datetime,
        description: str = "",
        reminder_minutes: int = 1440,
    ) -> dict:
        """Create a calendar event. Returns the created event dict."""
        event_body = {
            "summary": summary,
            "description": description,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": "America/New_York",
            },
            "end": {
                "dateTime": (start_dt + timedelta(minutes=30)).isoformat(),
                "timeZone": "America/New_York",
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": reminder_minutes},
                ],
            },
        }
        event = (
            self._service.events()
            .insert(calendarId="primary", body=event_body)
            .execute()
        )
        log.info("Created calendar event: %s → %s", summary, event.get("htmlLink"))
        return event

    def add_deadlines(
        self,
        deadlines: dict,
        *,
        course_filter: str | None = None,
    ) -> dict:
        """Add deadlines to Google Calendar.

        Args:
            deadlines: Dict from get_all_upcoming_deadlines
                       {course_name: [items...]}
            course_filter: Optional course name to limit to.
                           (The date window is applied when the
                           deadlines are fetched.)

        Returns:
            Summary dict with added/skipped/failed counts.
        """
        added = 0
        skipped = 0
        failed = 0
        added_items: list[str] = []

        for course_name, items in deadlines.items():
            if course_filter and course_filter.lower() not in course_name.lower():
                continue

            for item in items:
                # Get name — assignments use "name", quizzes use "title"
                name = item.get("name") or item.get("title") or "Untitled"
                due_at = item.get("due_at")
                item_type = item.get("type", "assignment")
                points = item.get("points_possible", "?")

                if not due_at:
                    log.debug("  skipping %s (no due date)", name)
                    skipped += 1
                    continue

                try:
                    due_dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    log.warning("  bad date for %s: %s", name, due_at)
                    failed += 1
                    continue

                summary = f"{course_name} — {name}"
                description = (
                    f"Type: {item_type}\n"
                    f"Points: {points}\n"
                    f"Course: {course_name}"
                )
                if item.get("html_url"):
                    description += f"\nLink: {item['html_url']}"

                # Skip duplicates
                try:
                    if self._event_exists(summary, due_dt):
                        log.info("  skipping duplicate: %s", summary)
                        skipped += 1
                        continue
                except Exception as exc:
                    log.warning("  duplicate check failed for %s: %s", summary, exc)

                try:
                    self.create_event(summary, due_dt, description)
                    added += 1
                    added_items.append(f"{name} (due {due_dt.strftime('%b %d %I:%M %p')})")
                except Exception as exc:
                    log.error("  failed to create event for %s: %s", name, exc)
                    failed += 1

        log.info("add_deadlines: added=%d, skipped=%d, failed=%d", added, skipped, failed)
        return {
            "added": added,
            "skipped_duplicates_or_no_date": skipped,
            "failed": failed,
            "added_items": added_items,
        }
