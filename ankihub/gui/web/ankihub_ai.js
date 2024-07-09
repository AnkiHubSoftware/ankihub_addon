
class AnkiHubAI {
    constructor() {
        this.appUrl = "{{ APP_URL }}";
        this.endpointPath = "{{ ENDPOINT_PATH }}";
        this.queryParameters = "{{ QUERY_PARAMETERS }}"
        this.embeddedAuthPath = "common/embedded-auth";

        this.noteIdOfReviewerCard = null; // The note ID for which the card is currently being reviewed.
        this.noteIdOfChatbot = null; // The note ID for which the chatbot page is currently loaded.
        this.authenticated = false;
        this.knoxToken = "{{ KNOX_TOKEN }}";
        this.iframeVisible = false;
        this.theme = "{{ THEME }}";

        this.setup();
    }

    setup() {
        this.iframe = this.setupIframe();
        this.button = this.setupIFrameToggleButton();

        this.setupMessageListener();
        let updateIframeHeight = this.updateIframeHeight
        let updateIframeWidth = this.updateIframeWidth
        let iframe = this.iframe
        window.parent.addEventListener('resize', function() {
            if (iframe.style.display !== "none") {
                updateIframeHeight(iframe, window.parent.innerHeight)
                updateIframeWidth(iframe, window.parent.innerWidth)
            }
        });
    }

    setupMessageListener() {
        window.addEventListener("message", (event) => {
            if (event.origin !== this.appUrl) {
                return;
            }

            if (event.data === "Authentication failed") {
                this.hideIframe();
                this.invalidateSessionAndPromptToLogin();
            }

            if (event.data.sendToPython) {
                pycmd(event.data.message);
            }
        });
    }

    setupIframe() {
        const iframe = document.createElement("iframe");
        iframe.id = "ankihub-ai-iframe";
        this.setIframeStyles(iframe, window.parent.innerHeight);

        iframe.onload = () => {
            if (iframe.src && iframe.src.includes(this.embeddedAuthPath)) {
                const message = {
                    token: this.knoxToken,
                    theme: this.theme,
                }
                iframe.contentWindow.postMessage(message, this.appUrl);
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
            if (!this.knoxToken) {
                this.invalidateSessionAndPromptToLogin()
                return;
            }
            if (!this.iframeVisible) {
                this.maybeUpdateIframeSrc();
                this.showIframe();
            } else {
                this.hideIframe();
            }
        };
        document.body.appendChild(button);
        return button;
    }

    invalidateSessionAndPromptToLogin() {
        this.authenticated = false;
        this.knoxToken = null;
        this.noteIdOfChatbot = null;
        pycmd("ankihub_ai_invalid_auth_token");
    }

    showIframe() {
        this.button.style.backgroundImage = "url('_chevron-down-solid.svg')";
        this.button.style.backgroundSize = "30%";
        this.iframe.style.display = "block";
        this.iframeVisible = true;
    }

    hideIframe() {
        this.button.style.backgroundImage = "url('_robotking.png')";
        this.button.style.backgroundSize = "cover";
        this.iframe.style.display = "none";
        this.iframeVisible = false;
    }

    cardChanged(noteId) {
        this.noteIdOfReviewerCard = noteId;

        if (this.iframeVisible) {
            this.maybeUpdateIframeSrc();
        }
    }

    setToken(token) {
        this.knoxToken = token;
    }

    sendNoteSuspensionStates(noteSuspensionStates) {
        const message = {
            noteSuspensionStates: noteSuspensionStates
        }
        this.iframe.contentWindow.postMessage(message, this.appUrl);
    }

    maybeUpdateIframeSrc() {
        if (this.noteIdOfChatbot === this.noteIdOfReviewerCard) {
            // No need to reload the iframe.
            // This prevents the iframe from reloading when the user reopens the chatbot on the same card.
            return;
        }

        const targetUrl = `${this.appUrl}/${this.endpointPath}/${this.noteIdOfReviewerCard}/?${this.queryParameters}`;
        if (!this.authenticated) {
            this.iframe.src = `${this.appUrl}/${this.embeddedAuthPath}/?next=${encodeURIComponent(targetUrl)}`;
            this.authenticated = true;
        } else {
            this.iframe.src = targetUrl;
        }

        this.noteIdOfChatbot = this.noteIdOfReviewerCard;
    }

    setButtonStyles(button) {
        button.style.width = "40px";
        button.style.height = "40px";

        button.style.position = "fixed";
        button.style.bottom = "0px";
        button.style.right = "15px";
        button.style.zIndex = "9999";

        button.style.borderRadius = "100%";
        button.style.border = "none";

        button.style.padding = "8px";

        button.style.backgroundImage = "url('_robotking.png')";
        button.style.backgroundSize = "cover";
        button.style.backgroundPosition = "center";
        button.style.backgroundRepeat = "no-repeat";
        button.style.backgroundColor = "#4f46e5";

        button.style.cursor = "pointer";
    }

    setIframeStyles(iframe, parentWindowHeight) {
        iframe.style.display = "none"

        iframe.style.width = "100%";
        iframe.style.maxWidth = "700px";
        iframe.style.minWidth = "360px";

        iframe.style.height = "100%";
        iframe.style.maxHeight = `${parentWindowHeight-95}px`;

        iframe.style.position = "fixed"
        iframe.style.bottom = "85px"
        iframe.style.right = "20px"

        iframe.style.border = "none"
        iframe.style.borderRadius = "10px"

        iframe.style.boxShadow = "10px 10px 40px 0px rgba(0, 0, 0, 0.25)"

        iframe.style.overflow = "hidden"
    }

    updateIframeHeight(iframe, parentWindowHeight) {
        iframe.style.maxHeight = `${parentWindowHeight-95}px`;
    }

    updateIframeWidth(iframe, parentWindowWidth) {
        iframe.style.width = `${parentWindowWidth-36}px`;
    }

}

window.ankihubAI = new AnkiHubAI();
