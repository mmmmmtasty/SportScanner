# Local Testing Setup

This checkout is configured to run `sportscanner2` locally on the MacBook and use the Unraid-hosted
media folders over SMB.

## Local Runtime Files

- `.env` points at:
  - incoming: `/Volumes/data/torrents/sport/incoming/sportscanner2-dev`
  - library: `/Volumes/data/media/sport/sportscanner2-dev`
  - Plex PMS: `http://192.168.0.127:32400`
  - provider callback URL: `http://192.168.0.174:32699`
  - provider identifier: `tv.plex.agents.custom.sportscanner.metadata.local`

## Start Locally

From `sportscanner2/`:

```bash
PYTHONPATH=src .venv/bin/alembic upgrade head
PYTHONPATH=src .venv/bin/uvicorn sportscanner.main:create_app --factory --host 0.0.0.0 --port 32699
```

Health check:

```bash
curl http://127.0.0.1:32699/health
```

## Remaining Manual Steps

1. On Unraid, create SMB user `sportscanner-dev` and grant it read/write access to the `data` share.
2. On Unraid, create:
   - `/mnt/user/data/torrents/sport/incoming/sportscanner2-dev`
   - `/mnt/user/data/media/sport/sportscanner2-dev`
3. On the Mac, mount the share as `smb://sportscanner-dev@mj-unraid-1.local/data` so it appears at `/Volumes/data`.
4. If macOS Firewall prompts for Python or Terminal incoming access, allow it so Plex can reach `http://192.168.0.174:32699`.
5. Open `http://127.0.0.1:32699/admin/settings` and save:
   - Plex Server URL: `http://192.168.0.127:32400`
   - Plex Token: your Plex admin `X-Plex-Token`
   - Provider Public URL: `http://192.168.0.174:32699`
   - Provider Identifier In Plex: `tv.plex.agents.custom.sportscanner.metadata.local`
   - Provider Group Name In Plex: `SportScanner 2 Local`
6. In the SportScanner admin UI, click `Register Provider And Group`.
7. In Plex, create a `TV Shows` library named `Sport_Test` pointed at `/sport/sportscanner2-dev`, then set its agent to `SportScanner 2 Local` and refresh metadata.
8. Put a test file like `Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv` into the incoming test folder and click `Rescan Incoming`.

## Deterministic Workflow Harness

This repo now includes an executable workflow validator at:

```bash
cd sportscanner2
.venv/bin/python scripts/test_sport_test_workflow.py
```

It runs a self-contained SportScanner + fake Plex flow and asserts:

- file intake and publish into the managed library
- `.plexmatch` generation and provider metadata availability
- review-task creation and manual conflict resolution
- incomplete seasons staying staged until upstream data becomes complete
- replayed fixtures staying distinct episodes
- rescheduled fixtures updating Plex-facing ordering and dates
- log messages such as `rescan_started`, `review_task_open`, `segment_published`, `season_publish_reconciled`, and `review_task_resolved`

## Pytest Modes

From `sportscanner2/`:

```bash
.venv/bin/pytest
.venv/bin/pytest --test-mode=deterministic -q
SPORTSCANNER_PMS_TOKEN=your-token .venv/bin/pytest --test-mode=plex -q
```

The default `pytest` run only executes isolated unit tests. The `deterministic` mode runs the fake
Plex harness, and the `plex` mode hits the live local SportScanner runtime plus Plex.

## Notes

- The app has no separate login layer; `/admin` is open on the local runtime.
- `.env` intentionally does not store the Plex token.
- This setup isolates testing into `sportscanner2-dev` subfolders so the live library stays untouched.
