from pathlib import Path

import numpy as np
from PIL import Image

import engine
from models import CropSettings, Participant, Project


def test_all_default_layouts_are_complete_and_inside_canvas() -> None:
    for count in range(1, 9):
        positions = engine.default_positions(count)
        assert len(positions) == count
        assert all(0 < x < 1 and 0 < y < 1 for x, y in positions)


def test_silent_audio_has_no_activity() -> None:
    rms = np.zeros(30, dtype=np.float32)
    envelope = engine.activity_envelope(rms, fps=30, sensitivity=1.0, hold_ms=150)
    assert np.count_nonzero(envelope) == 0


def test_voice_activity_is_detected() -> None:
    rms = np.zeros(30, dtype=np.float32)
    rms[10:18] = 0.3
    envelope = engine.activity_envelope(rms, fps=30, sensitivity=1.0, hold_ms=150)
    assert float(envelope.max()) > 0.5
    assert float(envelope[18]) > 0


def test_prepare_avatar_creates_transparent_corners(tmp_path: Path) -> None:
    path = tmp_path / "photo.png"
    Image.new("RGB", (300, 500), "#C08565").save(path)

    avatar = engine.prepare_avatar(
        path,
        size=256,
        auto_style=True,
        crop=CropSettings(zoom=1.3, offset_x=0.1, offset_y=-0.2),
    )

    assert avatar.mode == "RGBA"
    assert avatar.size == (256, 256)
    assert avatar.getpixel((0, 0))[3] == 0
    assert avatar.getchannel("A").getbbox() is not None


def test_discover_folder_matches_numbered_files(tmp_path: Path) -> None:
    for name in ("master.wav", "voice1.wav", "voice2.mp3", "avatar1.png", "avatar2.webp"):
        (tmp_path / name).touch()

    master, participants = engine.discover_folder(tmp_path)

    assert master == tmp_path / "master.wav"
    assert len(participants) == 2
    assert participants[0].avatar.endswith("avatar1.png")
    assert participants[1].voice.endswith("voice2.mp3")


def test_validate_project_reports_missing_files(tmp_path: Path) -> None:
    project = Project(
        master_audio=str(tmp_path / "missing-master.wav"),
        output_path=str(tmp_path / "result.mov"),
        participants=[Participant(avatar="", voice="")],
    )

    errors = engine.validate_project(project)

    assert len(errors) == 3
    assert any("общую аудиодорожку" in error for error in errors)
