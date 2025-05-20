(function () {
    const theme = "{{ THEME }}";  // "light" or "dark"

    // Locate the “FSRS parameters” header
    const titles = Array.from(document.querySelectorAll('.setting-title'));
    const paramTitle = titles.find(el =>
        el.textContent.includes('FSRS') &&
        el.textContent.trim() !== 'FSRS'
    );
    if (!paramTitle) return;

    // Find its container and primary buttons
    const section = paramTitle.parentElement;
    if (!section) return;
    const buttons = section.querySelectorAll('button.btn.btn-primary');
    if (!buttons.length) return;

    // Pick the Evaluate button (last one)
    const evaluateBtn = buttons[1];

    // Insert the new button
    const revertBtnId = 'revertFsrsParametersBtn';
    evaluateBtn.insertAdjacentHTML(
        'afterend',
        ` <button id="${revertBtnId}" class="${evaluateBtn.className}">Revert to previous parameters</button>`
    );

    // Hook up click handler and expose for enable/disable
    const revertBtn = document.getElementById(revertBtnId);
    revertBtn.addEventListener('click', () => {
        pycmd("ankihub_revert_fsrs_parameters");
    });
    window.revertFsrsParametersBtn = revertBtn;

    // Choose disabled colors per theme
    const lightBg = '#e6e6e6';
    const lightText = '#6c717e';
    const darkBg = '#505050';
    const darkText = '#CCCCCC';

    const disabledBg = theme === 'dark' ? darkBg : lightBg;
    const disabledText = theme === 'dark' ? darkText : lightText;
    const disabledBorder = theme === 'dark' ? darkBg : lightBg;

    // Inject CSS override
    const style = document.createElement('style');
    style.textContent = `
      #${revertBtnId}:disabled {
        background-color: ${disabledBg}      !important;
        border-color:     ${disabledBorder}  !important;
        color:            ${disabledText}    !important;
        box-shadow:       none               !important;
        outline:          none               !important;
      }
      #${revertBtnId}:disabled:focus {
        box-shadow: none !important;
      }
    `;
    document.head.appendChild(style);
})();
