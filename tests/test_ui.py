import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui import MainWindow  # noqa: E402


def test_main_window_supports_one_to_eight_participants() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    window.count_spin.setValue(8)
    app.processEvents()
    assert window.people_list.count() == 8

    window.count_spin.setValue(1)
    app.processEvents()
    assert window.people_list.count() == 1

    window.close()


def test_main_window_has_caption_workspace() -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()

    assert window.tabs.count() == 2
    assert window.tabs.tabText(1) == "Субтитры"
    assert window.captions_widget.mode_combo.count() == 4

    window.close()
