# Omi-to-WebDAV Sync Service

**System Requirements & Technical Design Document**

Version 1.0 | February 2025

---

## 1. Executive Summary

This document specifies requirements for a Docker-based service that synchronizes conversation transcripts from the Omi wearable application to a WebDAV server as Markdown files. The service runs continuously, polling the Omi API at configurable intervals and uploading new or updated conversations while preserving local modifications to files when the source content has not changed.

### 1.1 Core Requirements

- Fetch all conversations from Omi API including full transcripts
- Convert each conversation to a structured Markdown file with YAML front matter
- Name files using the conversation title (sanitized for filesystem safety)
- Store the Omi conversation ID in front matter for change tracking
- Upload Markdown files to a WebDAV server
- Track sync state to avoid redundant uploads
- Preserve local file edits when Omi content is unchanged
- Run as a long-lived Docker container with Docker Compose
- Use UV as the Python package manager and runtime

### 1.2 Sync Behavior Summary

The sync strategy follows a "source wins with change detection" model:

| Scenario | Outcome |
|----------|---------|
| New conversation in Omi | Markdown file created |
| User edits conversation in Omi app | Markdown file overwritten (Omi wins) |
| User edits Markdown file locally | Edits preserved until Omi content changes |
| Omi content unchanged, Markdown edited | File untouched, local edits safe |
| Service restarts, state file intact | Resumes without re-uploading unchanged files |

### 1.3 Design Principles

This service follows the Unix philosophy and YAGNI principle:

- Do one thing well: fetch → convert → upload
- Prefer simple solutions (JSON state file over database)
- Hardcode good defaults rather than over-configuring
- Add features only when actually needed

---

## 2. Omi API Reference

The Omi Developer API provides REST endpoints for accessing user data including conversations, memories, and facts extracted from recordings.

**Documentation:** https://docs.omi.me/doc/developer/api

### 2.1 Authentication

Users generate API keys from the Omi mobile application:

**Settings → Developer → Create Key**

Keys are prefixed with `omi_dev_` and must be treated as secrets.

All requests require the authorization header:

```
Authorization: Bearer omi_dev_xxxxxxxxxx
```

**Reference:** https://docs.omi.me/doc/developer/api/overview#authentication

### 2.2 Base URL

```
https://api.omi.me/v1/dev
```

### 2.3 Rate Limits

| Limit Type | Value | Notes |
|------------|-------|-------|
| Requests per minute | 100 | Per API key |
| Requests per day | 10,000 | Per API key |
| Response header | `X-RateLimit-Remaining` | Check during pagination |

### 2.4 GET /user/conversations

Returns a list of the user's conversations with optional transcript inclusion.

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `include_transcript` | boolean | No | When true, includes `transcript_segments` array. Default: false |
| `limit` | integer | No | Number of results per page. Default: 25, Max: 100 |
| `offset` | integer | No | Number of results to skip for pagination. Default: 0 |

#### Response Structure

The endpoint returns a JSON array of conversation objects:

```json
{
  "id": "conv_202",
  "created_at": "2025-01-20T13:50:00Z",
  "updated_at": "2025-01-20T14:30:00Z",
  "structured": {
    "title": "Product Roadmap Discussion",
    "overview": "Team meeting to discuss Q1 priorities and resource allocation...",
    "emoji": "📋",
    "category": "business",
    "action_items": [
      {"description": "Draft PRD by Friday", "completed": false},
      {"description": "Schedule follow-up meeting", "completed": true}
    ]
  },
  "transcript_segments": [
    {"text": "Let's start with priorities", "speaker_id": 0, "start": 0.0, "end": 2.1},
    {"text": "I think we should focus on mobile", "speaker_id": 1, "start": 2.5, "end": 4.8}
  ]
}
```

#### Field Reference

**Top-level fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique conversation identifier (e.g., "conv_202") |
| `created_at` | ISO 8601 string | Timestamp when conversation was recorded |
| `updated_at` | ISO 8601 string | Timestamp of last modification (may be null) |
| `structured` | object | Contains title, overview, emoji, category, action_items |
| `transcript_segments` | array | Array of transcript segment objects (when requested) |

**Structured object fields:**

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | AI-generated title for the conversation |
| `overview` | string | Summary/overview of the conversation content |
| `emoji` | string | Single emoji representing conversation theme |
| `category` | string | Category tag (e.g., "business", "personal", "other") |
| `action_items` | array | List of extracted action items |

