from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QObject, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QDragEnterEvent, QDropEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import engine
from models import APP_NAME, APP_ROOT, APP_VERSION, CropSettings, Participant, Project

IMAGE_FILTER = "Изображения (*.png *.jpg *.jpeg *.webp *.bmp);;Все файлы (*.*)"
AUDIO_FILTER = "Аудио (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus);;Все файлы (*.*)"


STYLE = """
QWidget {
    background: #0D0F14;
    color: #F5F7FB;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QMainWindow { background: #0D0F14; }
QLabel, QCheckBox, QSlider { background: transparent; }
QFrame#TopBar, QFrame#BottomBar { background: #11141B; border: 0; }
QFrame#Panel, QFrame#ParticipantCard {
    background: #171A22;
    border: 1px solid #282D37;
    border-radius: 12px;
}
QFrame#ParticipantCard:hover { border-color: #3B424F; }
QLabel#Title { font-size: 19pt; font-weight: 700; }
QLabel#Section { font-size: 12pt; font-weight: 650; }
QLabel#Muted { color: #9198A7; }
QLabel#Green { color: #25F47B; font-weight: 600; }
QLabel#Badge {
    color: #25F47B;
    background: #123522;
    border: 1px solid #1A5A34;
    border-radius: 9px;
    padding: 2px 8px;
}
QPushButton {
    background: #212630;
    border: 1px solid #343B48;
    border-radius: 8px;
    padding: 7px 12px;
    font-weight: 600;
}
QPushButton:hover { background: #29303B; border-color: #4A5363; }
QPushButton:pressed { background: #191D25; }
QPushButton:disabled { color: #606674; background: #171A20; border-color: #262A32; }
QPushButton#Primary { background: #25F47B; color: #07120B; border-color: #25F47B; padding: 9px 18px; }
QPushButton#Primary:hover { background: #54FA96; }
QPushButton#Danger { color: #FF8D99; }
QLineEdit, QComboBox, QSpinBox {
    background: #10131A;
    border: 1px solid #303641;
    border-radius: 8px;
    padding: 7px 9px;
    min-height: 18px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #25F47B; }
QListWidget { background: transparent; border: 0; outline: 0; }
QListWidget::item { background: transparent; border: 0; padding: 0; margin: 0 0 8px 0; }
QListWidget::item:selected { background: transparent; }
QScrollArea { background: transparent; border: 0; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #343A46; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSlider::groove:horizontal { background: #303641; height: 5px; border-radius: 2px; }
QSlider::handle:horizontal { background: #25F47B; width: 16px; margin: -6px 0; border-radius: 8px; }
QProgressBar { background: #20242D; border: 0; border-radius: 5px; height: 10px; text-align: center; color: transparent; }
QProgressBar::chunk { background: #25F47B; border-radius: 5px; }
QCheckBox::indicator { width: 17px; height: 17px; }
QCheckBox::indicator:unchecked { background: #10131A; border: 1px solid #3A414E; border-radius: 4px; }
QCheckBox::indicator:checked { background: #25F47B; border: 1px solid #25F47B; border-radius: 4px; }
QSplitter::handle { background: #0D0F14; width: 8px; }
QMenu { background: #171A22; border: 1px solid #303641; padding: 5px; }
QMenu::item { padding: 7px 24px 7px 10px; border-radius: 5px; }
QMenu::item:selected { background: #252B35; }
"""


