setInterval(function () {
    // Notify python to sync notes actions after the notes action is created for
    // the selected flashcards.
    const unsuspendButtons = document.querySelectorAll(
        '[id$="{{ FLASHCARD_SELECTOR_UNSUSPEND_BUTTON_ID_SUFFIX }}"]'
    );
    for (const unsuspendButton of unsuspendButtons) {
        if (unsuspendButton && !unsuspendButton.appliedModifications) {
            unsuspendButton.setAttribute("x-on:htmx:after-request", "ankihubHandleUnsuspendNotesResponse")
            htmx.process(unsuspendButton);

            unsuspendButton.appliedModifications = true;
            console.log("Added htmx:after-request attribute to unsuspend button.");

        }
    }
}, 100);

window.ankihubHandleUnsuspendNotesResponse = function (event) {
    if (event.detail.xhr.status === 201) {
        // Extract deck id from the url of the page
        const uuidRegex = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/;
        const deckId = uuidRegex.exec(window.location.href)[0];

        // Notify python to sync notes actions for the deck
        console.log(`Unsuspending notes for deckId=${deckId}`);
        pycmd(`{{ FLASHCARD_SELECTOR_SYNC_NOTES_ACTIONS_PYCMD }} ${deckId}`);
    } else {
        console.error("Request to creates notes action failed");
    }
}
