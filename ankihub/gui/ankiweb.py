from __future__ import annotations

import sys
import time
from concurrent.futures import Future
from enum import Enum
from math import ceil
from typing import Any, Callable, NoReturn, Union

import aqt
import aqt.main
import aqt.preferences
import aqt.sync
from anki.hooks import wrap
from aqt.qt import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRegularExpression,
    QRegularExpressionValidator,
    QSize,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
    qconnect,
    sip,
)
from aqt.utils import openLink, tooltip

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import AnkiHubRequestException
from ..settings import config
from ..user_state import add_user_state_refreshed_callback
from .errors import _error_reporting_enabled, _show_feedback_dialog, report_exception_and_upload_logs
from .operations import AddonQueryOp
from .utils import error_icon, is_email

EMAIL_INSTRUCTIONS = (
    "Didn't receive an e-mail?<ul><li>Check the spam folder. "
    "E-mails can end up there.</li><li>Resend the e-mail when the countdown ends.</li></ul>"
)
# Client-side only; the verification-resend API reports daily throttling via
# `throttled`, not a per-request cooldown. Matches the web verification-sent page.
VERIFICATION_EMAIL_RESEND_COOLDOWN_SECS = 60
ERROR_DIALOG_LINK = "#error-dialog"


def fit_wrapped_labels(root: QWidget, fallback_width: int = 437) -> None:
    """Pin word-wrapped QLabels to their real height at the current width.

    QLabel sizeHint under-reports height for wrapped/rich text (especially
    lists), which clips the last line(s). Same approach as
    bulk_suggestion_summary_dialog._fit_to_content.
    """
    for label in root.findChildren(QLabel):
        if not label.wordWrap():
            continue
        width = label.width() if label.width() > 0 else fallback_width
        label.setMinimumHeight(label.heightForWidth(width))


class AnkiwebLinkIds(Enum):
    LOGIN_CODE = "#sign-in-code"
    LOGIN_PASSWORD = "#sign-in-password"
    SIGNUP_CODE = "#sign-up-code"
    SIGNUP_PASSWORD = "#sign-up-password"


def assert_exhaustive(arg: NoReturn) -> NoReturn:
    raise Exception(f"unexpected arg received: {type(arg)} {arg}")


def persist_ankiweb_credentials(email: str, host_key: str) -> None:
    aqt.mw.pm.set_sync_username(email)
    aqt.mw.pm.set_sync_key(host_key)
    aqt.mw.pm.save()


def ankiweb_reset_url() -> str:
    return f"{config.ankiweb_url}/account/reset-password"


def ankiweb_terms_url() -> str:
    return f"{config.ankiweb_url}/account/terms"


def html_link(url: str, title: str, bold: bool = True) -> str:
    anchor = f"<a href='{url}'>{title}</a>"
    if bold:
        anchor = f"<b>{anchor}</b>"
    return anchor


def widget_for_link(link: AnkiwebLinkIds) -> Callable[[AnkiwebDialog], BaseAnkiwebWidget]:
    if link == AnkiwebLinkIds.LOGIN_CODE:
        return LoginWithCodeWidget
    elif link == AnkiwebLinkIds.LOGIN_PASSWORD:
        return LoginWithPasswordWidget
    elif link == AnkiwebLinkIds.SIGNUP_CODE:
        return SignupWithCodeWidget
    elif link == AnkiwebLinkIds.SIGNUP_PASSWORD:
        return SignupWithPasswordWidget
    else:
        assert_exhaustive(link)


def _report_and_format_exception(exc: Exception) -> tuple[str, str | None]:
    if isinstance(exc, AnkiHubRequestException):
        LOGGER.info("AnkiWeb request exception", exc_info=exc.original_exception)
        sentry_event_id = report_exception_and_upload_logs(exc) if _error_reporting_enabled() else None
        return (
            "Can't reach AnkiWeb. "
            "Check your connection and retry. If it keeps failing, AnkiWeb may be temporarily down. "
            + html_link(ERROR_DIALOG_LINK, "See error details.", False)
        ), sentry_event_id
    else:
        return str(exc), None


def destroy_timer(timer: QTimer | None) -> None:
    if timer and not sip.isdeleted(timer):
        timer.stop()
        timer.deleteLater()


def timer_is_active(timer: Countdown | None) -> bool:
    return bool(timer) and not sip.isdeleted(timer) and timer.isActive() and timer.remaining_seconds > 0


class ResendCooldownTracker:
    """Remembers each email's resend cooldown independently of any widget, so
    that closing and reopening the AnkiWeb dialog within the same Anki session
    keeps showing the correct remaining countdown instead of resetting it.

    Used for magic-code resends (server provides the TTL) and for password-
    signup verification emails (fixed client-side TTL).

    This is a UI convenience only, not an enforcement mechanism: the tracker
    lives in memory for the current session, so restarting Anki clears it and
    lets the resend button appear enabled again immediately. That's fine
    because abuse limits are enforced server-side; this class only avoids a
    redundant request (and confusing UI) in the common case where the dialog
    is closed and reopened without restarting Anki.
    """

    def __init__(self) -> None:
        self._deadlines: dict[tuple[str, str], float] = {}

    def start(self, scope: str, email: str, seconds: int) -> None:
        key = (scope, email)
        if seconds > 0:
            self._deadlines[key] = time.monotonic() + seconds
        else:
            self._deadlines.pop(key, None)

    def remaining_seconds(self, scope: str, email: str) -> int:
        key = (scope, email)
        deadline = self._deadlines.get(key)
        if deadline is None:
            return 0
        remaining = ceil(deadline - time.monotonic())
        if remaining <= 0:
            self._deadlines.pop(key, None)
            return 0
        return remaining


_resend_cooldowns = ResendCooldownTracker()


