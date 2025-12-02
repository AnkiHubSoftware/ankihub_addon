(function(n,d){typeof exports=="object"&&typeof module<"u"?d(exports):typeof define=="function"&&define.amd?define(["exports"],d):(n=typeof globalThis<"u"?globalThis:n||self,d(n.AnkiHub={}))})(this,(function(n){"use strict";function d(i,t){window.bridgeCommand(i,t)}class c{options;isVisible=!1;targetElement=null;modalElement;backdropElement;arrowElement=null;shadowRoot;hostElement;resizeHandler;resizeTimeout=null;constructor(t={}){this.options={body:"",footer:"",showCloseButton:!0,closeOnBackdropClick:!1,backdrop:!0,target:null,position:"center",arrowPosition:"top",showArrow:!0,blockTargetClick:!1,...t},this.createModal(),this.bindEvents()}createModal(){this.hostElement=document.createElement("div"),this.hostElement.style.position="fixed",this.hostElement.style.top="0",this.hostElement.style.left="0",this.hostElement.style.width="100%",this.hostElement.style.height="100%",this.hostElement.style.zIndex="10000",this.hostElement.style.pointerEvents="none",this.shadowRoot=this.hostElement.attachShadow({mode:"open"}),this.injectStyles(),this.backdropElement=document.createElement("div"),this.backdropElement.className="ah-modal-backdrop",this.modalElement=document.createElement("div"),this.modalElement.className="ah-modal-container";const t=document.createElement("div");if(t.className="ah-modal-content",this.options.showCloseButton){const o=document.createElement("div");o.className="ah-modal-header";const r=document.createElement("button");r.className="ah-modal-close-button",r.innerHTML="×",r.setAttribute("aria-label","Close modal"),o.appendChild(r),t.appendChild(o)}const e=document.createElement("div");if(e.className="ah-modal-body",typeof this.options.body=="string"?e.innerHTML=this.options.body:this.options.body instanceof Element&&e.appendChild(this.options.body),t.appendChild(e),this.options.footer){const o=document.createElement("div");o.className="ah-modal-footer",typeof this.options.footer=="string"?o.innerHTML=this.options.footer:this.options.footer instanceof HTMLElement&&o.appendChild(this.options.footer),t.appendChild(o)}this.modalElement.appendChild(t),this.options.body&&this.backdropElement.appendChild(this.modalElement),this.shadowRoot.appendChild(this.backdropElement)}injectStyles(){const t=document.createElement("style");t.textContent=`
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
                ${this.options.backdrop?"backdrop-filter: brightness(0.5);":""}
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
        `,this.shadowRoot.appendChild(t)}createArrow(){this.options.showArrow&&(this.arrowElement=document.createElement("div"),this.arrowElement.className="ah-modal-arrow",this.modalElement.appendChild(this.arrowElement))}updateArrowPosition(){this.arrowElement&&(this.arrowElement.classList.remove("ah-arrow-top","ah-arrow-bottom","ah-arrow-left","ah-arrow-right"),this.arrowElement.classList.add(`ah-arrow-${this.options.arrowPosition}`))}bindEvents(){const t=this.modalElement.querySelector(".ah-modal-close-button");t&&t.addEventListener("click",()=>{this.close(),d("ankihub_modal_closed")}),this.backdropElement.addEventListener("click",e=>{e.target===this.backdropElement&&this.options.closeOnBackdropClick&&this.close()}),this.resizeHandler=()=>{this.isVisible&&(this.resizeTimeout&&clearTimeout(this.resizeTimeout),this.resizeTimeout=setTimeout(()=>{this.positionModal(),this.updateArrowPosition()},16))},window.addEventListener("resize",this.resizeHandler),this.modalElement.addEventListener("click",e=>{e.stopPropagation()})}show(){this.isVisible||(document.body.appendChild(this.hostElement),this.options.target&&(this.targetElement=typeof this.options.target=="string"?document.querySelector(this.options.target):this.options.target,this.applySpotlight()),this.createArrow(),this.positionModal(),this.updateArrowPosition(),requestAnimationFrame(()=>{this.backdropElement.classList.add("ah-modal-show")}),this.isVisible=!0)}close(){this.isVisible&&(this.removeSpotlight(),this.arrowElement&&this.arrowElement.parentNode&&(this.arrowElement.parentNode.removeChild(this.arrowElement),this.arrowElement=null),this.backdropElement.classList.remove("ah-modal-show"),this.backdropElement.classList.add("ah-modal-hide"),setTimeout(()=>{this.hostElement.parentNode&&this.hostElement.parentNode.removeChild(this.hostElement),this.backdropElement.classList.remove("ah-modal-hide")},200),this.isVisible=!1)}destroy(){this.close(),window.removeEventListener("resize",this.resizeHandler),this.removeSpotlight(),this.resizeTimeout&&(clearTimeout(this.resizeTimeout),this.resizeTimeout=null),this.arrowElement&&this.arrowElement.parentNode&&(this.arrowElement.parentNode.removeChild(this.arrowElement),this.arrowElement=null)}spotlightClasses(){let t=["ah-spotlight-active"];return this.options.backdrop&&t.push("ah-with-backdrop"),t}applySpotlight(){if(this.targetElement){if(this.targetElement.classList.add(...this.spotlightClasses()),this.options.blockTargetClick){const t=this.targetElement.style.pointerEvents;this.targetElement.style.pointerEvents="none",this.targetElement.setAttribute("data-original-pointer-events",t)}this.targetElement.parentElement&&(this.targetElement.parentElement.style.backdropFilter="none")}}removeSpotlight(){if(!this.targetElement)return;this.targetElement.classList.remove(...this.spotlightClasses());const t=this.targetElement.getAttribute("data-original-pointer-events");t?(this.targetElement.style.pointerEvents=t,this.targetElement.removeAttribute("data-original-pointer-events")):this.targetElement.style.pointerEvents="",this.targetElement=null}_positionModal(t,e,o){this.modalElement.style.position="fixed",this.modalElement.style.top=`${typeof t=="string"?t:t+10}px`,this.modalElement.style.left=`${e}px`,this.modalElement.style.transform=o}positionModal(){if(!this.targetElement)return;const t=this.targetElement.getBoundingClientRect(),e=this.modalElement.getBoundingClientRect(),o=window.innerWidth,r=window.innerHeight;let a,s,h;switch(this.options.position){case"top":a=t.top-e.height-10,s=t.left+(t.width-e.width)/2,h="none";break;case"bottom":a=t.bottom+10,s=t.left+(t.width-e.width)/2,h="none";break;case"left":a=t.top+(t.height-e.height)/2,s=t.left-e.width-10,h="none";break;case"right":a=t.top+(t.height-e.height)/2,s=t.right+10,h="none";break;default:a="50%",s="50%",h="translate(-50%, -50%)"}const b=typeof a=="string"?a:Math.max(10,Math.min(a,r-e.height-10)),u=typeof s=="string"?s:Math.max(10,Math.min(s,o-e.width-10));this._positionModal(b,u,h),this.updateArrowPosition()}setModalPosition(t,e,o=""){this._positionModal(t,e,o)}}let l=null,m=null;function p(){l&&(l.destroy(),l=null)}function g(){p();const i=`
<h2>📚 First time with Anki?</h2>
<p>Find your way in the app with this onboarding tour.</p>
    `,t=`<button class="ah-button ah-secondary-button" onclick="destroyAnkiHubTutorialModal()">Close</button><button class="ah-button ah-primary-button" onclick="pycmd('ankihub_start_onboarding')">Take tour</button>`,e=new c({body:i,footer:t,showArrow:!1});e.show(),l=e}function w({body:i,currentStep:t,stepCount:e,target:o,position:r="bottom",primaryButton:a={show:!0,label:"Next"},showArrow:s=!0,blockTargetClick:h=!1,backdrop:b=!0}){p();let u=`<span>${t} of ${e}</span>`;a.show&&(u+=`<button class="ah-button ah-primary-button" onclick="pycmd('ankihub_tutorial_primary_button_clicked')">${a.label}</button>`);const f=new c({body:i,footer:u,target:o,position:r,showArrow:s,blockTargetClick:h,backdrop:b});f.show(),l=f}function E({target:i,currentStep:t,blockTargetClick:e=!1}){p();var o=new c({body:"",footer:"",target:i,blockTargetClick:e});o.show(),l=o,m&&(window.removeEventListener("resize",m),m=null),m=()=>{const{top:r,left:a,width:s}=o.targetElement.getBoundingClientRect();d(`ankihub_tutorial_target_resize:${t}:${r}:${a}:${s}`)},window.addEventListener("resize",m),m()}function y({top:i,left:t,transform:e}){l&&l.setModalPosition(i,t,e)}function v(){p();var i=new c({body:"",footer:""});i.show(),l=i}n.Modal=c,n.addTutorialBackdrop=v,n.destroyActiveTutorialModal=p,n.highlightTutorialTarget=E,n.positionTutorialTarget=y,n.promptForOnboardingTour=g,n.showTutorialModal=w,Object.defineProperty(n,Symbol.toStringTag,{value:"Module"})}));
