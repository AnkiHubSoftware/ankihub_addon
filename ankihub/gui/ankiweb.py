from __future__ import annotations

import os
import time
from concurrent.futures import Future
from enum import Enum
from typing import Callable, NoReturn

import aqt
from aqt.qt import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QIntValidator,
    QLabel,
    QLineEdit,
    QPushButton,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
    qconnect,
    sip,
)
from aqt.utils import openLink, tooltip

from ..settings import config
from .utils import is_email, warning_icon

ANKIWEB_RESET_LINK = "https://ankiweb.net/account/reset-password"
ANKIWEB_TERMS_LINK = "https://ankiweb.net/account/terms"
EMAIL_INSTRUCTIONS = (
    "Didn't receive an email?<ul><li>Check your spam folder. "
    "Emails can end up there.</li><li>Resend the email when the countdown ends.</li></ul>"
)


class AnkiwebLinkIds(Enum):
    LOGIN_CODE = "#sign-in-code"
    LOGIN_PASSWORD = "#sign-in-password"
    SIGNUP_CODE = "#sign-up-code"
    SIGNUP_PASSWORD = "#sign-up-password"


def simulate_existing_account() -> bool:
    return os.environ.get("ANKIWEB_SIMULATE_EXISTING_ACCOUNT") == "true"


def simulate_expired_code() -> bool:
    return os.environ.get("ANKIWEB_SIMULATE_EXPIRED_CODE") == "true"


def simulate_general_error() -> bool:
    return os.environ.get("ANKIWEB_SIMULATE_GENERAL_ERROR") == "true"


def assert_exhaustive(arg: NoReturn) -> NoReturn:
    raise Exception(f"unexpected arg received: {type(arg)} {arg}")


def widget_for_link(link: AnkiwebLinkIds) -> type[BaseAnkiwebWidget]:
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


def destroy_timer(timer: QTimer | None) -> None:
    if timer and not sip.isdeleted(timer):
        timer.stop()
        timer.deleteLater()


def timer_is_active(timer: Countdown | None) -> bool:
    return timer and not sip.isdeleted(timer) and timer.isActive() and timer.remaining_seconds > 0


class Countdown(QTimer):
    def __init__(self, callback: Callable[[int], None], seconds: int = 5, parent: QWidget | None = None):
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
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        font = self.font()
        font.setBold(True)
        font.setPointSize(20)
        self.setFont(font)


