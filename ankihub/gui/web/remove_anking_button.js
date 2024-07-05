function ankihubRemoveElementWithRetries(selector, retries, delay) {
    const element = document.querySelector(selector);
    if (element) {
        element.remove();
    } else if (retries > 0) {
        setTimeout(() => {
            ankihubRemoveElementWithRetries(selector, retries - 1, delay);
        }, delay);
    }
}

// Attempt to remove the AnKing button (with retries and a delay between each retry) if it exists
setTimeout(() => {
    ankihubRemoveElementWithRetries("a img#pic", 5, 300);
}, 100);
