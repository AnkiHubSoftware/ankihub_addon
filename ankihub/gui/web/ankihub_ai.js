
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
        [this.button, this.tooltip] = this.setupIFrameToggleButton();

        this.setupMessageListener();
        let updateIframeHeight = this.updateIframeHeight
        let updateIframeWidth = this.updateIframeWidth
        let iframe = this.iframe
        window.parent.addEventListener('resize', function () {
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
                this.hideTooltip();
            } else {
                this.hideIframe();
                this.showTooltip();
            }
        };
        document.body.appendChild(button);

        const tooltip = document.createElement("div");
        tooltip.id = "ankihub-ai-tooltip";
        tooltip.innerHTML = "Learn more about this flashcard<br>topic or explore related cards.";

        const tooltipArrow = document.createElement("div");
        tooltipArrow.id = "ankihub-ai-tooltip-arrow";
        tooltip.appendChild(tooltipArrow);

        this.setTooltipAndTooltipArrowStyles(tooltip, tooltipArrow);

        button.addEventListener("mouseover", () => {
            if (this.iframe.style.display === "none") {
                tooltip.style.display = "block";
            }
        });

        button.addEventListener("mouseout", function () {
            tooltip.style.display = "none";
        });

        document.body.appendChild(tooltip);

        return [button, tooltip];
    }

    setTooltipAndTooltipArrowStyles(tooltip, tooltipArrow) {
        tooltip.style.position = "absolute";
        tooltip.style.bottom = "13px";
        tooltip.style.right = "98px";
        tooltip.style.zIndex = "1000";
        tooltip.style.fontSize = "medium";
        tooltip.style.borderRadius = "5px";
        tooltip.style.textAlign = "center";
        tooltip.style.padding = "10px";
        tooltip.style.display = "none";

        tooltipArrow.style.position = "absolute";
        tooltipArrow.style.top = "50%";
        tooltipArrow.style.right = "-6px";
        tooltipArrow.style.marginTop = "-4px";
        tooltipArrow.style.width = "0";
        tooltipArrow.style.height = "0";
        tooltipArrow.style.borderLeft = "6px solid";
        tooltipArrow.style.borderTop = "6px solid transparent";
        tooltipArrow.style.borderBottom = "6px solid transparent";

        const style = document.createElement("style");
        style.innerHTML = `
            :root {
                --neutral-200: #e5e5e5;
                --neutral-800: #1f2937;
            }

            #ankihub-ai-tooltip {
                background-color: var(--neutral-800);
                color: white;
            }

            .night-mode #ankihub-ai-tooltip {
                background-color: var(--neutral-200);
                color: black;
            }

            #ankihub-ai-tooltip-arrow {
                border-color: var(--neutral-800);
                color: var(--neutral-800);
            }

            .night-mode #ankihub-ai-tooltip-arrow {
                border-color: var(--neutral-200);
                color: var(--neutral-200);
            }
        `;
        document.head.appendChild(style);
    }

    showTooltip() {
        this.tooltip.style.display = "block";
    }

    hideTooltip() {
        this.tooltip.style.display = "none";
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
        this.button.style.backgroundImage = "url('/_robot_icon.svg')";
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
        button.style.boxSizing = "content-box";
        button.style.padding = "8px 10px 8px 10px";
        button.style.margin = "0px 4px 0px 4px";

        button.style.position = "fixed";
        button.style.bottom = "13px";
        button.style.right = "15px";
        button.style.zIndex = "9999";

        button.style.borderRadius = "100%";
        button.style.border = "none";

        button.style.backgroundImage = "url('/_robot_icon.svg')";
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
        iframe.style.maxHeight = `${parentWindowHeight - 95}px`;

        iframe.style.position = "fixed"
        iframe.style.bottom = "85px"
        iframe.style.right = "20px"

        iframe.style.border = "none"
        iframe.style.borderRadius = "10px"

        iframe.style.boxShadow = "10px 10px 40px 0px rgba(0, 0, 0, 0.25)"

        iframe.style.overflow = "hidden"
    }

    updateIframeHeight(iframe, parentWindowHeight) {
        iframe.style.maxHeight = `${parentWindowHeight - 95}px`;
    }

    updateIframeWidth(iframe, parentWindowWidth) {
        iframe.style.width = `${parentWindowWidth - 36}px`;
    }

}

window.ankihubAI = new AnkiHubAI();
