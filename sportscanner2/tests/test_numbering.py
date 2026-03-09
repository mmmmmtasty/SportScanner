from __future__ import annotations

from datetime import date, time

from sportscanner.organizer.numbering import EventSequenceInput, SegmentCodeInput, compute_event_sequences, compute_segment_codes, episode_number


def test_weekend_event_sequences_group_sessions_together() -> None:
    events = [
        EventSequenceInput("race", "Austrian Grand Prix Race", date(2025, 6, 29), time(14, 0), 11, "austrian grand prix"),
        EventSequenceInput("fp1", "Austrian Grand Prix Practice 1", date(2025, 6, 27), time(11, 30), 11, "austrian grand prix"),
        EventSequenceInput("qual", "Austrian Grand Prix Qualifying", date(2025, 6, 28), time(15, 0), 11, "austrian grand prix"),
    ]

    sequence_map = compute_event_sequences(events, "weekend")

    assert sequence_map == {"fp1": 1, "qual": 2, "race": 3}


def test_segment_codes_avoid_collisions_for_alt_and_editorial() -> None:
    segments = [
        SegmentCodeInput("primary", "race", "Austrian Grand Prix Race", "/tmp/1.mkv"),
        SegmentCodeInput("alt1", "alt_feed", "Austrian Grand Prix Race Alternate", "/tmp/2.mkv"),
        SegmentCodeInput("analysis", "analysis", "Post-Race Analysis", "/tmp/3.mkv"),
        SegmentCodeInput("highlights", "highlights", "Race Highlights", "/tmp/4.mkv"),
    ]

    codes = compute_segment_codes(segments)

    assert codes["primary"] == 50
    assert codes["alt1"] == 51
    assert codes["analysis"] == 80
    assert codes["highlights"] == 70
    assert len(set(codes.values())) == len(codes)


def test_episode_number_uses_base_100() -> None:
    assert episode_number(11, 51) == 1151

