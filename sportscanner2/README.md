# SportScanner 2

## What It Is

SportScanner 2 is a self-hosted sports media organizer for Plex.

It does three jobs:

1. Watches an `incoming` folder for sports video files.
2. Publishes matched files into a separate managed `library` tree with `.plexmatch` files.
3. Serves a Plex custom TV metadata provider at `/provider/tv`.

If a file is unclear, SportScanner does not guess. It keeps the file out of Plex and puts it in the
web `Review` queue instead.

This app lives in `sportscanner2/`. The older Plex scanner and metadata agent elsewhere in this repo
are legacy code.

### Important Rules

- Plex must point at the managed `library` folder, not `incoming`.
- `incoming` can be mounted read-only. SportScanner only reads from it.
- Keep `incoming` and `library` on the same filesystem or share if you want hardlinks instead of copies.
- Plex must be able to reach SportScanner by IP or hostname. Do not use `localhost` unless Plex and
  SportScanner are truly running in the same container or host namespace.

### What You Need

- Plex Media Server that supports custom metadata providers
- A TheSportsDB API key
- Three folders:
  - `data`: database and cached images
  - `incoming`: your source files
  - `library`: the managed Plex-facing output

For basic testing, TheSportsDB currently documents `123` as the public development key.
For regular use, use your own key.

## Install

### Published Image Repository

This repository publishes a multi-architecture container image to GitHub Container Registry.

Default image:

```text
ghcr.io/mmmmmtasty/sportscanner2:latest
```

If you fork this repository and keep the GitHub Actions workflow, your fork can publish:

```text
ghcr.io/YOUR-GITHUB-OWNER/sportscanner2:latest
```

### Docker Install

Run these commands from `sportscanner2/`.

#### 1. Create Local Folders

```bash
mkdir -p data incoming library
```

#### 2. Start The Container

```bash
docker run -d \
  --name sportscanner2 \
  -p 32699:32699 \
  -e SPORTSCANNER_DB_PATH=/data/sportscanner.db \
  -e SPORTSCANNER_INCOMING_DIR=/incoming \
  -e SPORTSCANNER_LIBRARY_DIR=/library \
  -e SPORTSCANNER_ASSET_CACHE_DIR=/data/cache \
  -e TSDB_API_KEY=123 \
  -v "$PWD/data:/data" \
  -v "$PWD/incoming:/incoming:ro" \
  -v "$PWD/library:/library" \
  ghcr.io/mmmmmtasty/sportscanner2:latest
```

If you have your own TheSportsDB key, replace `123`.

#### 3. Confirm It Started

```bash
curl http://127.0.0.1:32699/health
curl http://127.0.0.1:32699/provider/tv
```

Then open:

```text
http://127.0.0.1:32699/
```

The root URL should redirect to `/admin/`.

#### Docker Compose

The included `docker-compose.yml` also works:

```bash
docker compose up -d
```

By default it pulls `ghcr.io/mmmmmtasty/sportscanner2:latest` and uses `TSDB_API_KEY=123` if you do
not override either value.

If you want to build locally instead of pulling from GitHub Container Registry:

```bash
docker build -t sportscanner2:latest .
SPORTSCANNER_IMAGE=sportscanner2:latest docker compose up -d
```

### Unraid Install

With the published image, you do not need to copy the source tree onto Unraid just to create the
container.

#### 1. Create The Container In The Unraid Web UI

1. Open the Unraid web interface.
2. Click `Docker`.
3. Click `Add Container`.
4. Set `Name` to `sportscanner2`.
5. Set `Repository` to `ghcr.io/mmmmmtasty/sportscanner2:latest`.
6. Set `Network Type` to `Bridge`.
7. Add a port mapping:
   - host port `32699`
   - container port `32699`
8. Add a path mapping for `data`:
   - host path `/mnt/user/appdata/sportscanner2`
   - container path `/data`
9. Add a path mapping for `incoming`:
   - host path `/mnt/user/media/incoming`
   - container path `/incoming`
10. Add a path mapping for `library`:
    - host path `/mnt/user/media/plex/sportscanner2`
    - container path `/library`
11. Add a variable:
    - name `TSDB_API_KEY`
    - value `123` for testing, or your own key
12. Click `Apply`.

#### 2. Open The Web UI

After the container starts, open the container `WebUI` action in Unraid, or browse to:

```text
http://YOUR-UNRAID-IP:32699/
```

#### Recommended Unraid Folder Choices

- Put `data` under `/mnt/user/appdata/sportscanner2`
- Put `incoming` and `library` on the same share or pool if you want hardlinks
- If `incoming` and `library` land on different underlying devices, SportScanner will copy files
  instead of hardlinking them

#### Optional: Use The Included Unraid Template

This repository includes `unraid-template.xml`.

If you want to use it:

1. Copy `unraid-template.xml` to:

