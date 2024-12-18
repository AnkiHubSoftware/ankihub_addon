function addTooltip(button, tooltipText) {
    const tooltip = document.createElement("div");
    tooltip.classList.add("ankihub-tooltip");
    tooltip.innerHTML = tooltipText;

    const tooltipArrow = document.createElement("div");
    tooltipArrow.classList.add("ankihub-tooltip-arrow");
    tooltip.appendChild(tooltipArrow);

    setTooltipAndTooltipArrowStyles(tooltip, tooltipArrow);

    button.addEventListener("mouseover", () => {
        if (button.hasAttribute("disabled")) {
            return;
        }

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
        tooltip.style.left = `${buttonRect.left - tooltipRect.width - 10}px`;
        tooltip.style.visibility = 'visible';
    });

    button.addEventListener("mouseout", function () {
        tooltip.style.visibility = 'hidden';
    });

    // Create a container that won't affect document flow
    const container = document.createElement("div");
    container.style.position = "fixed";
    container.style.pointerEvents = "none";
    container.style.top = "0";
    container.style.left = "0";
    container.style.width = "0";
    container.style.height = "0";
    container.style.overflow = "visible";
    container.style.zIndex = "99999";
    container.style.pointerEvents = "none";
    container.appendChild(tooltip);
    document.body.appendChild(container);
}

function setTooltipAndTooltipArrowStyles(tooltip, tooltipArrow) {
    tooltip.style.position = "absolute";
    tooltip.style.zIndex = "1000";
    tooltip.style.fontSize = "medium";
    tooltip.style.borderRadius = "5px";
    tooltip.style.textAlign = "center";
    tooltip.style.padding = "10px";
    tooltip.style.visibility = "hidden";
    tooltip.style.whiteSpace = "nowrap";

    tooltipArrow.style.position = "absolute";
    tooltipArrow.style.top = "50%";
    tooltipArrow.style.right = "-6px";
    tooltipArrow.style.marginTop = "-4px";
    tooltipArrow.style.width = "0";
    tooltipArrow.style.height = "0";
    tooltipArrow.style.borderLeft = "6px solid";
    tooltipArrow.style.borderTop = "6px solid transparent";
    tooltipArrow.style.borderBottom = "6px solid transparent";

    ensureTooltipStyles();
}

// Create and add styles only once
const tooltipStyleId = 'ankihub-tooltip-styles';
function ensureTooltipStyles() {
    if (!document.getElementById(tooltipStyleId)) {
        const style = document.createElement("style");
        style.id = tooltipStyleId;
        style.innerHTML = `
            :root {
                --neutral-200: #e5e5e5;
                --neutral-800: #1f2937;
            }
            .ankihub-tooltip {
                background-color: var(--neutral-800);
                color: white;
            }
            .night-mode .ankihub-tooltip,
            .dark .ankihub-tooltip {
                background-color: var(--neutral-200);
                color: black;
            }
            .ankihub-tooltip-arrow {
                border-color: var(--neutral-800);
                color: var(--neutral-800);
            }
            .night-mode .ankihub-tooltip-arrow,
            .dark .ankihub-tooltip-arrow {
                border-color: var(--neutral-200);
                color: var(--neutral-200);
            }
        `;
        document.head.appendChild(style);
    }
}
