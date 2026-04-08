"""
Firestore-backed task management tools.
Each function is registered as an ADK tool — Gemini can call these
to create, query, update, and complete tasks on behalf of the user.
"""

import uuid
import logging
from datetime import datetime, timezone
from google.cloud import firestore
from google.adk.tools.tool_context import ToolContext
from .calendar_tools import create_calendar_event

_db = firestore.Client()
TASKS_COLLECTION = "tasks"

def create_task(
    tool_context: ToolContext,
    title: str,
    description: str = "",
    due_date: str = "",
    priority: str = "medium",
    tags: str = ""
) -> dict:

    user_id = tool_context.state.get("user_id", "default_user")
    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    tags_list = [t.strip() for t in tags.split(",")] if tags else []

    task = {
        "id": task_id,
        "title": title,
        "description": description,
        "due_date": due_date,
        "priority": priority,
        "tags": tags_list,
        "status": "pending",
        "user_id": user_id,
        "created_at": now,
        "updated_at": now,
    }

    _db.collection(TASKS_COLLECTION).document(task_id).set(task)

    if due_date:
        try:
            create_calendar_event(
                tool_context=tool_context,
                title=title,
                start_datetime=f"{due_date}T09:00:00",
                end_datetime=f"{due_date}T10:00:00",
                description=description or f"Task: {title}",
            )
            logging.info(f"[task_tools] Calendar event created for task {task_id}")
        except Exception as e:
            logging.warning(f"[task_tools] Failed to create calendar event: {e}")

    logging.info(f"[task_tools] Created task {task_id}: {title}")

    return {"status": "success", "task_id": task_id, "task": task}

def get_tasks(
    tool_context: ToolContext,
    status: str = "",
    priority: str = "",
    tag: str = "",
) -> dict:
    """
    Retrieves tasks for the current user, with optional filters.

    Args:
        status: Filter by status: "pending" or "completed".
        priority: Filter by priority: "low", "medium", or "high".
        tag: Filter tasks that include this tag.

    Returns:
        dict with status, list of tasks, and count.
    """
    user_id = tool_context.state.get("user_id", "default_user")
    query = _db.collection(TASKS_COLLECTION).where("user_id", "==", user_id)

    if status:
        query = query.where("status", "==", status)
    if priority:
        query = query.where("priority", "==", priority)

    tasks = [doc.to_dict() for doc in query.stream()]

    if tag:
        tasks = [t for t in tasks if tag in t.get("tags", [])]

    # Sort pending tasks by priority then due_date
    priority_order = {"high": 0, "medium": 1, "low": 2}
    tasks.sort(key=lambda t: (priority_order.get(t.get("priority", "medium"), 1),
                               t.get("due_date", "9999")))

    logging.info(f"[task_tools] Retrieved {len(tasks)} tasks for user {user_id}")
    return {"status": "success", "tasks": tasks, "count": len(tasks)}


def update_task(
    tool_context: ToolContext,
    task_id: str,
    title: str = "",
    description: str = "",
    due_date: str = "",
    priority: str = "",
) -> dict:
    """
    Updates fields on an existing task.

    Args:
        task_id: The ID of the task to update.
        title: New title (leave empty to keep existing).
        description: New description (leave empty to keep existing).
        due_date: New due date (leave empty to keep existing).
        priority: New priority (leave empty to keep existing).
    """
    updates: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if title:
        updates["title"] = title
    if description:
        updates["description"] = description
    if due_date:
        updates["due_date"] = due_date
    if priority:
        updates["priority"] = priority

    _db.collection(TASKS_COLLECTION).document(task_id).update(updates)
    return {"status": "success", "task_id": task_id, "updated_fields": list(updates.keys())}


def complete_task(tool_context: ToolContext, task_id: str) -> dict:
    """
    Marks a task as completed.

    Args:
        task_id: The ID of the task to complete.
    """
    _db.collection(TASKS_COLLECTION).document(task_id).update({
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    logging.info(f"[task_tools] Completed task {task_id}")
    return {"status": "success", "task_id": task_id, "new_status": "completed"}


def delete_task(tool_context: ToolContext, task_id: str) -> dict:
    """
    Permanently deletes a task.

    Args:
        task_id: The ID of the task to delete.
    """
    _db.collection(TASKS_COLLECTION).document(task_id).delete()
    return {"status": "success", "task_id": task_id}