class Button(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setFixedWidth(125)


class CancelButton(Button):
    def __init__(self, dialog: AnkiwebDialog, parent: QWidget | None = None):
        super().__init__("Cancel", parent)
        qconnect(self.clicked, lambda: dialog.close())


class LabelWithLink(QLabel):
    def __init__(self, text: str, dialog: AnkiwebDialog, parent: QWidget | None = None):
        self._dialog = dialog
        super().__init__(text, parent)
        qconnect(self.linkActivated, self._on_link_activated)

    def _on_link_activated(self, link: str) -> None:
        if link in (link.value for link in AnkiwebLinkIds):
            widget_type = widget_for_link(AnkiwebLinkIds(link))
            widget = widget_type(self._dialog)
            self._dialog.replace_widget(widget)
        else:
            openLink(link)


class ErrorLabel(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setVisible(False)
        hbox = QHBoxLayout()
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(1)
        self.status = status = QLabel("")
        status.setTextFormat(Qt.TextFormat.RichText)
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        error_icon = warning_icon()
        icon_label = QLabel()
        icon_label.setPixmap(error_icon.pixmap(16, 16))
        hbox.addStretch()
        hbox.addWidget(icon_label)
        hbox.addWidget(status)
        hbox.addStretch()
        hbox.setContentsMargins(0, 0, 0, 0)
        self.setLayout(hbox)

    def set_error(self, text: str) -> None:
        self.setVisible(bool(text))
        self.status.setText(text)


class BaseInput(QLineEdit):
    def is_initial_text_valid(self) -> bool:
        """Used by InputWithButtonHbox to determine if the associated button should be enabled by default."""
        return False


class PasswordInput(BaseInput):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)


class CodeInput(BaseInput):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        code_validator = QIntValidator(100000, 999999)
        self.setValidator(code_validator)
        self.setStyleSheet("""QLineEdit {letter-spacing: 2px}""")


class EmailInput(BaseInput):
    def __init__(self, text: str = "", parent: QWidget | None = None):
        ankihub_email = config.user()
        if ankihub_email and is_email(ankihub_email) and not text:
            text = ankihub_email
        super().__init__(text, parent)

    def is_initial_text_valid(self):
        return is_email(self.text())


FormRow = tuple[str, QWidget] | QWidget


class FormWidget(QGroupBox):
    def __init__(self, description: str, rows: list[FormRow], dialog: AnkiwebDialog):
        self._dialog = dialog
        super().__init__()
        self._setup_ui(description, rows)

    def _setup_ui(self, description: str, rows: list[FormRow]) -> None:
        self.error_label = error_label = ErrorLabel(self)
        description = LabelWithLink(description, self._dialog)
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form_layout = QFormLayout()
        form_layout.addRow(error_label)
        form_layout.addRow(description)
        for row in rows:
            if isinstance(row, tuple):
                form_layout.addRow(*row)
            else:
                form_layout.addRow(row)
        form_layout.setVerticalSpacing(10)
        form_layout.setHorizontalSpacing(5)
        self.setLayout(form_layout)


class InputWithButtonHbox(QHBoxLayout):
    def __init__(self, input_widget: BaseInput, button_label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.button = button = Button(button_label)
        button.setEnabled(input_widget.is_initial_text_valid())
        self.setSpacing(5)
        self.addWidget(input_widget)
        self.addWidget(button)


class AnkiwebDialog(QDialog):
    def __init__(self, initial_widget: BaseAnkiwebWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self._setup_ui(initial_widget)

    def _setup_ui(self, initial_widget: BaseAnkiwebWidget) -> None:
        self._widget = initial_widget
        vbox = QVBoxLayout()
        vbox.addWidget(initial_widget)
        vbox.setContentsMargins(0, 20, 0, 0)
        self.setLayout(vbox)
        self.setFixedWidth(525)
        self.setMaximumHeight(450)
        self.setWindowTitle(initial_widget.title)

    def replace_widget(self, widget: BaseAnkiwebWidget) -> None:
        self.layout().replaceWidget(self._widget, widget)
        destroy_timer(self._widget._timer)
        self._widget.deleteLater()
        self._widget = widget
        self.setWindowTitle(widget.title)
        self.adjustSize()


class BaseAnkiwebWidget(QWidget):
    title: str

    def __init__(
        self,
        heading: str,
        main_description: str,
        form_widget: FormWidget,
        bottom_label: str,
        dialog: AnkiwebDialog,
        extr_bottom_button: QPushButton | None = None,
    ):
        self._dialog = dialog
        self._timer: Countdown | None = None
        super().__init__()
        self._setup_ui(heading, main_description, form_widget, bottom_label, extr_bottom_button)

    def _setup_ui(
        self,
        heading: str,
        main_description: str,
        form_widget: FormWidget,
        bottom_label: str,
        extr_bottom_button: QPushButton | None,
    ) -> None:
        vbox = QVBoxLayout()

        if heading:
            heading = Heading(heading)
            vbox.addWidget(heading)

        if main_description:
            description_label = QLabel(main_description)
            description_label.setWordWrap(True)
            vbox.addWidget(description_label)

        self.form_widget = form_widget
        vbox.addWidget(form_widget)

        self.status_label = status_label = QLabel("")
        status_label.setWordWrap(True)
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(status_label)

        bottom_hbox = QHBoxLayout()
        if bottom_label:
            signup_link = LabelWithLink(bottom_label, self._dialog)
            bottom_hbox.addWidget(signup_link)

        cancel_button = CancelButton(self._dialog)
        buttons_hbox = QHBoxLayout()
        buttons_hbox.setAlignment(Qt.AlignmentFlag.AlignRight)
        buttons_hbox.addWidget(cancel_button)
        if extr_bottom_button:
            buttons_hbox.addWidget(extr_bottom_button)
        bottom_hbox.addLayout(buttons_hbox)
        vbox.addLayout(bottom_hbox)

        vbox.setContentsMargins(20, 0, 20, 20)
        self.setLayout(vbox)

    def init_timer(self, on_timeout: Callable[[int], None]) -> None:
        destroy_timer(self._timer)
        self._timer = Countdown(on_timeout, parent=self)


class BaseLoginWidget(BaseAnkiwebWidget):
    title = "Sign into AnkiWeb"

    def __init__(
        self,
        main_description: str,
        form_widget: FormWidget,
        bottom_label: str,
        dialog: AnkiwebDialog,
        extr_bottom_button: QPushButton | None = None,
    ):
        super().__init__(self.title, main_description, form_widget, bottom_label, dialog, extr_bottom_button)


class LoginWithCodeWidget(BaseLoginWidget):
    def __init__(self, dialog: AnkiwebDialog):
        self._dialog = dialog
        super().__init__(
            main_description="<b>Sign in to your account without having to type your password</b>.<br>"
            " A free account is required to keep your collection synchronized.",
            form_widget=self._create_form_widget(),
            bottom_label=f"<a href='{AnkiwebLinkIds.SIGNUP_CODE.value}'>Sign up for a new account</a>",
            dialog=dialog,
        )

    def _create_form_widget(self) -> FormWidget:
        self.email_input = email_input = EmailInput()
        qconnect(email_input.textChanged, self._on_email_changed)
        self.email_box = email_box = InputWithButtonHbox(email_input, "Get code")
        qconnect(email_box.button.clicked, self._on_get_code)

        self.code_input = code_input = CodeInput()
        qconnect(code_input.textChanged, self._on_code_changed)
        self.code_box = code_box = InputWithButtonHbox(code_input, "Sign in")
        qconnect(code_box.button.clicked, self._on_sign_in)

        form_widget = FormWidget(
            description="We'll email you a magic code for a password-free sign in."
            f"<br>Or you can <a href='{AnkiwebLinkIds.LOGIN_PASSWORD.value}'>sign in with password instead</a>",
            rows=[("Email", email_box), ("Code", code_box)],
            dialog=self._dialog,
        )
        return form_widget

    def _on_email_changed(self, text: str) -> None:
        self.email_box.button.setEnabled(is_email(text))

    def _on_code_changed(self, text: str) -> None:
        self.code_box.button.setEnabled(bool(text) and is_email(self.email_input.text()))

    def _on_get_code(self) -> None:
        def on_timeout(remaining_secs: int) -> None:
            email = self.email_input.text()
            resend_available_status = (
                f"<br>Resend available in {remaining_secs}s" if remaining_secs else "Resend available"
            )
            self.status_label.setText(
                f"If {email} belongs to an existing account, you will receive a message in your inbox.<br>"
                + resend_available_status
            )
            if not remaining_secs:
                self.email_box.button.setEnabled(True)

        self.init_timer(on_timeout)
        self._timer.start()
        self.email_box.button.setEnabled(False)
        self.form_widget.error_label.set_error("")

    def _on_sign_in(self) -> None:
        def task() -> None:
            time.sleep(1)
            if simulate_expired_code():
                raise Exception("This code has expired. Request another.")

        def on_done(fut: Future) -> None:
            try:
                fut.result()
                self._dialog.close()
                tooltip("Sign-in successful!", parent=aqt.mw)
            except Exception as exc:
                self.form_widget.error_label.set_error(str(exc))
                self.code_input.clear()

        aqt.mw.taskman.with_progress(task, on_done, parent=self, label="Signing you in", immediate=True)


class LoginWithPasswordWidget(BaseLoginWidget):
    def __init__(self, dialog: AnkiwebDialog):
        self._dialog = dialog
        super().__init__(
            main_description="<b>Sign in with your email and password.</b><br>"
            "A free account is required to keep your collection synchronized.",
            form_widget=self._create_form_widget(),
            bottom_label=f"<a href='{AnkiwebLinkIds.SIGNUP_CODE.value}'>Sign up for a new account</a>",
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
            f"<a href='{ANKIWEB_RESET_LINK}'>Forgot password?</a>",
            self._dialog,
        )
        form_widget = FormWidget(
            description="We can email you a magic code for a password-free sign in.<br>"
            f"<a href='{AnkiwebLinkIds.LOGIN_CODE.value}'>Get a code instead</a>",
            rows=[("Email", email_input), ("Password", password_box), forgot_password_label],
            dialog=self._dialog,
        )
        return form_widget

    def _on_email_changed(self, text: str) -> None:
        self.password_box.button.setEnabled(is_email(text) and bool(self.password_input.text()))

    def _on_password_changed(self, text: str) -> None:
        self.password_box.button.setEnabled(bool(text) and is_email(self.email_input.text()))

    def _on_sign_in(self) -> None:
        def task() -> None:
            time.sleep(1)
            if simulate_general_error():
                raise Exception("Inserted email and/or password are incorrect.")

        def on_done(fut: Future) -> None:
            try:
                fut.result()
                self._dialog.close()
                tooltip("Sign-in successful!", parent=aqt.mw)
            except Exception as exc:
                self.form_widget.error_label.set_error(str(exc))

        aqt.mw.taskman.with_progress(task, on_done, parent=self, label="Signing you in", immediate=True)


class BaseSignupWidget(BaseAnkiwebWidget):
    title = "Create an AnkiWeb account"

    def __init__(
        self,
        heading: str,
        main_description: str,
        form_widget: FormWidget,
        bottom_label: str,
        dialog: AnkiwebDialog,
        extr_bottom_button: QPushButton | None = None,
    ):
        super().__init__(heading, main_description, form_widget, bottom_label, dialog, extr_bottom_button)


class SignupErrorWidget(BaseSignupWidget):
    def __init__(self, error: str, dialog: AnkiwebDialog):
        self._dialog = dialog
        super().__init__("Create an AnkiWeb account", "", self._create_form_widget(error), "", dialog)

    def _create_form_widget(self, error: str) -> FormWidget:
        form_widget = FormWidget(
            description="We can email you a magic code for password-free sign-in.<br>"
            f"<a href='{AnkiwebLinkIds.LOGIN_CODE.value}'>Sign in with code.</a><br><br>"
            f"Alternatively, you can <a href='{ANKIWEB_RESET_LINK}'>reset your password</a>, if you forgot it.",
            rows=[],
            dialog=self._dialog,
        )
        form_widget.error_label.set_error(error)

        return form_widget


class SignupEmailVerificationWidget(BaseSignupWidget):
    def __init__(self, email: str, dialog: AnkiwebDialog):
        self.email = email
        self._dialog = dialog
        login_button = Button("Sign in")
        qconnect(login_button.clicked, self._on_login)
        super().__init__("Create an AnkiWeb account", "", self._create_form_widget(), "", dialog, login_button)
        self._start_timer()

    def _create_form_widget(self) -> FormWidget:
        self.description_label = description_label = QLabel("")
        description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.resend_button = resend_button = QPushButton("Resend verification email")
        resend_button.setEnabled(False)
        qconnect(resend_button.clicked, self._on_resend)
        instructions_label = QLabel(EMAIL_INSTRUCTIONS)
        instructions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form_widget = FormWidget(
            description="",
            rows=[
                description_label,
                resend_button,
                instructions_label,
            ],
            dialog=self._dialog,
        )

        return form_widget

    def _start_timer(self) -> None:
        def on_timeout(remaining_secs: int) -> None:
            resend_available_status = (
                f"Resend available in {remaining_secs}s" if remaining_secs else "Resend available."
            )
            self.description_label.setText(
                f"✉️ If {self.email} exists, we sent a verification link to its inbox.<br>" + resend_available_status
            )
            if not remaining_secs:
                self.resend_button.setEnabled(True)

        self.init_timer(on_timeout)
        self._timer.start()

    def _on_resend(self) -> None:
        self._start_timer()

    def _on_login(self) -> None:
        self._dialog.replace_widget(LoginWithPasswordWidget(self._dialog))


class SignupCodeVerificationWidget(BaseSignupWidget):
    def __init__(self, email: str, dialog: AnkiwebDialog, error: str = ""):
        self.email = email
        self._dialog = dialog
        self._is_retry = bool(error)
        super().__init__(
            heading="Email confirmation",
            main_description="",
            form_widget=self._create_form_widget(error),
            bottom_label=f"<a href='{AnkiwebLinkIds.LOGIN_CODE.value}'>Have an account? Sign in.</a>",
            dialog=dialog,
        )
        if not self._is_retry:
            self._start_timer()
        else:
            self._update_code_button_state()

    def _create_form_widget(self, error: str = "") -> FormWidget:
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
            rows = [("Email", email_box), ("Code", code_box)]
        else:
            instructions_label = QLabel(EMAIL_INSTRUCTIONS)
            instructions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            description = (
                f"Insert the verification code we've sent to {self.email}.<br>"
                f"If the email is not correct, <a href='{AnkiwebLinkIds.SIGNUP_CODE.value}'>please change it</a>."
            )
            rows = [("Code", code_box), instructions_label]
        form_widget = FormWidget(
            description=description,
            rows=rows,
            dialog=self._dialog,
        )
        form_widget.error_label.set_error(error)

        return form_widget

    def _is_resend(self) -> bool:
        return not timer_is_active(self._timer) and not bool(self.code_input.text())

    def _update_code_button_state(self) -> None:
        button = self.code_box.button
        filled = bool(self.code_input.text())
        enabled = filled or (not self._is_retry and not timer_is_active(self._timer))
        if not self._is_retry:
            if self._is_resend():
                button.setText("Resend code")
            else:
                button.setText("Verify code")
        button.setEnabled(enabled)

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

        self.init_timer(on_timeout)
        self._timer.start()
        self._update_code_button_state()

    def _on_code_changed(self, text: str) -> None:
        self._update_code_button_state()

    def _on_email_changed(self, text: str) -> None:
        self.email_box.button.setEnabled(is_email(text))

    def _on_get_code(self) -> None:
        self._start_timer()

    def _on_verify_or_resend(self) -> None:
        if self._is_resend():
            self._start_timer()
            return

        def task() -> None:
            time.sleep(1)
            if simulate_expired_code():
                raise Exception("This code has expired. Request another.")

        def on_done(fut: Future) -> None:
            try:
                fut.result()
                self._dialog.close()
                tooltip("Sign-in successful!", parent=aqt.mw)
            except Exception as exc:
                self._dialog.replace_widget(SignupCodeVerificationWidget(self.email, self._dialog, str(exc)))

        aqt.mw.taskman.with_progress(task, on_done, parent=self, label="Creating account", immediate=True)


class BaseSignupFirstPageWidget(BaseSignupWidget):
    def __init__(self, is_code_signup: bool, dialog: AnkiwebDialog):
        self.is_code_signup = is_code_signup
        self._dialog = dialog
        super().__init__(
            heading="Create an AnkiWeb account",
            main_description="<b>Sign up to gain access to Anki's web companion and cloud storage.</b><br>"
            "This is a free account and it can keep your flashcard data in sync across your devices and the cloud.",
            form_widget=self._create_form_widget(),
            bottom_label=f"<a href='{AnkiwebLinkIds.LOGIN_CODE.value}'>Have an account? Sign in.</a>",
            dialog=dialog,
        )

    def _create_form_widget(self) -> FormWidget:
        terms_hbox = QHBoxLayout()
        terms_hbox.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.terms_checkbox = terms_checkbox = QCheckBox()
        qconnect(terms_checkbox.toggled, self._on_terms_toggled)
        terms_label = LabelWithLink(
            f"I agree to AnkiWeb's <a href='{ANKIWEB_TERMS_LINK}'>Terms & Conditions</a>.", self._dialog
        )
        terms_hbox.addWidget(terms_checkbox)
        terms_hbox.addWidget(terms_label)
        if self.is_code_signup:
            self.email_input = email_input = EmailInput()
            qconnect(email_input.textChanged, self._on_email_changed)
            self.email_box = InputWithButtonHbox(email_input, "Sign up")
            qconnect(self.email_box.button.clicked, self._on_sign_up)
            rows = [terms_hbox, ("Email", self.email_box)]
        else:
            self.email_input = email_input = EmailInput()
            qconnect(email_input.textChanged, self._on_email_changed)
            self.password_input = password_input = PasswordInput()
            qconnect(password_input.textChanged, self._on_password_changed)
            self.repeat_password_input = repeat_password_input = PasswordInput()
            qconnect(repeat_password_input.textChanged, self._on_repeat_password_changed)
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
                f"Or you can <a href='{AnkiwebLinkIds.SIGNUP_PASSWORD.value}'>sign up with password instead</a>."
            )
            if self.is_code_signup
            else (
                "We can email you a magic code for a password-free sign up.<br>"
                f"<a href='{AnkiwebLinkIds.SIGNUP_CODE.value}'>Sign up with code.</a>"
            )
        )
        form_widget = FormWidget(
            form_description,
            rows=rows,
            dialog=self._dialog,
        )
        self._update_signup_button_state()
        return form_widget

    def _update_signup_button_state(self) -> bool:
        enabled = self.terms_checkbox.isChecked() and is_email(self.email_input.text())
        button = self.email_box.button if self.is_code_signup else self.repeat_password_box.button
        if not self.is_code_signup:
            password = self.password_input.text()
            repeat_password = self.repeat_password_input.text()
            enabled &= bool(password) and password == repeat_password

        button.setEnabled(enabled)

    def _on_terms_toggled(self, checked: bool) -> None:
        self._update_signup_button_state()

    def _on_email_changed(self, text: str) -> None:
        self._update_signup_button_state()

    def _on_password_changed(self, text: str) -> None:
        self._update_signup_button_state()

    def _on_repeat_password_changed(self, text: str) -> None:
        self._update_signup_button_state()

    def _on_sign_up(self) -> None:
        def task() -> None:
            time.sleep(1)
            if simulate_existing_account():
                raise Exception("An account with that email already exists.")
            elif simulate_general_error() and not simulate_expired_code():
                raise Exception("Some unknown error")

        def on_done(fut: Future) -> None:
            try:
                fut.result()
                args = (self.email_input.text(), self._dialog)
                if self.is_code_signup:
                    widget_class = SignupCodeVerificationWidget
                else:
                    widget_class = SignupEmailVerificationWidget
                self._dialog.replace_widget(widget_class(*args))
            except Exception as exc:
                if simulate_existing_account():
                    self._dialog.replace_widget(SignupErrorWidget(str(exc), self._dialog))
                else:
                    self.form_widget.error_label.set_error(str(exc))

        aqt.mw.taskman.with_progress(task, on_done, parent=self, label="Creating account", immediate=True)


class SignupWithPasswordWidget(BaseSignupFirstPageWidget):
    def __init__(self, dialog: AnkiwebDialog):
        super().__init__(is_code_signup=False, dialog=dialog)


class SignupWithCodeWidget(BaseSignupFirstPageWidget):
    def __init__(self, dialog: AnkiwebDialog):
        super().__init__(is_code_signup=True, dialog=dialog)


class AnkiwebLoginDialog(AnkiwebDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(initial_widget=LoginWithCodeWidget(self), parent=parent)


class AnkiwebSignupDialog(AnkiwebDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(initial_widget=SignupWithCodeWidget(self), parent=parent)
