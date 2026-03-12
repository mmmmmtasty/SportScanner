from __future__ import annotations

import errno

from sportscanner.organizer.placer import place_file


def test_place_file_removes_source_after_success(tmp_path) -> None:
    source = tmp_path / "incoming.mkv"
    destination = tmp_path / "library" / "episode.mkv"
    source.write_text("video", encoding="utf-8")
    destination.parent.mkdir()

    placed_path = place_file(source, destination)

    assert placed_path == str(destination)
    assert destination.read_text(encoding="utf-8") == "video"
    assert not source.exists()


def test_place_file_falls_back_to_copy_when_hardlink_is_rejected(tmp_path, monkeypatch) -> None:
    source = tmp_path / "incoming.mkv"
    destination = tmp_path / "library" / "episode.mkv"
    source.write_text("video", encoding="utf-8")
    destination.parent.mkdir()

    def fail_link(_source, _destination) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr("sportscanner.organizer.placer.os.link", fail_link)

    placed_path = place_file(source, destination)

    assert placed_path == str(destination)
    assert destination.read_text(encoding="utf-8") == "video"
    assert not source.exists()
