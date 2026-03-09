from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from sportscanner.db.models import Competition, ReviewTask, Segment, SegmentStatus


def test_ingest_publishes_matched_file(settings, organizer, session_factory) -> None:
    source = settings.incoming_dir / "Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv"
    source.write_text("video", encoding="utf-8")

    segment = organizer.ingest_path(source)

    assert segment.status == SegmentStatus.PUBLISHED.value
    with session_factory() as session:
        stored = session.get(Segment, segment.id)
        assert stored is not None
        assert stored.episode_number == 150
        competition = session.scalar(select(Competition).where(Competition.name == "Formula 1"))
        assert competition is not None


def test_ingest_without_complete_season_holds_for_review(settings, session_factory) -> None:
    from sportscanner.organizer.service import OrganizerService

    class IncompleteSource:
        name = "fake"

        def probe(self) -> str:
            return "v1"

        def all_competitions(self):
            return []

        def search_filename(self, query: str):
            return []

        def events_on_day(self, competition_name, event_date):
            return []

        def season_events(self, competition, season_label):
            return ([], False)

        def lookup_event(self, tsdb_event_id):
            return None

    organizer = OrganizerService(settings, session_factory, metadata_source=IncompleteSource())
    source = settings.incoming_dir / "Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv"
    source.write_text("video", encoding="utf-8")

    segment = organizer.ingest_path(source)

    assert segment.status == SegmentStatus.STAGED.value
    with session_factory() as session:
        tasks = list(session.scalars(select(ReviewTask)))
        assert len(tasks) == 1