**Transcript segment fields:**

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | The spoken text content |
| `speaker_id` | integer | Numeric identifier for the speaker (0, 1, 2, etc.) |
| `start` | float | Start time in seconds from beginning of recording |
| `end` | float | End time in seconds from beginning of recording |

### 2.5 Pagination Strategy

The API uses offset-based pagination. To fetch all conversations:

1. Request with `limit=25`, `offset=0`
2. If response contains items, increment offset by 25
3. Repeat until response returns empty array
4. Add 100-200ms delay between requests to respect rate limits

---

## 3. WebDAV Integration

### 3.1 Protocol Overview

WebDAV extends HTTP with methods for file management. The service requires:

- **PUT** — Upload/overwrite files
- **MKCOL** — Create directories
- **PROPFIND** — Check file/directory existence

### 3.2 Recommended Library: webdav4

The `webdav4` library provides a modern Python client with built-in retry logic and clean exception handling.

**PyPI:** https://pypi.org/project/webdav4/  
**Documentation:** https://skshetry.github.io/webdav4/  
**Repository:** https://github.com/skshetry/webdav4  
**Minimum Version:** 0.10.0

#### Key Methods

| Method | Purpose | Notes |
|--------|---------|-------|
| `Client(base_url, auth)` | Initialize client | `auth` is tuple of (username, password) |
| `client.exists(path)` | Check if path exists | Returns boolean |
| `client.mkdir(path)` | Create directory | Wrap in try/except; may raise if exists |
| `client.upload_fileobj(data, path, overwrite)` | Upload bytes | Set `overwrite=True` to replace |

### 3.3 Alternative: webdavclient3

An older but functional alternative if `webdav4` presents compatibility issues.

**Repository:** https://github.com/ezhov-evgeny/webdav-client-python-3

### 3.4 Directory Structure

All Markdown files are stored flat within a configurable output directory:

```
/omi-transcripts/
  Product-Roadmap-Discussion.md
  Morning-Standup.md
  Weekly-Planning-Session.md
```

---

## 4. Markdown Output Format

### 4.1 File Naming

Files are named using the sanitized conversation title with a `.md` extension.

#### Sanitization Rules

Use the `pathvalidate` library for cross-platform safety:

**PyPI:** https://pypi.org/project/pathvalidate/  
**Repository:** https://github.com/thombashi/pathvalidate

The sanitization process must:

1. Normalize Unicode to NFC form
2. Remove or replace characters forbidden on Windows: `< > : " / \ | ? *`
3. Remove control characters
4. Avoid Windows reserved names: CON, NUL, COM1-9, LPT1-9
5. Strip leading/trailing dots and spaces
6. Truncate to 200 bytes maximum (to leave room for path)
7. Fall back to "Untitled" if result is empty

#### Handling Duplicate Titles

When multiple conversations have the same sanitized title, append the date as a suffix:

```
Meeting-Notes.md           (first occurrence)
Meeting-Notes_02092026.md  (duplicate title)
```

The state file tracks which filename each conversation maps to, enabling lookup by ID rather than title matching.

### 4.2 YAML Front Matter

Each file begins with YAML front matter containing both user-visible metadata and sync-tracking fields. Sync-managed fields are prefixed with underscore to distinguish them from user-editable metadata.

```yaml
---
title: Product Roadmap Discussion
date: 2025-01-20T13:50:00Z
category: work
_omi_id: "conv_202"
_content_hash: "a1b2c3d4e5f67890"
_synced_at: "2025-02-04T10:30:00Z"
---
```

#### Front Matter Fields

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Original title from Omi (unmodified) |
| `date` | ISO 8601 | Conversation creation timestamp |
| `category` | string | Category from Omi (business, personal, etc.) |
| `_omi_id` | string | Omi conversation ID (sync tracking) |
| `_content_hash` | string | Hash of Omi content (change detection) |
| `_synced_at` | ISO 8601 | Last sync timestamp |


**Library:** Use `python-frontmatter` for reading/writing front matter. It should use Obsidian-style markdown.
**PyPI:** https://pypi.org/project/python-frontmatter/

### 4.3 Document Body Structure

```markdown
## Summary

{overview text from Omi}

## Transcript

**Speaker 0:** {text}

**Speaker 1:** {text}
```

#### Formatting Rules

- **Summary section** is always present; use "No summary available." if overview is empty
- **Transcript section** is omitted entirely if no transcript segments exist
- Each transcript segment is its own paragraph with speaker label in bold
- Blank line between each transcript segment for readability

### 4.4 Complete Example

