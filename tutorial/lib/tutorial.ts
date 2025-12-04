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
    const body = `
<h2>📚 First time with Anki?</h2>
<p>Find your way in the app with this onboarding tour.</p>
    `;
    const footer = `<button class="ah-button ah-secondary-button" onclick="destroyAnkiHubTutorialModal()">Close</button><button class="ah-button ah-primary-button" onclick="pycmd('ankihub_start_onboarding')">Take tour</button>`;

    const modal = new Modal({
        body,
        footer,
        showArrow: false,
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
    showArrow?: boolean,
    blockTargetClick?: boolean,
    backdrop?: boolean,
};

export function showTutorialModal({
    body,
    currentStep,
    stepCount,
    target,
    primaryButton = { show: true, label: "Next" },
    showArrow = true,
    blockTargetClick = false,
    backdrop = true,
}: ShowModalArgs) {
    destroyActiveTutorialModal();
    let footer = `<span>${currentStep} of ${stepCount}</span>`;
    if (primaryButton.show) {
        footer += `<button class="ah-button ah-primary-button" onclick="pycmd('ankihub_tutorial_primary_button_clicked')">${primaryButton.label}</button>`;
    }
    const modal = new Modal({
        body,
        footer,
        target,
        showArrow,
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
    var modal = new Modal({
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
    var modal = new Modal({ body: "", footer: "" });
    modal.show();
    activeModal = modal;
}

