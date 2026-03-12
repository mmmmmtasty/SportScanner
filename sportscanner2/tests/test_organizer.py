from __future__ import annotations

import threading
import time
from datetime import date

from sqlalchemy import func, select

from sportscanner.db.models import Competition, Event, ReviewTask, Segment, SegmentStatus
from sportscanner.organizer.service import OrganizerService
from sportscanner.upstream.base import UpstreamCompetition, UpstreamEvent


class MutableMetadataSource:
    name = "fake"

    def __init__(self, *, complete: bool, events: list[UpstreamEvent] | None = None) -> None:
        self._competition = UpstreamCompetition(
            id="tsdb_4370",
            tsdb_id=4370,
            name="Formula 1",
            poster_url="https://example.com/f1_poster.jpg",
            fanart_url="https://example.com/f1_fanart.jpg",
        )
        self.complete = complete
        self.events = events or []

    def probe(self) -> str:
        return "v1"

    def all_competitions(self) -> list[UpstreamCompetition]:
        return [self._competition]

    def search_filename(self, query: str) -> list[UpstreamEvent]:
        lowered = query.lower()
        return [event for event in self.events if event.name.lower() in lowered]

    def events_on_day(self, competition_name: str, event_date: date) -> list[UpstreamEvent]:
        return [
            event
            for event in self.events
            if event.competition_name == competition_name and event.date == event_date
        ]

    def season_events(self, competition: UpstreamCompetition, season_label: str) -> tuple[list[UpstreamEvent], bool]:
        if competition.tsdb_id == 4370 and season_label == "2025":
            return (self.events, self.complete)
        return ([], False)

    def lookup_competition(self, tsdb_id: int) -> UpstreamCompetition | None:
        if tsdb_id == self._competition.tsdb_id:
            return self._competition
        return None

    def lookup_event(self, tsdb_event_id: int) -> UpstreamEvent | None:
        return next((event for event in self.events if event.tsdb_id == tsdb_event_id), None)


class SlowMetadataSource(MutableMetadataSource):
    def all_competitions(self) -> list[UpstreamCompetition]:
        time.sleep(0.05)
        return super().all_competitions()


def test_ingest_publishes_matched_file(settings, organizer, session_factory) -> None:
    source = settings.incoming_dir / "Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv"
    source.write_text("video", encoding="utf-8")

    segment = organizer.ingest_path(source)

    assert segment.status == SegmentStatus.PUBLISHED.value
    with session_factory() as session:
        stored = session.get(Segment, segment.id)
        assert stored is not None
        assert stored.episode_number == 150
        assert stored.metadata_source == "fake"
        assert stored.metadata_record is not None
        assert stored.metadata_record["event"]["tsdbId"] == 1001
        assert any(image["url"] == "https://example.com/f1_poster.jpg" for image in stored.metadata_images or [])
        competition = session.scalar(select(Competition).where(Competition.name == "Formula 1"))
        assert competition is not None


def test_ingest_without_complete_season_holds_for_review(settings, session_factory) -> None:
    organizer = OrganizerService(settings, session_factory, metadata_source=MutableMetadataSource(complete=False))
    source = settings.incoming_dir / "Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv"
    source.write_text("video", encoding="utf-8")

    segment = organizer.ingest_path(source)

    assert segment.status == SegmentStatus.STAGED.value
    with session_factory() as session:
        tasks = list(session.scalars(select(ReviewTask)))
        assert len(tasks) == 1


def test_rescan_publishes_staged_segment_when_season_completes(settings, session_factory) -> None:
    source = MutableMetadataSource(
        complete=False,
        events=[
            UpstreamEvent(
                id="tsdb_1001",
                tsdb_id=1001,
                name="Austrian Grand Prix",
                competition_name="Formula 1",
                date=date(2025, 6, 29),
            )
        ],
    )
    organizer = OrganizerService(settings, session_factory, metadata_source=source)
    path = settings.incoming_dir / "Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv"
    path.write_text("video", encoding="utf-8")

    staged = organizer.ingest_path(path)
    assert staged.status == SegmentStatus.STAGED.value

    source.complete = True
    organizer.rescan_incoming()

    with session_factory() as session:
        stored = session.get(Segment, staged.id)
        assert stored is not None
        assert stored.status == SegmentStatus.PUBLISHED.value
        assert stored.episode_number == 150


