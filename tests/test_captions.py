import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import captions
from models import CaptionSegment, CaptionSettings, CaptionWord


def test_group_words_splits_by_size_and_long_pause() -> None:
    words = [
        CaptionWord(0.0, 0.3, "Это"),
        CaptionWord(0.3, 0.6, "первые"),
        CaptionWord(0.6, 0.9, "слова"),
        CaptionWord(2.0, 2.3, "После"),
        CaptionWord(2.3, 2.6, "паузы"),
    ]

    segments = captions.group_words(words, words_per_caption=3)

    assert [segment.text for segment in segments] == ["Это первые слова", "После паузы"]
    assert segments[1].start == 2.0


def test_srt_round_trip(tmp_path: Path) -> None:
    source = [
        CaptionSegment(0.25, 1.5, "Первая фраза"),
        CaptionSegment(2.0, 3.75, "Вторая фраза"),
    ]
    path = captions.export_srt(source, tmp_path / "captions.srt")

    restored = captions.import_srt(path)

    assert len(restored) == 2
    assert restored[0].text == "Первая фраза"
    assert restored[1].end == 3.75


def test_render_caption_frame_has_real_transparency_and_green_word() -> None:
    settings = CaptionSettings(width=640, height=360, font_size=48, y_position=0.7)
    segment = CaptionSegment(
        0.0,
        1.0,
        "Привет мир",
        [CaptionWord(0.0, 0.5, "Привет"), CaptionWord(0.5, 1.0, "мир")],
    )

    frame = captions.render_caption_frame(settings, segment, 0.25)

    assert frame.mode == "RGBA"
    assert frame.getpixel((0, 0))[3] == 0
    pixels = np.asarray(frame)
    red, green, blue, alpha = (pixels[:, :, index] for index in range(4))
    assert np.any((green > red * 1.3) & (green > blue * 1.3) & (alpha > 0))


def test_ass_export_contains_karaoke_timing(tmp_path: Path) -> None:
    segment = CaptionSegment(
        0.0,
        1.0,
        "Раз два",
        [CaptionWord(0.0, 0.4, "Раз"), CaptionWord(0.4, 1.0, "два")],
    )
    path = captions.export_ass([segment], CaptionSettings(), tmp_path / "captions.ass")

    text = path.read_text(encoding="utf-8-sig")
    assert "{\\kf40}Раз" in text
    assert "{\\kf60}два" in text


def test_transcribe_media_uses_word_timestamps(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "voice.wav"
    source.touch()

    class FakeModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, *_args, **_kwargs):
            words = [
                SimpleNamespace(start=0.0, end=0.4, word="Привет"),
                SimpleNamespace(start=0.4, end=0.8, word="мир"),
            ]
            return [SimpleNamespace(start=0.0, end=0.8, text="Привет мир", words=words)], SimpleNamespace(duration=0.8)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))

    result = captions.transcribe_media(source, CaptionSettings(words_per_caption=4))

    assert len(result) == 1
    assert result[0].text == "Привет мир"
    assert result[0].words[1].start == 0.4
