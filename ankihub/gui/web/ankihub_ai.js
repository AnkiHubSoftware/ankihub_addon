
function setup() {
    const knox_token = "{{ KNOX_TOKEN }}";
    const appUrl = "{{ APP_URL }}";
    const endpointPath = "{{ ENDPOINT_PATH }}";

    const iframe = setupIframe(knox_token, appUrl, endpointPath);
    setupIFrameToggleButton(iframe);
}

function setupIframe(token, appUrl, endpointPath) {
    const iframe = document.createElement("iframe");
    iframe.id = "ankihub-ai-iframe"

    const targetUrl = `${appUrl}/${endpointPath}`
    iframe.src = `${appUrl}/ai/embed_auth/?next=${encodeURIComponent(targetUrl)}`

    iframe.style.display = "none"

    iframe.style.width = "700px";
    iframe.style.height = "70%";

    iframe.style.position = "fixed"
    iframe.style.bottom = "85px"
    iframe.style.right = "20px"

    iframe.style.border = "none"
    iframe.style.borderRadius = "10px"

    iframe.style.boxShadow = "10px 10px 40px 0px rgba(0, 0, 0, 0.25)"

    // Hide scrollbar of iframe
    iframe.style.overflow = "hidden"
    iframe.scrolling = "no"

    iframe.onload = function () {
        iframe.contentWindow.postMessage(token, appUrl);
    }

    document.body.appendChild(iframe)

    return iframe;
}

function setupIFrameToggleButton(iframe) {
    const button = document.createElement("button");
    button.id = "ankihub-ai-button";

    button.style.width = "45px";
    button.style.height = "45px";

    button.style.position = "fixed";
    button.style.bottom = "0px";
    button.style.right = "15px";
    button.style.zIndex = "9999";

    button.style.borderRadius = "100%";
    button.style.border = "none";

    button.style.backgroundImage = "url('_robotking.png')";
    button.style.backgroundSize = "cover";
    button.style.backgroundPosition = "center";
    button.style.backgroundRepeat = "no-repeat";
    button.style.backgroundColor = "#4f46e5";

    button.style.cursor = "pointer";

    button.onclick = function () {
        // Toggle iframe visibility and change button icon
        if (iframe.style.display === "none") {
            button.style.backgroundImage = "url('_chevron-down-solid.svg')";
            button.style.backgroundSize = "30%";
            iframe.style.display = "block";
        } else {
            button.style.backgroundImage = "url('_robotking.png')";
            button.style.backgroundSize = "cover";
            iframe.style.display = "none";
        }
    }

    document.body.appendChild(button);
}

setup();