```markdown
---
title: Product Roadmap Discussion
date: 2025-01-20T13:50:00Z
category: work
_omi_id: "conv_202"
_content_hash: "a1b2c3d4e5f67890"
_synced_at: "2025-02-04T10:30:00Z"
---

## Summary

Team meeting to discuss Q1 priorities and resource allocation. Decided to focus on mobile app improvements and defer the desktop redesign to Q2.

## Transcript

**Speaker 0:** Let's start with priorities for the quarter.

**Speaker 1:** I think we should focus on mobile first. The analytics show that's where most users are.

**Speaker 0:** Agreed. Let's table the desktop redesign for now.
```

---

## 5. Sync Logic Design

### 5.1 Change Detection Strategy

Change detection uses content hashing of the Omi API response, not the generated Markdown. This ensures:

- Template changes don't trigger unnecessary re-uploads
- Only actual content changes from Omi cause overwrites
- Local edits to Markdown are preserved when Omi content is unchanged

#### What to Hash

Hash a normalized JSON representation of the content-relevant fields:

```python
{
    "structured": { ... },      # Entire structured object
    "transcript_segments": [...]  # Entire segments array
}
```

Do NOT include in hash:
- `id` (used for identification, not content)
- `created_at` / `updated_at` (timestamps may change without content change)

#### Hash Algorithm

Use xxHash64 truncated to 16 hex characters for fast, reliable change detection.

**PyPI:** https://pypi.org/project/xxhash/

Collision probability at 16 characters (64 bits): approximately 0.001% at 6,000 items—more than sufficient for personal use.

If xxhash is unavailable, SHA-256 truncated to 16 characters is acceptable (modern CPUs have hardware acceleration).

### 5.2 State File

The service maintains a JSON state file that tracks sync state. This file must persist across container restarts via a Docker volume.

#### State File Location

```
/app/state/sync_state.json
```

#### State File Structure

```json
{
  "version": 1,
  "last_sync": "2025-02-04T10:30:00Z",
  "conversations": {
    "conv_001": {
      "omi_hash": "a1b2c3d4e5f67890",
      "filename": "Morning-Standup.md"
    },
    "conv_002": {
      "omi_hash": "b2c3d4e5f6789012",
      "filename": "Product-Roadmap-Discussion.md"
    }
  }
}
```

#### State File Fields

| Field | Description |
|-------|-------------|
| `version` | Schema version for future migrations |
| `last_sync` | Timestamp of last successful full sync |
| `conversations` | Map of conversation ID to sync metadata |
| `conversations[id].omi_hash` | Hash of content from last sync |
| `conversations[id].filename` | Filename used (for duplicate title handling) |

#### Atomic Writes

Always write state files atomically to prevent corruption:

1. Write to temporary file (`sync_state.json.tmp`)
2. Call `fsync()` to ensure data is on disk
3. Rename temporary file to final name (atomic on POSIX)

### 5.3 Sync Algorithm

For each sync cycle:

```
1. Load state file (or start with empty state)
2. Fetch all conversations from Omi API with pagination
3. For each conversation:
   a. Compute omi_hash from API response
   b. Look up conversation ID in state
   c. If no previous state → CREATE new file, save state
   d. If omi_hash matches → SKIP (no changes)
   e. If omi_hash differs → UPDATE file, update state
4. Save state file atomically
5. Log summary statistics
```

### 5.4 Handling Existing Files

When updating a file that already exists on the WebDAV server:

1. Fetch the existing file
2. Parse YAML front matter using `python-frontmatter`
3. Preserve any user-added front matter fields (those not prefixed with `_`)
4. Update sync-managed fields (`_omi_id`, `_content_hash`, `_synced_at`)
5. Replace body content with new Markdown
6. Upload updated file

This preserves user customizations like additional tags or notes added to the front matter.

---

## 6. Docker Architecture

### 6.1 Runtime Approach

The service runs as a long-lived daemon with a sleep loop rather than cron. This approach:

- Simplifies environment variable handling
- Ensures logs go to stdout naturally
- Allows interval configuration without rebuilding
- Enables graceful shutdown handling

#### Sleep Loop Pattern

Sleep in 1-second increments to respond quickly to SIGTERM:

```python
for _ in range(interval_seconds):
    if not running:
        break
    time.sleep(1)
```

#### Signal Handling

Handle SIGTERM and SIGINT for graceful shutdown:

- Set a `running = False` flag
- Allow current sync to complete
- Save state before exit

**Reference:** https://medium.com/@khaerulumam42/gracefully-stopping-python-processes-inside-a-docker-container-0692bb5f860f

### 6.2 Process Init

