from pathlib import Path

from models import CropSettings, Participant, Project


def test_project_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "demo.ravproj"
    source = Project(
        master_audio="master.wav",
        output_path="result.mov",
        participants=[
            Participant(
                avatar="avatar.png",
                voice="voice.wav",
                sensitivity=1.25,
                crop=CropSettings(zoom=1.4, offset_x=0.2, offset_y=-0.1),
            )
        ],
        width=1280,
        height=720,
        fps=60,
        positions=[[0.3, 0.7]],
    )

    source.save(path)
    restored = Project.load(path)

    assert restored.width == 1280
    assert restored.height == 720
    assert restored.fps == 60
    assert restored.positions == [[0.3, 0.7]]
    assert restored.participants[0].sensitivity == 1.25
    assert restored.participants[0].crop.zoom == 1.4


def test_project_normalizes_invalid_values() -> None:
    project = Project(
        participants=[],
        width=1,
        height=99999,
        fps=200,
        avatar_size=1,
    ).normalized()

    assert len(project.participants) == 1
    assert project.width == 320
    assert project.height == 4320
    assert project.fps == 60
    assert project.avatar_size == 120
