
class AnkiHubReviewerButtons {
    constructor() {
        this.theme = "{{ THEME }}";

        this.colorButtonLight = "#F9FAFB";
        this.colorButtonSelectedLight = "#C7D2FE";
        this.colorButtonBorderLight = "#D1D5DB";

        this.colorButtonDark = "#030712";
        this.colorButtonSelectedDark = "#3730A3";
        this.colorButtonBorderDark = "#4b5563";

        this.buttonsData = [
            {
                name: "b&b",
                iconPath: "/_b&b_icon.svg",
                iconPathDarkTheme: "/_b&b_icon_dark_theme.svg",
                active: false,
                tooltip: "Boards & Beyond",
                resourceCount: Number.parseInt("{{ BOARDS_AND_BEYOND_RESOURCE_COUNT }}"),
            },
            {
                name: "fa4",
                iconPath: "/_fa4_icon.svg",
                iconPathDarkTheme: "/_fa4_icon_dark_theme.svg",
                active: false,
                tooltip: "First Aid Forward",
                resourceCount: Number.parseInt("{{ FIRST_AID_RESOURCE_COUNT }}"),
            },
            {
                name: "chatbot",
                iconPath: "/_chatbot_icon.svg",
                active: false,
                tooltip: "AI Chatbot"
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
            this.setButtonStyle(
                buttonElement,
                (
                    this.theme == "dark" && buttonData.iconPathDarkTheme ?
                        buttonData.iconPathDarkTheme : buttonData.iconPath
                ),
                buttonIdx === 0,
                buttonIdx === this.buttonsData.length - 1
            );

            if (buttonData.tooltip) {
                this.addTooltip(buttonElement, buttonData.tooltip);
            }
            if(buttonData.resourceCount) {
                document.addEventListener("DOMContentLoaded", () => {
                    this.addResourceCountIndicator(buttonElement, buttonData.resourceCount);
                });
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
        this.injectResourceCountIndicatorStylesheet();
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

    setButtonState(buttonData, buttonElement, active, sendToPython = true) {
        buttonData.active = active;
        if (active) {
            buttonElement.style.backgroundColor = this.theme == "light" ? this.colorButtonSelectedLight : this.colorButtonSelectedDark;

        } else {
            buttonElement.style.backgroundColor = this.theme == "light" ? this.colorButtonLight : this.colorButtonDark;
        }

        if(sendToPython) {
            const args = `{"buttonName": "${buttonData.name}", "isActive": "${buttonData.active}"}`;
            pycmd(`ankihub_reviewer_button_toggled ${args}`);
        }
    }

    unselectAllButtons(sendToPython = true) {
        for (const buttonData of this.buttonsData) {
            if (buttonData.active) {
                const buttonElement = this.getButtonElement(buttonData.name);
                this.setButtonState(buttonData, buttonElement, false, sendToPython);
            }
        }
    }

    setButtonStyle(button, iconPath, isTopButton, isBottomButton) {
        button.style.width = "48px";
        button.style.height = "48px";
        button.style.boxSizing = "border-box";
        button.style.padding = "4px";
        button.style.margin = "0px";
        button.style.border = "1px solid";
        button.style.borderColor = this.theme == "light" ? this.colorButtonBorderLight : this.colorButtonBorderDark;
        if (isBottomButton) {
            button.style.borderRadius = "0px 0px 0px 8px";
        } else if (isTopButton) {
            button.style.borderRadius = "8px 0px 0px 0px";
        } else {
            button.style.borderRadius = "0px";
        }
        button.style.backgroundImage = `url('${iconPath}')`;
        button.style.backgroundSize = "cover";
        button.style.backgroundPosition = "center";
        button.style.backgroundRepeat = "no-repeat";
        button.style.backgroundColor = this.theme == "light" ? this.colorButtonLight : this.colorButtonDark;

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
        tooltip.style.right = "60px";
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

    addResourceCountIndicator(button, resourceCount) {
        const indicator = document.createElement("div");
        indicator.classList.add("ankihub-reviewer-button-resource-count");
        indicator.innerHTML = resourceCount;
        this.setResourceCountIndicatorStyles(button, indicator);
        document.body.appendChild(indicator);
    }

    setResourceCountIndicatorStyles(button, indicator) {
        indicator.style.position = "absolute";
        indicator.style.right = "10px";
        indicator.style.zIndex = "999";
        indicator.style.fontSize = "small";
        indicator.style.borderRadius = "5px";
        indicator.style.textAlign = "left";
        indicator.style.fontWeight = "bold";
        indicator.style.padding = "8px";
        indicator.style.height = "12px";
        indicator.style.width = "48px";
        indicator.style.borderRadius = "92px";
        const buttonRect = button.getBoundingClientRect();
        const indicatorTop = buttonRect.top + 5;
        indicator.style.top = `${indicatorTop}px`;
    }

    injectResourceCountIndicatorStylesheet() {
        const style = document.createElement("style");
        style.innerHTML = `
            :root {
                --primary-600: #4F46E5;
                --primary-400: #818CF8;
            }

            .ankihub-reviewer-button-resource-count {
                background-color: var(--primary-600);
                color: white;
            }

            .night-mode .ankihub-reviewer-button-resource-count {
                background-color: var(--primary-400);
                color: black;
            }
        `;
        document.head.appendChild(style);
    }
}

window.ankihubReviewerButtons = new AnkiHubReviewerButtons();