Use **tini** or **dumb-init** as PID 1 to properly forward signals and reap zombie processes.

**tini:** Built into Docker (`docker run --init`)  
**dumb-init:** https://github.com/Yelp/dumb-init

Docker sends SIGTERM and waits only 10 seconds before SIGKILL. Ensure cleanup completes within this window.

### 6.3 UV Package Manager

UV is a fast Python package manager written in Rust.

**Documentation:** https://docs.astral.sh/uv/  
**Docker Guide:** https://docs.astral.sh/uv/guides/integration/docker/  
**Project Config:** https://docs.astral.sh/uv/concepts/projects/

#### Key Commands

| Command | Purpose |
|---------|---------|
| `uv lock` | Generate/update uv.lock from pyproject.toml |
| `uv sync --frozen` | Install dependencies exactly as specified in lockfile |
| `uv sync --frozen --no-dev` | Install without dev dependencies (production) |
| `uv run script.py` | Run script with virtual environment activated |

#### Required Environment Variables

| Variable | Value | Purpose |
|----------|-------|---------|
| `UV_COMPILE_BYTECODE` | 1 | Compile .pyc files for faster startup |
| `UV_LINK_MODE` | copy | Copy files instead of hardlinking (Docker compatibility) |

### 6.4 Dockerfile Structure

Use multi-stage builds with cache mounts for optimal layer caching:

**Reference:** https://depot.dev/docs/container-builds/how-to-guides/optimal-dockerfiles/python-uv-dockerfile

#### Build Stage

1. Start from `python:3.12-slim-bookworm`
2. Copy UV binary from `ghcr.io/astral-sh/uv:latest` (or pin specific version)
3. Set UV environment variables
4. Use cache mount for `/root/.cache/uv`
5. Bind mount `pyproject.toml` and `uv.lock`
6. Run `uv sync --frozen --no-install-project --no-dev`
7. Copy application code
8. Run `uv sync --frozen --no-dev`

#### Runtime Stage

1. Start from `python:3.12-slim-bookworm`
2. Install tini: `apt-get install -y tini`
3. Copy `/app` from builder stage
4. Set `PATH="/app/.venv/bin:$PATH"`
5. Set `PYTHONUNBUFFERED=1`
6. Use tini as entrypoint
7. Run main Python script

### 6.5 Docker Compose Configuration