def test_reschedule_reorders_published_segments(settings, session_factory) -> None:
    source = MutableMetadataSource(
        complete=True,
        events=[
            UpstreamEvent(
                id="tsdb_1001",
                tsdb_id=1001,
                name="Austrian Grand Prix",
                competition_name="Formula 1",
                date=date(2025, 6, 29),
            ),
            UpstreamEvent(
                id="tsdb_1002",
                tsdb_id=1002,
                name="British Grand Prix",
                competition_name="Formula 1",
                date=date(2025, 7, 6),
            ),
        ],
    )
    organizer = OrganizerService(settings, session_factory, metadata_source=source)
    first = settings.incoming_dir / "Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv"
    second = settings.incoming_dir / "Formula 1 2025-07-06 British Grand Prix - Race.mkv"
    first.write_text("video", encoding="utf-8")
    second.write_text("video", encoding="utf-8")

    first_segment = organizer.ingest_path(first)
    second_segment = organizer.ingest_path(second)

    assert first_segment.episode_number == 150
    assert second_segment.episode_number == 250

    source.events[1] = UpstreamEvent(
        id="tsdb_1002",
        tsdb_id=1002,
        name="British Grand Prix",
        competition_name="Formula 1",
        date=date(2025, 6, 15),
    )
    organizer.rescan_incoming()

    with session_factory() as session:
        refreshed_first = session.get(Segment, first_segment.id)
        refreshed_second = session.get(Segment, second_segment.id)
        assert refreshed_first is not None
        assert refreshed_second is not None
        assert refreshed_first.episode_number == 250
        assert refreshed_second.episode_number == 150


def test_replayed_event_is_published_as_distinct_episode(settings, session_factory) -> None:
    source = MutableMetadataSource(
        complete=True,
        events=[
            UpstreamEvent(
                id="tsdb_2001",
                tsdb_id=2001,
                name="Team A vs Team B",
                competition_name="Formula 1",
                date=date(2025, 6, 10),
            ),
            UpstreamEvent(
                id="tsdb_2002",
                tsdb_id=2002,
                name="Team A vs Team B Replay",
                competition_name="Formula 1",
                date=date(2025, 6, 12),
            ),
        ],
    )
    organizer = OrganizerService(settings, session_factory, metadata_source=source)
    first = settings.incoming_dir / "Formula 1 2025-06-10 Team A vs Team B - Match.mkv"
    replay = settings.incoming_dir / "Formula 1 2025-06-12 Team A vs Team B Replay - Match.mkv"
    first.write_text("video", encoding="utf-8")
    replay.write_text("video", encoding="utf-8")

    first_segment = organizer.ingest_path(first)
    replay_segment = organizer.ingest_path(replay)

    assert first_segment.status == SegmentStatus.PUBLISHED.value
    assert replay_segment.status == SegmentStatus.PUBLISHED.value
    assert first_segment.event_id != replay_segment.event_id
    assert first_segment.episode_number != replay_segment.episode_number


def test_concurrent_ingest_of_same_path_is_serialized(settings, session_factory) -> None:
    source = SlowMetadataSource(
        complete=True,
        events=[
            UpstreamEvent(
                id="tsdb_1001",
                tsdb_id=1001,
                name="Austrian Grand Prix",
                competition_name="Formula 1",
                date=date(2025, 6, 29),
            )
        ],
    )
    organizer = OrganizerService(settings, session_factory, metadata_source=source)
    path = settings.incoming_dir / "Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv"
    path.write_text("video", encoding="utf-8")

    errors: list[Exception] = []
    result_ids: list[str] = []

    def worker() -> None:
        try:
            result_ids.append(organizer.ingest_path(path).id)
        except Exception as exc:  # pragma: no cover - test should fail on any exception
            errors.append(exc)

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(result_ids) == 2
    assert result_ids[0] == result_ids[1]
    with session_factory() as session:
        assert session.scalar(select(func.count(Segment.id)).where(Segment.source_path == str(path))) == 1
        assert session.scalar(select(func.count(Competition.id)).where(Competition.name == "Formula 1")) == 1


