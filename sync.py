#!/usr/bin/env python3
"""Omi-to-WebDAV Sync Service.

Synchronizes conversation transcripts from the Omi wearable application
to a WebDAV server as Markdown files.
"""

import json
import logging
import os
import signal
import sys
import time
import unicodedata
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import frontmatter
import requests
import xxhash
from pathvalidate import sanitize_filename
from webdav4.client import Client as WebDAVClient

# Configuration from environment
OMI_API_KEY = os.environ.get("OMI_API_KEY", "")
WEBDAV_URL = os.environ.get("WEBDAV_URL", "")
WEBDAV_USER = os.environ.get("WEBDAV_USER", "")
WEBDAV_PASS = os.environ.get("WEBDAV_PASS", "")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/conversations")
SYNC_INTERVAL_SECONDS = int(os.environ.get("SYNC_INTERVAL_SECONDS", "300"))
STATE_FILE = Path("/app/state/sync_state.json")

# Omi API constants
OMI_API_BASE = "https://api.omi.me/v1/dev"
OMI_PAGE_SIZE = 25
OMI_REQUEST_DELAY = 0.15  # 150ms between paginated requests

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
running = True


def signal_handler(signum: int, frame: Any) -> None:
    """Handle SIGTERM and SIGINT for graceful shutdown."""
    global running
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name}, initiating graceful shutdown...")
    running = False


def validate_config() -> bool:
    """Validate required configuration is present."""
    missing = []
    if not OMI_API_KEY:
        missing.append("OMI_API_KEY")
    if not WEBDAV_URL:
        missing.append("WEBDAV_URL")
    if not WEBDAV_USER:
        missing.append("WEBDAV_USER")
    if not WEBDAV_PASS:
        missing.append("WEBDAV_PASS")

    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        return False
    return True


def load_state() -> dict:
    """Load sync state from file, returning empty state if not found."""
    if not STATE_FILE.exists():
        return {"version": 1, "last_sync": None, "conversations": {}}

    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
            logger.info(f"Loaded state with {len(state.get('conversations', {}))} conversations")
            return state
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to read state file: {e}")
        return {"version": 1, "last_sync": None, "conversations": {}}


