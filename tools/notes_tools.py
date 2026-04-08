"""
Firestore-backed notes / knowledge management tools.
Agents use these to save and retrieve user knowledge snippets.
"""

import uuid
import logging
from datetime import datetime, timezone
from google.cloud import firestore
from google.adk.tools.tool_context import ToolContext

_db = firestore.Client()
NOTES_COLLECTION = "notes"


def create_note(
    tool_context: ToolContext,
    title: str,
    content: str,
    tags: str = "",
) -> dict:
    """
    Saves a new note to Firestore.

    Args:
        title: Short title for the note.
        content: The full body of the note.
        tags: Optional tags for categorisation (e.g. ["project-x", "client"]).
    """
    user_id = tool_context.state.get("user_id", "default_user")
    note_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    tags_list = [t.strip() for t in tags.split(",")] if tags else []
    
    note = {
        "id": note_id,
        "title": title,
        "content": content,
        "tags": tags_list,
        "user_id": user_id,
        "created_at": now,
        "updated_at": now,
    }

    _db.collection(NOTES_COLLECTION).document(note_id).set(note)
    logging.info(f"[notes_tools] Created note {note_id}: {title}")
    return {"status": "success", "note_id": note_id, "note": note}


def search_notes(
    tool_context: ToolContext,
    query: str = "",
    tag: str = "",
) -> dict:
    """
    Searches notes by keyword (title or content) and/or tag.

    Args:
        query: Keyword to search for in title or content.
        tag: Only return notes with this tag.
    """
    user_id = tool_context.state.get("user_id", "default_user")
    notes_ref = _db.collection(NOTES_COLLECTION).where("user_id", "==", user_id)
    notes = [doc.to_dict() for doc in notes_ref.stream()]

    if query:
        q = query.lower()
        notes = [
            n for n in notes
            if q in n.get("title", "").lower() or q in n.get("content", "").lower()
        ]
    if tag:
        notes = [n for n in notes if tag in n.get("tags", [])]

    return {"status": "success", "notes": notes, "count": len(notes)}


def update_note(
    tool_context: ToolContext,
    note_id: str,
    title: str = "",
    content: str = "",
    tags: str = "",
) -> dict:
    """Updates an existing note. Pass only the fields to change."""
    updates: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if title:
        updates["title"] = title
    if content:
        updates["content"] = content
    if tags is not None:
        updates["tags"] = tags

    _db.collection(NOTES_COLLECTION).document(note_id).update(updates)
    return {"status": "success", "note_id": note_id}


def delete_note(tool_context: ToolContext, note_id: str) -> dict:
    """Permanently deletes a note."""
    _db.collection(NOTES_COLLECTION).document(note_id).delete()
    return {"status": "success", "note_id": note_id}