class Countdown(QTimer):
    def __init__(self, callback: Callable[[int], None], seconds: int = 120, parent: QWidget | None = None):
        self.remaining_seconds = seconds
        self._callback = callback
        super().__init__(parent)
        self.setInterval(1000)
        qconnect(self.timeout, self._on_timeout)
        self._on_timeout()

    def _on_timeout(self) -> None:
        self._callback(self.remaining_seconds)
        self.remaining_seconds -= 1
        if self.remaining_seconds < 0:
            destroy_timer(self)


class Heading(QLabel):
    def __init__(self, text: str, font_size: int = 20, parent: QWidget | None = None):
        super().__init__(text=text, parent=parent)
        font = self.font()
        font.setBold(True)
        font.setPointSize(font_size)
        self.setFont(font)


class Button(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text=text, parent=parent)
        self.setFixedWidth(125)


class CancelButton(Button):
    def __init__(self, dialog: AnkiwebDialog, parent: QWidget | None = None):
        super().__init__(text="Cancel", parent=parent)
        qconnect(self.clicked, lambda: dialog.close())


class LabelWithLink(QLabel):
    def __init__(
        self,
        text: str,
        dialog: AnkiwebDialog,
        link_handler: Callable[[str], bool] | None = None,
        parent: QWidget | None = None,
    ):
        self._dialog = dialog
        self._link_handler = link_handler
        super().__init__(text=text, parent=parent)
        qconnect(self.linkActivated, self._on_link_activated)

    def _on_link_activated(self, link: str) -> None:
        if link in (link_id.value for link_id in AnkiwebLinkIds):
            widget_type = widget_for_link(AnkiwebLinkIds(link))
            widget = widget_type(self._dialog)
            self._dialog.replace_widget(widget)
        elif not (self._link_handler and self._link_handler(link)):
            openLink(link)


class ErrorLabel(QWidget):
    _ICON_SIZE = 20
    # FormWidget's inner content width, given the dialog's fixed 525px width and margins.
    _FALLBACK_MAX_WIDTH = 461

    def __init__(self, dialog: AnkiwebDialog, parent: QWidget | None = None):
        self._dialog = dialog
        super().__init__(parent)
        self._setup_ui()
        self._exc: Exception | None = None
        self._sentry_event_id: str | None = None

    def _setup_ui(self) -> None:
        self.setVisible(False)
        hbox = QHBoxLayout()
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)
        self.status = status = LabelWithLink("", dialog=self._dialog, link_handler=self._link_handler)
        status.setWordWrap(True)
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.font()
        font.setBold(True)
        status.setFont(font)
        status.setTextFormat(Qt.TextFormat.RichText)
        icon_label = QLabel()
        icon_label.setPixmap(error_icon().pixmap(self._ICON_SIZE, self._ICON_SIZE))
        # status is given a shrink-to-fit width in _update_status_width (instead of
        # stretch=1), so the row has a fixed sizeHint that setAlignment can center
        # as a whole, with the row's width following the text length.
        hbox.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hbox.addWidget(icon_label)
        hbox.addWidget(status)
        self.setLayout(hbox)

    def set_error(self, text: str, clear_exception: bool = True) -> None:
        self.setVisible(bool(text))
        self.status.setText(text)
        self._update_status_width()
        if clear_exception:
            self._exc = None
            self._sentry_event_id = None

    def set_exception(self, exc: Exception) -> None:
        self._exc = exc
        error, self._sentry_event_id = _report_and_format_exception(exc)
        self.set_error(error, clear_exception=False)

    def _link_handler(self, link: str) -> bool:
        if link == ERROR_DIALOG_LINK:
            if self._exc:
                _show_feedback_dialog(self._exc, self._sentry_event_id)
            return True
        return False

    def _update_status_width(self) -> None:
        # Measure the label's own sizeHint with word wrap temporarily disabled. This
        # matches exactly how Qt will render the (rich) text on a single line, which
        # a manual QFontMetrics calculation doesn't reliably reproduce (e.g. it
        # ignores HTML formatting and any stylesheet font overrides applied at
        # render time).
        status = self.status
        status.setWordWrap(False)
        natural_width = status.sizeHint().width()
        status.setWordWrap(True)
        # Small buffer to avoid the rare case where sizeHint() is one pixel short
        # of what's needed, which would otherwise force an unwanted wrap.
        self.status.setFixedWidth(min(natural_width + 2, self._max_status_width()))

    def _max_status_width(self) -> int:
        # The dialog has a fixed width (see FixedDialogLayout), so relying on
        # parentWidget().width() is unreliable here: set_error() often runs before
        # the layout has been activated for the first time, when width() still
        # reports a placeholder size instead of the final ~461px content width.
        return max(self._FALLBACK_MAX_WIDTH - self._ICON_SIZE - 8, 0)


class BaseInput(QLineEdit):
    def is_initial_text_valid(self) -> bool:
        """Used by InputWithButtonHbox to determine if the associated button should be enabled by default."""
        return False


class PasswordInput(BaseInput):
    _BASE_STYLE = 'QLineEdit[echoMode="2"] { lineedit-password-character: 9733; }'
    _PROBLEM_STYLE = "QLineEdit { border: 1px solid #cc3333 }"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        # Change the mask character to a star.
        self.set_problem_style(False)

    def set_problem_style(self, problem: bool) -> None:
        style = self._BASE_STYLE
        if problem:
            style += f"\n{self._PROBLEM_STYLE}"
        self.setStyleSheet(style)


class CodeInput(BaseInput):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        code_validator = QRegularExpressionValidator(QRegularExpression(r"\d{6}"))
        self.setValidator(code_validator)
        self.setStyleSheet("""QLineEdit {letter-spacing: 2px}""")