class FileField(QWidget):
    path_changed = Signal(str)

    def __init__(self, kind: str, placeholder: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.kind = kind
        self.setAcceptDrops(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.editingFinished.connect(lambda: self.path_changed.emit(self.path()))
        self.button = QPushButton("Выбрать")
        self.button.clicked.connect(self.choose)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def choose(self) -> None:
        file_filter = IMAGE_FILTER if self.kind == "image" else AUDIO_FILTER
        path, _ = QFileDialog.getOpenFileName(self, "Выбор файла", self.start_folder(), file_filter)
        if path:
            self.set_path(path)

    def start_folder(self) -> str:
        value = Path(self.path()) if self.path() else APP_ROOT
        return str(value.parent if value.suffix else value)

    def set_path(self, path: str) -> None:
        self.edit.setText(str(path))
        self.path_changed.emit(self.path())

    def path(self) -> str:
        return self.edit.text().strip().strip('"')

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        extensions = engine.IMAGE_EXTENSIONS if self.kind == "image" else engine.AUDIO_EXTENSIONS
        selected = next((path for path in paths if path.is_file() and path.suffix.lower() in extensions), None)
        if selected:
            self.set_path(str(selected))
            event.acceptProposedAction()


class CropDialog(QDialog):
    def __init__(self, avatar_path: str, crop: CropSettings, auto_style: bool, parent=None):
        super().__init__(parent)
        self.avatar_path = avatar_path
        self.auto_style = auto_style
        self.result_crop = CropSettings(crop.zoom, crop.offset_x, crop.offset_y)
        self.setWindowTitle("Кадрирование аватарки")
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        title = QLabel("Кадрирование")
        title.setObjectName("Section")
        layout.addWidget(title)
        hint = QLabel("Приближай и двигай изображение. Круг и обводка будут добавлены автоматически.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.preview = QLabel()
        self.preview.setFixedSize(320, 320)
        self.preview.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.preview, 0, Qt.AlignHCenter)

        self.zoom = self._slider(layout, "Масштаб", 100, 300, round(crop.zoom * 100))
        self.offset_x = self._slider(layout, "Сдвиг по горизонтали", -100, 100, round(crop.offset_x * 100))
        self.offset_y = self._slider(layout, "Сдвиг по вертикали", -100, 100, round(crop.offset_y * 100))
        for slider in (self.zoom, self.offset_x, self.offset_y):
            slider.valueChanged.connect(self.refresh)

        buttons = QHBoxLayout()
        reset = QPushButton("Сбросить")
        reset.clicked.connect(self.reset_values)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Применить")
        save.setObjectName("Primary")
        save.clicked.connect(self.accept_values)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self.refresh()

    def _slider(self, parent: QVBoxLayout, label: str, minimum: int, maximum: int, value: int) -> QSlider:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        slider = QSlider(Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        row.addWidget(slider, 1)
        parent.addLayout(row)
        return slider

    def current_crop(self) -> CropSettings:
        return CropSettings(self.zoom.value() / 100, self.offset_x.value() / 100, self.offset_y.value() / 100)

    def refresh(self) -> None:
        try:
            image = engine.prepare_avatar(Path(self.avatar_path), 300, self.auto_style, self.current_crop())
            self.preview.setPixmap(QPixmap.fromImage(ImageQt(image)))
        except Exception as exc:
            self.preview.setText(str(exc))

    def reset_values(self) -> None:
        self.zoom.setValue(100)
        self.offset_x.setValue(0)
        self.offset_y.setValue(0)

    def accept_values(self) -> None:
        self.result_crop = self.current_crop()
        self.accept()


class ParticipantCard(QFrame):
    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, participant: Participant, number: int, auto_style_getter, parent=None):
        super().__init__(parent)
        self.participant = participant
        self.auto_style_getter = auto_style_getter
        self.setObjectName("ParticipantCard")
        self.setMinimumHeight(206)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 11, 12, 12)
        outer.setSpacing(8)

        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("Section")
        self.set_number(number)
        drag_hint = QLabel("↕  можно перетаскивать")
        drag_hint.setObjectName("Muted")
        remove = QPushButton("Удалить")
        remove.setObjectName("Danger")
        remove.clicked.connect(lambda: self.remove_requested.emit(self))
        header.addWidget(self.title)
        header.addWidget(drag_hint)
        header.addStretch(1)
        header.addWidget(remove)
        outer.addLayout(header)

        avatar_row = QHBoxLayout()
        self.thumb = QLabel()
        self.thumb.setFixedSize(72, 72)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background:#10131A;border:1px solid #303641;border-radius:36px;")
        avatar_controls = QVBoxLayout()
        avatar_label_row = QHBoxLayout()
        avatar_label_row.addWidget(QLabel("Аватарка"))
        self.crop_button = QPushButton("Кадрировать")
        self.crop_button.clicked.connect(self.edit_crop)
        avatar_label_row.addStretch(1)
        avatar_label_row.addWidget(self.crop_button)
        self.avatar_field = FileField("image", "PNG, JPG или WEBP")
        self.avatar_field.path_changed.connect(self.avatar_changed)
        avatar_controls.addLayout(avatar_label_row)
        avatar_controls.addWidget(self.avatar_field)
        avatar_row.addWidget(self.thumb)
        avatar_row.addLayout(avatar_controls, 1)
        outer.addLayout(avatar_row)

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Голос"))
        self.voice_field = FileField("audio", "Отдельная дорожка этого человека")
        self.voice_field.path_changed.connect(self.voice_changed)
        voice_row.addWidget(self.voice_field, 1)
        outer.addLayout(voice_row)

        sensitivity_row = QHBoxLayout()
        sensitivity_row.addWidget(QLabel("Чувствительность"))
        self.sensitivity = QSlider(Qt.Horizontal)
        self.sensitivity.setRange(50, 200)
        self.sensitivity.setValue(round(participant.sensitivity * 100))
        self.sensitivity.valueChanged.connect(self.sensitivity_changed)
        self.sensitivity_label = QLabel()
        self.sensitivity_label.setObjectName("Green")
        self.sensitivity_label.setFixedWidth(38)
        sensitivity_row.addWidget(self.sensitivity, 1)
        sensitivity_row.addWidget(self.sensitivity_label)
        outer.addLayout(sensitivity_row)

        self.avatar_field.edit.setText(participant.avatar)
        self.voice_field.edit.setText(participant.voice)
        self.sensitivity_changed(self.sensitivity.value())
        self.refresh_thumb()

    def set_number(self, number: int) -> None:
        self.title.setText(f"Участник {number}")

    def avatar_changed(self, path: str) -> None:
        self.participant.avatar = path
        self.refresh_thumb()
        self.changed.emit()

    def voice_changed(self, path: str) -> None:
        self.participant.voice = path
        self.changed.emit()

    def sensitivity_changed(self, value: int) -> None:
        self.participant.sensitivity = value / 100
        self.sensitivity_label.setText(f"{value / 100:.2f}")
        self.changed.emit()

    def edit_crop(self) -> None:
        path = Path(self.participant.avatar)
        if not path.is_file():
            QMessageBox.information(self, "Нет аватарки", "Сначала выбери изображение.")
            return
        dialog = CropDialog(str(path), self.participant.crop, self.auto_style_getter(), self)
        if dialog.exec() == QDialog.Accepted:
            self.participant.crop = dialog.result_crop
            self.refresh_thumb()
            self.changed.emit()

    def refresh_thumb(self) -> None:
        path = Path(self.participant.avatar)
        self.crop_button.setEnabled(path.is_file())
        if not path.is_file():
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText("Нет")
            return
        try:
            image = engine.prepare_avatar(path, 68, self.auto_style_getter(), self.participant.crop)
            self.thumb.setText("")
            self.thumb.setPixmap(QPixmap.fromImage(ImageQt(image)))
        except Exception:
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText("!")


class PreviewCanvas(QWidget):
    positions_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(440, 300)
        self.participants: list[Participant] = []
        self.positions: list[list[float]] = []
        self.auto_style = True
        self.avatar_cap = 470
        self.drag_index = -1
        self.pixmaps: list[QPixmap] = []

    def set_data(
        self,
        participants: list[Participant],
        positions: list[list[float]],
        avatar_cap: int,
        auto_style: bool,
    ) -> None:
        self.participants = participants
        self.positions = positions if len(positions) == len(participants) else engine.default_positions(len(participants))
        self.avatar_cap = avatar_cap
        self.auto_style = auto_style
        self._load_pixmaps()
        self.update()

    def _display_size(self) -> int:
        scale = max(0.01, self.width() / 1920)
        return max(44, engine.layout_avatar_size(len(self.participants), self.width(), self.height(), round(self.avatar_cap * scale)))

    def _load_pixmaps(self) -> None:
        self.pixmaps = []
        size = self._display_size()
        for index, participant in enumerate(self.participants, 1):
            path = Path(participant.avatar)
            try:
                if path.is_file():
                    image = engine.prepare_avatar(path, size, self.auto_style, participant.crop)
                    alpha = image.getchannel("A")
                    image = ImageEnhance.Brightness(image).enhance(0.82)
                    image.putalpha(alpha)
                else:
                    image = self._placeholder(size, index)
            except Exception:
                image = self._placeholder(size, index)
            self.pixmaps.append(QPixmap.fromImage(ImageQt(image)))

    def _placeholder(self, size: int, number: int) -> Image.Image:
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        margin = max(3, size // 35)
        draw.ellipse(
            (margin, margin, size - margin, size - margin),
            fill="#10131A",
            outline="#657081",
            width=max(2, size // 50),
        )
        text = str(number)
        bounds = draw.textbbox((0, 0), text)
        draw.text(
            ((size - (bounds[2] - bounds[0])) / 2, (size - (bounds[3] - bounds[1])) / 2),
            text,
            fill="#DDE2EB",
        )
        return image

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        cell = 18
        colors = (QColor("#20242D"), QColor("#252A34"))
        for y in range(0, self.height(), cell):
            for x in range(0, self.width(), cell):
                painter.fillRect(x, y, cell, cell, colors[((x // cell) + (y // cell)) % 2])
        painter.setPen(QPen(QColor("#3A414D"), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 11, 11)
        for position, pixmap in zip(self.positions, self.pixmaps):
            x = round(position[0] * self.width() - pixmap.width() / 2)
            y = round(position[1] * self.height() - pixmap.height() / 2)
            painter.drawPixmap(x, y, pixmap)

    def resizeEvent(self, event) -> None:
        self._load_pixmaps()
        super().resizeEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        for index in range(len(self.pixmaps) - 1, -1, -1):
            pixmap = self.pixmaps[index]
            center_x = self.positions[index][0] * self.width()
            center_y = self.positions[index][1] * self.height()
            if abs(event.position().x() - center_x) <= pixmap.width() / 2 and abs(event.position().y() - center_y) <= pixmap.height() / 2:
                self.drag_index = index
                self.setCursor(Qt.ClosedHandCursor)
                break

    def mouseMoveEvent(self, event) -> None:
        if self.drag_index < 0:
            return
        self.positions[self.drag_index] = [
            max(0.04, min(0.96, event.position().x() / max(1, self.width()))),
            max(0.04, min(0.96, event.position().y() / max(1, self.height()))),
        ]
        self.update()
        self.positions_changed.emit(self.positions)

    def mouseReleaseEvent(self, _event) -> None:
        self.drag_index = -1
        self.unsetCursor()


class RenderWorker(QObject):
    progress = Signal(int)
    status = Signal(str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, project: Project, cancel_event: threading.Event):
        super().__init__()
        self.project = project
        self.cancel_event = cancel_event

    @Slot()
    def run(self) -> None:
        try:
            result = engine.render_project(
                self.project,
                progress_callback=self.progress.emit,
                status_callback=self.status.emit,
                cancel_event=self.cancel_event,
            )
            self.finished.emit(str(result))
        except engine.RenderCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1440, 900)
        self.setMinimumSize(1080, 720)
        self.setAcceptDrops(True)
        self.settings = QSettings("ReactiveAvatars", "ReactiveAvatars2")
        self.project = Project()
        self.thread: QThread | None = None
        self.worker: RenderWorker | None = None
        self.cancel_event: threading.Event | None = None
        self.render_started = 0.0
        self.last_result = ""
        self._building = False
        self._build_ui()
        self._load_project(self.project)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(18, 14, 18, 16)
        main.setSpacing(12)

        top = QFrame()
        top.setObjectName("TopBar")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(14, 10, 14, 10)
        title_box = QVBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        subtitle = QLabel("Прозрачное видео с автоматической подсветкой говорящих")
        subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        top_layout.addLayout(title_box)
        top_layout.addStretch(1)
        new_button = QPushButton("Новый")
        open_button = QPushButton("Открыть проект")
        self.recent_button = QPushButton("Недавние ▾")
        save_button = QPushButton("Сохранить")
        new_button.clicked.connect(self.new_project)
        open_button.clicked.connect(self.open_project)
        save_button.clicked.connect(self.save_project)
        self.recent_button.clicked.connect(self.show_recent)
        for button in (new_button, open_button, self.recent_button, save_button):
            top_layout.addWidget(button)
        main.addWidget(top)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        main.addWidget(splitter, 1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(520)
        left_host = QWidget()
        left = QVBoxLayout(left_host)
        left.setContentsMargins(2, 2, 6, 2)
        left.setSpacing(11)
        left_scroll.setWidget(left_host)
        splitter.addWidget(left_scroll)

        source_panel = self._panel("1. Общая дорожка", "Финальный звук берётся только отсюда — голоса не дублируются.")
        self.master_field = FileField("audio", "Перетащи сюда master или выбери файл")
        self.master_field.path_changed.connect(self.mark_changed)
        source_panel.layout().addWidget(self.master_field)
        import_button = QPushButton("Импортировать подготовленную папку")
        import_button.clicked.connect(self.import_folder)
        source_panel.layout().addWidget(import_button)
        left.addWidget(source_panel)

        participants_panel = self._panel("2. Участники", "Перетаскивай карточки, чтобы изменить порядок на экране.")
        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("Количество"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 8)
        self.count_spin.valueChanged.connect(self.set_participant_count)
        count_row.addWidget(self.count_spin)
        count_row.addStretch(1)
        add_button = QPushButton("+ Добавить")
        add_button.clicked.connect(lambda: self.count_spin.setValue(min(8, self.count_spin.value() + 1)))
        count_row.addWidget(add_button)
        participants_panel.layout().addLayout(count_row)
        self.people_list = QListWidget()
        self.people_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.people_list.setDefaultDropAction(Qt.MoveAction)
        self.people_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.people_list.setSpacing(0)
        self.people_list.setMinimumHeight(330)
        self.people_list.model().rowsMoved.connect(self.people_reordered)
        participants_panel.layout().addWidget(self.people_list)
        left.addWidget(participants_panel, 1)

        output_panel = self._panel("3. Результат", "MOV ProRes 4444 с настоящим прозрачным фоном.")
        output_row = QHBoxLayout()
        self.output_edit = QLineEdit(str(APP_ROOT / "output" / "reactive_avatars.mov"))
        self.output_edit.textChanged.connect(self.mark_changed)
        output_button = QPushButton("Выбрать")
        output_button.clicked.connect(self.choose_output)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(output_button)
        output_panel.layout().addLayout(output_row)
        left.addWidget(output_panel)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 2, 2, 2)
        right_layout.setSpacing(11)
        splitter.addWidget(right)
        right.setMinimumWidth(500)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([650, 750])

        preview_panel = self._panel("Предпросмотр", "Аватарки можно двигать прямо мышкой.")
        self.preview = PreviewCanvas()
        self.preview.positions_changed.connect(self.positions_changed)
        preview_panel.layout().addWidget(self.preview, 1)
        preview_buttons = QHBoxLayout()
        auto_layout = QPushButton("Автораскладка")
        auto_layout.clicked.connect(self.reset_layout)
        preview_buttons.addWidget(auto_layout)
        preview_buttons.addStretch(1)
        preview_panel.layout().addLayout(preview_buttons)
        right_layout.addWidget(preview_panel, 1)

        settings_panel = self._panel("Настройки", "Свечение всегда зелёное, а фон всегда прозрачный.")
        resolution_row = QHBoxLayout()
        resolution_row.addWidget(QLabel("Разрешение"))
        self.resolution = QComboBox()
        self.resolution.addItem("Full HD — 1920×1080", "1920x1080")
        self.resolution.addItem("HD — 1280×720", "1280x720")
        self.resolution.currentIndexChanged.connect(self.settings_changed)
        resolution_row.addWidget(self.resolution, 1)
        resolution_row.addWidget(QLabel("FPS"))
        self.fps = QComboBox()
        self.fps.addItem("30", 30)
        self.fps.addItem("60", 60)
        self.fps.currentIndexChanged.connect(self.settings_changed)
        resolution_row.addWidget(self.fps)
        settings_panel.layout().addLayout(resolution_row)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Размер аватарок"))
        self.avatar_size = QSlider(Qt.Horizontal)
        self.avatar_size.setRange(240, 700)
        self.avatar_size.valueChanged.connect(self.settings_changed)
        self.avatar_size_label = QLabel()
        self.avatar_size_label.setObjectName("Green")
        size_row.addWidget(self.avatar_size, 1)
        size_row.addWidget(self.avatar_size_label)
        settings_panel.layout().addLayout(size_row)
        self.auto_style = QCheckBox("Обрезать обычные фото в круг и добавлять обводку")
        self.auto_style.toggled.connect(self.settings_changed)
        settings_panel.layout().addWidget(self.auto_style)
        right_layout.addWidget(settings_panel)

        bottom = QFrame()
        bottom.setObjectName("BottomBar")
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(14, 10, 14, 10)
        progress_row = QHBoxLayout()
        self.status_label = QLabel("Готово к работе")
        self.status_label.setObjectName("Muted")
        self.eta_label = QLabel("")
        self.eta_label.setObjectName("Muted")
        progress_row.addWidget(self.status_label)
        progress_row.addStretch(1)
        progress_row.addWidget(self.eta_label)
        bottom_layout.addLayout(progress_row)
        action_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.open_result_button = QPushButton("Открыть результат")
        self.open_result_button.setEnabled(False)
        self.open_result_button.clicked.connect(self.open_result)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setObjectName("Danger")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_render)
        self.render_button = QPushButton("Создать прозрачное видео")
        self.render_button.setObjectName("Primary")
        self.render_button.clicked.connect(self.start_render)
        action_row.addWidget(self.progress, 1)
        action_row.addWidget(self.open_result_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.render_button)
        bottom_layout.addLayout(action_row)
        main.addWidget(bottom)

    def _panel(self, title: str, hint: str) -> QFrame:
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 13, 14, 14)
        layout.setSpacing(9)
        title_label = QLabel(title)
        title_label.setObjectName("Section")
        hint_label = QLabel(hint)
        hint_label.setObjectName("Muted")
        hint_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(hint_label)
        return panel

    def _cards(self) -> list[ParticipantCard]:
        cards: list[ParticipantCard] = []
        for row in range(self.people_list.count()):
            widget = self.people_list.itemWidget(self.people_list.item(row))
            if isinstance(widget, ParticipantCard):
                cards.append(widget)
        return cards

    def _add_card(self, participant: Participant) -> None:
        item = QListWidgetItem()
        item.setFlags(item.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        card = ParticipantCard(participant, self.people_list.count() + 1, lambda: self.auto_style.isChecked())
        card.changed.connect(self.participant_changed)
        card.remove_requested.connect(self.remove_card)
        item.setSizeHint(QSize(100, 214))
        self.people_list.addItem(item)
        self.people_list.setItemWidget(item, card)

    def set_participant_count(self, count: int) -> None:
        if self._building:
            return
        while self.people_list.count() < count:
            self._add_card(Participant())
        while self.people_list.count() > count:
            self.people_list.takeItem(self.people_list.count() - 1)
        self.renumber_cards()
        self.project.positions = engine.default_positions(count)
        self.participant_changed()

    def remove_card(self, card: ParticipantCard) -> None:
        if self.people_list.count() <= 1:
            return
        for row in range(self.people_list.count()):
            if self.people_list.itemWidget(self.people_list.item(row)) is card:
                self.people_list.takeItem(row)
                break
        self.count_spin.blockSignals(True)
        self.count_spin.setValue(self.people_list.count())
        self.count_spin.blockSignals(False)
        self.project.positions = engine.default_positions(self.people_list.count())
        self.renumber_cards()
        self.participant_changed()

    def people_reordered(self, *_args) -> None:
        QTimer.singleShot(0, self._after_reorder)

    def _after_reorder(self) -> None:
        self.renumber_cards()
        self.project.positions = engine.default_positions(self.people_list.count())
        self.participant_changed()

    def renumber_cards(self) -> None:
        for number, card in enumerate(self._cards(), 1):
            card.set_number(number)

    def participant_changed(self) -> None:
        if self._building:
            return
        self.project.participants = [card.participant for card in self._cards()]
        if len(self.project.positions) != len(self.project.participants):
            self.project.positions = engine.default_positions(len(self.project.participants))
        self.refresh_preview()
        self.mark_changed()

    def settings_changed(self) -> None:
        if self._building:
            return
        self.avatar_size_label.setText(str(self.avatar_size.value()))
        for card in self._cards():
            card.refresh_thumb()
        self.refresh_preview()
        self.mark_changed()

    def positions_changed(self, positions: list) -> None:
        self.project.positions = [[float(x), float(y)] for x, y in positions]
        self.mark_changed()

    def reset_layout(self) -> None:
        self.project.positions = engine.default_positions(self.people_list.count())
        self.refresh_preview()
        self.mark_changed()

    def refresh_preview(self) -> None:
        participants = [card.participant for card in self._cards()]
        self.preview.set_data(participants, self.project.positions, self.avatar_size.value(), self.auto_style.isChecked())

    def collect_project(self) -> Project:
        width, height = (int(value) for value in str(self.resolution.currentData()).split("x"))
        self.project.master_audio = self.master_field.path()
        self.project.output_path = self.output_edit.text().strip().strip('"')
        self.project.participants = [card.participant for card in self._cards()]
        self.project.width = int(width)
        self.project.height = int(height)
        self.project.fps = int(self.fps.currentData())
        self.project.avatar_size = self.avatar_size.value()
        self.project.auto_avatar_style = self.auto_style.isChecked()
        if len(self.project.positions) != len(self.project.participants):
            self.project.positions = engine.default_positions(len(self.project.participants))
        return self.project.normalized()

    def _load_project(self, project: Project) -> None:
        self._building = True
        self.project = project.normalized()
        self.master_field.edit.setText(project.master_audio)
        self.output_edit.setText(project.output_path or str(APP_ROOT / "output" / "reactive_avatars.mov"))
        self.people_list.clear()
        self.auto_style.setChecked(project.auto_avatar_style)
        self.avatar_size.setValue(project.avatar_size)
        self.avatar_size_label.setText(str(project.avatar_size))
        resolution_index = self.resolution.findData(f"{project.width}x{project.height}")
        self.resolution.setCurrentIndex(max(0, resolution_index))
        fps_index = self.fps.findData(project.fps)
        self.fps.setCurrentIndex(max(0, fps_index))
        for participant in project.participants:
            self._add_card(participant)
        self.count_spin.setValue(len(project.participants))
        self._building = False
        self.refresh_preview()
        self.update_title(False)

    def mark_changed(self, *_args) -> None:
        if not self._building:
            self.update_title(True)

    def update_title(self, changed: bool) -> None:
        name = Path(self.project.project_path).stem if self.project.project_path else "Новый проект"
        marker = " •" if changed else ""
        self.setWindowTitle(f"{name}{marker} — {APP_NAME} {APP_VERSION}")

    def new_project(self) -> None:
        self._load_project(Project())

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Открыть проект", str(APP_ROOT), "Проекты Reactive Avatars (*.ravproj)")
        if path:
            self.open_project_path(Path(path))

    def open_project_path(self, path: Path) -> None:
        try:
            self._load_project(Project.load(path))
            self.add_recent(str(path))
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось открыть проект", str(exc))

    def save_project(self) -> None:
        project = self.collect_project()
        path = project.project_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Сохранить проект", str(APP_ROOT / "project.ravproj"), "Проекты Reactive Avatars (*.ravproj)")
        if not path:
            return
        try:
            project.save(Path(path))
            self.add_recent(project.project_path)
            self.update_title(False)
            self.status_label.setText("Проект сохранён")
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось сохранить проект", str(exc))

    def recent_paths(self) -> list[str]:
        values = self.settings.value("recentProjects", [])
        if isinstance(values, str):
            values = [values]
        return [str(path) for path in values if Path(str(path)).is_file()][:8]

    def add_recent(self, path: str) -> None:
        values = [item for item in self.recent_paths() if Path(item) != Path(path)]
        self.settings.setValue("recentProjects", [path, *values][:8])

    def show_recent(self) -> None:
        menu = QMenu(self)
        paths = self.recent_paths()
        if not paths:
            action = menu.addAction("Список пока пуст")
            action.setEnabled(False)
        for path in paths:
            action = menu.addAction(Path(path).name)
            action.setToolTip(path)
            action.triggered.connect(lambda _checked=False, value=path: self.open_project_path(Path(value)))
        menu.exec(self.recent_button.mapToGlobal(self.recent_button.rect().bottomLeft()))

    def choose_output(self) -> None:
        initial = self.output_edit.text() or str(APP_ROOT / "output" / "reactive_avatars.mov")
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить прозрачное видео", initial, "Прозрачное видео (*.mov)")
        if path:
            self.output_edit.setText(str(Path(path).with_suffix(".mov")))

    def import_folder(self, folder: str | None = None) -> None:
        if not folder:
            folder = QFileDialog.getExistingDirectory(self, "Папка с master, avatar1, voice1…", str(APP_ROOT))
        if not folder:
            return
        try:
            master, participants = engine.discover_folder(Path(folder))
            if not master and not participants:
                QMessageBox.information(
                    self,
                    "Файлы не найдены",
                    "Ожидаются master.wav, avatar1.png, voice1.wav и так далее.",
                )
                return
            project = self.collect_project()
            if master:
                project.master_audio = str(master)
            if participants:
                project.participants = participants
                project.positions = engine.default_positions(len(participants))
            self._load_project(project)
            self.mark_changed()
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка импорта", str(exc))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        folder = next((path for path in paths if path.is_dir()), None)
        project = next((path for path in paths if path.suffix.lower() == ".ravproj"), None)
        if project:
            self.open_project_path(project)
        elif folder:
            self.import_folder(str(folder))
        event.acceptProposedAction()

    def start_render(self) -> None:
        if self.thread:
            return
        project = self.collect_project()
        errors = engine.validate_project(project)
        if errors:
            QMessageBox.warning(self, "Проверь файлы", "\n".join(f"• {error}" for error in errors))
            return
        self.render_button.setVisible(False)
        self.cancel_button.setVisible(True)
        self.open_result_button.setEnabled(False)
        self.progress.setValue(0)
        self.eta_label.setText("")
        self.render_started = time.monotonic()
        self.cancel_event = threading.Event()
        self.thread = QThread(self)
        self.worker = RenderWorker(project, self.cancel_event)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.render_progress)
        self.worker.status.connect(self.status_label.setText)
        self.worker.finished.connect(self.render_finished)
        self.worker.failed.connect(self.render_failed)
        self.worker.cancelled.connect(self.render_cancelled)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.worker.cancelled.connect(self.thread.quit)
        self.thread.finished.connect(self.render_cleanup)
        self.thread.start()

    def cancel_render(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Останавливаю рендер…")

    def render_progress(self, value: int) -> None:
        self.progress.setValue(value)
        if value >= 32:
            elapsed = time.monotonic() - self.render_started
            render_fraction = (value - 30) / 70
            if render_fraction > 0.02:
                remaining = max(0, elapsed / render_fraction - elapsed)
                minutes, seconds = divmod(round(remaining), 60)
                self.eta_label.setText(f"Осталось примерно {minutes}:{seconds:02d}")

    def render_finished(self, path: str) -> None:
        self.last_result = path
        self.status_label.setText("Видео готово")
        self.eta_label.setText("")
        self.open_result_button.setEnabled(True)
        QMessageBox.information(self, "Готово", f"Видео сохранено:\n{path}")

    def render_failed(self, message: str) -> None:
        self.status_label.setText("Ошибка рендера")
        self.eta_label.setText("")
        QMessageBox.critical(self, "Ошибка рендера", message)

    def render_cancelled(self) -> None:
        self.status_label.setText("Рендер отменён")
        self.eta_label.setText("")

    def render_cleanup(self) -> None:
        if self.worker:
            self.worker.deleteLater()
        if self.thread:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None
        self.cancel_event = None
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(True)
        self.render_button.setVisible(True)

    def open_result(self) -> None:
        if self.last_result and Path(self.last_result).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_result))

    def closeEvent(self, event) -> None:
        if self.thread:
            answer = QMessageBox.question(self, "Идёт рендер", "Остановить рендер и закрыть приложение?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.cancel_render()
            self.thread.quit()
            self.thread.wait(3000)
        event.accept()


def run() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()
