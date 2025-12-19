import { bridgeCommand } from "./bridgecommand";
import { Modal } from "./modal";

let activeModal: Modal | null = null;
let targetResizeHandler: (() => void) | null = null;

export function destroyActiveTutorialModal() {
    if (activeModal) {
        activeModal.destroy();
        activeModal = null;
    }
}

export function promptForOnboardingTour() {
    destroyActiveTutorialModal();
    const body = `<h2>📚 First time with Anki?</h2><p>Find your way in the app with this onboarding tour.</p>`;
    const footer = [];
    const secondaryButton = document.createElement("button");
    secondaryButton.textContent = "Close";
    secondaryButton.classList.add("ah-button", "ah-secondary-button");
    secondaryButton.addEventListener("click", destroyActiveTutorialModal);
    footer.push(secondaryButton);
    const primaryButton = document.createElement("button");
    primaryButton.textContent = "Take tour";
    primaryButton.classList.add("ah-button", "ah-secondary-button");
    primaryButton.addEventListener("click", () => bridgeCommand("ankihub_start_onboarding"));
    footer.push(primaryButton);

    const modal = new Modal({
        body,
        footer,
    });
    modal.show();
    activeModal = modal;
}

type ShowModalArgs = {
    body: string,
    currentStep: number,
    stepCount: number,
    target: string,
    primaryButton?: { show: boolean, label: string },
    blockTargetClick?: boolean,
    backdrop?: boolean,
};

export function showTutorialModal({
    body,
    currentStep,
    stepCount,
    target,
    primaryButton = { show: true, label: "Next" },
    blockTargetClick = false,
    backdrop = true,
}: ShowModalArgs) {
    destroyActiveTutorialModal();
    const footer = [];
    const stepSpan = document.createElement("span");
    stepSpan.textContent = `${currentStep} of ${stepCount}`;
    footer.push(stepSpan);
    if (primaryButton.show) {
        const button = document.createElement("button");
        button.textContent = primaryButton.label;
        button.classList.add("ah-button", "ah-primary-button");
        button.addEventListener("click", () => bridgeCommand("ankihub_tutorial_primary_button_clicked"));
        footer.push(button);
    }
    const modal = new Modal({
        body,
        footer,
        target,
        blockTargetClick,
        backdrop,
    });
    modal.show();
    activeModal = modal;
}

type HighlightTargetArgs = {
    target: string | HTMLElement,
    currentStep: number,
    blockTargetClick?: boolean,
};

export function highlightTutorialTarget({
    target,
    currentStep,
    blockTargetClick = false,
}: HighlightTargetArgs) {
    destroyActiveTutorialModal();
    const modal = new Modal({
        body: "",
        footer: "",
        target,
        blockTargetClick,
    });
    modal.show();
    activeModal = modal;
    if (targetResizeHandler) {
        window.removeEventListener(
            "resize",
            targetResizeHandler
        );
        targetResizeHandler = null;
    }
    targetResizeHandler = () => {
        if (!modal.targetElement) return;
        const { top, left, width, height } =
            modal.targetElement!.getBoundingClientRect();
        bridgeCommand(
            `ankihub_tutorial_target_resize:${currentStep}:${top}:${left}:${width}:${height}`
        );
    };
    window.addEventListener(
        "resize",
        targetResizeHandler
    );
    targetResizeHandler();
}

type PositionTargetArgs = {
    top: number,
    left: number,
    width: number,
    height: number,
};

export function positionTutorialTarget({ top, left, width, height }: PositionTargetArgs) {
    if (activeModal) {
        activeModal.setModalPosition(top, left, width, height);
    }
}

export function addTutorialBackdrop() {
    destroyActiveTutorialModal();
    const modal = new Modal({ body: "", footer: "" });
    modal.show();
    activeModal = modal;
}

