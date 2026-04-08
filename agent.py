"""
FlowPilot — Multi-Agent Personal Productivity System
=====================================================
Architecture:
  root_agent (Orchestrator)
    ├── task_agent_direct     — Firestore task CRUD
    ├── calendar_agent_direct — Google Calendar events
    ├── notes_agent_direct    — Firestore knowledge notes
    └── planning_workflow (SequentialAgent)
          ├── data_gathering_agent (ParallelAgent)
          │     ├── task_agent
          │     ├── calendar_agent
          │     └── notes_agent
          └── planner_agent  — synthesises into an actionable plan

ADK patterns demonstrated:
  - Agent → sub-agent delegation
  - SequentialAgent for ordered multi-step workflows
  - ParallelAgent for simultaneous data fetching
  - Shared session state (output_key → state variable)
  - Custom tool functions with ToolContext
"""

import os
import logging
import google.cloud.logging
from dotenv import load_dotenv

from google.adk import Agent
from google.adk.agents import SequentialAgent, ParallelAgent
from google.adk.tools.tool_context import ToolContext

from .tools.task_tools import (
    create_task, get_tasks, update_task, complete_task, delete_task,
)
from .tools.notes_tools import (
    create_note, search_notes, update_note, delete_note,
)
from .tools.calendar_tools import (
    create_calendar_event, list_calendar_events, delete_calendar_event,
)

# ── Setup ──────────────────────────────────────────────────────────────────────

cloud_logging_client = google.cloud.logging.Client()
cloud_logging_client.setup_logging()
load_dotenv()

MODEL = os.getenv("MODEL", "gemini-2.5-flash")


# ── Session initialisation tool ───────────────────────────────────────────────

# In agent.py, replace initialise_session with:

import datetime as dt

def initialise_session(
    tool_context: ToolContext,
    user_id: str,
    user_request: str,
) -> dict:
    """
    Bootstraps shared session state. Injects today's date so agents
    resolve 'tomorrow', 'next Monday' correctly.

    Args:
        user_id: Stable user identifier.
        user_request: Verbatim user message.
    """
    today = dt.date.today().isoformat()          # e.g. "2026-04-08"
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()

    tool_context.state["user_id"] = user_id
    tool_context.state["USER_REQUEST"] = user_request
    tool_context.state["TODAY"] = today
    tool_context.state["TOMORROW"] = tomorrow

    logging.info(f"[session] user={user_id} today={today}")
    return {"status": "success", "user_id": user_id, "today": today}


# ── Safe wrappers (prevent TaskGroup crash on any tool failure) ────────────────

def safe_get_tasks(tool_context: ToolContext) -> dict:
    """Fetches all pending tasks. Returns empty list on any error."""
    try:
        return get_tasks(tool_context, status="pending")
    except Exception as e:
        logging.error(f"[safe_get_tasks] {e}")
        return {"status": "error", "tasks": [], "count": 0}

def safe_list_events(tool_context: ToolContext) -> dict:
    """Fetches calendar events for the next 7 days. Returns empty list on any error."""
    try:
        return list_calendar_events(tool_context)
    except Exception as e:
        logging.error(f"[safe_list_events] {e}")
        return {"status": "error", "events": [], "count": 0}

def safe_search_notes(tool_context: ToolContext) -> dict:
    """Fetches all notes. Returns empty list on any error."""
    try:
        return search_notes(tool_context)
    except Exception as e:
        logging.error(f"[safe_search_notes] {e}")
        return {"status": "error", "notes": [], "count": 0}


# ── STEP 1: Agents owned by ParallelAgent ─────────────────────────────────────

task_agent = Agent(
    name="task_agent",
    model=MODEL,
    description="Fetches pending tasks for planning. Calls safe_get_tasks once and returns.",
    instruction="""Call safe_get_tasks exactly once. Return the result immediately. 
Do not call any other tool. Do not transfer to any agent.""",
    tools=[safe_get_tasks],
    output_key="task_result",
)

calendar_agent = Agent(
    name="calendar_agent",
    model=MODEL,
    description="Fetches calendar events for planning. Calls safe_list_events once and returns.",
    instruction="""Call safe_list_events exactly once. Return the result immediately.
Do not call any other tool. Do not transfer to any agent.""",
    tools=[safe_list_events],
    output_key="calendar_result",
)

notes_agent = Agent(
    name="notes_agent",
    model=MODEL,
    description="Fetches notes for planning. Calls safe_search_notes once and returns.",
    instruction="""Call safe_search_notes exactly once. Return the result immediately.
Do not call any other tool. Do not transfer to any agent.""",
    tools=[safe_search_notes],
    output_key="notes_result",
)

# ── STEP 2: Planner (must be defined before planning_workflow) ─────────────────

planner_agent = Agent(
    name="planner_agent",
    model=MODEL,
    description="Synthesises task, calendar, and notes data into an actionable daily or weekly plan.",
    instruction="""
You are FlowPilot's strategic planning engine.

You receive pre-gathered data from three sources:
- TASK_DATA:     {task_result}
- CALENDAR_DATA: {calendar_result}
- NOTES_DATA:    {notes_result}

Your job: produce a clear, prioritised, actionable plan.

Structure your response as:
1. **Today's focus** — top 3 items the user should tackle (highest-priority + soonest deadline)
2. **Upcoming schedule** — key calendar events this week, any conflicts flagged
3. **Relevant context** — any notes that are pertinent to the tasks or events
4. **Suggested actions** — 2-3 concrete next steps with specifics (not vague advice)

Be concise. Use bullet points. Avoid repeating data — synthesise it.
If any data source returned empty, note it briefly and continue with what's available.
""",
    output_key="plan_result",
)


