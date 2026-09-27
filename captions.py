from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import engine
from models import CaptionSegment, CaptionSettings, CaptionWord

MEDIA_EXTENSIONS = engine.AUDIO_EXTENSIONS | {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


class CaptionError(RuntimeError):
    pass


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event and cancel_event.is_set():
        raise engine.RenderCancelled("Операция отменена")


def seconds_to_srt(value: float) -> str:
    milliseconds = max(0, round(value * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def seconds_to_ass(value: float) -> str:
    centiseconds = max(0, round(value * 100))
    hours, centiseconds = divmod(centiseconds, 360_000)
    minutes, centiseconds = divmod(centiseconds, 6_000)
    seconds, centiseconds = divmod(centiseconds, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def parse_timestamp(value: str) -> float:
    match = re.fullmatch(r"\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*", value)
    if not match:
        raise CaptionError(f"Неверный таймкод: {value}")
    hours, minutes, seconds, fraction = match.groups()
    milliseconds = int(fraction.ljust(3, "0")[:3])
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + milliseconds / 1000


def _tokens(text: str) -> list[str]:
    return [item for item in re.findall(r"\S+", text.strip()) if item]


def words_for_segment(segment: CaptionSegment) -> list[CaptionWord]:
    tokens = _tokens(segment.text)
    if not tokens:
        return []
    existing_text = " ".join(word.text for word in segment.words).strip()
    if segment.words and existing_text.casefold() == " ".join(tokens).casefold():
        return segment.words
    duration = max(0.04, segment.end - segment.start)
    step = duration / len(tokens)
    return [
        CaptionWord(
            start=segment.start + index * step,
            end=segment.start + (index + 1) * step,
            text=token,
        )
        for index, token in enumerate(tokens)
    ]


def group_words(words: Iterable[CaptionWord], words_per_caption: int = 4, max_gap: float = 0.72) -> list[CaptionSegment]:
    words_per_caption = max(1, min(12, int(words_per_caption)))
    result: list[CaptionSegment] = []
    group: list[CaptionWord] = []
    for word in words:
        cleaned = CaptionWord(max(0.0, word.start), max(word.start, word.end), word.text.strip())
        if not cleaned.text:
            continue
        gap = cleaned.start - group[-1].end if group else 0.0
        if group and (len(group) >= words_per_caption or gap > max_gap):
            result.append(_segment_from_words(group))
            group = []
        group.append(cleaned)
        if cleaned.text.endswith((".", "!", "?", "…")) and len(group) >= max(2, words_per_caption // 2):
            result.append(_segment_from_words(group))
            group = []
    if group:
        result.append(_segment_from_words(group))
    return result


def _segment_from_words(words: list[CaptionWord]) -> CaptionSegment:
    return CaptionSegment(
        start=words[0].start,
        end=max(words[-1].end, words[0].start + 0.04),
        text=" ".join(word.text for word in words),
        words=list(words),
    )


def regroup_segments(segments: Iterable[CaptionSegment], words_per_caption: int) -> list[CaptionSegment]:
    words: list[CaptionWord] = []
    for segment in segments:
        words.extend(words_for_segment(segment))
    return group_words(words, words_per_caption)


def transcribe_media(
    source: Path,
    settings: CaptionSettings,
    progress_callback: Callable[[int], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> list[CaptionSegment]:
    source = Path(source)
    if not source.is_file():
        raise CaptionError("Выбери существующее видео или аудиодорожку")
    progress = progress_callback or (lambda _value: None)
    status = status_callback or (lambda _value: None)
    _check_cancel(cancel_event)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise CaptionError(
            "Компонент распознавания не установлен. Запусти START.bat ещё раз "
            "или выполни: python -m pip install faster-whisper"
        ) from exc

    status("Загружаю модель распознавания… При первом запуске это может занять время")
    progress(3)
    try:
        model = WhisperModel(settings.model_size, device="cpu", compute_type="int8")
    except Exception as exc:
        raise CaptionError(f"Не удалось загрузить модель Whisper: {exc}") from exc
    _check_cancel(cancel_event)
    status("Распознаю речь…")
    progress(8)
    try:
        generated, info = model.transcribe(
            str(source),
            language=None if settings.language == "auto" else settings.language,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        duration = max(0.1, float(getattr(info, "duration", 0.0) or 0.0))
        words: list[CaptionWord] = []
        for segment in generated:
            _check_cancel(cancel_event)
            segment_words = list(getattr(segment, "words", None) or [])
            if segment_words:
                for word in segment_words:
                    text = str(getattr(word, "word", "")).strip()
                    if text:
                        words.append(
                            CaptionWord(
                                start=max(0.0, float(getattr(word, "start", segment.start) or segment.start)),
                                end=max(0.0, float(getattr(word, "end", segment.end) or segment.end)),
                                text=text,
                            )
                        )
            else:
                fallback = CaptionSegment(float(segment.start), float(segment.end), str(segment.text).strip())
                words.extend(words_for_segment(fallback))
            progress(min(96, 8 + round(float(segment.end) / duration * 88)))
    except engine.RenderCancelled:
        raise
    except Exception as exc:
        raise CaptionError(f"Ошибка распознавания: {exc}") from exc
    if not words:
        raise CaptionError("Речь не найдена. Проверь громкость дорожки или выбери другую модель")
    progress(100)
    status("Расшифровка готова")
    return group_words(words, settings.words_per_caption)


def export_srt(segments: Iterable[CaptionSegment], destination: Path) -> Path:
    destination = Path(destination).with_suffix(".srt")
    destination.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for index, segment in enumerate(segments, 1):
        blocks.append(
            f"{index}\n{seconds_to_srt(segment.start)} --> {seconds_to_srt(segment.end)}\n{segment.text.strip()}"
        )
    destination.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8-sig")
    return destination


def import_srt(path: Path) -> list[CaptionSegment]:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
    result: list[CaptionSegment] = []
    pattern = re.compile(
        r"(?:^|\n\s*\n)(?:\d+\s*\n)?"
        r"(?P<start>\d+:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
        r"(?P<end>\d+:\d{2}:\d{2}[,.]\d{1,3})[^\n]*\n"
        r"(?P<text>.*?)(?=\n\s*\n|\Z)",
        re.DOTALL,
    )
    for match in pattern.finditer(text):
        start = parse_timestamp(match.group("start"))
        end = max(start, parse_timestamp(match.group("end")))
        value = " ".join(line.strip() for line in match.group("text").splitlines()).strip()
        if value:
            segment = CaptionSegment(start=start, end=end, text=value)
            segment.words = words_for_segment(segment)
            result.append(segment)
    if not result:
        raise CaptionError("В SRT не найдено ни одного корректного субтитра")
    return result


def export_ass(segments: Iterable[CaptionSegment], settings: CaptionSettings, destination: Path) -> Path:
    destination = Path(destination).with_suffix(".ass")
    destination.parent.mkdir(parents=True, exist_ok=True)
    alignment = 2
    margin_v = max(0, round(settings.height * (1.0 - settings.y_position)))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {settings.width}
PlayResY: {settings.height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Reactive,{settings.font_family},{settings.font_size},&H00FFFFFF,&H007BF425,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{settings.stroke_width},2,{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    for segment in segments:
        words = words_for_segment(segment)
        karaoke = []
        for word in words:
            duration = max(1, round((word.end - word.start) * 100))
            value = word.text.upper() if settings.uppercase else word.text
            value = value.replace("{", "(").replace("}", ")")
            karaoke.append(f"{{\\kf{duration}}}{value}")
        text = " ".join(karaoke) if karaoke else segment.text
        events.append(
            f"Dialogue: 0,{seconds_to_ass(segment.start)},{seconds_to_ass(segment.end)},Reactive,,0,0,0,,{text}"
        )
    destination.write_text(header + "\n".join(events) + ("\n" if events else ""), encoding="utf-8-sig")
    return destination


def _font_candidates(family: str, bold: bool) -> list[Path]:
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    mapping = {
        "Segoe UI": "segoeuib.ttf" if bold else "segoeui.ttf",
        "Arial": "arialbd.ttf" if bold else "arial.ttf",
        "Impact": "impact.ttf",
        "DejaVu Sans": "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    }
    name = mapping.get(family, mapping["Segoe UI"])
    return [
        windows / name,
        Path("/usr/share/fonts/truetype/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2") / ("LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"),
    ]


def load_font(family: str, size: int, bold: bool = True) -> ImageFont.ImageFont:
    for path in _font_candidates(family, bold):
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


def active_segment(segments: list[CaptionSegment], time_seconds: float) -> CaptionSegment | None:
    return next((item for item in segments if item.start <= time_seconds < item.end), None)


def render_caption_frame(
    settings: CaptionSettings,
    segment: CaptionSegment | None,
    time_seconds: float,
) -> Image.Image:
    canvas = Image.new("RGBA", (settings.width, settings.height), (0, 0, 0, 0))
    if not segment or not segment.text.strip():
        return canvas
    words = words_for_segment(segment)
    if not words:
        return canvas
    values = [word.text.upper() if settings.uppercase or settings.preset == "meme" else word.text for word in words]
    active_index = 0
    for index, word in enumerate(words):
        if word.start <= time_seconds < word.end:
            active_index = index
            break
        if time_seconds >= word.end:
            active_index = index

    entrance = min(1.0, max(0.0, (time_seconds - segment.start) / 0.14))
    bounce = math.sin(entrance * math.pi) * 0.06 if settings.preset in {"reels", "meme"} else 0.0
    font_size = round(settings.font_size * (1.0 + bounce))
    font = load_font(settings.font_family, font_size, bold=True)
    draw = ImageDraw.Draw(canvas)
    spacing = max(10, round(font_size * 0.17))

    def measure(chosen_font: ImageFont.ImageFont) -> tuple[list[int], int, int]:
        widths = [draw.textbbox((0, 0), value, font=chosen_font, stroke_width=settings.stroke_width)[2] for value in values]
        height = max(
            draw.textbbox((0, 0), value, font=chosen_font, stroke_width=settings.stroke_width)[3]
            for value in values
        )
        return widths, sum(widths) + spacing * (len(widths) - 1), height

    widths, total_width, text_height = measure(font)
    while total_width > settings.width * 0.90 and font_size > 24:
        font_size = max(24, font_size - 4)
        font = load_font(settings.font_family, font_size, bold=True)
        spacing = max(8, round(font_size * 0.17))
        widths, total_width, text_height = measure(font)

    x = round((settings.width - total_width) / 2)
    y = round(settings.height * settings.y_position - text_height / 2)
    if settings.preset == "meme":
        y += round(math.sin(time_seconds * 31.0) * settings.height * 0.0025)
        x += round(math.sin(time_seconds * 43.0) * settings.width * 0.0025)
    shadow_offset = max(2, round(font_size * 0.055))
    for index, (value, width) in enumerate(zip(values, widths)):
        fill = "#25F47B" if index == active_index else "#FFFFFF"
        draw.text(
            (x + shadow_offset, y + shadow_offset),
            value,
            font=font,
            fill=(0, 0, 0, 150),
            stroke_width=settings.stroke_width + 2,
            stroke_fill=(0, 0, 0, 120),
        )
        draw.text(
            (x, y),
            value,
            font=font,
            fill=fill,
            stroke_width=settings.stroke_width,
            stroke_fill="#090B10",
        )
        x += width + spacing
    return canvas


def probe_duration(source: Path) -> float:
    command = [engine.ffmpeg_executable(), "-hide_banner", "-i", str(source)]
    process = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    text = process.stderr.decode("utf-8", errors="replace")
    match = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}\.\d+)", text)
    if not match:
        return 0.0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def _overlay_encoder(ffmpeg: str, settings: CaptionSettings, frames: int, destination: Path) -> list[str]:
    return [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{settings.width}x{settings.height}",
        "-r",
        str(settings.fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4",
        "-pix_fmt",
        "yuva444p10le",
        "-frames:v",
        str(frames),
        str(destination),
    ]


def render_overlay(
    source: Path,
    segments: list[CaptionSegment],
    settings: CaptionSettings,
    destination: Path,
    progress_callback: Callable[[int], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    if not segments:
        raise CaptionError("Сначала создай или импортируй субтитры")
    progress = progress_callback or (lambda _value: None)
    status = status_callback or (lambda _value: None)
    destination = Path(destination).with_suffix(".mov")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.stem + ".partial.mov")
    duration = max(probe_duration(source), max(item.end for item in segments) + 0.15)
    frames = max(1, math.ceil(duration * settings.fps))
    ffmpeg = engine.ffmpeg_executable()
    process: subprocess.Popen | None = None
    try:
        status("Создаю прозрачные субтитры…")
        process = subprocess.Popen(_overlay_encoder(ffmpeg, settings, frames, partial), stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdin is not None
        progress_every = max(1, frames // 300)
        for frame_index in range(frames):
            _check_cancel(cancel_event)
            current_time = frame_index / settings.fps
            frame = render_caption_frame(settings, active_segment(segments, current_time), current_time)
            try:
                process.stdin.write(np.asarray(frame, dtype=np.uint8).tobytes())
            except BrokenPipeError:
                break
            if frame_index % progress_every == 0 or frame_index == frames - 1:
                progress(round((frame_index + 1) / frames * 100))
        process.stdin.close()
        stderr = process.stderr.read() if process.stderr else b""
        return_code = process.wait()
        _check_cancel(cancel_event)
        if return_code != 0:
            raise CaptionError("FFmpeg не смог создать видео. " + stderr.decode("utf-8", errors="replace").strip())
        os.replace(partial, destination)
        status("Прозрачное видео готово")
        progress(100)
        return destination
    except BaseException:
        if process and process.poll() is None:
            process.terminate()
        if partial.exists():
            partial.unlink()
        raise


def render_burned_video(
    source: Path,
    segments: list[CaptionSegment],
    settings: CaptionSettings,
    destination: Path,
    progress_callback: Callable[[int], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    source = Path(source)
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise CaptionError("Для готового MP4 нужно выбрать исходное видео, а не только аудио")
    progress = progress_callback or (lambda _value: None)
    status = status_callback or (lambda _value: None)
    destination = Path(destination).with_suffix(".mp4")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.stem + ".partial.mp4")
    with tempfile.TemporaryDirectory(prefix="reactive_captions_") as folder:
        overlay = Path(folder) / "captions.mov"
        render_overlay(
            source,
            segments,
            settings,
            overlay,
            progress_callback=lambda value: progress(round(value * 0.88)),
            status_callback=status,
            cancel_event=cancel_event,
        )
        _check_cancel(cancel_event)
        status("Накладываю субтитры на исходное видео…")
        command = [
            engine.ffmpeg_executable(),
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-i",
            str(overlay),
            "-filter_complex",
            "[0:v][1:v]overlay=0:0:format=auto[v]",
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(partial),
        ]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        while process.poll() is None:
            if cancel_event and cancel_event.wait(0.15):
                process.terminate()
                process.wait(timeout=3)
                raise engine.RenderCancelled("Операция отменена")
        stderr = process.stderr.read() if process.stderr else b""
        if process.returncode != 0:
            raise CaptionError("Не удалось наложить субтитры. " + stderr.decode("utf-8", errors="replace").strip())
        os.replace(partial, destination)
        progress(100)
        status("Видео с субтитрами готово")
        return destination
