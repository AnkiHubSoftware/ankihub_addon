(function () {
    /* -----------------------------------------------------------------------
       context variables injected by add-on template
       -------------------------------------------------------------------- */
    const uiTheme = "{{ THEME }}";           // "light" | "dark"
    const activeDeckId = Number("{{ ANKI_DECK_ID }}");

    /* -----------------------------------------------------------------------
       locate FSRS parameter section and UI elements
       -------------------------------------------------------------------- */
    const headerEls = Array.from(document.querySelectorAll(".setting-title"));
    const fsrsHeader = headerEls.find(
        el => el.textContent.includes("FSRS") && el.textContent.trim() !== "FSRS"
    );
    if (!fsrsHeader) return;

    const section = fsrsHeader.parentElement;
    if (!section) return;

    const primaryBtns = section.querySelectorAll("button.btn.btn-primary");
    if (!primaryBtns.length) return;

    const optimiseBtn = primaryBtns[0];
    const evaluateBtn = primaryBtns[1];

    /* -----------------------------------------------------------------------
       add “Revert” button
       -------------------------------------------------------------------- */
    const revertBtnId = "revertFsrsParametersBtn";
    evaluateBtn.insertAdjacentHTML(
        "afterend",
        ` <button id="${revertBtnId}" class="${evaluateBtn.className}">
              Revert to previous parameters
          </button>`
    );
    const revertBtn = document.getElementById(revertBtnId);

    /* -----------------------------------------------------------------------
       textarea that holds the raw FSRS parameter list
       -------------------------------------------------------------------- */
    const paramsTextarea = section.querySelector("textarea");
    if (!paramsTextarea) return;

    /* -----------------------------------------------------------------------
       helper called by Python side to overwrite textarea contents
       -------------------------------------------------------------------- */
    function updateFsrsParametersTextarea(params) {
        const text =
            Array.isArray(params) ? params.join(", ") : (params || "");
        paramsTextarea.value = text;
        paramsTextarea.dispatchEvent(new Event("input", { bubbles: true }));
    }
    window.updateFsrsParametersTextarea = updateFsrsParametersTextarea;

    /* -----------------------------------------------------------------------
       1.  Revert button handler
       -------------------------------------------------------------------- */
    revertBtn.addEventListener("click", () => {
        const payload = JSON.stringify({ anki_deck_id: activeDeckId });
        pycmd(`ankihub_revert_fsrs_parameters ${payload}`);
        revertBtn.disabled = true;
    });

    /* -----------------------------------------------------------------------
       2.  Debounced watcher for *any* change to the textarea
       -------------------------------------------------------------------- */
    const DEBOUNCE_DELAY_MS = 400;
    let debounceTimerId = null;
    let lastSnapshot = paramsTextarea.value;

    paramsTextarea.addEventListener("input", () => {
        if (debounceTimerId !== null) {
            clearTimeout(debounceTimerId);
        }
        debounceTimerId = setTimeout(() => {
            debounceTimerId = null;

            const currentValue = paramsTextarea.value;
            if (currentValue === lastSnapshot) return;   // nothing new

            lastSnapshot = currentValue;                 // update snapshot
            const payload = JSON.stringify({
                anki_deck_id: activeDeckId,
                raw_params: currentValue
            });
            pycmd(`ankihub_fsrs_parameters_changed ${payload}`);
        }, DEBOUNCE_DELAY_MS);
    });

    /* -----------------------------------------------------------------------
       3.  legacy optimise-button hook (optional but harmless)
       -------------------------------------------------------------------- */
    optimiseBtn.addEventListener("click", () => {
        const payload = JSON.stringify({ anki_deck_id: activeDeckId });
        pycmd(`ankihub_on_optimize_fsrs_parameters ${payload}`);
    });

    /* -----------------------------------------------------------------------
       initialise Revert button disabled state from template flag
       -------------------------------------------------------------------- */
    revertBtn.disabled =
        "{{ FSRS_PARAMETERS_BACKUP_ENTRY_EXISTS }}" === "False";

    /* -----------------------------------------------------------------------
       theme-aware styling for disabled Revert button
       -------------------------------------------------------------------- */
    const lightBg = "#e6e6e6", lightText = "#6c717e";
    const darkBg = "#505050", darkText = "#CCCCCC";
    const disabledBg = uiTheme === "dark" ? darkBg : lightBg;
    const disabledText = uiTheme === "dark" ? darkText : lightText;

    const styleTag = document.createElement("style");
    styleTag.textContent = `
        #${revertBtnId}:disabled {
            background-color: ${disabledBg} !important;
            border-color:     ${disabledBg} !important;
            color:            ${disabledText} !important;
            box-shadow:       none          !important;
            outline:          none          !important;
        }
        #${revertBtnId}:disabled:focus { box-shadow: none !important; }
    `;
    document.head.appendChild(styleTag);
})();
