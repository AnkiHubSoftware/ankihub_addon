function destroyAnkiHubTutorialModal() {
    if (window.ankihubTutorialModal) {
        window.ankihubTutorialModal.destroy();
        window.ankihubTutorialModal = null;
    }
}

function showAnkiHubTutorialModal({
    body,
    currentStep,
    stepCount,
    target,
    position = "bottom",
    primaryButton = { show: true, label: "Next" },
    showArrow = true,
}) {
    destroyAnkiHubTutorialModal();
    let footer = `<span>${currentStep} of ${stepCount}</span>`;
    if (primaryButton.show) {
        footer += `<button class="ah-primary-button" onclick="pycmd('ankihub_tutorial_primary_button_clicked')">${primaryButton.label}</button>`;
    }

    const modal = new AnkiHubModal({
        body,
        footer,
        target,
        position,
        showArrow,
    });
    modal.show();
    window.ankihubTutorialModal = modal;
}

function highlightAnkiHubTutorialTarget({ target, currentStep }) {
    destroyAnkiHubTutorialModal();
    var modal = new AnkiHubModal({ body: "", footer: "", target });
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