class EmailInput(BaseInput):
    def __init__(self, contents: str = "", parent: QWidget | None = None):
        ankihub_email = config.user()
        if ankihub_email and is_email(ankihub_email) and not contents:
            contents = ankihub_email
        super().__init__(contents, parent=parent)

    def is_initial_text_valid(self):
        return is_email(self.text())


FormRow = Union[tuple[str, Union[QWidget, QLayout]], QWidget, QLayout]


class FormWidget(QGroupBox):
    def __init__(
        self,
        description: str,
        rows: list[FormRow],
        dialog: AnkiwebDialog,
        parent: QWidget | None = None,
        back_to: AnkiwebLinkIds | None = None,
    ):
        self._dialog = dialog
        self.back_to = back_to
        super().__init__(parent)
        self._setup_ui(description, rows)

    def _setup_ui(self, description: str, rows: list[FormRow]) -> None:
        self.setContentsMargins(0, 0, 0, 0)
        vbox = QVBoxLayout()
        vbox.setSpacing(16)
        vbox.setContentsMargins(12, 12, 12, 12)
        vbox.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.error_label = error_label = ErrorLabel(self._dialog, self)
        vbox.addWidget(error_label)

        if description:
            description_label = LabelWithLink(description, self._dialog)
            description_label.setWordWrap(True)
            description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vbox.addWidget(description_label)

        form_layout = QFormLayout()
        # Bare QWidgets (e.g. multi-line instruction labels) go on the vbox —
        # QFormLayout often under-reports their height and clips rich text.
        trailing_widgets: list[QWidget] = []
        for row in rows:
            if isinstance(row, tuple):
                label_text, field = row
                label = QLabel(label_text)
                font = label.font()
                font.setBold(True)
                label.setFont(font)
                form_layout.addRow(label, field)
            elif isinstance(row, QLayout):
                form_layout.addRow(row)
            else:
                trailing_widgets.append(row)
        form_layout.setSpacing(8)
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        if form_layout.rowCount():
            vbox.addLayout(form_layout)
        for widget in trailing_widgets:
            vbox.addWidget(widget)

        self.setLayout(vbox)


