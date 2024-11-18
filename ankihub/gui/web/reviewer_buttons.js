
class AnkiHubReviewerButtons {
    constructor() {
        this.theme = "{{ THEME }}";

        this.buttonsData = [
            { name: "fa4", iconPath: "/_fa4_icon.svg", active: false, tooltip: null },
            { name: "b&b", iconPath: "/_b&b_icon.svg", active: false, tooltip: null },
            {
                name: "chatbot", iconPath: "/_chatbot_icon.svg", active: false,
                tooltip: "Learn more about this flashcard topic<br>or explore related cards."
            },
        ]

        this.setupButtons();
    }

    setupButtons() {
        const buttonContainer = document.createElement("div");
        this.setButtonContainerStyle(buttonContainer);

        this.buttonsData.forEach((buttonData, buttonIdx) => {
            const buttonElement = document.createElement("button");
            buttonElement.id = `ankihub-${buttonData.name}-button`;
            this.setButtonStyle(buttonElement, buttonData.iconPath, buttonIdx === 0);

            if (buttonData.tooltip) {
                this.addTooltip(buttonElement, buttonData.tooltip);
            }

            buttonElement.onclick = () => {
                // Deactivate all other buttons when a button is activated
                if (!buttonData.active) {
                    for (const otherButtonData of this.buttonsData) {
                        if (otherButtonData.active) {
                            const otherButtonElement = this.getButtonElement(otherButtonData.name);
                            this.setButtonState(otherButtonData, otherButtonElement, false);
                        }
                    }
                }

                // Toggle current button
                this.setButtonState(buttonData, buttonElement, !buttonData.active);
            };

            buttonContainer.appendChild(buttonElement);
        })

        document.body.appendChild(buttonContainer);
    }

    getButtonElement(buttonName) {
        return document.getElementById(`ankihub-${buttonName}-button`);
    }

    setButtonContainerStyle(buttonContainer) {
        buttonContainer.style.position = "fixed";
        buttonContainer.style.bottom = "0px";
        buttonContainer.style.right = "0px";
        buttonContainer.style.zIndex = "9999";
        buttonContainer.style.display = "flex";
        buttonContainer.style.flexDirection = "column";
    }

    setButtonState(buttonData, buttonElement, active) {
        buttonData.active = active;
        buttonElement.style.backgroundColor = active ? "#C7D2FE" : "#ffffff";

        const args = `{"buttonName": "${buttonData.name}", "isActive": "${buttonData.active}"}`
        pycmd(`ankihub_reviewer_button_toggled ${args}`);
    }

    setButtonStyle(button, iconPath, isTopButton) {
        button.style.width = "48px";
        button.style.height = "48px";

        button.style.margin = "0px";
        button.style.borderRadius = isTopButton ? "8px 0px 0px 0px" : "0px";
        button.style.border = "none";
        button.style.borderBottom = "1px solid #C7D2FE";

        button.style.backgroundImage = `url('${iconPath}')`;
        button.style.backgroundSize = "cover";
        button.style.backgroundPosition = "center";
        button.style.backgroundRepeat = "no-repeat";
        button.style.backgroundColor = "#ffffff";

        button.style.cursor = "pointer";
    }

    addTooltip(button, tooltipText) {
        const tooltip = document.createElement("div");
        tooltip.classList.add("ankihub-reviewer-button-tooltip");
        tooltip.innerHTML = tooltipText;

        const tooltipArrow = document.createElement("div");
        tooltipArrow.classList.add("ankihub-reviewer-button-tooltip-arrow");
        tooltip.appendChild(tooltipArrow);

        this.setTooltipAndTooltipArrowStyles(tooltip, tooltipArrow);

        button.addEventListener("mouseover", () => {
            // Get positions and dimensions
            const buttonRect = button.getBoundingClientRect();
            const tooltipRect = tooltip.getBoundingClientRect();

            // Calculate button vertical center
            const buttonCenter = buttonRect.top + (buttonRect.height / 2);

            // Center tooltip using its height
            const tooltipOffset = tooltipRect.height / 2;
            const tooltipTop = buttonCenter - tooltipOffset;

            // Position and show tooltip
            tooltip.style.top = `${tooltipTop}px`;
            tooltip.style.visibility = 'visible';
        });

        button.addEventListener("mouseout", function () {
            tooltip.style.visibility = 'hidden';
        });

        document.body.appendChild(tooltip);
    }

    setTooltipAndTooltipArrowStyles(tooltip, tooltipArrow) {
        tooltip.style.position = "absolute";
        tooltip.style.right = "93px";
        tooltip.style.zIndex = "1000";
        tooltip.style.fontSize = "medium";
        tooltip.style.borderRadius = "5px";
        tooltip.style.textAlign = "center";
        tooltip.style.padding = "10px";
        tooltip.style.visibility = "hidden";

        tooltipArrow.style.position = "absolute";
        tooltipArrow.style.top = "50%";
        tooltipArrow.style.right = "-6px";
        tooltipArrow.style.marginTop = "-4px";
        tooltipArrow.style.width = "0";
        tooltipArrow.style.height = "0";
        tooltipArrow.style.borderLeft = "6px solid";
        tooltipArrow.style.borderTop = "6px solid transparent";
        tooltipArrow.style.borderBottom = "6px solid transparent";

        const style = document.createElement("style");
        style.innerHTML = `
            :root {
                --neutral-200: #e5e5e5;
                --neutral-800: #1f2937;
            }

            .ankihub-reviewer-button-tooltip {
                background-color: var(--neutral-800);
                color: white;
            }

            .night-mode .ankihub-reviewer-button-tooltip {
                background-color: var(--neutral-200);
                color: black;
            }

            .ankihub-reviewer-button-tooltip-arrow {
                border-color: var(--neutral-800);
                color: var(--neutral-800);
            }

            .night-mode .ankihub-reviewer-button-tooltip-arrow {
                border-color: var(--neutral-200);
                color: var(--neutral-200);
            }
        `;
        document.head.appendChild(style);
    }

}

window.ankihubReviewerButtons = new AnkiHubReviewerButtons();
