(function () {
    const appUrl = "{{ APP_URL }}";
    const messageJson = `{{ MESSAGE_JSON }}`;

    const message = JSON.parse(messageJson);
    if (typeof window.ankihubAI !== 'undefined') {
        window.ankihubAI.iframe.contentWindow.postMessage(message, appUrl);
    } else {
        window.postMessage(message, appUrl);
    }
})();
