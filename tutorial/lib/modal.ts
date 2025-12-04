import { arrow, autoPlacement, autoUpdate, computePosition, offset, type ReferenceElement } from '@floating-ui/dom';
import { bridgeCommand } from "./bridgecommand";

type ModalOptions = {
    body: string | HTMLElement,
    footer: string | HTMLElement,
    showCloseButton: boolean,
    closeOnBackdropClick: boolean,
    backdrop: boolean,
    target: string | null,
    showArrow: boolean,
    blockTargetClick: boolean,
};

export class Modal {

    options: ModalOptions;
    isVisible: boolean = false;
    targetElement: HTMLElement | null = null;
    modalElement!: HTMLElement;
    backdropElement!: HTMLElement;
    arrowElement: HTMLElement | null = null;
    shadowRoot!: ShadowRoot;
    hostElement!: HTMLDivElement;
    cleanUpdateHandler?: () => void;
    resizeTimeout: number | null = null;

    constructor(options = {}) {
        this.options = {
            body: "",
            footer: "",
            showCloseButton: true,
            closeOnBackdropClick: false,
            backdrop: true,
            target: null,
            showArrow: true,
            blockTargetClick: false,
            ...options,
        };
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
            } else if (this.options.footer instanceof HTMLElement) {
                footer.appendChild(this.options.footer);
            }
            modalContent.appendChild(footer);
        }
        this.modalElement.appendChild(modalContent);
        if (this.options.body) {
            this.backdropElement.appendChild(this.modalElement);
            if (this.targetElement) {
                this.cleanUpdateHandler = autoUpdate(this.targetElement, this.modalElement, this.positionModal.bind(this, this.targetElement));
            }
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
                --ah-modal-secondary-button-bg: #E7000B;
            }
            :host-context(.night_mode) {
                --ah-modal-bg: #1f2937;
                --ah-modal-text: #f9fafb;
                --ah-modal-border: #374151;
                --ah-modal-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
                --ah-modal-close-hover: #374151;
                --ah-modal-close-text: #d1d5db;
                --ah-modal-primary-button-bg: #818CF8;
                --ah-modal-secondary-button-bg: #FF6467;
            }
            .ah-modal-backdrop {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                ${this.options.backdrop ? 'backdrop-filter: brightness(0.5);' : ''}
                z-index: 10000;
                opacity: 0;
                transition: opacity 0.2s ease-in-out;
                display: flex;
                align-items: center;
                justify-content: center;
                pointer-events: auto;
            }
            .ah-modal-container {
                box-sizing: border-box;
                position: absolute;
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
            .ah-button {
                color: #ffffff;
                border: none;
                padding: 10px 20px;
                border-radius: 8px;
                cursor: pointer;
                transition: background-color 0.2s ease;
            }
            .ah-primary-button {
                background-color: var(--ah-modal-primary-button-bg);
            }
            .ah-secondary-button {
                background-color: var(--ah-modal-secondary-button-bg);
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
                width: 20px;
                height: 20px;
                background: var(--ah-modal-bg);
                pointer-events: none;
                z-index: -1;
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

    bindEvents() {
        const closeButton = this.modalElement.querySelector(
            ".ah-modal-close-button"
        );
        if (closeButton) {
            closeButton.addEventListener("click", () => {
                this.close();
                bridgeCommand("ankihub_modal_closed");
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
        if (this.targetElement) {
            this.positionModal(this.targetElement);
        }
        requestAnimationFrame(() => {
            this.backdropElement.classList.add("ah-modal-show");
        });
        this.isVisible = true;
    }

    close() {
        if (!this.isVisible) return;
        this.cleanUpdateHandler?.();
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

    spotlightClasses() {
        let classes = ["ah-spotlight-active"];
        if (this.options.backdrop) {
            classes.push("ah-with-backdrop");
        }
        return classes;
    }

    applySpotlight() {
        if (!this.targetElement) return;

        this.targetElement.classList.add(...this.spotlightClasses());
        if (this.options.blockTargetClick) {
            const originalPointerEvents =
                this.targetElement.style.pointerEvents;
            this.targetElement.style.pointerEvents = "none";
            this.targetElement.setAttribute(
                "data-original-pointer-events",
                originalPointerEvents
            );
        }
        // Work around backdrop-filter set on Anki's top bar preventing spotlight from being visible
        if (this.targetElement.parentElement) {
            this.targetElement.parentElement.style.backdropFilter = "none";
        }
    }

    removeSpotlight() {
        if (!this.targetElement) return;
        this.targetElement.classList.remove(...this.spotlightClasses());
        const originalPointerEvents = this.targetElement.getAttribute(
            "data-original-pointer-events"
        );
        if (originalPointerEvents) {
            this.targetElement.style.pointerEvents = originalPointerEvents;
            this.targetElement.removeAttribute("data-original-pointer-events");
        } else {
            this.targetElement.style.pointerEvents = "";
        }
        this.targetElement = null;
    }

    _positionModal(y: number, x: number) {
        this.modalElement.style.top = `${y}px`;
        this.modalElement.style.left = `${x}px`;
    }

    async positionModal(target: ReferenceElement) {
        const arrowLength = this.arrowElement ? this.arrowElement.offsetWidth : 0;
        const floatingOffset = Math.sqrt(2 * arrowLength ** 2) / 2;

        let middleware = [autoPlacement()];
        if (this.arrowElement) {
            middleware.push(offset(floatingOffset));
            middleware.push(arrow({ element: this.arrowElement! }));
        }
        const { x, y, middlewareData, placement } = await computePosition(target, this.modalElement, {
            middleware
        });
        this._positionModal(y, x);
        const side = placement.split("-")[0];
        const staticSide = {
            top: "bottom",
            right: "left",
            bottom: "top",
            left: "right"
        }[side]!;

        if (middlewareData.arrow) {
            const { x, y } = middlewareData.arrow;
            Object.assign(this.arrowElement!.style, {
                left: x != null ? `${x}px` : "",
                top: y != null ? `${y}px` : "",
                [staticSide]: `${-arrowLength}px`,
                right: "",
                bottom: "",
                [staticSide]: `${-arrowLength / 2}px`,
                transform: "rotate(45deg)"
            });
        }
    }

    setModalPosition(top: number, left: number, width: number, height: number) {
        let virtualElement = {
            getBoundingClientRect() {
                return {
                    x: 0,
                    y: 0,
                    top,
                    left,
                    bottom: height,
                    right: width,
                    width,
                    height,
                };
            }
        };
        this.positionModal(virtualElement);
    }
}
