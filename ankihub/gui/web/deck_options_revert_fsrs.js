const config = {
    theme: "{{ THEME }}",
    deckId: Number("{{ ANKI_DECK_ID }}"),
    revertEnabledInit: "{{ RESET_BUTTON_ENABLED_INITIALLY }}" !== "False"
};

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

    textarea.value = Array.isArray(params) ? params.join(", ") : (params || "");
    // focus+blur to notify Anki of the change
    textarea.focus();
    textarea.blur();
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
    revertBtn.disabled = !config.revertEnabledInit;

    revertBtn.addEventListener("click", () => {
        const payload = JSON.stringify({ anki_deck_id: config.deckId });
        pycmd(`ankihub_revert_fsrs_parameters ${payload}`);
        revertBtn.disabled = true;
    });

    // Theme-aware styling for disabled state
    const lightBg = "#e6e6e6", lightText = "#6c717e";
    const darkBg = "#505050", darkText = "#CCCCCC";
    const bg = config.theme === "dark" ? darkBg : lightBg;
    const text = config.theme === "dark" ? darkText : lightText;

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


function setupTextAreaListener(textarea, revertBtn) {
    // Patch the textarea value setter to fire a custom event
    const proto = Object.getPrototypeOf(textarea);
    if (!textarea._fsrsRevertPatched) {
        const desc = Object.getOwnPropertyDescriptor(proto, "value");
        Object.defineProperty(textarea, "value", {
            get: desc.get,
            set(v) {
                desc.set.call(this, v);
                // Fire a custom event so we don't interfere with Anki's own logic
                this.dispatchEvent(new CustomEvent("fsrsParamsUpdated", { bubbles: true }));
            },
        });
        textarea._fsrsRevertPatched = true;
    }

    const DEBOUNCE_MS = 400;
    let debounceId = null;
    let lastValue = textarea.value;

    function processChange() {
        const current = textarea.value.trim();
        if (current === lastValue) return;
        lastValue = current;

        // parse numeric tokens only
        const numericList = current
            .split(/[,\s]+/)
            .filter(tok => /^-?\d+(\.\d+)?$/.test(tok))
            .map(Number);

        const payload = JSON.stringify({
            anki_deck_id: config.deckId,
            fsrs_parameters: numericList,
        });
        pycmd(`ankihub_fsrs_parameters_changed ${payload}`);
    }

    // Listen for programmatic updates
    textarea.addEventListener("fsrsParamsUpdated", () => {
        if (debounceId) clearTimeout(debounceId);
        debounceId = setTimeout(processChange, DEBOUNCE_MS);
    });

    // Listen for manual edits
    textarea.addEventListener("input", () => {
        if (debounceId) clearTimeout(debounceId);
        debounceId = setTimeout(processChange, DEBOUNCE_MS);
    });
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

    // Pass the newly inserted button to the listener setup
    const revertBtn = document.getElementById("revertFsrsParametersBtn");
    setupTextAreaListener(textarea, revertBtn);
}


initializeFsrsRevertUI();