```text
/boot/config/plugins/dockerMan/templates-user/sportscanner2.xml
```

2. Open the file and confirm the `Repository` value points at the image you want, for example:

```text
ghcr.io/mmmmmtasty/sportscanner2:latest
```

3. Go back to `Docker` in the Unraid web UI.
4. Click `Add Container`.
5. Select the SportScanner template if it appears in the template list.
6. Adjust the host paths before clicking `Apply`.

#### If You Want To Publish Your Own Image

If you are maintaining your own GitHub fork:

1. Push the repo to GitHub.
2. Let the `Publish SportScanner 2 Image` workflow run.
3. Use `ghcr.io/YOUR-GITHUB-OWNER/sportscanner2:latest` as the Unraid `Repository` value.

### Local Python Install

If you want to run directly from a checkout instead of Docker:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
mkdir -p data/cache incoming library
export PYTHONPATH="$PWD/src"
export SPORTSCANNER_DB_PATH="$PWD/data/sportscanner.db"
export SPORTSCANNER_INCOMING_DIR="$PWD/incoming"
export SPORTSCANNER_LIBRARY_DIR="$PWD/library"
export SPORTSCANNER_ASSET_CACHE_DIR="$PWD/data/cache"
export TSDB_API_KEY="123"
.venv/bin/uvicorn sportscanner.main:create_app --factory --host 0.0.0.0 --port 32699
```

If you are upgrading an existing database, run this first:

```bash
PYTHONPATH=src .venv/bin/alembic upgrade head
```

## Configure

### What The Settings Screen Means

PMS means Plex Media Server.

- `Plex Server URL`: the base URL of your Plex server, usually `http://YOUR-SERVER-IP:32400`
- `Plex Token (X-Plex-Token)`: Plex's API token; think of it as a long password SportScanner uses
  to register itself with Plex
- `Provider Public URL`: the URL Plex uses to reach SportScanner, usually
  `http://YOUR-SERVER-IP:32699`
- `Provider Group Name In Plex`: the name that appears in Plex's `Agent` dropdown; leaving
  `SportScanner 2` is fine

### How To Get Your Plex Token

1. Open the Plex Web App.
2. Open any movie, show, or episode details page.
3. Click `Get Info`.
4. Click `View XML`.
5. Copy the `X-Plex-Token` value from the page URL.

Plex's official token article is linked at the end of this README.

### Configure SportScanner

These steps match the current admin UI.

#### 1. Open The Admin UI

Go to:

```text
http://YOUR-SERVER-IP:32699/admin/
```

You should see these top navigation links:

- `Dashboard`
- `Review`
- `Competitions`
- `Settings`

#### 2. Save The Plex Connection Settings

1. Click `Settings`.
2. Fill in:
   - `Plex Server URL`
   - `Plex Token (X-Plex-Token)`
   - `Provider Public URL`
   - `Provider Group Name In Plex`
3. Click `Save Settings`.

Use these examples:

- `Plex Server URL`: `http://192.168.1.20:32400`
- `Provider Public URL`: `http://192.168.1.50:32699`

#### 3. Register SportScanner With Plex

1. Stay on `Settings`.
2. Click `Register Provider And Group`.
3. If it succeeds, you should land on a `Plex Registration` page that shows:
   - `Provider Identifier`
   - `Provider URI`
   - `Provider Group ID`
4. If it fails, fix the values on the `Settings` page first.

### Configure Plex

SportScanner can register the metadata provider for you, but you still create or edit the TV library
inside Plex yourself.

#### New Plex Library

1. Open the Plex Web App.
2. Click `+ Add Library`.
3. Choose `TV Shows`.
4. Enter a library name.
5. Click `Next`.
6. Click `Browse For Media Folder`.
7. Select the managed `library` folder created by SportScanner.
8. Click `Advanced`.
9. Set `Agent` to `SportScanner 2` or the custom provider group name you saved earlier.
10. Leave `Use season titles when available` checked.
11. Click `Add Library`.
12. Open the library's `...` menu.
13. Click `Manage Library`.
14. Click `Refresh All Metadata`.

#### Existing Plex Library

1. Open the existing TV library.
2. Click the library `...` menu.
3. Click `Manage Library`.
4. Click `Edit`.
5. Click `Advanced`.
6. Change `Agent` to `SportScanner 2` or your custom provider group name.
7. Turn on `Use season titles when available`.
8. Click `Save Changes`.
9. Open the library `...` menu again.
10. Click `Manage Library`.
11. Click `Refresh All Metadata`.

If you do not see `SportScanner 2` in the `Agent` list, the provider registration step did not
finish correctly.

`Use season titles when available` should be enabled for SportScanner. The provider returns sports
season labels such as `2025` or `2024-2025`, and this setting tells Plex to display those labels
instead of generic season names.

## Add Your First File

### 1. Put A Test File In `incoming`

