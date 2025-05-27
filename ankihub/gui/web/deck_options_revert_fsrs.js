const theme = "{{ THEME }}"

// Cached reference to the FSRS section element
let fsrsSection = null;


// Update the FSRS parameters textarea from Python side
function updateFsrsParametersTextarea(params) {
    if (!fsrsSection) {
        console.warn("deckOptionsFsrsRevert: FSRS section not initialized");
        return;
    }
    const textarea = fsrsSection.querySelector("textarea");
    if (!textarea) {
        console.warn("deckOptionsFsrsRevert: FSRS textarea not found");
        return;
    }

    const newValue = Array.isArray(params) ? params.join(", ") : (params || "");
    textarea.value = newValue;

    // Trigger events to notify Anki of the change
    ['input', 'change', 'blur'].forEach(eventType => {
        const event = new Event(eventType, { bubbles: true });
        Object.defineProperty(event, 'target', { value: textarea });
        textarea.dispatchEvent(event);
    });
}
window.updateFsrsParametersTextarea = updateFsrsParametersTextarea;


function setupRevertButton(evaluateBtn) {
    const revertBtnId = "revertFsrsParametersBtn";
    evaluateBtn.insertAdjacentHTML(
        "afterend",
        ` <button id="${revertBtnId}" class="${evaluateBtn.className}">
              Revert to previous parameters
          </button>`
    );
    const revertBtn = document.getElementById(revertBtnId);
    revertBtn.disabled = true;

    revertBtn.addEventListener("click", () => {
        pycmd("ankihub_revert_fsrs_parameters");
        revertBtn.disabled = true;
    });

    // Theme-aware styling for disabled state
    const lightBg = "#e6e6e6", lightText = "#6c717e";
    const darkBg = "#505050", darkText = "#CCCCCC";
    const bg = theme === "dark" ? darkBg : lightBg;
    const text = theme === "dark" ? darkText : lightText;

    const style = document.createElement("style");
    style.textContent = `
        #revertFsrsParametersBtn:disabled {
            background-color: ${bg} !important;
            border-color:     ${bg} !important;
            color:            ${text} !important;
            box-shadow:       none !important;
            outline:          none !important;
        }
        #revertFsrsParametersBtn:disabled:focus {
            box-shadow: none !important;
        }
    `;
    document.head.appendChild(style);
}


function setupTextAreaListener(textarea) {
    // Patch the textarea value setter to fire a custom event
    if (!textarea._fsrsRevertPatched) {
        const proto = Object.getPrototypeOf(textarea);
        const desc = Object.getOwnPropertyDescriptor(proto, "value");
        if (desc && typeof desc.set === "function") {
            const newDesc = {
                ...desc,
                set(v) {
                    desc.set.call(this, v);
                    this.dispatchEvent(new CustomEvent("fsrsParamsUpdated", { bubbles: true }));
                }
            };
            Object.defineProperty(textarea, "value", newDesc);
            textarea._fsrsRevertPatched = true;
        }
    }

    const DEBOUNCE_MS = 400;
    let debounceId = null;
    let lastValue = textarea.value;

    function processChange(force = false) {
        const current = textarea.value.trim();
        if (!force && current === lastValue) return;
        lastValue = current;

        const numericList = current
            .split(/[,\s]+/)
            .filter(str => str.length)
            .map(Number)
            .filter(n => !isNaN(n));

        const payload = JSON.stringify({ fsrs_parameters: numericList });
        pycmd(`ankihub_fsrs_parameters_changed ${payload}`);
    }

    const onChange = () => {
        if (debounceId) clearTimeout(debounceId);
        debounceId = setTimeout(processChange, DEBOUNCE_MS);
    };

    textarea.addEventListener("fsrsParamsUpdated", onChange);
    textarea.addEventListener("input", onChange);

    processChange(true); // Initial sync
}


function initializeFsrsRevertUI() {
    const headers = Array.from(document.querySelectorAll(".setting-title"));
    const fsrsHeader = headers.find(el =>
        el.textContent.includes("FSRS") && el.textContent.trim() !== "FSRS"
    );
    if (!fsrsHeader) {
        console.warn("deckOptionsFsrsRevert: FSRS section not found");
        return;
    }

    const section = fsrsHeader.parentElement;
    const buttons = section.querySelectorAll("button.btn.btn-primary");
    const textarea = section.querySelector("textarea");
    if (buttons.length < 2 || !textarea) {
        console.warn("deckOptionsFsrsRevert: FSRS buttons or textarea not found");
        return;
    }

    fsrsSection = section;

    const evaluateBtn = buttons[1];
    setupRevertButton(evaluateBtn);
    setupTextAreaListener(textarea);
}


initializeFsrsRevertUI();
