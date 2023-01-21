from concurrent.futures import Future
from typing import Sequence

from anki.notes import NoteId
from aqt import mw
from aqt.qt import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QVBoxLayout,
    qconnect,
)
from aqt.utils import showInfo, tooltip

from ..optional_tag_suggestions import OptionalTagsSuggestionHelper


class OptionalTagsSuggestionDialog(QDialog):
    def __init__(self, parent, nids: Sequence[NoteId]):
        super().__init__(parent)
        self.parent_ = parent
        self.nids = nids

        self._optional_tags_helper = OptionalTagsSuggestionHelper(list(self.nids))
        self._setup_ui()
        self._setup_tag_group_list()
        self._validate_tag_groups_and_update_ui()

    def exec(self):
        if self._optional_tags_helper.tag_group_names() == []:
            showInfo("No optional tags found for these notes.", parent=self.parent_)
            return

        super().exec()

    def _setup_ui(self):
        self.setWindowTitle("Optional Tag Suggestions")

        self.layout_ = QVBoxLayout()
        self.hlayout = QHBoxLayout()
        self.btn_bar = QVBoxLayout()

        self.tag_group_list = QListWidget()

        self.submit_btn = QPushButton("Submit valid suggestions")
        self.submit_btn.setDisabled(True)
        self.btn_bar.addWidget(self.submit_btn)
        qconnect(self.submit_btn.clicked, self._on_submit)

        self.cancel_btn = QPushButton("Cancel")
        self.btn_bar.addWidget(self.cancel_btn)
        qconnect(self.cancel_btn.clicked, self._on_cancel)

        self.btn_bar.addStretch(1)

        self.auto_accept_cb = QCheckBox("Submit without review (maintainers only)")
        self.auto_accept_cb.setToolTip(
            "If checked, the suggestions will be automatically accepted. "
            "This won't work if you are not a deck maintainer."
        )

        self.setLayout(self.layout_)
        self.layout_.addLayout(self.hlayout)
        self.hlayout.addWidget(self.tag_group_list)
        self.hlayout.addLayout(self.btn_bar)
        self.layout_.addWidget(self.auto_accept_cb)

    def _on_submit(self):
        mw.taskman.with_progress(
            task=lambda: self._optional_tags_helper.suggest_valid_tags(
                auto_accept=self.auto_accept_cb.isChecked()
            ),
            on_done=self._on_submit_finished,
            label="Submitting suggestions...",
        )

    def _on_submit_finished(self, future: Future):
        future.result()

        tooltip("Optional tags suggestions submitted.", parent=self.parent_)
        self.accept()

    def _on_cancel(self):
        self.reject()

    def _setup_tag_group_list(self):
        self.tag_group_list.clear()
        for tag_group in sorted(self._optional_tags_helper.tag_group_names()):
            item = QListWidgetItem(tag_group)
            self.tag_group_list.addItem(item)

        # add loading icons and tooltips to all items
        for i in range(self.tag_group_list.count()):
            item = self.tag_group_list.item(i)
            item.setIcon(
                self.style().standardIcon(
                    # hourglass icon
                    QStyle.StandardPixmap.SP_BrowserReload
                )
            )
            item.setToolTip("Validating...")

    def _validate_tag_groups_and_update_ui(self):
        mw.taskman.run_in_background(
            task=self._validate_tag_groups_in_background,
            on_done=self._on_validate_tag_groups_finished,
        )

    def _validate_tag_groups_in_background(self):
        result = self._optional_tags_helper.prevalidate()
        return result

    def _on_validate_tag_groups_finished(self, future: Future):
        tag_group_validation_responses = future.result()

        # update icons and tooltips
        for i in range(self.tag_group_list.count()):
            item = self.tag_group_list.item(i)
            response = next(
                response
                for response in tag_group_validation_responses
                if response.tag_group_name == item.text()
            )

            if response.success:
                item.setIcon(
                    self.style().standardIcon(
                        # checkmark icon
                        QStyle.StandardPixmap.SP_DialogApplyButton
                    )
                )
                item.setToolTip("")
            else:
                item.setIcon(
                    self.style().standardIcon(
                        # warning icon
                        QStyle.StandardPixmap.SP_MessageBoxWarning
                    )
                )
                if response.errors:
                    item.setToolTip("\n".join(response.errors))
                else:
                    item.setToolTip("Unknown error")

        # enable/disable submit button depending on if there are valid suggestions
        valid_groups_exist = any(
            response.success for response in tag_group_validation_responses
        )

        self.submit_btn.setEnabled(valid_groups_exist)
        if not valid_groups_exist:
            self.submit_btn.setToolTip(
                "There are no valid suggestions. Please check the tooltips of the tag groups for more information."
            )
        else:
            self.submit_btn.setToolTip("")
