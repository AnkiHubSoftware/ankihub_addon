import { arrow, autoPlacement, autoUpdate, computePosition, offset, type ReferenceElement } from '@floating-ui/dom';
import Alpine from 'alpinejs';
import { bridgeCommand } from "./bridgecommand";
import tailwindCss from './vendor/tailwind.css?inline';

let propertyRulesInjected = false;
let fontImportsInjected = false;

/**
 * Extract @property rules from CSS and inject them into the main document.
 * This is necessary because @property rules don't work inside shadow DOM -
 * they must be registered at the document level.
 */
function injectPropertyRulesIntoDocument(css: string): void {
    if (propertyRulesInjected) return;

    const propertyRuleRegex = /@property\s+[^{]+\{[^}]*\}/g;
    const propertyRules = css.match(propertyRuleRegex);

    if (propertyRules && propertyRules.length > 0) {
        const style = document.createElement("style");
        style.id = "ankihub-property-rules";
        style.textContent = propertyRules.join("\n");
        document.head.appendChild(style);
        propertyRulesInjected = true;
    }
}

/**
 * Extract Google Font @import rules from CSS and inject them into the main document.
 * This is necessary because @font-face rules (which the @import resolves to) don't work
 * inside shadow DOM - fonts must be loaded at the document level.
 */
function injectFontImportsIntoDocument(css: string): void {
    if (fontImportsInjected) return;

    const fontImportRegex = /@import.*?googleapis.*?";/g;
    const fontImports = css.match(fontImportRegex);
    if (fontImports && fontImports.length > 0) {
        const style = document.createElement("style");
        style.id = "ankihub-font-imports";
        style.textContent = fontImports.join("\n");
        document.head.appendChild(style);
        fontImportsInjected = true;
    }
}


function getTargetElement(target: string | HTMLElement): HTMLElement | null {
    return typeof target === "string"
        ? document.querySelector(target)
        : target
}

export function elementFromHtml(html: string): HTMLElement {
    const template = document.createElement("template");
    template.innerHTML = html;
    return template.content.firstElementChild as HTMLElement;
}

type TutorialEffectOptions = {
    modal?: string | HTMLElement,
    arrow?: string | HTMLElement,
    backdrop?: string | HTMLElement,
    target?: string | HTMLElement,
    blockTargetClick: boolean,
    clickTarget?: string,
};

export class TutorialEffect {

    options: TutorialEffectOptions;
    targetElement?: HTMLElement;
    clickTargetElement?: HTMLElement;
    modalElement!: HTMLElement;
    arrowElement?: HTMLElement;
    shadowRoot!: ShadowRoot;
    hostElement!: HTMLDivElement;
    cleanUpdateHandler?: () => void;

    constructor(options: Partial<TutorialEffectOptions> = {}) {
        this.options = {
            modal: "",
            blockTargetClick: false,
            clickTarget: "",
            ...options,
        };
        this.create();
    }

    create() {
        this.hostElement = document.createElement("div");
        this.hostElement.style.position = "fixed";
        this.hostElement.style.top = "0";
        this.hostElement.style.left = "0";
        this.hostElement.style.width = "100%";
        this.hostElement.style.height = "100%";
        this.hostElement.style.zIndex = "10000";
        this.shadowRoot = this.hostElement.attachShadow({ mode: "open" });
        let modalElement: HTMLElement | null = null;
        if (this.options.modal instanceof HTMLElement) {
            modalElement = this.options.modal;
        } else if (this.options.modal) {
            modalElement = elementFromHtml(this.options.modal);
        } else {
            modalElement = document.createElement("div");
        }
        this.modalElement = modalElement;
        let arrowElement: HTMLElement | null = null;
        if (this.options.arrow instanceof HTMLElement) {
            arrowElement = this.options.arrow;
        } else if (this.options.arrow) {
            arrowElement = elementFromHtml(this.options.arrow);
        }
        if (arrowElement) {
            this.arrowElement = arrowElement;
            this.getStepElement().appendChild(this.arrowElement);
        }
        let backdropElement: HTMLElement | null = null;
        if (this.options.backdrop instanceof HTMLElement) {
            backdropElement = this.options.backdrop;
        } else if (this.options.backdrop) {
            backdropElement = elementFromHtml(this.options.backdrop);
        }
        if (backdropElement) {
            this.modalElement.appendChild(backdropElement);
        }

        this.shadowRoot.appendChild(this.modalElement);
        if (this.options.target) {
            this.targetElement = getTargetElement(this.options.target)!;
            if (this.options.modal) {
                this.cleanUpdateHandler = autoUpdate(this.targetElement, this.modalElement, this.positionModal.bind(this, this.targetElement));
            }
            if (this.options.clickTarget) {
                this.clickTargetElement = document.querySelector(this.options.clickTarget) ?? undefined;
                if (this.clickTargetElement) {
                    this.clickTargetElement.addEventListener("click", this.targetClickHandler);
                    this.clickTargetElement.setAttribute("data-ah-original-onclick", this.clickTargetElement.getAttribute("onclick") ?? "");
                    this.clickTargetElement.removeAttribute("onclick");
                }
            }

        }

        const css = tailwindCss.replaceAll(":root", ":host");
        injectPropertyRulesIntoDocument(css);
        injectFontImportsIntoDocument(css);
        const style = document.createElement("style");
        style.textContent = css;
        this.shadowRoot.append(style);
        Alpine.initTree(this.modalElement);
    }

