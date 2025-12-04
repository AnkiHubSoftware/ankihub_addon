(function(k,q){typeof exports=="object"&&typeof module<"u"?q(exports):typeof define=="function"&&define.amd?define(["exports"],q):(k=typeof globalThis<"u"?globalThis:k||self,q(k.AnkiHub={}))})(this,(function(k){"use strict";const q=["top","right","bottom","left"],bt=["start","end"],yt=q.reduce((t,e)=>t.concat(e,e+"-"+bt[0],e+"-"+bt[1]),[]),V=Math.min,B=Math.max,Q=Math.round,Z=Math.floor,A=t=>({x:t,y:t}),$t={left:"right",right:"left",bottom:"top",top:"bottom"},_t={start:"end",end:"start"};function Vt(t,e,o){return B(t,V(e,o))}function tt(t,e){return typeof t=="function"?t(e):t}function X(t){return t.split("-")[0]}function M(t){return t.split("-")[1]}function Ht(t){return t==="x"?"y":"x"}function lt(t){return t==="y"?"height":"width"}const zt=new Set(["top","bottom"]);function at(t){return zt.has(X(t))?"y":"x"}function ct(t){return Ht(at(t))}function Wt(t,e,o){o===void 0&&(o=!1);const n=M(t),i=ct(t),r=lt(i);let s=i==="x"?n===(o?"end":"start")?"right":"left":n==="start"?"bottom":"top";return e.reference[r]>e.floating[r]&&(s=xt(s)),[s,xt(s)]}function jt(t){return t.replace(/start|end/g,e=>_t[e])}function xt(t){return t.replace(/left|right|bottom|top/g,e=>$t[e])}function It(t){return{top:0,right:0,bottom:0,left:0,...t}}function vt(t){return typeof t!="number"?It(t):{top:t,right:t,bottom:t,left:t}}function et(t){const{x:e,y:o,width:n,height:i}=t;return{width:n,height:i,top:o,left:e,right:e+n,bottom:o+i,x:e,y:o}}function Et(t,e,o){let{reference:n,floating:i}=t;const r=at(e),s=ct(e),l=lt(s),a=X(e),c=r==="y",h=n.x+n.width/2-i.width/2,f=n.y+n.height/2-i.height/2,u=n[l]/2-i[l]/2;let d;switch(a){case"top":d={x:h,y:n.y-i.height};break;case"bottom":d={x:h,y:n.y+n.height};break;case"right":d={x:n.x+n.width,y:f};break;case"left":d={x:n.x-i.width,y:f};break;default:d={x:n.x,y:n.y}}switch(M(e)){case"start":d[s]-=u*(o&&c?-1:1);break;case"end":d[s]+=u*(o&&c?-1:1);break}return d}const qt=async(t,e,o)=>{const{placement:n="bottom",strategy:i="absolute",middleware:r=[],platform:s}=o,l=r.filter(Boolean),a=await(s.isRTL==null?void 0:s.isRTL(e));let c=await s.getElementRects({reference:t,floating:e,strategy:i}),{x:h,y:f}=Et(c,n,a),u=n,d={},m=0;for(let p=0;p<l.length;p++){const{name:w,fn:g}=l[p],{x:b,y:x,data:v,reset:y}=await g({x:h,y:f,initialPlacement:n,placement:u,strategy:i,middlewareData:d,rects:c,platform:s,elements:{reference:t,floating:e}});h=b??h,f=x??f,d={...d,[w]:{...d[w],...v}},y&&m<=50&&(m++,typeof y=="object"&&(y.placement&&(u=y.placement),y.rects&&(c=y.rects===!0?await s.getElementRects({reference:t,floating:e,strategy:i}):y.rects),{x:h,y:f}=Et(c,u,a)),p=-1)}return{x:h,y:f,placement:u,strategy:i,middlewareData:d}};async function Xt(t,e){var o;e===void 0&&(e={});const{x:n,y:i,platform:r,rects:s,elements:l,strategy:a}=t,{boundary:c="clippingAncestors",rootBoundary:h="viewport",elementContext:f="floating",altBoundary:u=!1,padding:d=0}=tt(e,t),m=vt(d),w=l[u?f==="floating"?"reference":"floating":f],g=et(await r.getClippingRect({element:(o=await(r.isElement==null?void 0:r.isElement(w)))==null||o?w:w.contextElement||await(r.getDocumentElement==null?void 0:r.getDocumentElement(l.floating)),boundary:c,rootBoundary:h,strategy:a})),b=f==="floating"?{x:n,y:i,width:s.floating.width,height:s.floating.height}:s.reference,x=await(r.getOffsetParent==null?void 0:r.getOffsetParent(l.floating)),v=await(r.isElement==null?void 0:r.isElement(x))?await(r.getScale==null?void 0:r.getScale(x))||{x:1,y:1}:{x:1,y:1},y=et(r.convertOffsetParentRelativeRectToViewportRelativeRect?await r.convertOffsetParentRelativeRectToViewportRelativeRect({elements:l,rect:b,offsetParent:x,strategy:a}):b);return{top:(g.top-y.top+m.top)/v.y,bottom:(y.bottom-g.bottom+m.bottom)/v.y,left:(g.left-y.left+m.left)/v.x,right:(y.right-g.right+m.right)/v.x}}const Ut=t=>({name:"arrow",options:t,async fn(e){const{x:o,y:n,placement:i,rects:r,platform:s,elements:l,middlewareData:a}=e,{element:c,padding:h=0}=tt(t,e)||{};if(c==null)return{};const f=vt(h),u={x:o,y:n},d=ct(i),m=lt(d),p=await s.getDimensions(c),w=d==="y",g=w?"top":"left",b=w?"bottom":"right",x=w?"clientHeight":"clientWidth",v=r.reference[m]+r.reference[d]-u[d]-r.floating[m],y=u[d]-r.reference[d],F=await(s.getOffsetParent==null?void 0:s.getOffsetParent(c));let C=F?F[x]:0;(!C||!await(s.isElement==null?void 0:s.isElement(F)))&&(C=l.floating[x]||r.floating[m]);const Bt=v/2-y/2,J=C/2-p[m]/2-1,T=V(f[g],J),N=V(f[b],J),I=T,rt=C-p[m]-N,_=C/2-p[m]/2+Bt,pt=Vt(I,_,rt),gt=!a.arrow&&M(i)!=null&&_!==pt&&r.reference[m]/2-(_<I?T:N)-p[m]/2<0,wt=gt?_<I?_-I:_-rt:0;return{[d]:u[d]+wt,data:{[d]:pt,centerOffset:_-pt-wt,...gt&&{alignmentOffset:wt}},reset:gt}}});function Yt(t,e,o){return(t?[...o.filter(i=>M(i)===t),...o.filter(i=>M(i)!==t)]:o.filter(i=>X(i)===i)).filter(i=>t?M(i)===t||(e?jt(i)!==i:!1):!0)}const Kt=function(t){return t===void 0&&(t={}),{name:"autoPlacement",options:t,async fn(e){var o,n,i;const{rects:r,middlewareData:s,placement:l,platform:a,elements:c}=e,{crossAxis:h=!1,alignment:f,allowedPlacements:u=yt,autoAlignment:d=!0,...m}=tt(t,e),p=f!==void 0||u===yt?Yt(f||null,d,u):u,w=await Xt(e,m),g=((o=s.autoPlacement)==null?void 0:o.index)||0,b=p[g];if(b==null)return{};const x=Wt(b,r,await(a.isRTL==null?void 0:a.isRTL(c.floating)));if(l!==b)return{reset:{placement:p[0]}};const v=[w[X(b)],w[x[0]],w[x[1]]],y=[...((n=s.autoPlacement)==null?void 0:n.overflows)||[],{placement:b,overflows:v}],F=p[g+1];if(F)return{data:{index:g+1,overflows:y},reset:{placement:F}};const C=y.map(T=>{const N=M(T.placement);return[T.placement,N&&h?T.overflows.slice(0,2).reduce((I,rt)=>I+rt,0):T.overflows[0],T.overflows]}).sort((T,N)=>T[1]-N[1]),J=((i=C.filter(T=>T[2].slice(0,M(T[0])?2:3).every(N=>N<=0))[0])==null?void 0:i[0])||C[0][0];return J!==l?{data:{index:g+1,overflows:y},reset:{placement:J}}:{}}}},Gt=new Set(["left","top"]);async function Jt(t,e){const{placement:o,platform:n,elements:i}=t,r=await(n.isRTL==null?void 0:n.isRTL(i.floating)),s=X(o),l=M(o),a=at(o)==="y",c=Gt.has(s)?-1:1,h=r&&a?-1:1,f=tt(e,t);let{mainAxis:u,crossAxis:d,alignmentAxis:m}=typeof f=="number"?{mainAxis:f,crossAxis:0,alignmentAxis:null}:{mainAxis:f.mainAxis||0,crossAxis:f.crossAxis||0,alignmentAxis:f.alignmentAxis};return l&&typeof m=="number"&&(d=l==="end"?m*-1:m),a?{x:d*h,y:u*c}:{x:u*c,y:d*h}}const Qt=function(t){return t===void 0&&(t=0),{name:"offset",options:t,async fn(e){var o,n;const{x:i,y:r,placement:s,middlewareData:l}=e,a=await Jt(e,t);return s===((o=l.offset)==null?void 0:o.placement)&&(n=l.arrow)!=null&&n.alignmentOffset?{}:{x:i+a.x,y:r+a.y,data:{...a,placement:s}}}}};function ot(){return typeof window<"u"}function H(t){return Ct(t)?(t.nodeName||"").toLowerCase():"#document"}function E(t){var e;return(t==null||(e=t.ownerDocument)==null?void 0:e.defaultView)||window}function S(t){var e;return(e=(Ct(t)?t.ownerDocument:t.document)||window.document)==null?void 0:e.documentElement}function Ct(t){return ot()?t instanceof Node||t instanceof E(t).Node:!1}function R(t){return ot()?t instanceof Element||t instanceof E(t).Element:!1}function L(t){return ot()?t instanceof HTMLElement||t instanceof E(t).HTMLElement:!1}function Tt(t){return!ot()||typeof ShadowRoot>"u"?!1:t instanceof ShadowRoot||t instanceof E(t).ShadowRoot}const Zt=new Set(["inline","contents"]);function U(t){const{overflow:e,overflowX:o,overflowY:n,display:i}=O(t);return/auto|scroll|overlay|hidden|clip/.test(e+n+o)&&!Zt.has(i)}const te=new Set(["table","td","th"]);function ee(t){return te.has(H(t))}const oe=[":popover-open",":modal"];function nt(t){return oe.some(e=>{try{return t.matches(e)}catch{return!1}})}const ne=["transform","translate","scale","rotate","perspective"],ie=["transform","translate","scale","rotate","perspective","filter"],se=["paint","layout","strict","content"];function dt(t){const e=ft(),o=R(t)?O(t):t;return ne.some(n=>o[n]?o[n]!=="none":!1)||(o.containerType?o.containerType!=="normal":!1)||!e&&(o.backdropFilter?o.backdropFilter!=="none":!1)||!e&&(o.filter?o.filter!=="none":!1)||ie.some(n=>(o.willChange||"").includes(n))||se.some(n=>(o.contain||"").includes(n))}function re(t){let e=D(t);for(;L(e)&&!z(e);){if(dt(e))return e;if(nt(e))return null;e=D(e)}return null}function ft(){return typeof CSS>"u"||!CSS.supports?!1:CSS.supports("-webkit-backdrop-filter","none")}const le=new Set(["html","body","#document"]);function z(t){return le.has(H(t))}function O(t){return E(t).getComputedStyle(t)}function it(t){return R(t)?{scrollLeft:t.scrollLeft,scrollTop:t.scrollTop}:{scrollLeft:t.scrollX,scrollTop:t.scrollY}}function D(t){if(H(t)==="html")return t;const e=t.assignedSlot||t.parentNode||Tt(t)&&t.host||S(t);return Tt(e)?e.host:e}function kt(t){const e=D(t);return z(e)?t.ownerDocument?t.ownerDocument.body:t.body:L(e)&&U(e)?e:kt(e)}function Y(t,e,o){var n;e===void 0&&(e=[]),o===void 0&&(o=!0);const i=kt(t),r=i===((n=t.ownerDocument)==null?void 0:n.body),s=E(i);if(r){const l=ht(s);return e.concat(s,s.visualViewport||[],U(i)?i:[],l&&o?Y(l):[])}return e.concat(i,Y(i,[],o))}function ht(t){return t.parent&&Object.getPrototypeOf(t.parent)?t.frameElement:null}function Rt(t){const e=O(t);let o=parseFloat(e.width)||0,n=parseFloat(e.height)||0;const i=L(t),r=i?t.offsetWidth:o,s=i?t.offsetHeight:n,l=Q(o)!==r||Q(n)!==s;return l&&(o=r,n=s),{width:o,height:n,$:l}}function ut(t){return R(t)?t:t.contextElement}function W(t){const e=ut(t);if(!L(e))return A(1);const o=e.getBoundingClientRect(),{width:n,height:i,$:r}=Rt(e);let s=(r?Q(o.width):o.width)/n,l=(r?Q(o.height):o.height)/i;return(!s||!Number.isFinite(s))&&(s=1),(!l||!Number.isFinite(l))&&(l=1),{x:s,y:l}}const ae=A(0);function Ot(t){const e=E(t);return!ft()||!e.visualViewport?ae:{x:e.visualViewport.offsetLeft,y:e.visualViewport.offsetTop}}function ce(t,e,o){return e===void 0&&(e=!1),!o||e&&o!==E(t)?!1:e}function $(t,e,o,n){e===void 0&&(e=!1),o===void 0&&(o=!1);const i=t.getBoundingClientRect(),r=ut(t);let s=A(1);e&&(n?R(n)&&(s=W(n)):s=W(t));const l=ce(r,o,n)?Ot(r):A(0);let a=(i.left+l.x)/s.x,c=(i.top+l.y)/s.y,h=i.width/s.x,f=i.height/s.y;if(r){const u=E(r),d=n&&R(n)?E(n):n;let m=u,p=ht(m);for(;p&&n&&d!==m;){const w=W(p),g=p.getBoundingClientRect(),b=O(p),x=g.left+(p.clientLeft+parseFloat(b.paddingLeft))*w.x,v=g.top+(p.clientTop+parseFloat(b.paddingTop))*w.y;a*=w.x,c*=w.y,h*=w.x,f*=w.y,a+=x,c+=v,m=E(p),p=ht(m)}}return et({width:h,height:f,x:a,y:c})}function st(t,e){const o=it(t).scrollLeft;return e?e.left+o:$(S(t)).left+o}function At(t,e){const o=t.getBoundingClientRect(),n=o.left+e.scrollLeft-st(t,o),i=o.top+e.scrollTop;return{x:n,y:i}}function de(t){let{elements:e,rect:o,offsetParent:n,strategy:i}=t;const r=i==="fixed",s=S(n),l=e?nt(e.floating):!1;if(n===s||l&&r)return o;let a={scrollLeft:0,scrollTop:0},c=A(1);const h=A(0),f=L(n);if((f||!f&&!r)&&((H(n)!=="body"||U(s))&&(a=it(n)),L(n))){const d=$(n);c=W(n),h.x=d.x+n.clientLeft,h.y=d.y+n.clientTop}const u=s&&!f&&!r?At(s,a):A(0);return{width:o.width*c.x,height:o.height*c.y,x:o.x*c.x-a.scrollLeft*c.x+h.x+u.x,y:o.y*c.y-a.scrollTop*c.y+h.y+u.y}}function fe(t){return Array.from(t.getClientRects())}function he(t){const e=S(t),o=it(t),n=t.ownerDocument.body,i=B(e.scrollWidth,e.clientWidth,n.scrollWidth,n.clientWidth),r=B(e.scrollHeight,e.clientHeight,n.scrollHeight,n.clientHeight);let s=-o.scrollLeft+st(t);const l=-o.scrollTop;return O(n).direction==="rtl"&&(s+=B(e.clientWidth,n.clientWidth)-i),{width:i,height:r,x:s,y:l}}const St=25;function ue(t,e){const o=E(t),n=S(t),i=o.visualViewport;let r=n.clientWidth,s=n.clientHeight,l=0,a=0;if(i){r=i.width,s=i.height;const h=ft();(!h||h&&e==="fixed")&&(l=i.offsetLeft,a=i.offsetTop)}const c=st(n);if(c<=0){const h=n.ownerDocument,f=h.body,u=getComputedStyle(f),d=h.compatMode==="CSS1Compat"&&parseFloat(u.marginLeft)+parseFloat(u.marginRight)||0,m=Math.abs(n.clientWidth-f.clientWidth-d);m<=St&&(r-=m)}else c<=St&&(r+=c);return{width:r,height:s,x:l,y:a}}const me=new Set(["absolute","fixed"]);function pe(t,e){const o=$(t,!0,e==="fixed"),n=o.top+t.clientTop,i=o.left+t.clientLeft,r=L(t)?W(t):A(1),s=t.clientWidth*r.x,l=t.clientHeight*r.y,a=i*r.x,c=n*r.y;return{width:s,height:l,x:a,y:c}}function Lt(t,e,o){let n;if(e==="viewport")n=ue(t,o);else if(e==="document")n=he(S(t));else if(R(e))n=pe(e,o);else{const i=Ot(t);n={x:e.x-i.x,y:e.y-i.y,width:e.width,height:e.height}}return et(n)}function Mt(t,e){const o=D(t);return o===e||!R(o)||z(o)?!1:O(o).position==="fixed"||Mt(o,e)}function ge(t,e){const o=e.get(t);if(o)return o;let n=Y(t,[],!1).filter(l=>R(l)&&H(l)!=="body"),i=null;const r=O(t).position==="fixed";let s=r?D(t):t;for(;R(s)&&!z(s);){const l=O(s),a=dt(s);!a&&l.position==="fixed"&&(i=null),(r?!a&&!i:!a&&l.position==="static"&&!!i&&me.has(i.position)||U(s)&&!a&&Mt(t,s))?n=n.filter(h=>h!==s):i=l,s=D(s)}return e.set(t,n),n}function we(t){let{element:e,boundary:o,rootBoundary:n,strategy:i}=t;const s=[...o==="clippingAncestors"?nt(e)?[]:ge(e,this._c):[].concat(o),n],l=s[0],a=s.reduce((c,h)=>{const f=Lt(e,h,i);return c.top=B(f.top,c.top),c.right=V(f.right,c.right),c.bottom=V(f.bottom,c.bottom),c.left=B(f.left,c.left),c},Lt(e,l,i));return{width:a.right-a.left,height:a.bottom-a.top,x:a.left,y:a.top}}function be(t){const{width:e,height:o}=Rt(t);return{width:e,height:o}}function ye(t,e,o){const n=L(e),i=S(e),r=o==="fixed",s=$(t,!0,r,e);let l={scrollLeft:0,scrollTop:0};const a=A(0);function c(){a.x=st(i)}if(n||!n&&!r)if((H(e)!=="body"||U(i))&&(l=it(e)),n){const d=$(e,!0,r,e);a.x=d.x+e.clientLeft,a.y=d.y+e.clientTop}else i&&c();r&&!n&&i&&c();const h=i&&!n&&!r?At(i,l):A(0),f=s.left+l.scrollLeft-a.x-h.x,u=s.top+l.scrollTop-a.y-h.y;return{x:f,y:u,width:s.width,height:s.height}}function mt(t){return O(t).position==="static"}function Pt(t,e){if(!L(t)||O(t).position==="fixed")return null;if(e)return e(t);let o=t.offsetParent;return S(t)===o&&(o=o.ownerDocument.body),o}function Ft(t,e){const o=E(t);if(nt(t))return o;if(!L(t)){let i=D(t);for(;i&&!z(i);){if(R(i)&&!mt(i))return i;i=D(i)}return o}let n=Pt(t,e);for(;n&&ee(n)&&mt(n);)n=Pt(n,e);return n&&z(n)&&mt(n)&&!dt(n)?o:n||re(t)||o}const xe=async function(t){const e=this.getOffsetParent||Ft,o=this.getDimensions,n=await o(t.floating);return{reference:ye(t.reference,await e(t.floating),t.strategy),floating:{x:0,y:0,width:n.width,height:n.height}}};function ve(t){return O(t).direction==="rtl"}const Ee={convertOffsetParentRelativeRectToViewportRelativeRect:de,getDocumentElement:S,getClippingRect:we,getOffsetParent:Ft,getElementRects:xe,getClientRects:fe,getDimensions:be,getScale:W,isElement:R,isRTL:ve};function Dt(t,e){return t.x===e.x&&t.y===e.y&&t.width===e.width&&t.height===e.height}function Ce(t,e){let o=null,n;const i=S(t);function r(){var l;clearTimeout(n),(l=o)==null||l.disconnect(),o=null}function s(l,a){l===void 0&&(l=!1),a===void 0&&(a=1),r();const c=t.getBoundingClientRect(),{left:h,top:f,width:u,height:d}=c;if(l||e(),!u||!d)return;const m=Z(f),p=Z(i.clientWidth-(h+u)),w=Z(i.clientHeight-(f+d)),g=Z(h),x={rootMargin:-m+"px "+-p+"px "+-w+"px "+-g+"px",threshold:B(0,V(1,a))||1};let v=!0;function y(F){const C=F[0].intersectionRatio;if(C!==a){if(!v)return s();C?s(!1,C):n=setTimeout(()=>{s(!1,1e-7)},1e3)}C===1&&!Dt(c,t.getBoundingClientRect())&&s(),v=!1}try{o=new IntersectionObserver(y,{...x,root:i.ownerDocument})}catch{o=new IntersectionObserver(y,x)}o.observe(t)}return s(!0),r}function Te(t,e,o,n){n===void 0&&(n={});const{ancestorScroll:i=!0,ancestorResize:r=!0,elementResize:s=typeof ResizeObserver=="function",layoutShift:l=typeof IntersectionObserver=="function",animationFrame:a=!1}=n,c=ut(t),h=i||r?[...c?Y(c):[],...Y(e)]:[];h.forEach(g=>{i&&g.addEventListener("scroll",o,{passive:!0}),r&&g.addEventListener("resize",o)});const f=c&&l?Ce(c,o):null;let u=-1,d=null;s&&(d=new ResizeObserver(g=>{let[b]=g;b&&b.target===c&&d&&(d.unobserve(e),cancelAnimationFrame(u),u=requestAnimationFrame(()=>{var x;(x=d)==null||x.observe(e)})),o()}),c&&!a&&d.observe(c),d.observe(e));let m,p=a?$(t):null;a&&w();function w(){const g=$(t);p&&!Dt(p,g)&&o(),p=g,m=requestAnimationFrame(w)}return o(),()=>{var g;h.forEach(b=>{i&&b.removeEventListener("scroll",o),r&&b.removeEventListener("resize",o)}),f?.(),(g=d)==null||g.disconnect(),d=null,a&&cancelAnimationFrame(m)}}const ke=Qt,Re=Kt,Oe=Ut,Ae=(t,e,o)=>{const n=new Map,i={platform:Ee,...o},r={...i.platform,_c:n};return qt(t,e,{...i,platform:r})};function Nt(t,e){window.bridgeCommand(t,e)}class K{options;isVisible=!1;targetElement=null;modalElement;backdropElement;arrowElement=null;shadowRoot;hostElement;cleanUpdateHandler;resizeTimeout=null;constructor(e={}){this.options={body:"",footer:"",showCloseButton:!0,closeOnBackdropClick:!1,backdrop:!0,target:null,showArrow:!0,blockTargetClick:!1,...e},this.createModal(),this.bindEvents()}createModal(){this.hostElement=document.createElement("div"),this.hostElement.style.position="fixed",this.hostElement.style.top="0",this.hostElement.style.left="0",this.hostElement.style.width="100%",this.hostElement.style.height="100%",this.hostElement.style.zIndex="10000",this.hostElement.style.pointerEvents="none",this.shadowRoot=this.hostElement.attachShadow({mode:"open"}),this.injectStyles(),this.backdropElement=document.createElement("div"),this.backdropElement.className="ah-modal-backdrop",this.modalElement=document.createElement("div"),this.modalElement.className="ah-modal-container";const e=document.createElement("div");if(e.className="ah-modal-content",this.options.showCloseButton){const n=document.createElement("div");n.className="ah-modal-header";const i=document.createElement("button");i.className="ah-modal-close-button",i.innerHTML="×",i.setAttribute("aria-label","Close modal"),n.appendChild(i),e.appendChild(n)}const o=document.createElement("div");if(o.className="ah-modal-body",typeof this.options.body=="string"?o.innerHTML=this.options.body:this.options.body instanceof Element&&o.appendChild(this.options.body),e.appendChild(o),this.options.footer){const n=document.createElement("div");n.className="ah-modal-footer",typeof this.options.footer=="string"?n.innerHTML=this.options.footer:this.options.footer instanceof HTMLElement&&n.appendChild(this.options.footer),e.appendChild(n)}this.modalElement.appendChild(e),this.options.body&&(this.backdropElement.appendChild(this.modalElement),this.targetElement&&(this.cleanUpdateHandler=Te(this.targetElement,this.modalElement,this.positionModal.bind(this,this.targetElement)))),this.shadowRoot.appendChild(this.backdropElement)}injectStyles(){const e=document.createElement("style");e.textContent=`
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
        `,this.shadowRoot.appendChild(e)}createArrow(){this.options.showArrow&&(this.arrowElement=document.createElement("div"),this.arrowElement.className="ah-modal-arrow",this.modalElement.appendChild(this.arrowElement))}bindEvents(){const e=this.modalElement.querySelector(".ah-modal-close-button");e&&e.addEventListener("click",()=>{this.close(),Nt("ankihub_modal_closed")}),this.backdropElement.addEventListener("click",o=>{o.target===this.backdropElement&&this.options.closeOnBackdropClick&&this.close()}),this.modalElement.addEventListener("click",o=>{o.stopPropagation()})}show(){this.isVisible||(document.body.appendChild(this.hostElement),this.options.target&&(this.targetElement=typeof this.options.target=="string"?document.querySelector(this.options.target):this.options.target,this.applySpotlight()),this.createArrow(),this.targetElement&&this.positionModal(this.targetElement),requestAnimationFrame(()=>{this.backdropElement.classList.add("ah-modal-show")}),this.isVisible=!0)}close(){this.isVisible&&(this.cleanUpdateHandler?.(),this.removeSpotlight(),this.arrowElement&&this.arrowElement.parentNode&&(this.arrowElement.parentNode.removeChild(this.arrowElement),this.arrowElement=null),this.backdropElement.classList.remove("ah-modal-show"),this.backdropElement.classList.add("ah-modal-hide"),setTimeout(()=>{this.hostElement.parentNode&&this.hostElement.parentNode.removeChild(this.hostElement),this.backdropElement.classList.remove("ah-modal-hide")},200),this.isVisible=!1)}destroy(){this.close(),this.removeSpotlight(),this.resizeTimeout&&(clearTimeout(this.resizeTimeout),this.resizeTimeout=null),this.arrowElement&&this.arrowElement.parentNode&&(this.arrowElement.parentNode.removeChild(this.arrowElement),this.arrowElement=null)}spotlightClasses(){let e=["ah-spotlight-active"];return this.options.backdrop&&e.push("ah-with-backdrop"),e}applySpotlight(){if(this.targetElement){if(this.targetElement.classList.add(...this.spotlightClasses()),this.options.blockTargetClick){const e=this.targetElement.style.pointerEvents;this.targetElement.style.pointerEvents="none",this.targetElement.setAttribute("data-original-pointer-events",e)}this.targetElement.parentElement&&(this.targetElement.parentElement.style.backdropFilter="none")}}removeSpotlight(){if(!this.targetElement)return;this.targetElement.classList.remove(...this.spotlightClasses());const e=this.targetElement.getAttribute("data-original-pointer-events");e?(this.targetElement.style.pointerEvents=e,this.targetElement.removeAttribute("data-original-pointer-events")):this.targetElement.style.pointerEvents="",this.targetElement=null}_positionModal(e,o){this.modalElement.style.top=`${e}px`,this.modalElement.style.left=`${o}px`}async positionModal(e){const o=this.arrowElement?this.arrowElement.offsetWidth:0,n=Math.sqrt(2*o**2)/2;let i=[Re()];this.arrowElement&&(i.push(ke(n)),i.push(Oe({element:this.arrowElement})));const{x:r,y:s,middlewareData:l,placement:a}=await Ae(e,this.modalElement,{middleware:i});this._positionModal(s,r);const c=a.split("-")[0],h={top:"bottom",right:"left",bottom:"top",left:"right"}[c];if(l.arrow){const{x:f,y:u}=l.arrow;Object.assign(this.arrowElement.style,{left:f!=null?`${f}px`:"",top:u!=null?`${u}px`:"",[h]:`${-o}px`,right:"",bottom:"",[h]:`${-o/2}px`,transform:"rotate(45deg)"})}}setModalPosition(e,o,n,i){let r={getBoundingClientRect(){return{x:0,y:0,top:e,left:o,bottom:i,right:n,width:n,height:i}}};this.positionModal(r)}}let P=null,j=null;function G(){P&&(P.destroy(),P=null)}function Se(){G();const t=`
<h2>📚 First time with Anki?</h2>
<p>Find your way in the app with this onboarding tour.</p>
    `,e=`<button class="ah-button ah-secondary-button" onclick="destroyAnkiHubTutorialModal()">Close</button><button class="ah-button ah-primary-button" onclick="pycmd('ankihub_start_onboarding')">Take tour</button>`,o=new K({body:t,footer:e,showArrow:!1});o.show(),P=o}function Le({body:t,currentStep:e,stepCount:o,target:n,primaryButton:i={show:!0,label:"Next"},showArrow:r=!0,blockTargetClick:s=!1,backdrop:l=!0}){G();let a=`<span>${e} of ${o}</span>`;i.show&&(a+=`<button class="ah-button ah-primary-button" onclick="pycmd('ankihub_tutorial_primary_button_clicked')">${i.label}</button>`);const c=new K({body:t,footer:a,target:n,showArrow:r,blockTargetClick:s,backdrop:l});c.show(),P=c}function Me({target:t,currentStep:e,blockTargetClick:o=!1}){G();var n=new K({body:"",footer:"",target:t,blockTargetClick:o});n.show(),P=n,j&&(window.removeEventListener("resize",j),j=null),j=()=>{if(!n.targetElement)return;const{top:i,left:r,width:s,height:l}=n.targetElement.getBoundingClientRect();Nt(`ankihub_tutorial_target_resize:${e}:${i}:${r}:${s}:${l}`)},window.addEventListener("resize",j),j()}function Pe({top:t,left:e,width:o,height:n}){P&&P.setModalPosition(t,e,o,n)}function Fe(){G();var t=new K({body:"",footer:""});t.show(),P=t}k.Modal=K,k.addTutorialBackdrop=Fe,k.destroyActiveTutorialModal=G,k.highlightTutorialTarget=Me,k.positionTutorialTarget=Pe,k.promptForOnboardingTour=Se,k.showTutorialModal=Le,Object.defineProperty(k,Symbol.toStringTag,{value:"Module"})}));