def save_state(state: dict) -> None:
    """Save sync state atomically."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = STATE_FILE.with_suffix(".json.tmp")

    try:
        with open(temp_file, "w") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        temp_file.rename(STATE_FILE)
        logger.info("State saved successfully")
    except OSError as e:
        logger.error(f"Failed to save state file: {e}")


def compute_content_hash(conversation: dict) -> str:
    """Compute xxhash64 of conversation content (structured + transcript_segments)."""
    # Extract only content-relevant fields
    content = {
        "structured": conversation.get("structured", {}),
        "transcript_segments": conversation.get("transcript_segments", []),
    }
    # Normalize to JSON with sorted keys for consistent hashing
    normalized = json.dumps(content, sort_keys=True, ensure_ascii=False)
    hash_value = xxhash.xxh64(normalized.encode("utf-8")).hexdigest()
    # Truncate to 16 characters
    return hash_value[:16]


def sanitize_title(title: str) -> str:
    """Sanitize conversation title for use as filename."""
    if not title or not title.strip():
        return "Untitled"

    # Normalize Unicode to NFC form
    normalized = unicodedata.normalize("NFC", title.strip())

    # Use pathvalidate for cross-platform safety
    sanitized = sanitize_filename(
        normalized,
        max_len=200,
        replacement_text="-",
    )

    # Remove consecutive spaces
    while "  " in sanitized:
        sanitized = sanitized.replace("  ", " ")

    # Strip leading/trailing spaces
    sanitized = sanitized.strip()

    return sanitized if sanitized else "Untitled"


def generate_filename(title: str, created_at: str, state: dict) -> str:
    """Generate unique filename, appending date suffix for duplicates."""
    base_name = sanitize_title(title)

    # Check if this exact filename is already in use by another conversation
    filename = f"{base_name}.md"
    existing_files = {conv["filename"] for conv in state.get("conversations", {}).values()}

    if filename not in existing_files:
        return filename

    # Append date suffix for duplicates (MMDDYYYY format)
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        date_suffix = dt.strftime("%m%d%Y")
    except (ValueError, AttributeError):
        date_suffix = datetime.now(timezone.utc).strftime("%m%d%Y")

    return f"{base_name}_{date_suffix}.md"


def generate_markdown(conversation: dict, content_hash: str) -> str:
    """Generate Markdown content with YAML front matter."""
    structured = conversation.get("structured", {})
    transcript_segments = conversation.get("transcript_segments", [])

    # Build front matter
    metadata = {
        "title": structured.get("title", "Untitled"),
        "date": conversation.get("created_at", ""),
        "category": structured.get("category", ""),
        "_omi_id": conversation.get("id", ""),
        "_content_hash": content_hash,
        "_synced_at": datetime.now(timezone.utc).isoformat(),
    }

    # Build body
    body_parts = []

    # Summary section
    body_parts.append("## Summary")
    body_parts.append("")  # blank line after heading
    overview = structured.get("overview", "").strip()
    body_parts.append(overview if overview else "No summary available.")

    # Transcript section (only if segments exist)
    if transcript_segments:
        body_parts.append("")  # blank line before section
        body_parts.append("## Transcript")
        body_parts.append("")  # blank line after heading
        for segment in transcript_segments:
            speaker_id = segment.get("speaker_id", 0)
            text = segment.get("text", "").strip()
            if text:
                body_parts.append(f"**Speaker {speaker_id}:** {text}")

    body = "\n".join(body_parts)

    # Create post with frontmatter
    post = frontmatter.Post(body)
    post.metadata = metadata

    return frontmatter.dumps(post)


def fetch_conversations() -> list[dict] | None:
    """Fetch all conversations from Omi API with pagination. Returns None on error."""
    conversations = []
    offset = 0
    headers = {"Authorization": f"Bearer {OMI_API_KEY}"}

    while True:
        if not running:
            logger.info("Shutdown requested, stopping conversation fetch")
            break

        params = {
            "include_transcript": "true",
            "limit": OMI_PAGE_SIZE,
            "offset": offset,
        }

        try:
            response = requests.get(
                f"{OMI_API_BASE}/user/conversations",
                headers=headers,
                params=params,
                timeout=30,
            )

            if response.status_code == 429:
                # Rate limited - exponential backoff
                retry_after = int(response.headers.get("Retry-After", "60"))
                logger.warning(f"Rate limited, sleeping for {retry_after}s")
                time.sleep(retry_after)
                continue

            response.raise_for_status()
            page = response.json()

            if not page:
                break

            conversations.extend(page)
            logger.info(f"Fetched {len(page)} conversations (total: {len(conversations)})")

            if len(page) < OMI_PAGE_SIZE:
                break

            offset += OMI_PAGE_SIZE
            time.sleep(OMI_REQUEST_DELAY)

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch conversations: {e}")
            return None

    return conversations


def sync_conversation(
    conversation: dict,
    state: dict,
    webdav: WebDAVClient,
) -> bool:
    """Sync a single conversation to WebDAV. Returns True if file was created/updated."""
    conv_id = conversation.get("id", "")
    if not conv_id:
        logger.warning("Conversation missing ID, skipping")
        return False

    # Get current title and content hash from Omi
    structured = conversation.get("structured", {})
    current_title = structured.get("title", "Untitled")
    created_at = conversation.get("created_at", "")
    content_hash = compute_content_hash(conversation)

    # Check existing state
    conv_state = state.get("conversations", {}).get(conv_id)

    # Determine what changed
    title_changed = False
    content_changed = True  # Assume changed for new conversations
    old_filename = None

    if conv_state:
        stored_title = conv_state.get("title", "")
        stored_hash = conv_state.get("omi_hash", "")

        title_changed = stored_title and stored_title != current_title
        content_changed = stored_hash != content_hash

        if not title_changed and not content_changed:
            # Nothing changed, skip
            return False

    # Determine filename
    if conv_state and conv_state.get("filename"):
        if title_changed:
            # Title changed - need to rename the file
            old_filename = conv_state["filename"]
            # Temporarily remove old filename from state to allow reuse of similar names
            temp_state = state.copy()
            temp_state["conversations"] = {
                k: v for k, v in state.get("conversations", {}).items()
                if k != conv_id
            }
            filename = generate_filename(current_title, created_at, temp_state)
            logger.info(
                f"Title changed: '{stored_title}' -> '{current_title}', "
                f"renaming {old_filename} -> {filename}"
            )
        else:
            # No title change, use existing filename
            filename = conv_state["filename"]
    else:
        # Generate new filename for new conversation
        filename = generate_filename(current_title, created_at, state)

    remote_path = f"{OUTPUT_DIR}/{filename}"

    # Handle title-only change: use move() to preserve local file modifications
    if title_changed and not content_changed and old_filename:
        old_remote_path = f"{OUTPUT_DIR}/{old_filename}"
        try:
            if webdav.exists(old_remote_path):
                webdav.move(old_remote_path, remote_path, overwrite=True)
                logger.info(f"Renamed: {old_filename} -> {filename}")
            else:
                logger.warning(f"Old file not found for rename: {old_filename}")

            # Update state
            if "conversations" not in state:
                state["conversations"] = {}
            state["conversations"][conv_id] = {
                "omi_hash": content_hash,
                "filename": filename,
                "title": current_title,
            }
            return True

        except Exception as e:
            logger.error(f"Failed to rename {old_filename} -> {filename}: {e}")
            return False

    # Content changed: generate and upload new markdown
    markdown_content = generate_markdown(conversation, content_hash)

    # Check if file exists and preserve user metadata (frontmatter only)
    metadata_source_path = f"{OUTPUT_DIR}/{old_filename}" if old_filename else remote_path
    try:
        if webdav.exists(metadata_source_path):
            # Fetch existing file to preserve user-added front matter
            existing_content = webdav.read_bytes(metadata_source_path)
            try:
                existing_post = frontmatter.loads(existing_content.decode("utf-8"))
                new_post = frontmatter.loads(markdown_content)

                # Preserve user-added fields (those not prefixed with _)
                for key, value in existing_post.metadata.items():
                    if not key.startswith("_") and key not in new_post.metadata:
                        new_post.metadata[key] = value

                markdown_content = frontmatter.dumps(new_post)
            except Exception as e:
                logger.warning(f"Could not parse existing file {metadata_source_path}: {e}")
    except Exception as e:
        logger.warning(f"Could not check existing file {metadata_source_path}: {e}")

    # Upload to WebDAV
    try:
        content_bytes = markdown_content.encode("utf-8")
        webdav.upload_fileobj(BytesIO(content_bytes), remote_path, overwrite=True)

        # Delete old file if this was a rename operation
        if old_filename and old_filename != filename:
            old_remote_path = f"{OUTPUT_DIR}/{old_filename}"
            try:
                if webdav.exists(old_remote_path):
                    webdav.remove(old_remote_path)
                    logger.info(f"Deleted old file: {old_filename}")
            except Exception as e:
                logger.warning(f"Failed to delete old file {old_filename}: {e}")

        action = "Renamed" if old_filename else ("Updated" if conv_state else "Created")
        logger.info(f"{action}: {filename}")

        # Update state (include title for change detection)
        if "conversations" not in state:
            state["conversations"] = {}
        state["conversations"][conv_id] = {
            "omi_hash": content_hash,
            "filename": filename,
            "title": current_title,
        }

        return True

    except Exception as e:
        logger.error(f"Failed to upload {filename}: {e}")
        return False


def ensure_output_directory(webdav: WebDAVClient) -> bool:
    """Ensure the output directory exists on WebDAV server."""
    try:
        if not webdav.exists(OUTPUT_DIR):
            webdav.mkdir(OUTPUT_DIR)
            logger.info(f"Created output directory: {OUTPUT_DIR}")
        return True
    except Exception as e:
        logger.error(f"Failed to create output directory: {e}")
        return False


def handle_deletions(
    omi_conversation_ids: set[str],
    state: dict,
    webdav: WebDAVClient,
) -> int:
    """Delete files from WebDAV for conversations no longer in Omi. Returns count."""
    state_conv_ids = set(state.get("conversations", {}).keys())
    deleted_ids = state_conv_ids - omi_conversation_ids

    if not deleted_ids:
        return 0

    logger.info(f"Detected {len(deleted_ids)} deleted conversation(s)")
    deleted_count = 0

    for conv_id in deleted_ids:
        if not running:
            break

        conv_state = state["conversations"].get(conv_id)
        if not conv_state or not conv_state.get("filename"):
            del state["conversations"][conv_id]
            continue

        filename = conv_state["filename"]
        remote_path = f"{OUTPUT_DIR}/{filename}"

        try:
            if webdav.exists(remote_path):
                webdav.remove(remote_path)
                logger.info(f"Deleted: {filename} (conversation {conv_id} removed from Omi)")
            else:
                logger.info(f"File already gone: {filename}")

            del state["conversations"][conv_id]
            deleted_count += 1
        except Exception as e:
            logger.error(f"Failed to delete {filename}: {e}")
            # Keep in state to retry next cycle

    return deleted_count


def run_sync_cycle(state: dict) -> dict:
    """Run a single sync cycle. Returns updated state."""
    logger.info("Starting sync cycle")

    # Initialize WebDAV client
    try:
        webdav = WebDAVClient(WEBDAV_URL, auth=(WEBDAV_USER, WEBDAV_PASS))
    except Exception as e:
        logger.error(f"Failed to initialize WebDAV client: {e}")
        return state

    # Ensure output directory exists
    if not ensure_output_directory(webdav):
        return state

    # Fetch conversations
    conversations = fetch_conversations()

    # Safety: Don't delete if API fetch failed
    if conversations is None:
        logger.warning("Failed to fetch conversations, skipping cycle")
        return state

    # Handle deletions before syncing (only if we got valid data)
    omi_conv_ids = {c.get("id") for c in conversations if c.get("id")}
    deleted = handle_deletions(omi_conv_ids, state, webdav)

    if not conversations:
        logger.info("No conversations to sync")
        # Still save state if deletions occurred
        return state

    # Sync each conversation
    created = 0
    updated = 0
    skipped = 0

    for conversation in conversations:
        if not running:
            logger.info("Shutdown requested, stopping sync")
            break

        conv_id = conversation.get("id", "")
        was_existing = conv_id in state.get("conversations", {})

        if sync_conversation(conversation, state, webdav):
            if was_existing:
                updated += 1
            else:
                created += 1
        else:
            skipped += 1

    # Update last sync timestamp
    state["last_sync"] = datetime.now(timezone.utc).isoformat()

    logger.info(f"Sync complete: {created} created, {updated} updated, {skipped} skipped, {deleted} deleted")
    return state


def main() -> int:
    """Main entry point."""
    global running

    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    logger.info("Omi-to-WebDAV Sync Service starting")

    # Validate configuration
    if not validate_config():
        return 1

    logger.info(f"Sync interval: {SYNC_INTERVAL_SECONDS}s")
    logger.info(f"Output directory: {OUTPUT_DIR}")

    # Load initial state
    state = load_state()

    # Main sync loop
    while running:
        try:
            state = run_sync_cycle(state)
            save_state(state)
        except Exception as e:
            logger.error(f"Sync cycle failed: {e}", exc_info=True)

        # Sleep in 1-second increments for responsive shutdown
        for _ in range(SYNC_INTERVAL_SECONDS):
            if not running:
                break
            time.sleep(1)

    logger.info("Shutting down, saving final state")
    save_state(state)
    logger.info("Goodbye!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