def test_rescan_skips_unparsable_media_files(settings, organizer, session_factory) -> None:
    valid = settings.incoming_dir / "Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv"
    invalid = settings.incoming_dir / "Vacation Clip.mp4"
    valid.write_text("video", encoding="utf-8")
    invalid.write_text("video", encoding="utf-8")

    processed = organizer.rescan_incoming()

    assert str(valid) in processed
    assert str(invalid) not in processed
    with session_factory() as session:
        assert session.scalar(select(func.count(Segment.id))) == 1


def test_competition_config_applies_event_order(settings, session_factory) -> None:
    organizer = OrganizerService(settings, session_factory, metadata_source=MutableMetadataSource(complete=True))
    fixture_dir = settings.incoming_dir / "Formula 1"
    fixture_dir.mkdir()
    (fixture_dir / "competition.sportscanner.yml").write_text("event_order: weekend\n", encoding="utf-8")
    source = fixture_dir / "Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv"
    source.write_text("video", encoding="utf-8")

    organizer.ingest_path(source)

    with session_factory() as session:
        competition = session.scalar(select(Competition).where(Competition.name == "Formula 1"))
        assert competition is not None
        assert competition.event_order == "weekend"


def test_sidecar_lookup_persists_derived_weekend_group(settings, session_factory) -> None:
    organizer = OrganizerService(
        settings,
        session_factory,
        metadata_source=MutableMetadataSource(
            complete=True,
            events=[
                UpstreamEvent(
                    id="tsdb_1001",
                    tsdb_id=1001,
                    name="2025 Formula 1 Austrian Grand Prix - Qualifying",
                    competition_name="Formula 1",
                    date=date(2025, 6, 28),
                ),
            ],
        ),
    )
    source = settings.incoming_dir / "Formula 1 2025-06-28 Austrian Grand Prix - Qualifying.mkv"
    sidecar = source.with_suffix(".sportscanner.yml")
    source.write_text("video", encoding="utf-8")
    sidecar.write_text("tsdb_event_id: 1001\n", encoding="utf-8")

    organizer.ingest_path(source)

    with session_factory() as session:
        event = session.scalar(select(Event).where(Event.tsdb_id == 1001))
        assert event is not None
        assert event.weekend_group == "formula 1 austrian grand prix"


def test_resolve_review_task_accepts_upstream_lookup_event(settings, session_factory) -> None:
    organizer = OrganizerService(settings, session_factory, metadata_source=MutableMetadataSource(complete=False))
    source = settings.incoming_dir / "Formula 1 2025-06-28 Austrian Grand Prix - Qualifying.mkv"
    source.write_text("video", encoding="utf-8")

    segment = organizer.ingest_path(source)
    assert segment.status == SegmentStatus.STAGED.value

    organizer.metadata_source = MutableMetadataSource(
        complete=True,
        events=[
            UpstreamEvent(
                id="tsdb_1001",
                tsdb_id=1001,
                name="2025 Formula 1 Austrian Grand Prix - Qualifying",
                competition_name="Formula 1",
                date=date(2025, 6, 28),
            )
        ],
    )

    with session_factory() as session:
        task = session.scalar(select(ReviewTask).where(ReviewTask.segment_id == segment.id))
        assert task is not None
        task_id = task.id

    resolved = organizer.resolve_review_task(task_id, tsdb_event_id=1001)

    assert resolved.status == SegmentStatus.PUBLISHED.value
    assert resolved.event_id == "tsdb_1001"
