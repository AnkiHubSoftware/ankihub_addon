function destroyAnkiHubTutorialModal() {
    if (window.ankihubTutorialModal) {
        window.ankihubTutorialModal.destroy();
        window.ankihubTutorialModal = null;
    }
}

function promptForAnkiHubOnboarding() {
    destroyAnkiHubTutorialModal();
    const body = `
<h2>📚 First time with Anki?</h2>
<p>Find your way in the app with this onboarding tour.</p>
    `;
    const footer = `<button class="ah-button ah-secondary-button" onclick="destroyAnkiHubTutorialModal()">Close</button><button class="ah-button ah-primary-button" onclick="pycmd('ankihub_start_onboarding')">Take tour</button>`;

    const modal = new AnkiHubModal({
        body,
        footer,
        showArrow: false,
    });
    modal.show();
    window.ankihubTutorialModal = modal;
}

function showAnkiHubTutorialModal({
    body,
    currentStep,
    stepCount,
    target,
    position = "bottom",
    primaryButton = { show: true, label: "Next" },
    showArrow = true,
    blockTargetClick = false,
    backdrop = true,
}) {
    destroyAnkiHubTutorialModal();
    let footer = `<span>${currentStep} of ${stepCount}</span>`;
    if (primaryButton.show) {
        footer += `<button class="ah-button ah-primary-button" onclick="pycmd('ankihub_tutorial_primary_button_clicked')">${primaryButton.label}</button>`;
    }

    const modal = new AnkiHubModal({
        body,
        footer,
        target,
        position,
        showArrow,
        blockTargetClick,
        backdrop,
    });
    modal.show();
    window.ankihubTutorialModal = modal;
}

function highlightAnkiHubTutorialTarget({
    target,
    currentStep,
    blockTargetClick = false,
}) {
    destroyAnkiHubTutorialModal();
    var modal = new AnkiHubModal({
        body: "",
        footer: "",
        target,
        blockTargetClick,
    });
    modal.show();
    window.ankihubTutorialModal = modal;
    if (window.ankihubTutorialTargetResizeHandler) {
        window.removeEventListener(
            "resize",
            window.ankihubTutorialTargetResizeHandler
        );
        window.ankihubTutorialTargetResizeHandler = null;
    }
    window.ankihubTutorialTargetResizeHandler = () => {
        const { top, left, width } =
            modal.targetElement.getBoundingClientRect();
        pycmd(
            `ankihub_tutorial_target_resize:${currentStep}:${top}:${left}:${width}`
        );
    };
    window.addEventListener(
        "resize",
        window.ankihubTutorialTargetResizeHandler
    );
    window.ankihubTutorialTargetResizeHandler();
}

function positionAnkiHubTutorialTarget({ top, left, transform }) {
    if (window.ankihubTutorialModal) {
        window.ankihubTutorialModal.setModalPosition(top, left, transform);
    }
}

function addAnkiHubTutorialBackdrop() {
    destroyAnkiHubTutorialModal();
    var modal = new AnkiHubModal({ body: "", footer: "" });
    modal.show();
    window.ankihubTutorialModal = modal;
}
