# omi-webdav-replicate

A Docker container that syncs conversation transcripts from the Omi wearable app to a WebDAV server as Markdown files.

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

## Quick Start

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/omi-webdav-replicate.git
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
| Markdown file edited locally | Edits preserved until Omi content changes |
| Service restarts | Resumes without re-uploading unchanged files |

## Stopping the Service

```bash
docker compose down
```

The sync state is preserved in a Docker volume and will be restored on next start.

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

3. Set environment variables and run:
   ```bash
   export OMI_API_KEY=your_key
   export WEBDAV_URL=https://your-server.com
   export WEBDAV_USER=user
   export WEBDAV_PASS=pass
   uv run python sync.py
   ```

## License

MIT
