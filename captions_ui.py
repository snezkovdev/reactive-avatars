from __future__ import annotations

import threading
from dataclasses import asdict
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import captions
import engine
from models import APP_ROOT, CaptionSegment, CaptionSettings, Project

MEDIA_FILTER = "Видео и аудио (*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus);;Все файлы (*.*)"


def panel(title: str, hint: str) -> QFrame:
    result = QFrame()
    result.setObjectName("Panel")
    layout = QVBoxLayout(result)
    layout.setContentsMargins(14, 13, 14, 14)
    layout.setSpacing(9)
    title_label = QLabel(title)
    title_label.setObjectName("Section")
    hint_label = QLabel(hint)
    hint_label.setObjectName("Muted")
    hint_label.setWordWrap(True)
    layout.addWidget(title_label)
    layout.addWidget(hint_label)
    return result


class CaptionPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(390, 370)
        self.settings = CaptionSettings()
        self.segment: CaptionSegment | None = None
        self.time_seconds = 0.0
        self.pixmap = QPixmap()

    def set_data(self, settings: CaptionSettings, segment: CaptionSegment | None) -> None:
        self.settings = CaptionSettings.from_dict(asdict(settings))
        self.segment = segment
        self.time_seconds = segment.start + 0.2 if segment else 0.0
        self.refresh()

    def refresh(self) -> None:
        if not self.segment:
            self.pixmap = QPixmap()
            self.update()
            return
        scale = min(1.0, 720 / max(self.settings.width, self.settings.height))
        preview = CaptionSettings.from_dict(asdict(self.settings))
        preview.width = max(320, round(preview.width * scale))
        preview.height = max(240, round(preview.height * scale))
        preview.font_size = max(24, round(preview.font_size * scale))
        preview.stroke_width = max(1, round(preview.stroke_width * scale))
        image = captions.render_caption_frame(preview, self.segment, self.time_seconds)
        self.pixmap = QPixmap.fromImage(ImageQt(image))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        cell = 18
        colors = (QColor("#20242D"), QColor("#252A34"))
        for y in range(0, self.height(), cell):
            for x in range(0, self.width(), cell):
                painter.fillRect(x, y, cell, cell, colors[((x // cell) + (y // cell)) % 2])
        painter.setPen(QPen(QColor("#3A414D"), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 11, 11)
        if self.pixmap.isNull():
            painter.setPen(QColor("#9198A7"))
            painter.drawText(self.rect(), Qt.AlignCenter, "После расшифровки здесь появится пример")
            return
        scaled = self.pixmap.scaled(self.size() - self.size() * 0.06, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)


class TranscribeWorker(QObject):
    progress = Signal(int)
    status = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, source: Path, settings: CaptionSettings, cancel_event: threading.Event):
        super().__init__()
        self.source = source
        self.settings = settings
        self.cancel_event = cancel_event

    @Slot()
    def run(self) -> None:
        try:
            result = captions.transcribe_media(
                self.source,
                self.settings,
                progress_callback=self.progress.emit,
                status_callback=self.status.emit,
                cancel_event=self.cancel_event,
            )
            self.finished.emit(result)
        except engine.RenderCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class CaptionExportWorker(QObject):
    progress = Signal(int)
    status = Signal(str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        source: Path,
        segments: list[CaptionSegment],
        settings: CaptionSettings,
        destination: Path,
        cancel_event: threading.Event,
    ):
        super().__init__()
        self.source = source
        self.segments = segments
        self.settings = settings
        self.destination = destination
        self.cancel_event = cancel_event

    @Slot()
    def run(self) -> None:
        try:
            if self.settings.export_mode == "srt":
                result = captions.export_srt(self.segments, self.destination)
            elif self.settings.export_mode == "ass":
                result = captions.export_ass(self.segments, self.settings, self.destination)
            elif self.settings.export_mode == "burned":
                result = captions.render_burned_video(
                    self.source,
                    self.segments,
                    self.settings,
                    self.destination,
                    self.progress.emit,
                    self.status.emit,
                    self.cancel_event,
                )
            else:
                result = captions.render_overlay(
                    self.source,
                    self.segments,
                    self.settings,
                    self.destination,
                    self.progress.emit,
                    self.status.emit,
                    self.cancel_event,
                )
            self.progress.emit(100)
            self.finished.emit(str(result))
        except engine.RenderCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class CaptionsWidget(QWidget):
    changed = Signal()
    master_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments: list[CaptionSegment] = []
        self.thread: QThread | None = None
        self.worker: QObject | None = None
        self.cancel_event: threading.Event | None = None
        self.last_result = ""
        self._building = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(11)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(2, 2, 6, 2)
        left_layout.setSpacing(11)
        splitter.addWidget(left)

        source_panel = panel("1. Видео или аудио", "Распознавание выполняется локально. При первом запуске модель будет загружена один раз.")
        source_row = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Выбери видео, master или отдельную дорожку")
        self.source_edit.textChanged.connect(self._changed)
        source_button = QPushButton("Выбрать")
        source_button.clicked.connect(self.choose_source)
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(source_button)
        source_panel.layout().addLayout(source_row)
        master_button = QPushButton("Взять общую дорожку из вкладки «Аватарки»")
        master_button.clicked.connect(self.master_requested.emit)
        source_panel.layout().addWidget(master_button)
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Модель"))
        self.model_combo = QComboBox()
        self.model_combo.addItem("Tiny — быстро, черновик", "tiny")
        self.model_combo.addItem("Base — быстрее", "base")
        self.model_combo.addItem("Small — рекомендуется", "small")
        self.model_combo.addItem("Medium — точнее, медленно", "medium")
        self.model_combo.currentIndexChanged.connect(self._changed)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(QLabel("Язык"))
        self.language_combo = QComboBox()
        self.language_combo.addItem("Русский", "ru")
        self.language_combo.addItem("Определить автоматически", "auto")
        self.language_combo.currentIndexChanged.connect(self._changed)
        model_row.addWidget(self.language_combo)
        source_panel.layout().addLayout(model_row)
        action_row = QHBoxLayout()
        self.transcribe_button = QPushButton("Распознать речь")
        self.transcribe_button.setObjectName("Primary")
        self.transcribe_button.clicked.connect(self.start_transcription)
        import_button = QPushButton("Импорт SRT")
        import_button.clicked.connect(self.import_srt)
        action_row.addWidget(self.transcribe_button)
        action_row.addWidget(import_button)
        source_panel.layout().addLayout(action_row)
        left_layout.addWidget(source_panel)

        editor_panel = panel("2. Текст и тайминги", "Текст можно исправлять прямо в таблице. Время указано в секундах.")
        editor_buttons = QHBoxLayout()
        add_button = QPushButton("+ Фраза")
        add_button.clicked.connect(self.add_segment)
        delete_button = QPushButton("Удалить")
        delete_button.setObjectName("Danger")
        delete_button.clicked.connect(self.delete_selected)
        regroup_button = QPushButton("Перегруппировать")
        regroup_button.clicked.connect(self.regroup)
        editor_buttons.addWidget(add_button)
        editor_buttons.addWidget(delete_button)
        editor_buttons.addWidget(regroup_button)
        editor_buttons.addStretch(1)
        editor_panel.layout().addLayout(editor_buttons)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Начало", "Конец", "Текст"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.cellChanged.connect(self.table_changed)
        self.table.itemSelectionChanged.connect(self.refresh_preview)
        editor_panel.layout().addWidget(self.table, 1)
        left_layout.addWidget(editor_panel, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 2, 2, 2)
        right_layout.setSpacing(11)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([820, 560])

        preview_panel = panel("Предпросмотр", "Зелёным выделяется слово, которое произносится прямо сейчас.")
        self.preview = CaptionPreview()
        preview_panel.layout().addWidget(self.preview, 1)
        right_layout.addWidget(preview_panel, 1)

        settings_panel = panel("3. Оформление", "Готовые пресеты можно дополнительно настроить.")
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Стиль"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Reels / TikTok", "reels")
        self.preset_combo.addItem("Рофл-монтаж", "meme")
        self.preset_combo.addItem("Классические", "classic")
        self.preset_combo.addItem("Караоке", "karaoke")
        self.preset_combo.currentIndexChanged.connect(self.apply_preset)
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(QLabel("Кадр"))
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItem("Вертикальный 1080×1920", "1080x1920")
        self.resolution_combo.addItem("Full HD 1920×1080", "1920x1080")
        self.resolution_combo.addItem("HD 1280×720", "1280x720")
        self.resolution_combo.currentIndexChanged.connect(self.settings_changed)
        preset_row.addWidget(self.resolution_combo)
        settings_panel.layout().addLayout(preset_row)

        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Шрифт"))
        self.font_combo = QComboBox()
        for family in ("Segoe UI", "Arial", "Impact", "DejaVu Sans"):
            self.font_combo.addItem(family, family)
        self.font_combo.currentIndexChanged.connect(self.settings_changed)
        font_row.addWidget(self.font_combo, 1)
        font_row.addWidget(QLabel("Размер"))
        self.font_size = QSpinBox()
        self.font_size.setRange(24, 220)
        self.font_size.valueChanged.connect(self.settings_changed)
        font_row.addWidget(self.font_size)
        font_row.addWidget(QLabel("Обводка"))
        self.stroke = QSpinBox()
        self.stroke.setRange(0, 20)
        self.stroke.valueChanged.connect(self.settings_changed)
        font_row.addWidget(self.stroke)
        settings_panel.layout().addLayout(font_row)

        position_row = QHBoxLayout()
        position_row.addWidget(QLabel("Положение"))
        self.position = QSlider(Qt.Horizontal)
        self.position.setRange(10, 92)
        self.position.valueChanged.connect(self.settings_changed)
        position_row.addWidget(self.position, 1)
        position_row.addWidget(QLabel("Слов в фразе"))
        self.words_count = QSpinBox()
        self.words_count.setRange(1, 12)
        self.words_count.valueChanged.connect(self.settings_changed)
        position_row.addWidget(self.words_count)
        settings_panel.layout().addLayout(position_row)
        self.uppercase = QCheckBox("ВЕРХНИЙ РЕГИСТР")
        self.uppercase.toggled.connect(self.settings_changed)
        settings_panel.layout().addWidget(self.uppercase)
        right_layout.addWidget(settings_panel)

        export_panel = panel("4. Экспорт", "MOV сохраняется с прозрачным фоном и подходит для наложения в CapCut.")
        mode_row = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Прозрачный MOV для монтажа", "overlay")
        self.mode_combo.addItem("Готовый MP4 поверх видео", "burned")
        self.mode_combo.addItem("Обычный файл SRT", "srt")
        self.mode_combo.addItem("Анимированный файл ASS", "ass")
        self.mode_combo.currentIndexChanged.connect(self.mode_changed)
        mode_row.addWidget(self.mode_combo, 1)
        export_panel.layout().addLayout(mode_row)
        output_row = QHBoxLayout()
        self.output_edit = QLineEdit(str(APP_ROOT / "output" / "reactive_captions.mov"))
        self.output_edit.textChanged.connect(self._changed)
        choose_output = QPushButton("Выбрать")
        choose_output.clicked.connect(self.choose_output)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(choose_output)
        export_panel.layout().addLayout(output_row)
        right_layout.addWidget(export_panel)

        bottom = QFrame()
        bottom.setObjectName("BottomBar")
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(14, 10, 14, 10)
        status_row = QHBoxLayout()
        self.status_label = QLabel("Готово к работе")
        self.status_label.setObjectName("Muted")
        self.counter_label = QLabel("0 фраз")
        self.counter_label.setObjectName("Muted")
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        status_row.addWidget(self.counter_label)
        bottom_layout.addLayout(status_row)
        controls = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.open_button = QPushButton("Открыть результат")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_result)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setObjectName("Danger")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.export_button = QPushButton("Экспортировать субтитры")
        self.export_button.setObjectName("Primary")
        self.export_button.clicked.connect(self.start_export)
        controls.addWidget(self.progress, 1)
        controls.addWidget(self.open_button)
        controls.addWidget(self.cancel_button)
        controls.addWidget(self.export_button)
        bottom_layout.addLayout(controls)
        root.addWidget(bottom)
        self.load_project(Project())

    def choose_source(self) -> None:
        start = self.source_edit.text() or str(APP_ROOT)
        path, _ = QFileDialog.getOpenFileName(self, "Видео или аудио", start, MEDIA_FILTER)
        if path:
            self.source_edit.setText(path)

    def use_master(self, path: str) -> None:
        if path:
            self.source_edit.setText(path)
            self.status_label.setText("Общая дорожка выбрана")
        else:
            QMessageBox.information(self, "Нет общей дорожки", "Сначала выбери master во вкладке «Аватарки».")

    def current_settings(self) -> CaptionSettings:
        width, height = (int(value) for value in str(self.resolution_combo.currentData()).split("x"))
        return CaptionSettings(
            preset=str(self.preset_combo.currentData()),
            model_size=str(self.model_combo.currentData()),
            language=str(self.language_combo.currentData()),
            font_family=str(self.font_combo.currentData()),
            font_size=self.font_size.value(),
            stroke_width=self.stroke.value(),
            y_position=self.position.value() / 100,
            words_per_caption=self.words_count.value(),
            uppercase=self.uppercase.isChecked(),
            width=width,
            height=height,
            fps=30,
            export_mode=str(self.mode_combo.currentData()),
        ).normalized()

    def load_project(self, project: Project) -> None:
        self._building = True
        settings = project.caption_settings.normalized()
        self.source_edit.setText(project.caption_source)
        self.output_edit.setText(project.caption_output or str(APP_ROOT / "output" / "reactive_captions.mov"))
        for combo, value in (
            (self.model_combo, settings.model_size),
            (self.language_combo, settings.language),
            (self.preset_combo, settings.preset),
            (self.resolution_combo, f"{settings.width}x{settings.height}"),
            (self.font_combo, settings.font_family),
            (self.mode_combo, settings.export_mode),
        ):
            index = combo.findData(value)
            combo.setCurrentIndex(max(0, index))
        self.font_size.setValue(settings.font_size)
        self.stroke.setValue(settings.stroke_width)
        self.position.setValue(round(settings.y_position * 100))
        self.words_count.setValue(settings.words_per_caption)
        self.uppercase.setChecked(settings.uppercase)
        self.segments = list(project.caption_segments)
        self.fill_table()
        self._building = False
        self.refresh_preview()

    def collect_into_project(self, project: Project) -> None:
        self.sync_from_table()
        project.caption_source = self.source_edit.text().strip().strip('"')
        project.caption_output = self.output_edit.text().strip().strip('"')
        project.caption_settings = self.current_settings()
        project.caption_segments = list(self.segments)

    def fill_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.segments))
        for row, segment in enumerate(self.segments):
            self.table.setItem(row, 0, QTableWidgetItem(f"{segment.start:.3f}"))
            self.table.setItem(row, 1, QTableWidgetItem(f"{segment.end:.3f}"))
            self.table.setItem(row, 2, QTableWidgetItem(segment.text))
        self.table.blockSignals(False)
        self.counter_label.setText(f"{len(self.segments)} фраз")
        if self.segments and self.table.currentRow() < 0:
            self.table.selectRow(0)

    def sync_from_table(self) -> None:
        result: list[CaptionSegment] = []
        for row in range(self.table.rowCount()):
            try:
                start = max(0.0, float(self.table.item(row, 0).text().replace(",", ".")))
                end = max(start + 0.04, float(self.table.item(row, 1).text().replace(",", ".")))
                text = self.table.item(row, 2).text().strip()
            except (AttributeError, ValueError):
                continue
            if text:
                old = self.segments[row] if row < len(self.segments) else CaptionSegment()
                result.append(CaptionSegment(start, end, text, old.words))
        self.segments = result

    def table_changed(self, _row: int, _column: int) -> None:
        if self._building:
            return
        self.sync_from_table()
        self.refresh_preview()
        self._changed()

    def add_segment(self) -> None:
        self.sync_from_table()
        start = self.segments[-1].end if self.segments else 0.0
        self.segments.append(CaptionSegment(start, start + 2.0, "Новая фраза"))
        self.fill_table()
        self.table.selectRow(len(self.segments) - 1)
        self._changed()

    def delete_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        self.sync_from_table()
        if row < len(self.segments):
            self.segments.pop(row)
        self.fill_table()
        if self.segments:
            self.table.selectRow(min(row, len(self.segments) - 1))
        self.refresh_preview()
        self._changed()

    def regroup(self) -> None:
        self.sync_from_table()
        if not self.segments:
            return
        self.segments = captions.regroup_segments(self.segments, self.words_count.value())
        self.fill_table()
        self.refresh_preview()
        self._changed()

    def import_srt(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Импорт SRT", str(APP_ROOT), "SubRip (*.srt)")
        if not path:
            return
        try:
            self.segments = captions.import_srt(Path(path))
            self.fill_table()
            self.refresh_preview()
            self.status_label.setText("SRT импортирован")
            self._changed()
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось импортировать SRT", str(exc))

    def apply_preset(self) -> None:
        if self._building:
            return
        presets = {
            "reels": ("Segoe UI", 100, 6, 76, 4, False),
            "meme": ("Impact", 116, 7, 68, 3, True),
            "classic": ("Arial", 58, 4, 86, 8, False),
            "karaoke": ("Segoe UI", 86, 5, 82, 6, False),
        }
        family, size, stroke, position, words, uppercase = presets[str(self.preset_combo.currentData())]
        self._building = True
        self.font_combo.setCurrentIndex(max(0, self.font_combo.findData(family)))
        self.font_size.setValue(size)
        self.stroke.setValue(stroke)
        self.position.setValue(position)
        self.words_count.setValue(words)
        self.uppercase.setChecked(uppercase)
        self._building = False
        if self.segments:
            self.regroup()
        self.settings_changed()

    def settings_changed(self) -> None:
        if self._building:
            return
        self.refresh_preview()
        self._changed()

    def refresh_preview(self) -> None:
        row = self.table.currentRow()
        segment = self.segments[row] if 0 <= row < len(self.segments) else (self.segments[0] if self.segments else None)
        self.preview.set_data(self.current_settings(), segment)

    def start_transcription(self) -> None:
        if self.thread:
            return
        source = Path(self.source_edit.text().strip().strip('"'))
        if not source.is_file():
            QMessageBox.warning(self, "Нет файла", "Выбери существующее видео или аудиодорожку.")
            return
        self._start_worker(TranscribeWorker(source, self.current_settings(), self._new_cancel_event()), "transcribe")

    def start_export(self) -> None:
        if self.thread:
            return
        self.sync_from_table()
        if not self.segments:
            QMessageBox.warning(self, "Нет субтитров", "Сначала распознай речь или импортируй SRT.")
            return
        source = Path(self.source_edit.text().strip().strip('"'))
        settings = self.current_settings()
        if settings.export_mode in {"overlay", "burned"} and not source.is_file():
            QMessageBox.warning(self, "Нет исходника", "Для видеоэкспорта выбери существующее видео или аудио.")
            return
        destination = Path(self.output_edit.text().strip().strip('"'))
        if not str(destination):
            QMessageBox.warning(self, "Нет результата", "Выбери место сохранения.")
            return
        worker = CaptionExportWorker(source, list(self.segments), settings, destination, self._new_cancel_event())
        self._start_worker(worker, "export")

    def _new_cancel_event(self) -> threading.Event:
        self.cancel_event = threading.Event()
        return self.cancel_event

    def _start_worker(self, worker: QObject, kind: str) -> None:
        self.thread = QThread(self)
        self.worker = worker
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.progress.connect(self.progress.setValue)
        worker.status.connect(self.status_label.setText)
        worker.failed.connect(self.failed)
        worker.cancelled.connect(self.cancelled)
        worker.failed.connect(self.thread.quit)
        worker.cancelled.connect(self.thread.quit)
        if kind == "transcribe":
            worker.finished.connect(self.transcription_finished)
        else:
            worker.finished.connect(self.export_finished)
        worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.cleanup_worker)
        self.progress.setValue(0)
        self.cancel_button.setVisible(True)
        self.transcribe_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.thread.start()

    def transcription_finished(self, result: list[CaptionSegment]) -> None:
        self.segments = result
        self.fill_table()
        self.refresh_preview()
        self.status_label.setText("Расшифровка готова — проверь текст")
        self._changed()

    def export_finished(self, path: str) -> None:
        self.last_result = path
        self.status_label.setText("Экспорт готов")
        self.open_button.setEnabled(True)
        QMessageBox.information(self, "Готово", f"Результат сохранён:\n{path}")

    def failed(self, message: str) -> None:
        self.status_label.setText("Ошибка")
        QMessageBox.critical(self, "Ошибка", message)

    def cancelled(self) -> None:
        self.status_label.setText("Операция отменена")

    def cleanup_worker(self) -> None:
        if self.worker:
            self.worker.deleteLater()
        if self.thread:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None
        self.cancel_event = None
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(True)
        self.transcribe_button.setEnabled(True)
        self.export_button.setEnabled(True)

    def cancel(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Останавливаю…")

    def choose_output(self) -> None:
        mode = str(self.mode_combo.currentData())
        suffix = {"overlay": ".mov", "burned": ".mp4", "srt": ".srt", "ass": ".ass"}[mode]
        filters = {"overlay": "Прозрачное видео (*.mov)", "burned": "Видео (*.mp4)", "srt": "SubRip (*.srt)", "ass": "Advanced SubStation Alpha (*.ass)"}
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить результат", self.output_edit.text(), filters[mode])
        if path:
            self.output_edit.setText(str(Path(path).with_suffix(suffix)))

    def mode_changed(self) -> None:
        if self._building:
            return
        mode = str(self.mode_combo.currentData())
        suffix = {"overlay": ".mov", "burned": ".mp4", "srt": ".srt", "ass": ".ass"}[mode]
        current = Path(self.output_edit.text() or (APP_ROOT / "output" / "reactive_captions"))
        self.output_edit.setText(str(current.with_suffix(suffix)))
        self._changed()

    def open_result(self) -> None:
        if self.last_result and Path(self.last_result).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_result))

    def _changed(self, *_args) -> None:
        if not self._building:
            self.changed.emit()

    def stop_and_wait(self) -> None:
        if not self.thread:
            return
        self.cancel()
        self.thread.quit()
        self.thread.wait(3000)
