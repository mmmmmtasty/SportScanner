#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sportscanner.admin.router import router as admin_router
from sportscanner.config import Settings
from sportscanner.db.models import Base, CompetitionSeason, ReviewTask, Segment, SegmentStatus
from sportscanner.organizer.service import OrganizerService
from sportscanner.plex import PlexPmsClient
from sportscanner.provider.router import router as provider_router
from sportscanner.provider.rating_keys import make_season_rating_key, make_show_rating_key
from sportscanner.services import SportScannerServices
from sportscanner.upstream.base import UpstreamCompetition, UpstreamEvent


class ScenarioFailure(RuntimeError):
    pass


class LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))

    def clear(self) -> None:
        self.messages.clear()

    def require(self, *needles: str) -> None:
        for needle in needles:
            if any(needle in message for message in self.messages):
                continue
            raise ScenarioFailure(f"Missing expected log message containing: {needle}")


class ScenarioMetadataSource:
    name = "scenario"

    def __init__(self) -> None:
        self._competition = UpstreamCompetition(id="tsdb_4370", tsdb_id=4370, name="Formula 1")
        self._season_events: dict[str, list[UpstreamEvent]] = {}
        self._season_complete: dict[str, bool] = {}

    def probe(self) -> str:
        return "scenario"

    def all_competitions(self) -> list[UpstreamCompetition]:
        return [self._competition]

    def search_filename(self, query: str) -> list[UpstreamEvent]:
        lowered = query.lower()
        return [event for event in self._all_events() if event.name.lower() in lowered]

    def events_on_day(self, competition_name: str, event_date: date) -> list[UpstreamEvent]:
        return [
            event
            for event in self._all_events()
            if event.competition_name == competition_name and event.date == event_date
        ]

    def season_events(self, competition: UpstreamCompetition, season_label: str) -> tuple[list[UpstreamEvent], bool]:
        if competition.tsdb_id != self._competition.tsdb_id:
            return ([], False)
        return (list(self._season_events.get(season_label, [])), self._season_complete.get(season_label, False))

    def lookup_event(self, tsdb_event_id: int) -> UpstreamEvent | None:
        for event in self._all_events():
            if event.tsdb_id == tsdb_event_id:
                return event
        return None

    def set_season(self, season_label: str, events: list[UpstreamEvent], *, complete: bool) -> None:
        self._season_events[season_label] = list(events)
        self._season_complete[season_label] = complete

    def _all_events(self) -> list[UpstreamEvent]:
        return [event for season in self._season_events.values() for event in season]


@dataclass(slots=True)
class FakePlexLibrary:
    name: str

    def refresh(self, client: TestClient, session_factory) -> list[dict[str, object]]:
        ingested: list[dict[str, object]] = []
        with session_factory() as session:
            seasons = list(session.scalars(select(CompetitionSeason).order_by(CompetitionSeason.season_number.asc())))
            segments = list(
                session.scalars(
                    select(Segment)
                    .where(Segment.status == SegmentStatus.PUBLISHED.value)
                    .order_by(Segment.episode_number.asc(), Segment.source_path.asc())
                )
            )
        seen_shows: set[str] = set()
        seen_seasons: set[tuple[str, int]] = set()
        for season in seasons:
            show_key = make_show_rating_key(season.competition_id)
            season_key = make_season_rating_key(season.competition_id, season.season_number)
            if season.competition_id not in seen_shows:
                response = client.get(f"/provider/tv/library/metadata/{show_key}")
                self._check(response.status_code == 200, f"{self.name}: show metadata failed for {show_key}")
                response = client.get(f"/provider/tv/library/metadata/{show_key}/children")
                self._check(response.status_code == 200, f"{self.name}: show children failed for {show_key}")
                seen_shows.add(season.competition_id)
            if (season.competition_id, season.season_number) not in seen_seasons:
                response = client.get(f"/provider/tv/library/metadata/{season_key}")
                self._check(response.status_code == 200, f"{self.name}: season metadata failed for {season_key}")
                response = client.get(f"/provider/tv/library/metadata/{season_key}/children")
                self._check(response.status_code == 200, f"{self.name}: season children failed for {season_key}")
                seen_seasons.add((season.competition_id, season.season_number))
        for segment in segments:
            response = client.get(f"/provider/tv/library/metadata/episode_{segment.id}")
            self._check(response.status_code == 200, f"{self.name}: episode metadata failed for {segment.id}")
            ingested.append(response.json()["MediaContainer"]["Metadata"][0])
        return ingested

    @staticmethod
    def _check(condition: bool, message: str) -> None:
        if not condition:
            raise ScenarioFailure(message)