#### Required Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OMI_API_KEY` | Yes | Omi developer API key (omi_dev_xxx) |
| `WEBDAV_URL` | Yes | Full URL to WebDAV server (https://...) |
| `WEBDAV_USER` | Yes | WebDAV authentication username |
| `WEBDAV_PASS` | Yes | WebDAV authentication password |
| `OUTPUT_DIR` | No | Remote directory path. Default: `/conversations` |
| `SYNC_INTERVAL_SECONDS` | No | Seconds between sync runs. Default: `300` (5 minutes) |
| `TZ` | No | Timezone for logging. Default: `UTC` |

#### Volumes

A named volume must persist the sync state file:

| Mount Point | Purpose |
|-------------|---------|
| `/app/state` | Sync state JSON file |

#### Restart Policy

Use `unless-stopped` to:
- Restart after failures
- Restart after host reboots
- Respect manual stops

#### Secrets Management

Store sensitive values in a `.env` file excluded from version control, or use Docker secrets for enhanced security.

---

## 7. Error Handling

### 7.1 Resilience Requirements

The service must continue running when errors occur. Individual failures should not crash the daemon.

### 7.2 Error Categories

| Error Type | Handling | Retry Behavior |
|------------|----------|----------------|
| Omi API authentication failure | Log error, continue to next cycle | Retry next interval |
| Omi API rate limit (429) | Log warning, sleep, retry | Exponential backoff within cycle |
| Omi API timeout | Log error, continue to next cycle | Retry next interval |
| WebDAV connection failure | Log error, continue to next cycle | Retry next interval |
| WebDAV authentication failure | Log error, continue to next cycle | Retry next interval |
| Individual file upload failure | Log error, continue to next file | Retry same file next cycle |
| State file read failure | Start with empty state | State rebuilt over time |
| State file write failure | Log error, continue | In-memory state lost on restart |

### 7.3 Logging

All logs must go to stdout for Docker logging compatibility.

| Level | Usage |
|-------|-------|
| INFO | Sync start/complete, files uploaded, summary statistics |
| WARNING | Rate limits approached, non-critical issues |
| ERROR | API failures, upload failures, exceptions with stack traces |

Use Python's `logging` module with format:

```
%(asctime)s - %(levelname)s - %(message)s
```

---

## 8. Dependencies

### 8.1 Python Version

Requires Python 3.11 or later.

### 8.2 Runtime Dependencies

| Package | Minimum Version | Purpose | PyPI |
|---------|-----------------|---------|------|
| requests | 2.31.0 | HTTP client for Omi API | https://pypi.org/project/requests/ |
| webdav4 | 0.10.0 | WebDAV client | https://pypi.org/project/webdav4/ |
| python-frontmatter | 1.0.0 | YAML front matter parsing | https://pypi.org/project/python-frontmatter/ |
| pathvalidate | 3.0.0 | Filename sanitization | https://pypi.org/project/pathvalidate/ |
| xxhash | 3.0.0 | Fast content hashing | https://pypi.org/project/xxhash/ |

### 8.3 pyproject.toml

```toml
[project]
name = "omi-webdav-sync"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = [
    "requests>=2.31.0",
    "webdav4>=0.10.0",
    "python-frontmatter>=1.0.0",
    "pathvalidate>=3.0.0",
    "xxhash>=3.0.0",
]
```

---

## 9. Testing

### 9.1 Unit Tests

- Hash computation produces consistent results for identical input
- Hash computation produces different results for different input
- Filename sanitization handles edge cases (empty, special chars, reserved names, long titles)
- Duplicate title handling appends ID suffix correctly
- Markdown generation handles missing optional fields gracefully
- Front matter round-trips correctly (read → modify → write)
- State file serialization/deserialization preserves all data
- Sync decision logic matches specification for all scenarios

### 9.2 Integration Tests

- Successful authentication with Omi API using valid key
- Pagination correctly fetches all conversations
- Rate limiting is respected during pagination
- WebDAV upload creates file with correct content
- WebDAV directory creation is idempotent
- Existing files are updated, not duplicated

### 9.3 Manual Verification

Before deployment, verify:

1. Conversation content matches between Omi app and generated Markdown
2. File names match conversation titles (sanitized appropriately)
3. Front matter contains correct `_omi_id` for each file
4. Local edits to Markdown persist after sync when Omi unchanged
5. Local edits are overwritten when Omi content changes
6. Service recovers gracefully from network interruptions

Keep the repository organized, and keep non-production critical code in a separate subfolder.

---

## 10. Documentation References

### 10.1 Omi API

| Resource | URL |
|----------|-----|
| Developer API Overview | https://docs.omi.me/doc/developer/api/overview |
| API Reference | https://docs.omi.me/doc/developer/api |
| Authentication Guide | https://docs.omi.me/doc/developer/api/overview#authentication |

### 10.2 Python Libraries

| Library | Documentation |
|---------|---------------|
| webdav4 | https://skshetry.github.io/webdav4/ |
| python-frontmatter | https://python-frontmatter.readthedocs.io/ |
| pathvalidate | https://pathvalidate.readthedocs.io/ |
| xxhash | https://github.com/ifduyue/python-xxhash |
| requests | https://requests.readthedocs.io/ |

### 10.3 UV Package Manager

| Resource | URL |
|----------|-----|
| UV Documentation | https://docs.astral.sh/uv/ |
| Docker Integration | https://docs.astral.sh/uv/guides/integration/docker/ |
| pyproject.toml Reference | https://docs.astral.sh/uv/concepts/projects/ |

### 10.4 Docker

| Resource | URL |
|----------|-----|
| Dockerfile Reference | https://docs.docker.com/reference/dockerfile/ |
| Docker Compose Reference | https://docs.docker.com/compose/compose-file/ |
| Multi-stage Builds | https://docs.docker.com/build/building/multi-stage/ |
| tini | https://github.com/krallin/tini |
| dumb-init | https://github.com/Yelp/dumb-init |

### 10.5 Best Practices

| Topic | URL |
|-------|-----|
| UV Docker Best Practices | https://depot.dev/docs/container-builds/how-to-guides/optimal-dockerfiles/python-uv-dockerfile |
| Graceful Shutdown in Containers | https://medium.com/@khaerulumam42/gracefully-stopping-python-processes-inside-a-docker-container-0692bb5f860f |

---

## 11. Deliverables Checklist

| File | Purpose |
|------|---------|
| `sync.py` | Main application code |
| `pyproject.toml` | Project metadata and dependencies |
| `uv.lock` | Locked dependency versions (generate with `uv lock`) |
| `Dockerfile` | Container build instructions |
| `docker-compose.yml` | Service orchestration |
| `.env.example` | Template for environment variables |
| `README.md` | Setup and usage instructions |

---

*End of Document*
