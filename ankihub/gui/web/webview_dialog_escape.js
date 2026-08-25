// Reports Escape presses the page didn't consume, so the hosting AnkiHubWebViewDialog closes
// only when nothing inside the page handled the key. A page keeps the window open by calling
// preventDefault() on the keydown event.
(function () {
    if (window.ankihubEscapeHandlerInstalled) {
        return;
    }
    window.ankihubEscapeHandlerInstalled = true;

    window.addEventListener(
        "keydown",
        (event) => {
            if (event.key !== "Escape") {
                return;
            }

            // Deferred to a macrotask so every synchronous handler has run and
            // defaultPrevented is final. Reading it inline would depend on listener
            // registration order, and page code commonly listens on window.
            setTimeout(() => {
                if (!event.defaultPrevented) {
                    pycmd("{{ ESCAPE_PYCMD }}");
                }
            }, 0);
        },
        // Capture phase, so this runs even if page code calls stopPropagation().
        true
    );
})();
