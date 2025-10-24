class AnkiHubModal {
    constructor(options = {}) {
        this.options = {
            body: "",
            footer: "",
            showCloseButton: true,
            closeOnBackdropClick: false,
            target: null,
            position: "center",
            arrowPosition: "top",
            showArrow: true,
            ...options,
        };

        this.isVisible = false;
        this.targetElement = null;
        this.modalElement = null;
        this.backdropElement = null;
        this.shadowRoot = null;
        this.arrowElement = null;
        this.createModal();
        this.bindEvents();
    }

    createModal() {
        this.hostElement = document.createElement("div");
        this.hostElement.style.position = "fixed";
        this.hostElement.style.top = "0";
        this.hostElement.style.left = "0";
        this.hostElement.style.width = "100%";
        this.hostElement.style.height = "100%";
        this.hostElement.style.zIndex = "10000";
        this.hostElement.style.pointerEvents = "none";
        this.shadowRoot = this.hostElement.attachShadow({ mode: "open" });
        this.injectStyles();
        this.backdropElement = document.createElement("div");
        this.backdropElement.className = "ah-modal-backdrop";
        this.modalElement = document.createElement("div");
        this.modalElement.className = "ah-modal-container";
        const modalContent = document.createElement("div");
        modalContent.className = "ah-modal-content";
        if (this.options.showCloseButton) {
            const header = document.createElement("div");
            header.className = "ah-modal-header";
            const closeButton = document.createElement("button");
            closeButton.className = "ah-modal-close-button";
            closeButton.innerHTML = "×";
            closeButton.setAttribute("aria-label", "Close modal");
            header.appendChild(closeButton);
            modalContent.appendChild(header);
        }
        const body = document.createElement("div");
        body.className = "ah-modal-body";
        if (typeof this.options.body === "string") {
            body.innerHTML = this.options.body;
        } else if (this.options.body instanceof Element) {
            body.appendChild(this.options.body);
        }
        modalContent.appendChild(body);
        if (this.options.footer) {
            const footer = document.createElement("div");
            footer.className = "ah-modal-footer";
            if (typeof this.options.footer === "string") {
                footer.innerHTML = this.options.footer;
            } else if (this.options.footer instanceof Element) {
                footer.appendChild(this.options.footer);
            }
            modalContent.appendChild(footer);
        }
        this.modalElement.appendChild(modalContent);
        if (this.options.body) {
            this.backdropElement.appendChild(this.modalElement);
        }
        this.shadowRoot.appendChild(this.backdropElement);
    }

    injectStyles() {
        const style = document.createElement("style");
        style.textContent = `
            :host {
                --ah-modal-bg: #ffffff;
                --ah-modal-text: #374151;
                --ah-modal-border: #e5e7eb;
                --ah-modal-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
                --ah-modal-close-hover: #f3f4f6;
                --ah-modal-close-text: #6b7280;
                --ah-modal-primary-button-bg: #4F46E5;
            }
            :host-context(.night_mode) {
                --ah-modal-bg: #1f2937;
                --ah-modal-text: #f9fafb;
                --ah-modal-border: #374151;
                --ah-modal-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
                --ah-modal-close-hover: #374151;
                --ah-modal-close-text: #d1d5db;
                --ah-modal-primary-button-bg: #818CF8;
            }
            .ah-modal-backdrop {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                backdrop-filter: brightness(0.5);
                z-index: 10000;
                opacity: 0;
                transition: opacity 0.2s ease-in-out;
                display: flex;
                align-items: center;
                justify-content: center;
                pointer-events: auto;
            }
            .ah-modal-container {
                position: relative;
                max-width: 90vw;
                max-height: 90vh;
                width: auto;
                min-width: 300px;
                transform: scale(0.9);
                transition: transform 0.2s ease-in-out;
            }
            .ah-modal-content {
                background-color: var(--ah-modal-bg);
                border-radius: 8px;
                box-shadow: var(--ah-modal-shadow);
                overflow: hidden;
                display: flex;
                flex-direction: column;
                max-height: 90vh;
            }
            .ah-modal-header {
                position: relative;
                padding: 16px 20px 0 20px;
                flex-shrink: 0;
            }
            .ah-modal-close-button {
                position: absolute;
                top: 12px;
                right: 12px;
                background: none;
                border: none;
                font-size: 24px;
                font-weight: bold;
                color: var(--ah-modal-close-text);
                cursor: pointer;
                width: 32px;
                height: 32px;
                display: flex;
                align-items: center;
                justify-content: center;
                border-radius: 4px;
                transition: background-color 0.2s ease;
                line-height: 1;
            }
            .ah-modal-close-button:hover {
                background-color: var(--ah-modal-close-hover);
            }
            .ah-modal-close-button:focus {
                outline: 2px solid #4f46e5;
                outline-offset: 2px;
            }
            .ah-primary-button {
                background-color: var(--ah-modal-primary-button-bg);
                color: #ffffff;
                border: none;
                padding: 10px 20px;
                border-radius: 8px;
                cursor: pointer;
                transition: background-color 0.2s ease;
            }
            .ah-modal-body {
                padding: 20px;
                color: var(--ah-modal-text);
                overflow-y: auto;
                flex: 1;
                min-height: 0;
            }
            .ah-modal-footer {
                padding: 16px 20px 20px 20px;
                border-top: 1px solid var(--ah-modal-border);
                flex-shrink: 0;
                display: flex;
                gap: 12px;
                justify-content: space-between;
                align-items: center;
            }
            .ah-modal-backdrop.ah-modal-show {
                opacity: 1;
            }
            .ah-modal-backdrop.ah-modal-show .ah-modal-container {
                transform: scale(1);
            }
            .ah-modal-backdrop.ah-modal-hide {
                opacity: 0;
            }
            .ah-modal-backdrop.ah-modal-hide .ah-modal-container {
                transform: scale(0.9);
            }
            @media (max-width: 640px) {
                .ah-modal-container {
                    max-width: 95vw;
                    margin: 20px;
                }
                .ah-modal-content {
                    max-height: calc(100vh - 40px);
                }
                .ah-modal-body {
                    padding: 16px;
                }
                .ah-modal-footer {
                    padding: 12px 16px 16px 16px;
                    flex-direction: column;
                }
                .ah-modal-footer > * {
                    width: 100%;
                }
            }
            .ah-modal-backdrop:focus {
                outline: none;
            }
            .ah-modal-content {
                outline: none;
            }
            .ah-modal-arrow {
                position: absolute;
                width: 0;
                height: 0;
                border: 8px solid transparent;
                z-index: 10002;
            }
            .ah-modal-arrow.ah-arrow-top {
                bottom: 100%;
                left: 50%;
                transform: translateX(-50%);
                border-bottom-color: var(--ah-modal-bg);
                border-top: none;
            }
            .ah-modal-arrow.ah-arrow-bottom {
                top: 100%;
                left: 50%;
                transform: translateX(-50%);
                border-top-color: var(--ah-modal-bg);
                border-bottom: none;
            }
            .ah-modal-arrow.ah-arrow-left {
                right: 100%;
                top: 50%;
                transform: translateY(-50%);
                border-right-color: var(--ah-modal-bg);
                border-left: none;
            }
            .ah-modal-arrow.ah-arrow-right {
                left: 100%;
                top: 50%;
                transform: translateY(-50%);
                border-left-color: var(--ah-modal-bg);
                border-right: none;
            }
        `;
        this.shadowRoot.appendChild(style);
    }

    createArrow() {
        if (!this.options.showArrow) return;
        this.arrowElement = document.createElement("div");
        this.arrowElement.className = "ah-modal-arrow";
        this.modalElement.appendChild(this.arrowElement);
    }

    updateArrowPosition() {
        if (!this.arrowElement) return;
        this.arrowElement.classList.remove(
            "ah-arrow-top",
            "ah-arrow-bottom",
            "ah-arrow-left",
            "ah-arrow-right"
        );
        this.arrowElement.classList.add(
            `ah-arrow-${this.options.arrowPosition}`
        );
    }

    bindEvents() {
        const closeButton = this.modalElement.querySelector(
            ".ah-modal-close-button"
        );
        if (closeButton) {
            closeButton.addEventListener("click", () => {
                this.close();
                pycmd("ankihub_modal_closed");
            });
        }
        this.backdropElement.addEventListener("click", (e) => {
            if (
                e.target === this.backdropElement &&
                this.options.closeOnBackdropClick
            ) {
                this.close();
            }
        });
        this.resizeHandler = () => {
            if (this.isVisible) {
                if (this.resizeTimeout) {
                    clearTimeout(this.resizeTimeout);
                }
                this.resizeTimeout = setTimeout(() => {
                    this.positionModal();
                    this.updateArrowPosition();
                }, 16);
            }
        };
        window.addEventListener("resize", this.resizeHandler);

        this.modalElement.addEventListener("click", (e) => {
            e.stopPropagation();
        });
    }

    show() {
        if (this.isVisible) return;

        document.body.appendChild(this.hostElement);
        if (this.options.target) {
            this.targetElement =
                typeof this.options.target === "string"
                    ? document.querySelector(this.options.target)
                    : this.options.target;
            this.applySpotlight();
        }
        this.createArrow();
        this.positionModal();
        this.updateArrowPosition();
        requestAnimationFrame(() => {
            this.backdropElement.classList.add("ah-modal-show");
        });
        this.isVisible = true;
    }

    close() {
        if (!this.isVisible) return;

        this.removeSpotlight();
        if (this.arrowElement && this.arrowElement.parentNode) {
            this.arrowElement.parentNode.removeChild(this.arrowElement);
            this.arrowElement = null;
        }
        this.backdropElement.classList.remove("ah-modal-show");
        this.backdropElement.classList.add("ah-modal-hide");
        setTimeout(() => {
            if (this.hostElement.parentNode) {
                this.hostElement.parentNode.removeChild(this.hostElement);
            }
            this.backdropElement.classList.remove("ah-modal-hide");
        }, 200);
        this.isVisible = false;
    }

    destroy() {
        this.close();
        window.removeEventListener("resize", this.resizeHandler);
        this.removeSpotlight();
        if (this.resizeTimeout) {
            clearTimeout(this.resizeTimeout);
            this.resizeTimeout = null;
        }
        if (this.arrowElement && this.arrowElement.parentNode) {
            this.arrowElement.parentNode.removeChild(this.arrowElement);
            this.arrowElement = null;
        }
    }

    applySpotlight() {
        if (!this.options.target) return;

        this.targetElement.classList.add("ah-spotlight-active");
        const originalZIndex = this.targetElement.style.zIndex;
        this.targetElement.style.zIndex = "10001";
        this.targetElement.setAttribute(
            "data-original-z-index",
            originalZIndex
        );
        // Work around backdrop-filter set on Anki's top bar preventing spotlight from being visible
        this.targetElement.parentElement.style.backdropFilter = "none";
    }

    removeSpotlight() {
        if (!this.targetElement) return;

        this.targetElement.classList.remove("ah-spotlight-active");
        const originalZIndex = this.targetElement.getAttribute(
            "data-original-z-index"
        );
        if (originalZIndex) {
            this.targetElement.style.zIndex = originalZIndex;
            this.targetElement.removeAttribute("data-original-z-index");
        } else {
            this.targetElement.style.zIndex = "";
        }
        this.targetElement = null;
    }

    _positionModal(top, left, transform) {
        this.modalElement.style.position = "fixed";
        // FIXME: add margin depending on arrow position if external target
        this.modalElement.style.top = `${top + 10}px`;
        this.modalElement.style.left = `${left}px`;
        this.modalElement.style.transform = transform;
    }

    positionModal() {
        if (!this.options.target) {
            return;
        }

        const targetRect = this.targetElement.getBoundingClientRect();
        const modalRect = this.modalElement.getBoundingClientRect();
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;
        let top, left, transform;
        switch (this.options.position) {
            case "top":
                top = targetRect.top - modalRect.height - 10;
                left =
                    targetRect.left + (targetRect.width - modalRect.width) / 2;
                transform = "none";
                break;
            case "bottom":
                top = targetRect.bottom + 10;
                left =
                    targetRect.left + (targetRect.width - modalRect.width) / 2;
                transform = "none";
                break;
            case "left":
                top =
                    targetRect.top + (targetRect.height - modalRect.height) / 2;
                left = targetRect.left - modalRect.width - 10;
                transform = "none";
                break;
            case "right":
                top =
                    targetRect.top + (targetRect.height - modalRect.height) / 2;
                left = targetRect.right + 10;
                transform = "none";
                break;
            default:
                top = "50%";
                left = "50%";
                transform = "translate(-50%, -50%)";
        }
        const finalTop =
            typeof top === "string"
                ? top
                : Math.max(
                      10,
                      Math.min(top, viewportHeight - modalRect.height - 10)
                  );
        const finalLeft =
            typeof left === "string"
                ? left
                : Math.max(
                      10,
                      Math.min(left, viewportWidth - modalRect.width - 10)
                  );
        this._positionModal(finalTop, finalLeft, transform);

        this.updateArrowPosition();
    }

    setModalPosition(top, left, transform = "") {
        this._positionModal(top, left, transform);
    }
}
