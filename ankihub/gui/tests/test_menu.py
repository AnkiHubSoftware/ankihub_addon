from unittest.mock import Mock

from PyQt6 import QtCore

from ankihub.gui.menu import SubscribeToDeck


def test_subscribe_to_deck(anki_session_with_addon, monkeypatch):
    session = anki_session_with_addon
    monkeypatch.setattr("ankihub.gui.menu.showText", Mock())
    window = SubscribeToDeck()
    window.show()
    session.qtbot.addWidget(window)
    session.qtbot.mouseClick(window.subscribe_button, QtCore.Qt.LeftButton)
