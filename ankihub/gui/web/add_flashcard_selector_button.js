if (!document.getElementById("{{ FLASHCARD_SELECTOR_OPEN_BUTTON_ID }}")) {
    const button = document.createElement("button");
    button.id = "{{ FLASHCARD_SELECTOR_OPEN_BUTTON_ID }}";

    button.style.position = "absolute";
    button.style.bottom = "0px";
    button.style.right = "25px";
    button.style.zIndex = "1000";
    button.style.width = "50px";
    button.style.height = "50px";
    button.style.boxSizing = "content-box";
    button.style.padding = "8px 10px 8px 10px";
    button.style.margin = "0px 4px 0px 4px";
    button.style.backgroundImage = "url('/_robot_icon.svg')";
    button.style.backgroundSize = "cover";
    button.style.borderRadius = "100%";
    button.style.border = "none";
    button.style.outline = "none";
    button.style.cursor = "pointer";

    button.addEventListener("click", function () {
        pycmd("{{ FLASHCARD_SELECTOR_OPEN_PYCMD }}");
    });

    document.body.appendChild(button);

    const tooltip = document.createElement("div");
    tooltip.innerHTML = "Find relevant<br>flashcards with AI";

    const tooltipBackgroundColor = "#d1d5db";
    const tooltipColor = "black";

    tooltip.style.position = "absolute";
    tooltip.style.bottom = "2px";
    tooltip.style.right = "110px";
    tooltip.style.zIndex = "1000";
    tooltip.style.backgroundColor = tooltipBackgroundColor;
    tooltip.style.fontSize = "medium";
    tooltip.style.color = tooltipColor;
    tooltip.style.borderRadius = "5px";
    tooltip.style.textAlign = "center";
    tooltip.style.padding = "10px";
    tooltip.style.display = "none";

    const tooltipArrow = document.createElement("div");
    tooltipArrow.style.position = "absolute";
    tooltipArrow.style.top = "50%";
    tooltipArrow.style.right = "-6px";
    tooltipArrow.style.marginTop = "-4px";
    tooltipArrow.style.width = "0";
    tooltipArrow.style.height = "0";
    tooltipArrow.style.borderLeft = `6px solid ${tooltipBackgroundColor}`;
    tooltipArrow.style.borderTop = "6px solid transparent";
    tooltipArrow.style.borderBottom = "6px solid transparent";

    tooltip.appendChild(tooltipArrow);

    button.addEventListener("mouseover", function () {
        tooltip.style.display = "block";
    });

    button.addEventListener("mouseout", function () {
        tooltip.style.display = "none";
    });

    document.body.appendChild(tooltip);
}
