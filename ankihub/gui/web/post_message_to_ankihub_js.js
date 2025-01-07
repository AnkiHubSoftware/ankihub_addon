(function () {
    const messageJson = `{{ MESSAGE_JSON }}`;

    const message = JSON.parse(messageJson);
    window.postMessage(message);
})();
