class AnkiHubRevertFSRS {
    constructor() {
        this.theme = "{{ THEME }}";
        this.fsrsSection = null;
        this.revertButton = null;
        this.debounceId = null;
        this.lastValue = "";
        this.DEBOUNCE_MS = 400;

        this.initializeFsrsRevertUI();
    }

    initializeFsrsRevertUI() {
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

        this.fsrsSection = section;

        const evaluateBtn = buttons[1];
        this.setupRevertButton(evaluateBtn);
        this.setupTextAreaListener(textarea);
    }

    setupRevertButton(evaluateBtn) {
        const revertBtnId = "revert-fsrs-parameters-btn";
        evaluateBtn.insertAdjacentHTML(
            "afterend",
            ` <button id="${revertBtnId}" class="${evaluateBtn.className}">
                  Revert to previous parameters
              </button>`
        );
        this.revertButton = document.getElementById(revertBtnId);
        this.revertButton.disabled = true;

        this.revertButton.addEventListener("click", () => {
            pycmd("ankihub_revert_fsrs_parameters");
            this.revertButton.disabled = true;
        });

        this.addRevertButtonStyles();
    }

    addRevertButtonStyles() {
        // Theme-aware styling for disabled state
        const lightBg = "#e6e6e6", lightText = "#6c717e";
        const darkBg = "#505050", darkText = "#CCCCCC";
        const bg = this.theme === "dark" ? darkBg : lightBg;
        const text = this.theme === "dark" ? darkText : lightText;

        const style = document.createElement("style");
        style.textContent = `
            #revert-fsrs-parameters-btn:disabled {
                background-color: ${bg} !important;
                border-color:     ${bg} !important;
                color:            ${text} !important;
                box-shadow:       none !important;
                outline:          none !important;
            }
            #revert-fsrs-parameters-btn:disabled:focus {
                box-shadow: none !important;
            }
        `;
        document.head.appendChild(style);
    }

    setupTextAreaListener(textarea) {
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

        this.lastValue = textarea.value;


        const onChange = () => {
            if (this.debounceId) clearTimeout(this.debounceId);
            this.debounceId = setTimeout(() => this.processTextAreaChange(textarea), this.DEBOUNCE_MS);
        };

        textarea.addEventListener("fsrsParamsUpdated", onChange);
        textarea.addEventListener("input", onChange);

        this.processTextAreaChange(textarea, true); // Initial sync
    }

    processTextAreaChange(textarea, force = false) {
        const current = textarea.value.trim();
        if (!force && current === this.lastValue) return;
        this.lastValue = current;

        const numericList = current
            .split(/[,\s]+/)
            .filter(str => str.length)
            .map(Number)
            .filter(n => !isNaN(n));

        const payload = JSON.stringify({ fsrs_parameters: numericList });
        pycmd(`ankihub_fsrs_parameters_changed ${payload}`);
    }

    updateFsrsParametersTextarea(params) {
        if (!this.fsrsSection) {
            console.warn("deckOptionsFsrsRevert: FSRS section not initialized");
            return;
        }
        const textarea = this.fsrsSection.querySelector("textarea");
        if (!textarea) {
            console.warn("deckOptionsFsrsRevert: FSRS textarea not found");
            return;
        }

        const newValue = Array.isArray(params) ? params.join(", ") : (params || "");
        textarea.value = newValue;

        // Notify Anki that the value has changed
        ['input', 'change', 'blur'].forEach(eventType => {
            const event = new Event(eventType, { bubbles: true });
            Object.defineProperty(event, 'target', { value: textarea });
            textarea.dispatchEvent(event);
        });
    }

}

window.ankihubRevertFSRS = new AnkiHubRevertFSRS();