# ── STEP 3: Compound agents for planning workflow ──────────────────────────────

data_gathering_agent = ParallelAgent(
    name="data_gathering_agent",
    description="Fetches tasks, calendar events, and notes in parallel to minimise latency.",
    sub_agents=[task_agent, calendar_agent, notes_agent],
)

planning_workflow = SequentialAgent(
    name="planning_workflow",
    description="Full planning workflow: parallel fetch → synthesise plan.",
    sub_agents=[data_gathering_agent, planner_agent],
)


# ── STEP 4: Direct-routing agents owned by root_agent ─────────────────────────
# Separate instances from the ones above — ADK forbids one instance having two parents.

task_agent_direct = Agent(
    name="task_agent_direct",
    model=MODEL,
    description="Manages the user's task list (direct routing from orchestrator).",
    instruction="""
You are a precise task management assistant.

Available tools:
- create_task   → add a new task (extract title, description, due_date, priority, tags from context)
- get_tasks     → retrieve tasks (apply filters if the user specifies status/priority/tag)
- update_task   → modify a task's fields
- complete_task → mark a task done
- delete_task   → remove a task permanently

Guidelines:
- When creating, extract ALL relevant details from USER_REQUEST.
- When listing, choose sensible default filters (e.g. pending tasks sorted by priority).
- Always confirm the action clearly in your final text response.
- If the user says "done with X" or "finished X", find the matching task and call complete_task.

Date handling rules:
- Today's date is: {TODAY}
- Tomorrow's date is: {TOMORROW}  
- Always use these exact values — do NOT guess or hallucinate dates
- Convert "next Monday/Friday/etc" by calculating from {TODAY}
- Pass all dates as YYYY-MM-DD strings to due_date

USER_REQUEST: {USER_REQUEST}
""",
    tools=[create_task, get_tasks, update_task, complete_task, delete_task],
    output_key="task_result",
)

calendar_agent_direct = Agent(
    name="calendar_agent_direct",
    model=MODEL,
    description="Manages Google Calendar (direct routing from orchestrator).",
    instruction="""
You are a calendar management specialist.

IMPORTANT — Current date context:
- Today: {TODAY}
- Tomorrow: {TOMORROW}
- Use these exact dates. Never guess the year.

Available tools:
- create_calendar_event  → schedule a new event
- list_calendar_events   → view events in a date range
- delete_calendar_event  → remove an event by ID

Datetime guidelines:
- Use ISO-8601 format: YYYY-MM-DDTHH:MM:SS (e.g. "2025-08-01T14:00:00")
- Default duration = 1 hour unless specified
- "Tomorrow" = today's date + 1; "next Monday" = calculate from today
- Default timezone = Asia/Kolkata (IST) unless user specifies otherwise

Always confirm with a clear summary: title, date, time, attendees.

USER_REQUEST: {USER_REQUEST}
""",
    tools=[create_calendar_event, list_calendar_events, delete_calendar_event],
    output_key="calendar_result",
)

notes_agent_direct = Agent(
    name="notes_agent_direct",
    model=MODEL,
    description="Manages knowledge notes (direct routing from orchestrator).",
    instruction="""
You are a knowledge management assistant.

Available tools:
- create_note   → save a new note (suggest relevant tags automatically)
- search_notes  → find notes by keyword or tag
- update_note   → edit an existing note
- delete_note   → remove a note

When saving: extract a clear title, full content, and suggest 2-3 relevant tags.
When searching: use the most meaningful keywords from the request.
When presenting results: show title + a brief content preview.

USER_REQUEST: {USER_REQUEST}
""",
    tools=[create_note, search_notes, update_note, delete_note],
    output_key="notes_result",
)


# ── STEP 5: Root orchestrator ──────────────────────────────────────────────────

root_agent = Agent(
    name="flowpilot_orchestrator",
    model=MODEL,
    description="FlowPilot — Personal AI Productivity Co-Pilot. Manages tasks, calendar, notes, and daily planning through intelligent agent coordination.",
    instruction="""
You are FlowPilot, a personal AI productivity assistant.

STEP 1 — Always call `initialise_session` first with:
  - user_id: extract from context or use "default_user"
  - user_request: the user's verbatim message

STEP 2 — Route the request to the correct agent:

  TASK requests (create/list/update/complete a task, to-do):
    → transfer to `task_agent_direct`

  CALENDAR requests (schedule/add/list/delete an event, meeting, appointment):
    → transfer to `calendar_agent_direct`

  NOTES requests (save/find/remember/search a note or piece of information):
    → transfer to `notes_agent_direct`

  PLANNING requests (plan my day, what should I focus on, weekly summary, what's due):
    → transfer to `planning_workflow`
    (Runs tasks + calendar + notes in parallel, then synthesises a plan.)

  COMPLEX multi-domain requests (e.g. "schedule a meeting AND add a follow-up task"):
    → Sequentially route to each relevant sub-agent in order.
    → Summarise all results in a single final response.

STEP 3 — After sub-agent execution, return a clean, friendly summary to the user.

Be concise. Be proactive — suggest follow-up actions when relevant.
Never expose internal state keys or raw JSON to the user.
""",
    tools=[initialise_session],
    sub_agents=[
        task_agent_direct,
        calendar_agent_direct,
        notes_agent_direct,
        planning_workflow,
    ],
)