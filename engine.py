from __future__ import annotations

import math
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

from models import CropSettings, Participant, Project

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MIN_PEOPLE = 1
MAX_PEOPLE = 8


class RenderError(RuntimeError):
    pass


class RenderCancelled(RenderError):
    pass


def _cancelled(cancel_event: threading.Event | None) -> bool:
    return bool(cancel_event and cancel_event.is_set())


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if _cancelled(cancel_event):
        raise RenderCancelled("Рендер отменён")


def ffmpeg_executable() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RenderError(f"Не удалось найти FFmpeg: {exc}") from exc


def decode_audio(
    ffmpeg: str,
    path: Path,
    sample_rate: int = 16000,
    cancel_event: threading.Event | None = None,
) -> np.ndarray:
    _check_cancel(cancel_event)
    command = [
        ffmpeg,
        "-v",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _check_cancel(cancel_event)
    if process.returncode != 0:
        details = process.stderr.decode("utf-8", errors="replace").strip()
        raise RenderError(f"Не удалось прочитать {path.name}. {details}")
    audio = np.frombuffer(process.stdout, dtype=np.float32).copy()
    if audio.size == 0:
        raise RenderError(f"Дорожка {path.name} пустая")
    return audio


def rms_per_frame(audio: np.ndarray, frames: int) -> np.ndarray:
    boundaries = np.linspace(0, len(audio), frames + 1, dtype=np.int64)
    result = np.zeros(frames, dtype=np.float32)
    for index in range(frames):
        chunk = audio[boundaries[index] : boundaries[index + 1]]
        if chunk.size:
            result[index] = float(np.sqrt(np.mean(chunk * chunk) + 1e-12))
    return result


def activity_envelope(rms: np.ndarray, fps: int, sensitivity: float, hold_ms: int) -> np.ndarray:
    positive = rms[rms > 1e-5]
    if positive.size == 0:
        return np.zeros_like(rms)
    floor = float(np.percentile(positive, 15))
    voice = float(np.percentile(positive, 92))
    spread = max(voice - floor, 1e-5)
    threshold = max(0.0025, floor + spread * 0.10) / max(sensitivity, 0.1)
    # A heavily normalized track can contain almost identical non-zero peaks.
    # Keep the threshold below the detected voice level in that case.
    threshold = min(threshold, voice * 0.80)
    ceiling = max(float(np.percentile(positive, 97)), threshold * 1.5)
    strength = np.clip((rms - threshold) / (ceiling - threshold), 0.0, 1.0)
    target = np.where(rms >= threshold, 0.62 + strength * 0.38, 0.0)

    hold_frames = max(0, round(hold_ms * fps / 1000))
    held = target.copy()
    remaining = 0
    last = 0.0
    for index in range(len(held)):
        if held[index] > 0:
            last = float(held[index])
            remaining = hold_frames
        elif remaining > 0:
            held[index] = max(0.45, last * (remaining / max(1, hold_frames)))
            remaining -= 1

    smooth = np.zeros_like(held)
    current = 0.0
    for index, value in enumerate(held):
        factor = 0.62 if value > current else 0.20
        current += (float(value) - current) * factor
        smooth[index] = current
    return np.clip(smooth, 0.0, 1.0)


def _already_styled_avatar(image: Image.Image) -> bool:
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    height, width = alpha.shape
    sample = max(2, min(width, height) // 24)
    corners = np.concatenate(
        [
            alpha[:sample, :sample].ravel(),
            alpha[:sample, -sample:].ravel(),
            alpha[-sample:, :sample].ravel(),
            alpha[-sample:, -sample:].ravel(),
        ]
    )
    return float(np.mean(corners < 20)) > 0.85 and float(np.mean(alpha > 20)) < 0.90


def _circle_mask(size: int, inset: int = 1) -> Image.Image:
    scale = 4
    large = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(large).ellipse(
        (inset * scale, inset * scale, (size - inset - 1) * scale, (size - inset - 1) * scale),
        fill=255,
    )
    return large.resize((size, size), Image.Resampling.LANCZOS)


def _cropped_square(image: Image.Image, crop: CropSettings, target: int) -> Image.Image:
    width, height = image.size
    base = float(min(width, height))
    window = max(2.0, base / crop.zoom)
    travel_x = max(0.0, width - window)
    travel_y = max(0.0, height - window)
    center_x = width / 2 + crop.offset_x * travel_x / 2
    center_y = height / 2 + crop.offset_y * travel_y / 2
    left = max(0.0, min(width - window, center_x - window / 2))
    top = max(0.0, min(height - window, center_y - window / 2))
    box = (round(left), round(top), round(left + window), round(top + window))
    return image.crop(box).resize((target, target), Image.Resampling.LANCZOS)


def contain_rgba(image: Image.Image, size: int) -> Image.Image:
    image = image.copy().convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox:
        image = image.crop(bbox)
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
    return result


def prepare_avatar(
    path: Path,
    size: int,
    auto_style: bool = True,
    crop: CropSettings | None = None,
) -> Image.Image:
    original = Image.open(path).convert("RGBA")
    crop = crop or CropSettings()
    if not auto_style or _already_styled_avatar(original):
        return contain_rgba(original, size)

    inset = max(8, round(size * 0.055))
    diameter = size - inset * 2
    photo = _cropped_square(original, crop, diameter)
    photo.putalpha(ImageChops.multiply(_circle_mask(diameter), photo.getchannel("A")))

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    center = size // 2
    radius = diameter // 2
    glow_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(glow_mask).ellipse(
        (center - radius - 2, center - radius - 2, center + radius + 2, center + radius + 2),
        outline=210,
        width=max(5, round(size * 0.025)),
    )
    glow_mask = glow_mask.filter(ImageFilter.GaussianBlur(max(5, round(size * 0.025))))
    glow = Image.new("RGBA", (size, size), (205, 207, 212, 0))
    glow.putalpha(glow_mask)
    canvas.alpha_composite(glow)
    canvas.alpha_composite(photo, (inset, inset))

    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ring)
    outer_width = max(4, round(size * 0.018))
    white_width = max(2, round(size * 0.008))
    bounds = (inset, inset, size - inset - 1, size - inset - 1)
    draw.ellipse(bounds, outline=(145, 147, 153, 235), width=outer_width)
    inner = max(2, outer_width // 2)
    draw.ellipse(
        (inset + inner, inset + inner, size - inset - inner - 1, size - inset - inner - 1),
        outline=(255, 255, 255, 255),
        width=white_width,
    )
    canvas.alpha_composite(ring)
    return canvas


def build_states(
    source: Image.Image,
    avatar_size: int,
    idle_brightness: float,
    active_scale: float,
    active_color: tuple[int, int, int],
    steps: int = 21,
) -> list[Image.Image]:
    patch_size = int(math.ceil(avatar_size * active_scale + 100))
    patch_size += patch_size % 2
    states: list[Image.Image] = []
    for index in range(steps):
        level = index / (steps - 1)
        current_size = max(2, round(avatar_size * (1 + (active_scale - 1) * level)))
        avatar = source.resize((current_size, current_size), Image.Resampling.LANCZOS)
        avatar = ImageEnhance.Brightness(avatar).enhance(idle_brightness + (1 - idle_brightness) * level)
        patch = Image.new("RGBA", (patch_size, patch_size), (0, 0, 0, 0))
        mask = Image.new("L", patch.size, 0)
        position = ((patch_size - current_size) // 2, (patch_size - current_size) // 2)
        mask.paste(avatar.getchannel("A"), position)
        if level > 0.02:
            expanded = mask.filter(ImageFilter.MaxFilter(17))
            glow_mask = expanded.filter(ImageFilter.GaussianBlur(24))
            glow_mask = glow_mask.point(lambda value: int(value * 0.70 * level))
            glow = Image.new("RGBA", patch.size, (*active_color, 0))
            glow.putalpha(glow_mask)
            patch.alpha_composite(glow)
            ring_values = np.asarray(expanded, dtype=np.int16) - np.asarray(mask, dtype=np.int16)
            ring_values = np.uint8(np.clip(ring_values * min(1.0, level * 1.6), 0, 255))
            outline = Image.new("RGBA", patch.size, (*active_color, 0))
            outline.putalpha(Image.fromarray(ring_values, "L"))
            patch.alpha_composite(outline)
        patch.alpha_composite(avatar, position)
        states.append(patch)
    return states


def default_positions(count: int) -> list[list[float]]:
    layouts: dict[int, list[tuple[float, float]]] = {
        1: [(0.50, 0.52)],
        2: [(0.34, 0.52), (0.66, 0.52)],
        3: [(0.22, 0.52), (0.50, 0.52), (0.78, 0.52)],
        4: [(0.34, 0.30), (0.66, 0.30), (0.34, 0.72), (0.66, 0.72)],
        5: [(0.22, 0.29), (0.50, 0.29), (0.78, 0.29), (0.36, 0.72), (0.64, 0.72)],
        6: [(0.22, 0.29), (0.50, 0.29), (0.78, 0.29), (0.22, 0.72), (0.50, 0.72), (0.78, 0.72)],
        7: [(0.14, 0.29), (0.38, 0.29), (0.62, 0.29), (0.86, 0.29), (0.26, 0.72), (0.50, 0.72), (0.74, 0.72)],
        8: [(0.14, 0.29), (0.38, 0.29), (0.62, 0.29), (0.86, 0.29), (0.14, 0.72), (0.38, 0.72), (0.62, 0.72), (0.86, 0.72)],
    }
    return [[x, y] for x, y in layouts[max(1, min(8, count))]]


def layout_avatar_size(count: int, width: int, height: int, size_cap: int) -> int:
    factors = {1: 0.54, 2: 0.46, 3: 0.39, 4: 0.34, 5: 0.30, 6: 0.29, 7: 0.25, 8: 0.25}
    return min(size_cap, round(height * factors[max(1, min(8, count))]))


def _hex(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise RenderError(f"Неверный цвет: {value}")
    try:
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError as exc:
        raise RenderError(f"Неверный цвет: {value}") from exc


def _encoder_command(
    ffmpeg: str,
    width: int,
    height: int,
    fps: int,
    frames: int,
    destination: Path,
    master_audio: Path,
) -> list[str]:
    duration = frames / fps
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
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-i",
        str(master_audio),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4",
        "-pix_fmt",
        "yuva444p10le",
        "-c:a",
        "pcm_s16le",
        "-t",
        f"{duration:.6f}",
        "-movflags",
        "+faststart",
        str(destination),
    ]


def validate_project(project: Project) -> list[str]:
    errors: list[str] = []
    if not project.master_audio or not Path(project.master_audio).is_file():
        errors.append("Выбери существующую общую аудиодорожку")
    if not MIN_PEOPLE <= len(project.participants) <= MAX_PEOPLE:
        errors.append("Количество участников должно быть от 1 до 8")
    for number, participant in enumerate(project.participants, 1):
        if not participant.avatar or not Path(participant.avatar).is_file():
            errors.append(f"Участник {number}: не выбрана аватарка")
        if not participant.voice or not Path(participant.voice).is_file():
            errors.append(f"Участник {number}: не выбрана дорожка голоса")
    if not project.output_path:
        errors.append("Выбери место сохранения результата")
    return errors


def render_project(
    project: Project,
    progress_callback: Callable[[int], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    progress = progress_callback or (lambda _value: None)
    status = status_callback or (lambda _value: None)
    project.normalized()
    errors = validate_project(project)
    if errors:
        raise RenderError("\n".join(errors))

    master_path = Path(project.master_audio)
    destination = Path(project.output_path).with_suffix(".mov")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.stem + ".partial.mov")
    ffmpeg = ffmpeg_executable()
    sample_rate = 16000
    process: subprocess.Popen | None = None

    try:
        status("Читаю общую дорожку…")
        progress(1)
        master = decode_audio(ffmpeg, master_path, sample_rate, cancel_event)
        total_samples = len(master)
        frames = max(1, math.ceil(total_samples / sample_rate * project.fps))
        voice_samples: list[np.ndarray] = []
        for index, participant in enumerate(project.participants, 1):
            status(f"Читаю голос {index} из {len(project.participants)}…")
            voice_samples.append(decode_audio(ffmpeg, Path(participant.voice), sample_rate, cancel_event))
            progress(2 + round(index / len(project.participants) * 10))

        status("Определяю моменты речи…")
        envelopes: list[np.ndarray] = []
        for index, (samples, participant) in enumerate(zip(voice_samples, project.participants), 1):
            samples = samples[:total_samples]
            samples = np.pad(samples, (0, max(0, total_samples - len(samples))))
            rms = rms_per_frame(samples, frames)
            envelopes.append(activity_envelope(rms, project.fps, participant.sensitivity, 150))
            progress(12 + round(index / len(project.participants) * 8))
            _check_cancel(cancel_event)

        status("Готовлю аватарки…")
        count = len(project.participants)
        avatar_size = layout_avatar_size(count, project.width, project.height, project.avatar_size)
        active_color = _hex("#25F47B")
        all_states: list[list[Image.Image]] = []
        for index, participant in enumerate(project.participants, 1):
            source = prepare_avatar(
                Path(participant.avatar),
                avatar_size,
                project.auto_avatar_style,
                participant.crop,
            )
            all_states.append(build_states(source, avatar_size, 0.48, 1.065, active_color))
            progress(20 + round(index / count * 10))
            _check_cancel(cancel_event)

        positions = project.positions if len(project.positions) == count else default_positions(count)
        centers = [(round(x * project.width), round(y * project.height)) for x, y in positions]
        command = _encoder_command(
            ffmpeg,
            project.width,
            project.height,
            project.fps,
            frames,
            partial,
            master_path,
        )
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdin is not None
        steps = len(all_states[0])
        patch_size = all_states[0][0].width
        progress_every = max(1, frames // 350)
        status("Создаю прозрачное видео…")

        for frame_index in range(frames):
            _check_cancel(cancel_event)
            canvas = Image.new("RGBA", (project.width, project.height), (0, 0, 0, 0))
            for person_index in range(count):
                level = float(envelopes[person_index][frame_index])
                state = all_states[person_index][min(steps - 1, round(level * (steps - 1)))]
                center_x, center_y = centers[person_index]
                bob = round(-9 * level + math.sin(frame_index * 0.40 + person_index * 0.83) * 1.5 * level)
                canvas.alpha_composite(
                    state,
                    (round(center_x - patch_size / 2), round(center_y - patch_size / 2 + bob)),
                )
            try:
                process.stdin.write(np.asarray(canvas, dtype=np.uint8).tobytes())
            except BrokenPipeError:
                break
            if frame_index % progress_every == 0 or frame_index == frames - 1:
                progress(30 + round((frame_index + 1) / frames * 70))

        process.stdin.close()
        stderr = process.stderr.read() if process.stderr else b""
        return_code = process.wait()
        if _cancelled(cancel_event):
            raise RenderCancelled("Рендер отменён")
        if return_code != 0:
            details = stderr.decode("utf-8", errors="replace").strip()
            raise RenderError("FFmpeg не смог создать видео. " + details)
        os.replace(partial, destination)
        progress(100)
        status("Готово")
        return destination
    except BaseException:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        if partial.exists():
            partial.unlink()
        raise


def discover_folder(folder: Path) -> tuple[Path | None, list[Participant]]:
    folder = Path(folder)
    files = [path for path in folder.iterdir() if path.is_file()]
    master = next(
        (path for path in files if path.stem.lower() in {"master", "общая", "mix", "main"} and path.suffix.lower() in AUDIO_EXTENSIONS),
        None,
    )
    participants: list[Participant] = []
    for number in range(1, MAX_PEOPLE + 1):
        avatar = next(
            (path for path in files if path.stem.lower() in {f"avatar{number}", f"avatar_{number}", f"аватар{number}"} and path.suffix.lower() in IMAGE_EXTENSIONS),
            None,
        )
        voice = next(
            (path for path in files if path.stem.lower() in {f"voice{number}", f"voice_{number}", f"голос{number}"} and path.suffix.lower() in AUDIO_EXTENSIONS),
            None,
        )
        if avatar or voice:
            participants.append(Participant(avatar=str(avatar or ""), voice=str(voice or "")))
    return master, participants
