"""
Google Calendar API tools.
Agents use these to create, list, and delete calendar events.

Auth: The Cloud Run service account must have been granted
'Make changes to events' on the target calendar.
For personal calendars, share the calendar with the service account email.
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from google.auth import default as google_auth_default
from googleapiclient.discovery import build
from google.adk.tools.tool_context import ToolContext

SCOPES = ["https://www.googleapis.com/auth/calendar"]
_service = None


def _get_service():
    """Lazily builds and caches the Calendar API service."""
    global _service
    if _service is None:
        creds, _ = google_auth_default(scopes=SCOPES)
        _service = build("calendar", "v3", credentials=creds)
    return _service

def _to_ist(dt_str: str) -> str:
    """Ensures datetime string has IST offset (+05:30) appended.
    Accepts: '2026-04-09T14:00:00' → returns: '2026-04-09T14:00:00+05:30'
    If offset already present, returns as-is.
    """
    if "+" in dt_str or dt_str.endswith("Z"):
        return dt_str
    return dt_str + "+05:30"

def create_calendar_event(
    tool_context: ToolContext,
    title: str,
    start_datetime: str,
    end_datetime: str,
    description: str = "",
    attendees: str = "",
    location: str = "",
) -> dict:
    """
    Creates a new Google Calendar event.

    Args:
        title: Event title / summary.
        start_datetime: ISO-8601 datetime, e.g. "2025-08-01T10:00:00".
        end_datetime: ISO-8601 datetime, e.g. "2025-08-01T11:00:00".
        description: Optional event description.
        attendees: Optional list of attendee email addresses.
        location: Optional location string.

    Returns:
        dict with status, event_id, and event link.
    """
    calendar_id = tool_context.state.get("calendar_id",
                                          os.getenv("CALENDAR_ID", "primary"))
    service = _get_service()

    attendees_list = [a.strip() for a in attendees.split(",")] if attendees else []
    
    # Service accounts cannot add attendees without Domain-Wide Delegation.
    # We store them in the description so the info is not lost.
    attendees_note = ""
    if attendees_list:
        attendees_note = f"\nAttendees: {', '.join(attendees_list)}"

    # In calendar_tools.py, replace the event_body start/end lines:

    event_body = {
        "summary": title,
        "description": (description + attendees_note).strip(),
        "location": location,
        # Append +05:30 so Google Calendar never misreads the timezone
        "start": {"dateTime": _to_ist(start_datetime), "timeZone": "Asia/Kolkata"},
        "end":   {"dateTime": _to_ist(end_datetime),   "timeZone": "Asia/Kolkata"},
    }

    created = service.events().insert(calendarId=calendar_id, body=event_body,
                                       sendNotifications=False).execute()
    logging.info(f"[calendar_tools] Created event {created['id']}: {title}")
    return {
        "status": "success",
        "event_id": created["id"],
        "event_link": created.get("htmlLink", ""),
        "title": title,
        "start": start_datetime,
        "end": end_datetime,
    }


def list_calendar_events(
    tool_context: ToolContext,
    start_date: str = "",
    end_date: str = "",
    max_results: int = 15,
) -> dict:
    """
    Lists upcoming Google Calendar events in a date range.

    Args:
        start_date: ISO-8601 datetime for range start (defaults to now).
        end_date: ISO-8601 datetime for range end (defaults to 7 days from now).
        max_results: Maximum number of events to return (default 15).
    """
    calendar_id = tool_context.state.get("calendar_id",
                                          os.getenv("CALENDAR_ID", "primary"))
    service = _get_service()

    now = datetime.now(timezone.utc)
    time_min = start_date if start_date else now.isoformat()
    time_max = end_date  if end_date  else (now + timedelta(days=7)).isoformat()

    result = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = [
        {
            "id":          e["id"],
            "title":       e.get("summary", "(no title)"),
            "start":       e["start"].get("dateTime", e["start"].get("date")),
            "end":         e["end"].get("dateTime", e["end"].get("date")),
            "description": e.get("description", ""),
            "location":    e.get("location", ""),
            "attendees":   [a.get("email") for a in e.get("attendees", [])],
        }
        for e in result.get("items", [])
    ]

    return {"status": "success", "events": events, "count": len(events)}


def delete_calendar_event(
    tool_context: ToolContext,
    event_id: str,
) -> dict:
    """
    Deletes a Google Calendar event.

    Args:
        event_id: The event ID from list_calendar_events.
    """
    calendar_id = tool_context.state.get("calendar_id",
                                          os.getenv("CALENDAR_ID", "primary"))
    _get_service().events().delete(calendarId=calendar_id, eventId=event_id).execute()
    logging.info(f"[calendar_tools] Deleted event {event_id}")
    return {"status": "success", "event_id": event_id}