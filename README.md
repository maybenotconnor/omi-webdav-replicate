# omi-webdav-replicate

A Docker container that syncs conversation transcripts from the [Omi](https://www.omi.me/) wearable app to a WebDAV server as Markdown files.

Omi is an AI-powered wearable that records and transcribes your conversations. This tool backs up those conversations to your own storage (Nextcloud, ownCloud, or any WebDAV server) as searchable Markdown files.

## Features

- Fetches all conversations from the Omi API including transcripts
- Converts conversations to Markdown with YAML front matter
- Uploads to any WebDAV-compatible server (Nextcloud, ownCloud, etc.)
- Preserves local file edits when Omi content hasn't changed
- Tracks sync state to avoid redundant uploads
- Runs continuously with configurable sync intervals

## Prerequisites

- Docker and Docker Compose
- An Omi developer API key (from the Omi app: Settings → Developer → Create Key)
- A WebDAV server with authentication credentials

For local development without Docker:
- Python 3.11+
- [UV](https://github.com/astral-sh/uv) package manager

## Quick Start

1. Clone this repository:
   ```bash
   git clone https://github.com/maybenotconnor/omi-webdav-replicate.git
   cd omi-webdav-replicate
   ```

2. Create your environment file:
   ```bash
   cp .env.example .env
   ```

3. Edit `.env` with your credentials:
   ```
   OMI_API_KEY=omi_dev_your_key_here
   WEBDAV_URL=https://your-webdav-server.com
   WEBDAV_USER=your_username
   WEBDAV_PASS=your_password
   ```

4. Start the service:
   ```bash
   docker compose up -d
   ```

5. Check the logs:
   ```bash
   docker compose logs -f
   ```

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OMI_API_KEY` | Yes | - | Omi developer API key (starts with `omi_dev_`) |
| `WEBDAV_URL` | Yes | - | Full URL to your WebDAV server |
| `WEBDAV_USER` | Yes | - | WebDAV authentication username |
| `WEBDAV_PASS` | Yes | - | WebDAV authentication password |
| `OUTPUT_DIR` | No | `/conversations` | Remote directory for markdown files |
| `SYNC_INTERVAL_SECONDS` | No | `300` | Seconds between sync runs (default: 5 minutes) |
| `TZ` | No | `UTC` | Timezone for logging |

## Markdown Output Format

Each conversation is saved as a Markdown file with YAML front matter:

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

Team meeting to discuss Q1 priorities and resource allocation.

## Transcript

**Speaker 0:** Let's start with priorities for the quarter.

**Speaker 1:** I think we should focus on mobile first.
```

## Sync Behavior

| Scenario | Outcome |
|----------|---------|
| New conversation in Omi | Markdown file created |
| Conversation edited in Omi | Markdown file overwritten |
| Conversation deleted in Omi | Markdown file deleted from WebDAV |
| Markdown file edited locally | Edits preserved until Omi content changes |
| Service restarts | Resumes without re-uploading unchanged files |
| API error during fetch | Sync cycle skipped (no deletions occur) |

## How It Works

The service runs a continuous sync loop with the following logic:

1. **Fetch**: Retrieves all conversations from the Omi API with pagination (25 per page)
2. **Detect deletions**: Compares conversation IDs from the API against the local state file. Any IDs in state but not in the API response are marked for deletion.
3. **Delete orphaned files**: Removes markdown files from WebDAV for deleted conversations
4. **Sync updates**: For each conversation from the API:
   - Computes a content hash (xxhash64) of the `structured` and `transcript_segments` fields
   - Compares against the stored hash in state
   - Skips if unchanged, uploads if new or modified
5. **Save state**: Persists the updated state file

### State File

The service maintains a JSON state file (`/app/state/sync_state.json`) that tracks:

```json
{
  "version": 1,
  "last_sync": "2025-02-04T10:30:00Z",
  "conversations": {
    "conv_123": {
      "omi_hash": "a1b2c3d4e5f67890",
      "filename": "Product Discussion.md"
    }
  }
}
```

This allows the service to:
- Skip unchanged conversations (comparing content hashes)
- Detect deleted conversations (IDs in state but not in API)
- Preserve filenames across syncs (avoiding duplicates)

### Safety Measures

- **API failures don't trigger deletions**: If the Omi API returns an error, the entire sync cycle is skipped to prevent accidental mass deletion
- **Graceful shutdown**: SIGTERM/SIGINT are handled to save state before exiting
- **Atomic state saves**: State is written to a temp file then renamed to prevent corruption
- **Retry on WebDAV errors**: Failed deletions remain in state to retry next cycle
- **Rate limit handling**: If the Omi API returns HTTP 429, the service respects the `Retry-After` header before continuing

### User Metadata Preservation

When updating an existing file, the service preserves any custom front matter fields you've added locally (fields that don't start with `_`). This means you can add your own tags, notes, or other metadata to the markdown files and they won't be overwritten unless the Omi content itself changes.

## Stopping the Service

```bash
docker compose down
```

The sync state is preserved in the `./sync-state` directory (bind-mounted into the container) and will be restored on next start.

## Development

To run locally without Docker:

1. Install UV:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Install dependencies:
   ```bash
   uv sync
   ```

3. Create the state directory (the script expects `/app/state` by default):
   ```bash
   sudo mkdir -p /app/state
   sudo chown $(whoami) /app/state
   ```

4. Set environment variables and run:
   ```bash
   export OMI_API_KEY=your_key
   export WEBDAV_URL=https://your-server.com
   export WEBDAV_USER=user
   export WEBDAV_PASS=pass
   uv run python sync.py
   ```

### Dependencies

The service uses the following Python packages:
- `requests` - HTTP client for Omi API
- `webdav4` - WebDAV client library
- `python-frontmatter` - YAML front matter parsing/generation
- `pathvalidate` - Filename sanitization
- `xxhash` - Fast content hashing

## Troubleshooting

**"Missing required environment variables" error**
- Ensure all required variables (`OMI_API_KEY`, `WEBDAV_URL`, `WEBDAV_USER`, `WEBDAV_PASS`) are set in your `.env` file
- Check that the `.env` file is in the same directory as `docker-compose.yml`

**"Failed to fetch conversations" error**
- Verify your Omi API key is valid and starts with `omi_dev_`
- Check if you can access the Omi app and your conversations are visible there

**"Failed to upload" or WebDAV errors**
- Verify the WebDAV URL is correct and accessible
- Check that your username/password are correct
- Ensure the WebDAV server allows creating directories if `OUTPUT_DIR` doesn't exist

**Files not updating after changes in Omi**
- Changes are detected by hashing the `structured` and `transcript_segments` fields
- Minor metadata changes (like `updated_at`) won't trigger a re-sync
- Check the logs to see if changes are being detected: `docker compose logs -f`

**Resetting sync state**
- To force a full re-sync, delete the state file: `rm ./sync-state/sync_state.json`
- Restart the container: `docker compose restart`

## License

MIT
