
class AnkiHubAI {
    constructor() {
        this.knoxToken = "{{ KNOX_TOKEN }}";
        this.appUrl = "{{ APP_URL }}";
        this.endpointPath = "{{ ENDPOINT_PATH }}";

        this.noteId = null;
        this.noteIdICurrentlyLoaded = null;
        this.authenticated = false;
        this.iframeVisible = false;

        this.setup();
    }

    setup() {
        this.iframe = this.setupIframe();
        this.button = this.setupIFrameToggleButton();
    }

    setupIframe() {
        const iframe = document.createElement("iframe");
        iframe.id = "ankihub-ai-iframe";
        this.setIframeStyles(iframe);

        iframe.onload = () => {
            if (iframe.src) {
                iframe.contentWindow.postMessage(this.knoxToken, this.appUrl);
            }
        };
        document.body.appendChild(iframe);
        return iframe;
    }

    setupIFrameToggleButton() {
        const button = document.createElement("button");
        button.id = "ankihub-ai-button";
        this.setButtonStyles(button)
        button.onclick = () => {
            this.maybeUpdateIframeSrc();

            if (!this.iframeVisible) {
                button.style.backgroundImage = "url('_chevron-down-solid.svg')";
                button.style.backgroundSize = "30%";
                this.iframe.style.display = "block";
                this.iframeVisible = true;
            } else {
                button.style.backgroundImage = "url('_robotking.png')";
                button.style.backgroundSize = "cover";
                this.iframe.style.display = "none";
                this.iframeVisible = false;
            }
        };
        document.body.appendChild(button);
        return button;
    }

    cardChanged(noteId) {
        this.noteId = noteId;

        if (this.iframeVisible) {
            this.maybeUpdateIframeSrc();
        }
    }

    maybeUpdateIframeSrc() {
        if (this.noteIdICurrentlyLoaded === this.noteId) {
            // No need to reload the iframe.
            // This prevents the iframe from reloading when the user reopens the chatbot on the same card.
            return;
        }

        const targetUrl = `${this.appUrl}/${this.endpointPath}/${this.noteId}`;
        if (!this.authenticated) {
            this.iframe.src = `${this.appUrl}/common/embedded-auth/?next=${encodeURIComponent(targetUrl)}`;
            this.authenticated = true;
        } else {
            this.iframe.src = targetUrl;
        }

        this.noteIdICurrentlyLoaded = this.noteId;
    }

    setButtonStyles(button) {
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
    }

    setIframeStyles(iframe) {
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
    }

}

window.ankihubAI = new AnkiHubAI();