class ScenarioHarness:
    def __init__(self, *, keep_temp: bool) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="sportscanner-e2e-")
        self.base_dir = Path(self._temp.name)
        self.keep_temp = keep_temp
        self.incoming_dir = self.base_dir / "incoming"
        self.library_dir = self.base_dir / "library"
        self.cache_dir = self.base_dir / "cache"
        self.incoming_dir.mkdir()
        self.library_dir.mkdir()
        self.cache_dir.mkdir()
        self.settings = Settings(
            db_path=self.base_dir / "sportscanner.db",
            incoming_dir=self.incoming_dir,
            library_dir=self.library_dir,
            asset_cache_dir=self.cache_dir,
            tsdb_api_key="123",
            tsdb_api_mode="v1",
            plex_provider_identifier="tv.plex.agents.custom.sportscanner.metadata.test",
            plex_provider_group_name="Sport_Test",
        )
        self.engine = create_engine(f"sqlite:///{self.settings.db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        self.metadata_source = ScenarioMetadataSource()
        self.organizer = OrganizerService(self.settings, self.session_factory, metadata_source=self.metadata_source)
        self.plex = PlexPmsClient()
        self.fake_plex = FakePlexLibrary("Sport_Test")
        self.log_capture = LogCapture()
        self.log_capture.setFormatter(logging.Formatter("%(name)s %(message)s"))
        logger = logging.getLogger("sportscanner")
        logger.handlers = []
        logger.addHandler(self.log_capture)
        logger.propagate = False
        logger.setLevel(logging.INFO)
        self.app = self._build_app()
        self.client = TestClient(self.app)

    def close(self) -> None:
        self.client.close()
        self.engine.dispose()
        if self.keep_temp:
            print(f"kept temp workspace at {self.base_dir}")
        else:
            self._temp.cleanup()

    def run(self) -> None:
        self.scenario_publish_known_event()
        self.scenario_resolve_conflict_review()
        self.scenario_incomplete_season_then_complete()
        self.scenario_replay_and_reschedule()

    def scenario_publish_known_event(self) -> None:
        self.log_capture.clear()
        self.metadata_source.set_season(
            "2025",
            [
                UpstreamEvent(
                    id="tsdb_1001",
                    tsdb_id=1001,
                    name="Austrian Grand Prix",
                    competition_name="Formula 1",
                    date=date(2025, 6, 29),
                )
            ],
            complete=True,
        )
        path = self._write_media("Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv")
        self._rescan()
        segment = self._segment_for_path(path)
        self._check(segment.status == SegmentStatus.PUBLISHED.value, "known event should publish")
        self._check(Path(segment.managed_path or "").exists(), "managed file should exist")
        self._check((self.library_dir / "Formula 1" / "Season 2025" / ".plexmatch").exists(), "season plexmatch missing")
        ingested = self.fake_plex.refresh(self.client, self.session_factory)
        self._check(any(item["title"] == "Austrian Grand Prix" for item in ingested), "Plex ingest missed Austrian Grand Prix")
        self.log_capture.require("rescan_started", "segment_published", "ingest_completed")
        print("PASS publish_known_event")

    def scenario_resolve_conflict_review(self) -> None:
        self.log_capture.clear()
        self.metadata_source.set_season(
            "2025",
            self.metadata_source.season_events(self.metadata_source.all_competitions()[0], "2025")[0]
            + [
                UpstreamEvent(
                    id="tsdb_3001",
                    tsdb_id=3001,
                    name="Summer Clash Qualifier",
                    competition_name="Formula 1",
                    date=date(2025, 7, 12),
                ),
                UpstreamEvent(
                    id="tsdb_3002",
                    tsdb_id=3002,
                    name="Summer Clash Finals",
                    competition_name="Formula 1",
                    date=date(2025, 7, 12),
                ),
            ],
            complete=True,
        )
        path = self._write_media("Formula 1 2025-07-12 Summer Clash - Match.mkv")
        self._rescan()
        segment = self._segment_for_path(path)
        self._check(segment.status == SegmentStatus.REVIEW.value, "ambiguous event should go to review")
        task = self._open_task_for_segment(segment.id)
        self._check(task is not None, "review task should exist")
        response = self.client.post(
            f"/admin/review/{task.id}/resolve",
            data={"event_id": "tsdb_3002"},
            follow_redirects=False,
        )
        self._check(response.status_code == 303, "review resolve should redirect")
        refreshed = self._segment_for_path(path)
        self._check(refreshed.status == SegmentStatus.PUBLISHED.value, "resolved review should publish")
        self.fake_plex.refresh(self.client, self.session_factory)
        self.log_capture.require("review_task_open", "review_task_resolved", "segment_published")
        print("PASS resolve_conflict_review")

    def scenario_incomplete_season_then_complete(self) -> None:
        self.log_capture.clear()
        future_event = UpstreamEvent(
            id="tsdb_4001",
            tsdb_id=4001,
            name="Future Grand Prix",
            competition_name="Formula 1",
            date=date(2026, 3, 1),
        )
        self.metadata_source.set_season("2026", [future_event], complete=False)
        path = self._write_media("Formula 1 2026-03-01 Future Grand Prix - Race.mkv")
        self._rescan()
        staged = self._segment_for_path(path)
        self._check(staged.status == SegmentStatus.STAGED.value, "incomplete season should stage segment")
        self.log_capture.require("season_events_incomplete", "segment_staged_for_incomplete_season")

        self.log_capture.clear()
        self.metadata_source.set_season("2026", [future_event], complete=True)
        self._rescan()
        published = self._segment_for_path(path)
        self._check(published.status == SegmentStatus.PUBLISHED.value, "completed season should publish staged segment")
        self.fake_plex.refresh(self.client, self.session_factory)
        self.log_capture.require("season_events_synced", "segment_published")
        print("PASS incomplete_season_then_complete")

    def scenario_replay_and_reschedule(self) -> None:
        self.log_capture.clear()
        original = UpstreamEvent(
            id="tsdb_5001",
            tsdb_id=5001,
            name="Team A vs Team B",
            competition_name="Formula 1",
            date=date(2027, 3, 14),
        )
        replay = UpstreamEvent(
            id="tsdb_5002",
            tsdb_id=5002,
            name="Team A vs Team B Replay",
            competition_name="Formula 1",
            date=date(2027, 3, 16),
        )
        self.metadata_source.set_season("2027", [original, replay], complete=True)
        first_path = self._write_media("Formula 1 2027-03-14 Team A vs Team B - Match.mkv")
        replay_path = self._write_media("Formula 1 2027-03-16 Team A vs Team B Replay - Match.mkv")
        self._rescan()
        first_segment = self._segment_for_path(first_path)
        replay_segment = self._segment_for_path(replay_path)
        self._check(first_segment.event_id != replay_segment.event_id, "replay should map to distinct event")
        self._check(first_segment.episode_number != replay_segment.episode_number, "replay should get distinct episode number")

        self.log_capture.clear()
        replay.date = date(2027, 3, 10)
        self.metadata_source.set_season("2027", [original, replay], complete=True)
        self._rescan()
        first_segment = self._segment_for_path(first_path)
        replay_segment = self._segment_for_path(replay_path)
        self._check(replay_segment.episode_number < first_segment.episode_number, "reschedule should reorder episode numbers")
        ingested = self.fake_plex.refresh(self.client, self.session_factory)
        replay_metadata = next(item for item in ingested if item["title"] == "Team A vs Team B Replay")
        self._check(replay_metadata["originallyAvailableAt"] == "2027-03-10", "provider should expose rescheduled date")
        self.log_capture.require("season_publish_reconciled", "segment_published")
        print("PASS replay_and_reschedule")

    def _build_app(self) -> FastAPI:
        app = FastAPI()
        services = SportScannerServices(
            settings=self.settings,
            engine=self.engine,
            session_factory=self.session_factory,
            metadata_source=self.metadata_source,
            organizer=self.organizer,
            plex=self.plex,
            watcher=None,
        )
        app.state.services = services
        templates_dir = ROOT / "src" / "sportscanner" / "templates"
        static_dir = ROOT / "src" / "sportscanner" / "static"
        app.state.templates = Jinja2Templates(directory=str(templates_dir))
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        app.include_router(provider_router)
        app.include_router(admin_router)

        @app.get("/", include_in_schema=False)
        def root() -> RedirectResponse:
            return RedirectResponse(url="/admin/", status_code=307)

        return app

    def _rescan(self) -> None:
        response = self.client.post("/admin/rescan", follow_redirects=False)
        self._check(response.status_code == 303, "rescan should redirect")

    def _write_media(self, name: str) -> Path:
        path = self.incoming_dir / name
        path.write_text("video", encoding="utf-8")
        return path

    def _segment_for_path(self, path: Path) -> Segment:
        with self.session_factory() as session:
            segment = session.scalar(select(Segment).where(Segment.source_path == str(path)))
            self._check(segment is not None, f"missing segment for {path.name}")
            assert segment is not None
            return segment

    def _open_task_for_segment(self, segment_id: str) -> ReviewTask | None:
        with self.session_factory() as session:
            return session.scalar(
                select(ReviewTask).where(
                    ReviewTask.segment_id == segment_id,
                    ReviewTask.status == "open",
                )
            )

    @staticmethod
    def _check(condition: bool, message: str) -> None:
        if not condition:
            raise ScenarioFailure(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the deterministic Sport_Test end-to-end workflow validation.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep the temporary workspace after the run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    harness = ScenarioHarness(keep_temp=args.keep_temp)
    try:
        harness.run()
    except ScenarioFailure as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        if args.keep_temp:
            print(f"workspace {harness.base_dir}", file=sys.stderr)
        return 1
    finally:
        harness.close()
    print("PASS all scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
