from __future__ import annotations

import json
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "Reactive Avatars"
APP_VERSION = "2.0.0"
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


@dataclass
class CropSettings:
    zoom: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    @classmethod
    def from_dict(cls, value: dict | None) -> "CropSettings":
        value = value or {}
        return cls(
            zoom=max(1.0, min(3.0, float(value.get("zoom", 1.0)))),
            offset_x=max(-1.0, min(1.0, float(value.get("offset_x", 0.0)))),
            offset_y=max(-1.0, min(1.0, float(value.get("offset_y", 0.0)))),
        )


@dataclass
class Participant:
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    avatar: str = ""
    voice: str = ""
    sensitivity: float = 1.0
    crop: CropSettings = field(default_factory=CropSettings)

    @classmethod
    def from_dict(cls, value: dict) -> "Participant":
        return cls(
            uid=str(value.get("uid") or uuid.uuid4().hex),
            avatar=str(value.get("avatar", "")),
            voice=str(value.get("voice", "")),
            sensitivity=max(0.5, min(2.0, float(value.get("sensitivity", 1.0)))),
            crop=CropSettings.from_dict(value.get("crop")),
        )


@dataclass
class Project:
    master_audio: str = ""
    output_path: str = ""
    participants: list[Participant] = field(
        default_factory=lambda: [Participant(), Participant()]
    )
    width: int = 1920
    height: int = 1080
    fps: int = 30
    avatar_size: int = 470
    auto_avatar_style: bool = True
    positions: list[list[float]] = field(default_factory=list)
    project_path: str = ""

    def normalized(self) -> "Project":
        self.width = max(320, min(7680, int(self.width)))
        self.height = max(240, min(4320, int(self.height)))
        self.fps = max(12, min(60, int(self.fps)))
        self.avatar_size = max(120, min(1200, int(self.avatar_size)))
        self.participants = self.participants[:8] or [Participant()]
        if len(self.positions) != len(self.participants):
            self.positions = []
        else:
            self.positions = [
                [max(0.04, min(0.96, float(pos[0]))), max(0.04, min(0.96, float(pos[1])))]
                for pos in self.positions
                if isinstance(pos, (list, tuple)) and len(pos) == 2
            ]
            if len(self.positions) != len(self.participants):
                self.positions = []
        return self

    def to_dict(self) -> dict:
        value = asdict(self)
        value["format_version"] = 1
        value["app_version"] = APP_VERSION
        value.pop("project_path", None)
        return value

    def save(self, path: Path) -> None:
        path = Path(path).with_suffix(".ravproj")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.project_path = str(path)

    @classmethod
    def load(cls, path: Path) -> "Project":
        path = Path(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        participants = [Participant.from_dict(item) for item in value.get("participants", [])]
        project = cls(
            master_audio=str(value.get("master_audio", "")),
            output_path=str(value.get("output_path", "")),
            participants=participants or [Participant(), Participant()],
            width=int(value.get("width", 1920)),
            height=int(value.get("height", 1080)),
            fps=int(value.get("fps", 30)),
            avatar_size=int(value.get("avatar_size", 470)),
            auto_avatar_style=bool(value.get("auto_avatar_style", True)),
            positions=value.get("positions", []),
            project_path=str(path),
        )
        return project.normalized()