Examples that the parser understands:

```text
Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv
English Premier League 2024.12.14 Arsenal vs Bournemouth.mkv
Formula1-2025-20250629-Austrian-Grand-Prix.mp4
```

### 2. Trigger A Scan

1. Open `Dashboard`.
2. Click `Rescan Incoming`.

### 3. Check What Happened

If the file matched cleanly:

- the competition appears under `Competitions`
- the managed file appears under `library`
- `.plexmatch` files are written at the show and season levels

If the file did not match cleanly:

1. Click `Review`.
2. In `Review Queue`, click `Open`.
3. On the review page, click one of:
   - `Use This Event`
   - `Publish As Season 0 Special`

Use `Publish As Season 0 Special` only when the file really is a special. Do not use it as a
general fallback for ordinary matches or races.

## Workflow Validation Script

If you want a repeatable end-to-end check without depending on a live upstream schedule or a live Plex
server, run:

```bash
cd sportscanner2
.venv/bin/python scripts/test_sport_test_workflow.py
```

The script exercises the real SportScanner organizer, admin routes, provider routes, library output,
and `.plexmatch` generation against a deterministic fake Plex library named `Sport_Test`.

It verifies:

- files move from `incoming` to the managed `library`
- Plex-facing metadata is available through the provider
- review conflicts can be resolved and then published
- incomplete seasons stay staged until the event list is complete
- replayed fixtures stay separate episodes
- rescheduled fixtures update Plex ordering and `originallyAvailableAt`
- log messages confirm each transition

The harness expects log messages such as `rescan_started`, `review_task_open`, `segment_published`,
`season_publish_reconciled`, and `review_task_resolved`.

## Filename Tips

SportScanner works best when the competition name, date, and event name are all in the filename.

Good:

```text
Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv
```

Bad:

```text
Race 01.mkv
Weekend Match.mp4
```

The parser strips common release tags such as `720p`, `1080p`, `x264`, `x265`, and `WEB-DL`.

## Optional Sidecars

If a filename is too vague, add a `.sportscanner.yml` file next to it:

```yaml
competition: "Formula 1"
season: 2025
event: "Austrian Grand Prix"
segment_kind: "analysis"
title_suffix: "(Post-Race Analysis)"
tsdb_event_id: 1234567
```

For competitions that cross calendar years, add `competition.sportscanner.yml` somewhere above the
media file:

```yaml
season_pattern: "cross_year"
season_split_month: 7
season_split_day: 1
```

## Common Problems

### `Register Provider And Group` Fails

Check:

- `Plex Server URL` is correct
- `Plex Token (X-Plex-Token)` is correct
- `Provider Public URL` points to SportScanner, not `localhost`
- Plex can actually reach that URL

### Plex Sees Files But Metadata Does Not Load

Check:

- the Plex library is a `TV Shows` library
- Plex points at the managed `library` folder, not `incoming`
- the library `Agent` is set to `SportScanner 2`
- you ran `Refresh All Metadata` after changing the agent

### Files Stay In Review

Usually one of these is true:

- the filename is too vague
- the competition name does not match a real competition well enough
- the upstream season data is incomplete
- the file needs a `.sportscanner.yml` sidecar

## Schedule Changes And Uncertain Calendars

SportScanner intentionally treats unstable schedules conservatively.

- If the upstream season is incomplete, matching does not guess. The segment stays `staged`, an open
  review task is created, and Plex does not see the file yet.
- When the season later becomes complete, rescanning the same file publishes it normally.
- If multiple same-day events are plausible, SportScanner opens a review task instead of auto-picking
  the wrong event.
- If a replayed fixture is represented upstream as a distinct event, SportScanner publishes it as a
  separate episode with its own event id and episode number.
- If upstream dates change after publication, SportScanner recomputes season ordering and refreshes the
  Plex-facing episode numbers and `originallyAvailableAt` value on the next rescan.
- Managed filenames stay tied to the source file naming. The Plex-facing ordering and air-date metadata
  follow the latest canonical event data.

### Files Are Copied Instead Of Hardlinked

`incoming` and `library` are on different underlying devices. Move them onto the same filesystem,
share, or pool if you want hardlinks.

## Useful Links

- Plex token instructions:
  https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/
- Plex library creation:
  https://support.plex.tv/articles/200288926-creating-libraries/
- Plex library editing:
  https://support.plex.tv/articles/200289266-editing-libraries/
- Plex scan vs refresh metadata:
  https://support.plex.tv/articles/200289306-scanning-vs-refreshing-a-library/
- Unraid Docker management:
  https://docs.unraid.net/unraid-os/manual/docker-management/
- Unraid Community Applications and template storage:
  https://docs.unraid.net/unraid-os/community-applications/
- TheSportsDB API docs:
  https://www.thesportsdb.com/api/v2/json/123/all/livescore/boxing
