"""Dialog for suggesting optional tags for notes."""
from concurrent.futures import Future
from typing import List, Sequence

import aqt
from anki.notes import NoteId
from aqt.qt import (
    QAbstractItemView,
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

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import AnkiHubHTTPError
from ..ankihub_client.models import TagGroupValidationResponse
from ..main.optional_tag_suggestions import OptionalTagsSuggestionHelper
from .operations import AddonQueryOp
from .utils import show_error_dialog


class OptionalTagsSuggestionDialog(QDialog):
    def __init__(self, parent, nids: Sequence[NoteId]):
        super().__init__(parent)
        self._parent = parent
        self.nids = nids

        self._client = AnkiHubClient()
        self._optional_tags_helper = OptionalTagsSuggestionHelper(list(self.nids))
        self._valid_tag_groups: Sequence[str] = []
        self._setup_ui()
        self._setup_tag_group_list()
        self._validate_tag_groups_and_update_ui()

    def _setup_ui(self):
        self.setWindowTitle("Optional Tag Suggestions")

        self.layout_ = QVBoxLayout()
        self.hlayout = QHBoxLayout()
        self.btn_bar = QVBoxLayout()

        self.tag_group_list = QListWidget()
        # allow selecting multiple items at once
        self.tag_group_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )

        self.submit_btn = QPushButton("Submit Suggestions")
        self.submit_btn.setDisabled(True)
        self.btn_bar.addWidget(self.submit_btn)
        qconnect(self.submit_btn.clicked, self._on_submit)

        self.cancel_btn = QPushButton("Cancel")
        self.btn_bar.addWidget(self.cancel_btn)
        qconnect(self.cancel_btn.clicked, self._on_cancel)

        self.btn_bar.addStretch(1)

        self.auto_accept_cb = QCheckBox("Submit without review")
        self.auto_accept_cb.setToolTip(
            "If checked, the suggestions will be automatically accepted. "
            "This option is only available for maintainers of the deck."
        )

        self.setLayout(self.layout_)
        self.layout_.addLayout(self.hlayout)
        self.hlayout.addWidget(self.tag_group_list)
        self.hlayout.addLayout(self.btn_bar)
        self.layout_.addWidget(self.auto_accept_cb)

    def _on_submit(self):
        selected_tag_groups = [
            self.tag_group_list.item(i).text()
            for i in range(self.tag_group_list.count())
            if self.tag_group_list.item(i).isSelected()
        ]
        if not selected_tag_groups:
            showInfo("Please select at least one tag group.", parent=self._parent)
            return

        if not set(selected_tag_groups).issubset(self._valid_tag_groups):
            showInfo(
                "Some of the selected tag groups have problems. Hover over them to see the reason.",
                parent=self._parent,
            )
            return

        aqt.mw.taskman.with_progress(
            task=lambda: self._optional_tags_helper.suggest_tags_for_groups(
                tag_groups=selected_tag_groups,
                auto_accept=self.auto_accept_cb.isChecked(),
            ),
            on_done=self._on_submit_finished,
            label="Submitting suggestions...",
        )

    def _on_submit_finished(self, future: Future):
        try:
            future.result()
        except AnkiHubHTTPError as e:
            if e.response.status_code == 403:
                response_data = e.response.json()
                error_message = response_data.get("detail")
                if error_message:
                    show_error_dialog(
                        error_message,
                        parent=self._parent,
                        title="Error submitting Optional Tags suggestion :(",
                    )
                else:
                    raise e
        else:
            tooltip("Optional tags suggestions submitted.", parent=self._parent)
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

    def _validate_tag_groups_and_update_ui(self) -> None:
        AddonQueryOp(
            parent=self,
            op=lambda _: self._validate_tag_groups(),
            success=self._on_validate_tag_groups_finished,
        ).without_collection().run_in_background()

    def _validate_tag_groups(self) -> List[TagGroupValidationResponse]:
        result = self._optional_tags_helper.prevalidate_tag_groups()
        return result

    def _on_validate_tag_groups_finished(
        self, tag_group_validation_responses: List[TagGroupValidationResponse]
    ) -> None:
        self._valid_tag_groups = [
            response.tag_group_name
            for response in tag_group_validation_responses
            if response.success
        ]

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
                    LOGGER.debug(
                        f"Unknown error for tag group {item.text()}, {response=}"
                    )
                    item.setToolTip("Unknown error")

        # pre-select all valid tag groups
        for tag_group in self._valid_tag_groups:
            for i in range(self.tag_group_list.count()):
                item = self.tag_group_list.item(i)
                if item.text() == tag_group:
                    item.setSelected(True)
                    break

        # enable submit button (it was disabled while validating)
        self.submit_btn.setDisabled(False)
