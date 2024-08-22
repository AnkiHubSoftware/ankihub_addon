
if (!document.getElementById("{{ FLASHCARD_SELECTOR_OPEN_BUTTON_ID }}")) {
    const button = document.createElement("button");
    button.id = "{{ FLASHCARD_SELECTOR_OPEN_BUTTON_ID }}";

    button.style = `
        position: absolute;
        bottom: 0px;
        right: 25px;
        z-index: 1000;
        width: 50px;
        height: 50px;
        background-image: url('robot_icon.svg');
        background-size: cover;
        border-radius: 100%;
        border: none;
        outline: none;
        cursor: pointer;
    `

    button.addEventListener("click", function () {
        pycmd("{{ FLASHCARD_SELECTOR_OPEN_PYCMD }}");
    });

    document.body.appendChild(button);

    const tooltip = document.createElement("div");
    tooltip.textContent = "Find relveant flashcards with AI";
    tooltip.style = `
        position: absolute;
        bottom: 0px;
        right: 120px;
        z-index: 1000;
        width: 200px;
        height: 50px;
        background-color: #333;
        color: white;
        border-radius: 5px;
        text-align: center;
        padding: 10px;
        font-size: 12px;
        display: none;
    `

    button.addEventListener("mouseover", function () {
        tooltip.style.display = "block";
    });

    button.addEventListener("mouseout", function () {
        tooltip.style.display = "none";
    });

    document.body.appendChild(tooltip);
}