    targetClickHandler(event: MouseEvent) {
        event.preventDefault();
        event.stopPropagation();
        bridgeCommand("ankihub_tutorial_target_click");
    }

    show() {
        document.body.appendChild(this.hostElement);
        if (this.targetElement) {
            this.applySpotlight();
            this.positionModal(this.targetElement);
        }
    }

    destroy() {
        this.cleanUpdateHandler?.();
        this.clickTargetElement?.removeEventListener("click", this.targetClickHandler);
        this.clickTargetElement?.setAttribute("onclick", this.clickTargetElement.getAttribute("data-ah-original-onclick") ?? "");
        this.clickTargetElement?.removeAttribute("data-ah-original-onclick");
        this.removeSpotlight();
        this.hostElement.remove();
    }

    spotlightClasses() {
        let classes = ["ah-spotlight-active"];
        if (this.options.backdrop || this.options.modal) {
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
    }

    getStepElement(): HTMLElement {
        return this.modalElement.getElementsByClassName("ah-tour-step")[0] as HTMLElement;
    }

    _positionModal(y: number, x: number) {
        const stepElement = this.getStepElement();
        stepElement.style.top = `${y}px`;
        stepElement.style.left = `${x}px`;
    }

    async positionModal(target: ReferenceElement) {
        if (!this.options.modal) {
            return;
        }
        const arrowLength = this.arrowElement?.offsetWidth ?? 0;
        const floatingOffset = Math.sqrt(2 * arrowLength ** 2) / 2;

        let middleware = [autoPlacement()];
        if (this.arrowElement) {
            middleware.push(offset(floatingOffset));
            middleware.push(arrow({ element: this.arrowElement }));
        }
        const { x, y, middlewareData, placement } = await computePosition(target, this.getStepElement(), {
            middleware
        });
        this._positionModal(y, x);
        const side = placement.split("-")[0];
        const staticSide = {
            top: "bottom",
            right: "left",
            bottom: "top",
            left: "right",
        }[side]!;

        if (middlewareData.arrow) {
            const { x, y } = middlewareData.arrow;
            Object.assign(this.arrowElement!.style, {
                left: x != null ? `${x}px` : "",
                top: y != null ? `${y}px` : "",
                right: "",
                bottom: "",
                [staticSide]: `${-arrowLength / 2}px`,
                transform: "rotate(45deg)",
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

let activeEffect: TutorialEffect | null = null;
let targetResizeHandler: (() => void) | null = null;

export function destroyActiveTutorialEffect() {
    if (activeEffect) {
        activeEffect.destroy();
        activeEffect = null;
    }
}

function createAndShowEffect(options: Partial<TutorialEffectOptions>): TutorialEffect {
    destroyActiveTutorialEffect();
    const effect = new TutorialEffect(options);
    effect.show();
    activeEffect = effect;
    return effect;
}

export function showModal(modal: string) {
    createAndShowEffect({ modal });
}

type ShowStepArgs = {
    modal: string,
    arrow: string,
    target: string,
    blockTargetClick?: boolean,
    clickTarget?: string,
};

export function showTutorialStep({
    modal,
    arrow,
    target,
    blockTargetClick = false,
    clickTarget = "",
}: ShowStepArgs) {
    createAndShowEffect({
        modal,
        arrow,
        target,
        blockTargetClick,
        clickTarget,
    });
}

type HighlightTargetArgs = {
    target: string | HTMLElement,
    currentStep: number,
    blockTargetClick?: boolean,
    backdrop?: string,
};

export function highlightTutorialTarget({
    target,
    currentStep,
    blockTargetClick = false,
    backdrop,
}: HighlightTargetArgs) {
    const effect = createAndShowEffect({
        target,
        blockTargetClick,
        backdrop,
    });
    if (targetResizeHandler) {
        window.removeEventListener(
            "resize",
            targetResizeHandler
        );
        targetResizeHandler = null;
    }
    targetResizeHandler = () => {
        if (!effect.targetElement) return;
        const { top, left, width, height } =
            effect.targetElement!.getBoundingClientRect();
        bridgeCommand(
            `ankihub_tutorial_target_resize:${currentStep}:${top}:${left}:${width}:${height}`
        );
    };
    window.addEventListener(
        "resize",
        targetResizeHandler
    );
    targetResizeHandler();
}

type PositionTargetArgs = {
    top: number,
    left: number,
    width: number,
    height: number,
};

export function positionTutorialModal({ top, left, width, height }: PositionTargetArgs) {
    if (activeEffect) {
        activeEffect.setModalPosition(top, left, width, height);
    }
}

export function addTutorialBackdrop(backdrop: string) {
    createAndShowEffect({ backdrop });
}