class InputWithButtonHbox(QHBoxLayout):
    def __init__(self, input_widget: BaseInput, button_label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.button = button = Button(button_label)
        button.setEnabled(input_widget.is_initial_text_valid())
        self.setSpacing(8)
        self.addWidget(input_widget)
        self.addWidget(button)


class FixedDialogLayout(QVBoxLayout):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    def sizeHint(self) -> QSize:
        return QSize(525, super().sizeHint().height())


class AnkiwebDialog(QDialog):
    def __init__(
        self,
        initial_widget: BaseAnkiwebWidget,
        on_success: Callable[[], None] = lambda: None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._progress_widget: InlineProgressWidget | None = None
        self._on_success = on_success
        self._setup_ui(initial_widget)

    def _setup_ui(self, initial_widget: BaseAnkiwebWidget) -> None:
        self._widget = initial_widget
        vbox = FixedDialogLayout()
        vbox.addWidget(initial_widget)
        vbox.setContentsMargins(20, 20, 20, 20)
        self.setLayout(vbox)
        self.setWindowTitle(initial_widget.title)
        self._schedule_fit_wrapped_labels()

    def replace_widget(self, widget: BaseAnkiwebWidget) -> None:
        self.layout().replaceWidget(self._widget, widget)
        destroy_timer(self._widget._timer)
        self._widget.deleteLater()
        self._widget = widget
        self.setWindowTitle(widget.title)
        self.adjustSize()
        self._schedule_fit_wrapped_labels()

    def _schedule_fit_wrapped_labels(self) -> None:
        # Defer until after the layout has a real width; sizeHint alone clips
        # the last line(s) of word-wrapped / rich-text labels.
        widget = self._widget

        def fit() -> None:
            if sip.isdeleted(self) or sip.isdeleted(widget):
                return
            fit_wrapped_labels(widget)
            self.adjustSize()

        QTimer.singleShot(0, fit)

    def show_progress(self, widget: InlineProgressWidget) -> None:
        self._widget.setVisible(False)
        if self._progress_widget:
            self.layout().replaceWidget(self._progress_widget, widget)
        else:
            self.layout().addWidget(widget)
        self._progress_widget = widget
        self.setWindowTitle(widget.title)

    def hide_progress(self) -> None:
        assert self._progress_widget
        self.layout().removeWidget(self._progress_widget)
        self._progress_widget = None
        self._widget.setVisible(True)
        self.setWindowTitle(self._widget.title)

    def update_progress(self, status: str) -> None:
        if self._progress_widget:
            aqt.mw.taskman.run_on_main(lambda: self._progress_widget.set_progress_status(status))


class BaseAnkiwebWidget(QWidget):
    title: str

    def __init__(
        self,
        heading: str,
        main_description: str,
        form_widget: FormWidget,
        bottom_label: str,
        dialog: AnkiwebDialog,
        extra_bottom_button: QPushButton | None = None,
        show_cancel: bool = True,
        parent: QWidget | None = None,
    ):
        self._dialog = dialog
        self._timer: Countdown | None = None
        super().__init__(parent=parent)
        self._setup_ui(heading, main_description, form_widget, bottom_label, extra_bottom_button, show_cancel)

    def _setup_ui(
        self,
        heading: str,
        main_description: str,
        form_widget: FormWidget,
        bottom_label: str,
        extra_bottom_button: QPushButton | None,
        show_cancel: bool,
    ) -> None:
        vbox = QVBoxLayout()

        if heading:
            heading_label = Heading(heading)
            vbox.addWidget(heading_label)

        if main_description:
            description_label = QLabel(main_description)
            description_label.setWordWrap(True)
            vbox.addWidget(description_label)

        self.form_widget = form_widget
        vbox.addWidget(form_widget, stretch=1)

        self.status_label = status_label = QLabel("")
        status_label.setWordWrap(True)
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(status_label)

        bottom_hbox = QHBoxLayout()
        if bottom_label:
            signup_link = LabelWithLink(bottom_label, self._dialog)
            bottom_hbox.addWidget(signup_link)

        buttons_hbox = QHBoxLayout()
        buttons_hbox.setAlignment(Qt.AlignmentFlag.AlignRight)
        if form_widget.back_to:
            back_button = Button("Back")
            qconnect(
                back_button.clicked,
                lambda: self._dialog.replace_widget(widget_for_link(form_widget.back_to)(self._dialog)),
            )
            buttons_hbox.addWidget(back_button)
        if show_cancel:
            buttons_hbox.addWidget(CancelButton(self._dialog))
        if extra_bottom_button:
            buttons_hbox.addWidget(extra_bottom_button)
        bottom_hbox.addLayout(buttons_hbox)
        vbox.addLayout(bottom_hbox)

        vbox.setContentsMargins(0, 0, 0, 0)
        self.setLayout(vbox)

    def init_timer(self, on_timeout: Callable[[int], None], seconds: int = 120) -> None:
        destroy_timer(self._timer)
        self._timer = Countdown(callback=on_timeout, seconds=seconds, parent=self)
        self._timer.start()


def run_with_progress(
    dialog: AnkiwebDialog,
    heading: str,
    status: str,
    task: Callable,
    on_done: Callable[[Future], None] | None = None,
) -> None:
    def wrapped_on_done(fut: Future) -> None:
        if sip.isdeleted(dialog):
            return
        dialog.hide_progress()
        if on_done:
            on_done(fut)

    dialog.show_progress(InlineProgressWidget(heading=heading, status=status, dialog=dialog))
    aqt.mw.taskman.run_in_background(task, wrapped_on_done)


class InlineProgressWidget(BaseAnkiwebWidget):
    def __init__(self, heading: str, status: str, dialog: AnkiwebDialog, parent: QWidget | None = None):
        self._dialog = dialog
        self.title = heading
        super().__init__(
            heading=heading,
            main_description="",
            form_widget=self._create_form_widget(status),
            bottom_label="",
            dialog=dialog,
            extra_bottom_button=None,
            parent=parent,
        )

    def _create_form_widget(self, status: str) -> FormWidget:
        self.progress_label = label = Heading(status, 12)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress_bar = QProgressBar()
        progress_bar.setValue(0)
        progress_bar.setMaximum(0)
        form_widget = FormWidget(description="", rows=[label, progress_bar], dialog=self._dialog)

        return form_widget

    def set_progress_status(self, status: str) -> None:
        self.progress_label.setText(status)


class BaseLoginWidget(BaseAnkiwebWidget):
    title = "Sign into AnkiWeb"

    def __init__(
        self,
        main_description: str,
        form_widget: FormWidget,
        bottom_label: str,
        dialog: AnkiwebDialog,
        extra_bottom_button: QPushButton | None = None,
    ):
        super().__init__(
            heading=self.title,
            main_description=main_description,
            form_widget=form_widget,
            bottom_label=bottom_label,
            dialog=dialog,
            extra_bottom_button=extra_bottom_button,
        )


class LoginWithCodeWidget(BaseLoginWidget):
    _COOLDOWN_SCOPE = "login"

    def __init__(self, dialog: AnkiwebDialog):
        self._dialog = dialog
        super().__init__(
            main_description="<b>Sign in to your account without having to type your password</b>.<br>"
            " A free account is required to keep your collection synchronized.",
            form_widget=self._create_form_widget(),
            bottom_label=f"{html_link(AnkiwebLinkIds.SIGNUP_CODE.value, 'Sign up for a new account')}",
            dialog=dialog,
        )
        self._sync_state_for_email(self.email_input.text())

    def _create_form_widget(self) -> FormWidget:
        self.email_input = email_input = EmailInput()
        qconnect(email_input.textChanged, self._on_email_changed)
        self.email_box = email_box = InputWithButtonHbox(email_input, "Get code")
        qconnect(email_box.button.clicked, self._on_get_code)

        self.code_input = code_input = CodeInput()
        code_input.setEnabled(False)
        qconnect(code_input.textChanged, self._on_code_changed)
        self.code_box = code_box = InputWithButtonHbox(code_input, "Sign in")
        qconnect(code_box.button.clicked, self._on_sign_in)

        form_widget = FormWidget(
            description="We'll email you a magic code for a password-free sign in."
            f"<br>Or you can {html_link(AnkiwebLinkIds.LOGIN_PASSWORD.value, 'sign in with password instead')}",
            rows=[("Email", email_box), ("Code", code_box)],
            dialog=self._dialog,
        )
        return form_widget

    def _on_email_changed(self, text: str) -> None:
        self._sync_state_for_email(text)

    def _on_code_changed(self, text: str) -> None:
        self.code_box.button.setEnabled(self.code_input.hasAcceptableInput() and is_email(self.email_input.text()))

    def _sync_state_for_email(self, email: str) -> None:
        """Reflects any cooldown already tracked for `email` (e.g. from a code
        requested before the dialog was closed and reopened) instead of always
        allowing a new request whenever the email field shows a valid address.
        """
        email_valid = is_email(email)
        remaining = _resend_cooldowns.remaining_seconds(self._COOLDOWN_SCOPE, email) if email_valid else 0
        if remaining > 0:
            self.code_input.setEnabled(True)
            self._start_cooldown(remaining)
        else:
            if timer_is_active(self._timer):
                destroy_timer(self._timer)
                self._timer = None
                self.status_label.setText("")
            self.email_box.button.setEnabled(email_valid)
        self.code_box.button.setEnabled(self.code_input.hasAcceptableInput() and email_valid)

    def _start_cooldown(self, remaining_secs: int) -> None:
        def on_timeout(remaining_secs: int) -> None:
            email = self.email_input.text()
            resend_available_status = f"Resend available in {remaining_secs}s" if remaining_secs else "Resend available"
            self.status_label.setText(
                f"If {email} belongs to an existing account, you will receive a message in your inbox.<br>"
                + resend_available_status
            )
            if not remaining_secs:
                self.email_box.button.setEnabled(True)

        self.init_timer(on_timeout, remaining_secs)
        self.email_box.button.setEnabled(False)

    def _on_get_code(self) -> None:
        def on_success(resend_cooldown_secs: int) -> None:
            _resend_cooldowns.start(self._COOLDOWN_SCOPE, email, resend_cooldown_secs)
            self._start_cooldown(resend_cooldown_secs)
            self.code_input.setEnabled(True)
            self.form_widget.error_label.set_error("")

        email = self.email_input.text()
        AddonQueryOp(
            parent=self,
            op=lambda _: AnkiHubClient().ankiweb_request_login_code(email).resend_cooldown_secs,
            success=on_success,
        ).failure(lambda exc: self.form_widget.error_label.set_exception(exc)).run_in_background()

    def _on_sign_in(self) -> None:
        def task() -> str:
            return AnkiHubClient().ankiweb_verify_login_code(self.email_input.text(), self.code_input.text()).host_key

        def on_done(fut: Future) -> None:
            try:
                host_key = fut.result()
                email = self.email_input.text()
                persist_ankiweb_credentials(email=email, host_key=host_key)
                self._dialog.close()
                tooltip("Sign-in successful!", parent=aqt.mw)
                self._dialog._on_success()
            except Exception as exc:
                self.form_widget.error_label.set_exception(exc)
                self.code_input.clear()

        run_with_progress(dialog=self._dialog, heading=self.title, status="Signing you in", task=task, on_done=on_done)


class LoginWithPasswordWidget(BaseLoginWidget):
    def __init__(self, dialog: AnkiwebDialog):
        self._dialog = dialog
        super().__init__(
            main_description="<b>Sign in with your email and password.</b><br>"
            "A free account is required to keep your collection synchronized.",
            form_widget=self._create_form_widget(),
            bottom_label=f"{html_link(AnkiwebLinkIds.SIGNUP_CODE.value, 'Sign up for a new account')}",
            dialog=dialog,
        )

    def _create_form_widget(self) -> FormWidget:
        self.email_input = email_input = EmailInput()
        qconnect(email_input.textChanged, self._on_email_changed)
        self.password_input = password_input = PasswordInput()
        qconnect(password_input.textChanged, self._on_password_changed)
        self.password_box = password_box = InputWithButtonHbox(password_input, "Sign in")
        qconnect(password_box.button.clicked, self._on_sign_in)
        forgot_password_label = LabelWithLink(
            f"{html_link(ankiweb_reset_url(), 'Forgot password?', False)}",
            self._dialog,
        )
        form_widget = FormWidget(
            description="We can email you a magic code for a password-free sign in.<br>"
            f"{html_link(AnkiwebLinkIds.LOGIN_CODE.value, 'Get a code instead')}",
            rows=[("Email", email_input), ("Password", password_box), forgot_password_label],
            dialog=self._dialog,
        )
        return form_widget

    def _on_email_changed(self, text: str) -> None:
        self.password_box.button.setEnabled(is_email(text) and bool(self.password_input.text()))

    def _on_password_changed(self, text: str) -> None:
        self.password_box.button.setEnabled(bool(text) and is_email(self.email_input.text()))

    def _on_sign_in(self) -> None:
        def task() -> str:
            return AnkiHubClient().ankiweb_login(self.email_input.text(), self.password_input.text()).host_key

        def on_done(fut: Future) -> None:
            try:
                host_key = fut.result()
                email = self.email_input.text()
                persist_ankiweb_credentials(email=email, host_key=host_key)
                self._dialog.close()
                tooltip("Sign-in successful!", parent=aqt.mw)
                self._dialog._on_success()
            except Exception as exc:
                self.form_widget.error_label.set_exception(exc)

        run_with_progress(dialog=self._dialog, heading=self.title, status="Signing you in", task=task, on_done=on_done)


class BaseSignupWidget(BaseAnkiwebWidget):
    title = "Create an AnkiWeb account"

    def __init__(
        self,
        heading: str,
        main_description: str,
        form_widget: FormWidget,
        bottom_label: str,
        dialog: AnkiwebDialog,
        extra_bottom_button: QPushButton | None = None,
        show_cancel: bool = True,
    ):
        super().__init__(
            heading=heading,
            main_description=main_description,
            form_widget=form_widget,
            bottom_label=bottom_label,
            dialog=dialog,
            extra_bottom_button=extra_bottom_button,
            show_cancel=show_cancel,
        )


class SignupErrorWidget(BaseSignupWidget):
    def __init__(self, error: str, dialog: AnkiwebDialog, is_code_signup: bool):
        self._dialog = dialog
        self.is_code_signup = is_code_signup
        super().__init__(
            heading="Create an AnkiWeb account",
            main_description="",
            form_widget=self._create_form_widget(error),
            bottom_label="",
            dialog=dialog,
        )

    def _create_form_widget(self, error: str) -> FormWidget:
        form_widget = FormWidget(
            description="We can email you a magic code for password-free sign-in.<br>"
            f"{html_link(AnkiwebLinkIds.LOGIN_CODE.value, 'Sign in with code.')}<br><br>"
            f"Alternatively, you can {html_link(ankiweb_reset_url(), 'reset your password')}.",
            rows=[],
            dialog=self._dialog,
            back_to=AnkiwebLinkIds.SIGNUP_CODE if self.is_code_signup else AnkiwebLinkIds.SIGNUP_PASSWORD,
        )
        form_widget.error_label.set_error(error)

        return form_widget


class SignupEmailVerificationWidget(BaseSignupWidget):
    _COOLDOWN_SCOPE = "signup_verification"

    def __init__(self, email: str, host_key: str, dialog: AnkiwebDialog):
        self.email = email
        self.host_key = host_key
        self._dialog = dialog
        login_button = Button("Sign in")
        qconnect(login_button.clicked, self._on_login)
        super().__init__(
            heading="Create an AnkiWeb account",
            main_description="",
            form_widget=self._create_form_widget(),
            bottom_label="",
            dialog=dialog,
            extra_bottom_button=login_button,
            show_cancel=False,
        )
        # Signup already sent the verification email. Shouldn't call resend here.
        # Start the UI cooldown soresend isn't clickable yet (don't call the resend API here).
        remaining = _resend_cooldowns.remaining_seconds(self._COOLDOWN_SCOPE, email)
        if remaining <= 0:
            _resend_cooldowns.start(self._COOLDOWN_SCOPE, email, VERIFICATION_EMAIL_RESEND_COOLDOWN_SECS)
            remaining = VERIFICATION_EMAIL_RESEND_COOLDOWN_SECS
        self._start_cooldown(remaining)

    def _create_form_widget(self) -> FormWidget:
        self.resend_button = resend_button = QPushButton("Resend verification email")
        qconnect(resend_button.clicked, self._resend)
        # Separate plain labels: a single QLabel with <ul> under-reports height
        # and clips the last bullet even with wordWrap enabled.
        instructions = QWidget()
        instructions_layout = QVBoxLayout(instructions)
        instructions_layout.setContentsMargins(0, 0, 0, 0)
        instructions_layout.setSpacing(4)
        heading = QLabel("Didn't receive an e-mail?")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        instructions_layout.addWidget(heading)
        for text in (
            "Check the spam folder. E-mails can end up there.",
            "Resend the e-mail when the countdown ends.",
        ):
            item = QLabel(f"• {text}")
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            instructions_layout.addWidget(item)
        description = (
            f"📮 We sent a verification link to <b>{self.email}</b>. "
            f"<br/>If the email is not correct, "
            f"{html_link(AnkiwebLinkIds.SIGNUP_PASSWORD.value, 'please change it')}."
        )
        return FormWidget(
            description=description,
            rows=[resend_button, instructions],
            dialog=self._dialog,
            back_to=AnkiwebLinkIds.SIGNUP_PASSWORD,
        )

    def _start_cooldown(self, remaining_secs: int) -> None:
        def on_timeout(remaining_secs: int) -> None:
            resend_available_status = (
                f"Resend available in {remaining_secs}s" if remaining_secs else "Resend available."
            )
            self.status_label.setText(
                f"If {self.email} account exists, we sent a message to its inbox.<br>"
                + resend_available_status
            )
            if not remaining_secs:
                self.resend_button.setEnabled(True)

        self.init_timer(on_timeout, remaining_secs)
        self.resend_button.setEnabled(False)

    def _resend(self) -> None:
        # Disable immediately so a second click can't fire while the request is in flight.
        self.resend_button.setEnabled(False)

        def on_success(throttled: bool) -> None:
            if throttled:
                self.form_widget.error_label.set_error("Sorry, no more emails can be sent to that address today.")
                self.status_label.setText("")
            else:
                # TODO: use /verify-email to get actual status
                self.form_widget.error_label.set_error("")
                _resend_cooldowns.start(
                    self._COOLDOWN_SCOPE, self.email, VERIFICATION_EMAIL_RESEND_COOLDOWN_SECS
                )
                self._start_cooldown(VERIFICATION_EMAIL_RESEND_COOLDOWN_SECS)

        def on_failure(exc: Exception) -> None:
            self.form_widget.error_label.set_exception(exc)
            if not timer_is_active(self._timer):
                self.resend_button.setEnabled(True)

        AddonQueryOp(
            parent=self,
            op=lambda _: AnkiHubClient().ankiweb_resend_verification(self.host_key).throttled,
            success=on_success,
        ).failure(on_failure).run_in_background()

    def _on_login(self) -> None:
        self._dialog.replace_widget(LoginWithPasswordWidget(self._dialog))


class SignupCodeVerificationWidget(BaseSignupWidget):
    def __init__(self, email: str, dialog: AnkiwebDialog, exc: Exception | None = None, remaining_seconds: int = 0):
        self.email = email
        self._dialog = dialog
        self.remaining_seconds = remaining_seconds
        self._is_retry = bool(exc)
        super().__init__(
            heading="Email confirmation",
            main_description="",
            form_widget=self._create_form_widget(exc),
            bottom_label=f"{html_link(AnkiwebLinkIds.LOGIN_CODE.value, 'Have an account? Sign in.')}",
            dialog=dialog,
        )
        if not self._is_retry or self.remaining_seconds > 0:
            self._start_timer()
        else:
            self._update_code_button_state()

    def _create_form_widget(self, exc: Exception | None = None) -> FormWidget:
        self.code_input = code_input = CodeInput()
        qconnect(code_input.textChanged, self._on_code_changed)
        self.code_box = code_box = InputWithButtonHbox(code_input, "Verify code")
        qconnect(code_box.button.clicked, self._on_verify_or_resend)
        if self._is_retry:
            description = ""
            self.email_input = email_input = EmailInput(self.email)
            self.email_box = email_box = InputWithButtonHbox(email_input, "Get code")
            qconnect(email_input.textChanged, self._on_email_changed)
            self._on_email_changed(self.email)
            qconnect(email_box.button.clicked, self._on_get_code)
            rows: list[FormRow] = [("Email", email_box), ("Code", code_box)]
        else:
            instructions_label = QLabel(EMAIL_INSTRUCTIONS)
            instructions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            description = (
                f"Insert the verification code we've sent to {self.email}.<br>"
                f"If the email is not correct, {html_link(AnkiwebLinkIds.SIGNUP_CODE.value, 'please change it')}."
            )
            rows = [("Code", code_box), instructions_label]
        form_widget = FormWidget(
            description=description, rows=rows, dialog=self._dialog, back_to=AnkiwebLinkIds.SIGNUP_CODE
        )
        if exc:
            form_widget.error_label.set_exception(exc)

        return form_widget

    def _is_resend(self) -> bool:
        return not bool(self.code_input.text())

    def _update_code_button_state(self) -> None:
        code_button = self.code_box.button
        code_is_valid = self.code_input.hasAcceptableInput()
        # Button is enabled if the code is valid or it's empty while the timer is not running
        enabled = code_is_valid or (
            not self.code_input.text() and not self._is_retry and not timer_is_active(self._timer)
        )
        if not self._is_retry:
            if self._is_resend():
                code_button.setText("Resend code")
            else:
                code_button.setText("Verify code")
        code_button.setEnabled(enabled)
        if self._is_retry:
            self.email_box.button.setEnabled(not timer_is_active(self._timer))

    def _start_timer(self) -> None:
        def on_timeout(remaining_secs: int) -> None:
            resend_available_status = (
                f"Resend available in {remaining_secs}s" if remaining_secs else "Resend available."
            )
            self.status_label.setText(
                f"If {self.email} exists, we sent a message to its inbox.<br>" + resend_available_status
            )
            if not remaining_secs:
                self._update_code_button_state()

        self.init_timer(on_timeout, self.remaining_seconds)
        self._update_code_button_state()

    def _on_code_changed(self, text: str) -> None:
        self._update_code_button_state()

    def _on_email_changed(self, text: str) -> None:
        self.email_box.button.setEnabled(is_email(text))

    def _on_get_code(self) -> None:
        def on_success(resend_cooldown_secs: int) -> None:
            self.remaining_seconds = resend_cooldown_secs
            self._start_timer()

        email = self._get_email()
        AddonQueryOp(
            parent=self,
            op=lambda _: AnkiHubClient().ankiweb_request_login_code(email).resend_cooldown_secs,
            success=on_success,
        ).failure(lambda exc: self.form_widget.error_label.set_exception(exc)).run_in_background()

    def _get_email(self) -> str:
        return self.email_input.text() if self._is_retry else self.email

    def _on_verify_or_resend(self) -> None:
        if self._is_resend():
            self._start_timer()
            return

        def task() -> str:
            return AnkiHubClient().ankiweb_verify_signup_code(self._get_email(), self.code_input.text()).host_key

        def on_done(fut: Future) -> None:
            try:
                host_key = fut.result()
                persist_ankiweb_credentials(email=self._get_email(), host_key=host_key)
                self._dialog.close()
                tooltip("Sign-in successful!", parent=aqt.mw)
                self._dialog._on_success()
            except Exception as exc:
                if self._timer.remaining_seconds > 0:
                    remaining_seconds = self._timer.remaining_seconds
                else:
                    remaining_seconds = self.remaining_seconds
                self._dialog.replace_widget(
                    SignupCodeVerificationWidget(
                        email=self._get_email(),
                        dialog=self._dialog,
                        exc=exc,
                        remaining_seconds=remaining_seconds,
                    )
                )

        run_with_progress(
            dialog=self._dialog, heading=self.title, status="Creating account", task=task, on_done=on_done
        )


class BaseSignupFirstPageWidget(BaseSignupWidget):
    def __init__(self, is_code_signup: bool, dialog: AnkiwebDialog):
        self.is_code_signup = is_code_signup
        self._dialog = dialog
        super().__init__(
            heading="Create an AnkiWeb account",
            main_description="<b>Sign up to gain access to Anki's web companion and cloud storage.</b><br>"
            "This is a free account and it can keep your flashcard data in sync across your devices and the cloud.",
            form_widget=self._create_form_widget(),
            bottom_label=f"{html_link(AnkiwebLinkIds.LOGIN_CODE.value, 'Have an account? Sign in.')}",
            dialog=dialog,
        )

    def _create_form_widget(self) -> FormWidget:
        terms_hbox = QHBoxLayout()
        terms_hbox.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.terms_checkbox = terms_checkbox = QCheckBox()
        qconnect(terms_checkbox.toggled, self._on_terms_toggled)
        terms_label = LabelWithLink(
            f"I agree to AnkiWeb's {html_link(ankiweb_terms_url(), 'Terms & Conditions')}.", self._dialog
        )
        terms_hbox.addWidget(terms_checkbox)
        terms_hbox.addWidget(terms_label)
        if self.is_code_signup:
            self.email_input = email_input = EmailInput()
            qconnect(email_input.textChanged, self._on_email_changed)
            self.email_box = InputWithButtonHbox(email_input, "Sign up")
            qconnect(self.email_box.button.clicked, self._on_sign_up)
            rows: list[FormRow] = [terms_hbox, ("Email", self.email_box)]
        else:
            self.email_input = email_input = EmailInput()
            qconnect(email_input.textChanged, self._on_email_changed)
            self.password_input = password_input = PasswordInput()
            qconnect(password_input.textChanged, self._on_password_changed)
            qconnect(password_input.editingFinished, self._on_password_editing_finished)
            self.repeat_password_input = repeat_password_input = PasswordInput()
            qconnect(repeat_password_input.textChanged, self._on_password_changed)
            qconnect(repeat_password_input.editingFinished, self._on_password_editing_finished)
            self.repeat_password_box = repeat_password_box = InputWithButtonHbox(repeat_password_input, "Sign up")
            qconnect(repeat_password_box.button.clicked, self._on_sign_up)
            rows = [
                terms_hbox,
                ("Email", email_input),
                ("New Password", password_input),
                ("Repeat Password", repeat_password_box),
            ]
        form_description = (
            (
                "We'll email you a magic code for a password-free sign-up.<br>"
                f"Or you can {html_link(AnkiwebLinkIds.SIGNUP_PASSWORD.value, 'sign up with password instead.')}"
            )
            if self.is_code_signup
            else (
                "We can email you a magic code for a password-free sign up.<br>"
                f"{html_link(AnkiwebLinkIds.SIGNUP_CODE.value, 'Sign up with code.')}"
            )
        )
        form_widget = FormWidget(
            form_description,
            rows=rows,
            dialog=self._dialog,
        )
        self._update_signup_button_state()
        return form_widget

    def _update_signup_button_state(self) -> None:
        enabled = self.terms_checkbox.isChecked() and is_email(self.email_input.text())
        button = self.email_box.button if self.is_code_signup else self.repeat_password_box.button
        if not self.is_code_signup:
            password = self.password_input.text()
            repeat_password = self.repeat_password_input.text()
            enabled &= bool(password) and bool(repeat_password)

        button.setEnabled(enabled)

    def _set_password_problem_style(self, state: bool) -> None:
        def update_fields() -> None:
            self.password_input.set_problem_style(state)
            self.repeat_password_input.set_problem_style(state)

        aqt.mw.taskman.run_on_main(update_fields)

    def _update_mismatch_feedback(self) -> None:
        password = self.password_input.text()
        repeat_password = self.repeat_password_input.text()
        mismatch = repeat_password != "" and password != repeat_password
        self._set_password_problem_style(mismatch)

    def _on_terms_toggled(self, checked: bool) -> None:
        self._update_signup_button_state()

    def _on_email_changed(self, text: str) -> None:
        self._update_signup_button_state()

    def _on_password_changed(self, text: str) -> None:
        self._set_password_problem_style(False)
        self._update_signup_button_state()

    def _on_password_editing_finished(self) -> None:
        self._update_mismatch_feedback()

    def _on_sign_up(self) -> None:
        terms = self.terms_checkbox.isChecked()

        def task() -> Union[str, int]:
            client = AnkiHubClient()

            if not self.is_code_signup and self.password_input.text() != self.repeat_password_input.text():
                self._set_password_problem_style(True)
                raise ValueError("The passwords do not match")

            if self.is_code_signup:
                return client.ankiweb_request_signup_code(self.email_input.text(), terms).resend_cooldown_secs
            else:
                return client.ankiweb_signup(self.email_input.text(), self.password_input.text(), terms).host_key

        def on_done(fut: Future) -> None:
            try:
                hkey_or_ttl = fut.result()
                kwargs: dict[str, Any] = dict(email=self.email_input.text(), dialog=self._dialog)
                if self.is_code_signup:
                    kwargs["remaining_seconds"] = hkey_or_ttl
                    self._dialog.replace_widget(SignupCodeVerificationWidget(**kwargs))
                else:
                    kwargs["host_key"] = hkey_or_ttl
                    self._dialog.replace_widget(SignupEmailVerificationWidget(**kwargs))
            except Exception as exc:
                if "An account with this email already exists" in str(exc):
                    self._dialog.replace_widget(SignupErrorWidget(str(exc), self._dialog, self.is_code_signup))
                else:
                    self.form_widget.error_label.set_exception(exc)

        run_with_progress(
            dialog=self._dialog, heading=self.title, status="Creating account", task=task, on_done=on_done
        )


class SignupWithPasswordWidget(BaseSignupFirstPageWidget):
    def __init__(self, dialog: AnkiwebDialog):
        super().__init__(is_code_signup=False, dialog=dialog)


class SignupWithCodeWidget(BaseSignupFirstPageWidget):
    def __init__(self, dialog: AnkiwebDialog):
        super().__init__(is_code_signup=True, dialog=dialog)


class AnkiwebLoginDialog(AnkiwebDialog):
    def __init__(self, on_success: Callable[[], None] = lambda: None, parent: QWidget | None = None):
        super().__init__(initial_widget=LoginWithCodeWidget(self), on_success=on_success, parent=parent)


class AnkiwebSignupDialog(AnkiwebDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(initial_widget=SignupWithCodeWidget(self), parent=parent)


def patched_sync_login(mw: aqt.main.AnkiQt, on_success: Callable[[], None], *args: Any, **kwargs: Any) -> None:
    dialog = AnkiwebLoginDialog(on_success=on_success, parent=mw)
    dialog.setModal(True)
    dialog.show()


_original_sync_login = aqt.sync.sync_login
_patched_sync_login = wrap(  # type: ignore
    _original_sync_login,
    patched_sync_login,
    "around",
)


def _patch_or_revert() -> None:
    if config.get_feature_flags().get("ankiweb_magic_code_login", False) and sys.version_info >= (3, 10):
        func = _patched_sync_login
    else:
        if getattr(aqt.sync, "sync_login", None) != _patched_sync_login:
            # No revert required; avoid potentially overwriting other add-ons' patches
            LOGGER.info("Skipped AnkiWeb sync dialog patch.")
            return
        func = _original_sync_login

    try:
        aqt.sync.sync_login = func
        aqt.main.sync_login = func
        aqt.preferences.sync_login = func
    except Exception:
        LOGGER.exception("Failed to set up or revert AnkiWeb sync dialog patch")


def setup_sync_dialog_patch() -> None:
    _patch_or_revert()
    add_user_state_refreshed_callback(_patch_or_revert)
