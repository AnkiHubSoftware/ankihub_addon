(function(k,X){typeof exports=="object"&&typeof module<"u"?X(exports):typeof define=="function"&&define.amd?define(["exports"],X):(k=typeof globalThis<"u"?globalThis:k||self,X(k.AnkiHub={}))})(this,(function(k){"use strict";const X=["top","right","bottom","left"],yt=["start","end"],xt=X.reduce((t,e)=>t.concat(e,e+"-"+yt[0],e+"-"+yt[1]),[]),V=Math.min,B=Math.max,Q=Math.round,Z=Math.floor,O=t=>({x:t,y:t}),_t={left:"right",right:"left",bottom:"top",top:"bottom"},$t={start:"end",end:"start"};function Vt(t,e,o){return B(t,V(e,o))}function tt(t,e){return typeof t=="function"?t(e):t}function U(t){return t.split("-")[0]}function M(t){return t.split("-")[1]}function zt(t){return t==="x"?"y":"x"}function at(t){return t==="y"?"height":"width"}const Wt=new Set(["top","bottom"]);function ct(t){return Wt.has(U(t))?"y":"x"}function dt(t){return zt(ct(t))}function Ht(t,e,o){o===void 0&&(o=!1);const n=M(t),i=dt(t),r=at(i);let s=i==="x"?n===(o?"end":"start")?"right":"left":n==="start"?"bottom":"top";return e.reference[r]>e.floating[r]&&(s=vt(s)),[s,vt(s)]}function jt(t){return t.replace(/start|end/g,e=>$t[e])}function vt(t){return t.replace(/left|right|bottom|top/g,e=>_t[e])}function It(t){return{top:0,right:0,bottom:0,left:0,...t}}function Et(t){return typeof t!="number"?It(t):{top:t,right:t,bottom:t,left:t}}function et(t){const{x:e,y:o,width:n,height:i}=t;return{width:n,height:i,top:o,left:e,right:e+n,bottom:o+i,x:e,y:o}}function Ct(t,e,o){let{reference:n,floating:i}=t;const r=ct(e),s=dt(e),l=at(s),a=U(e),c=r==="y",f=n.x+n.width/2-i.width/2,h=n.y+n.height/2-i.height/2,u=n[l]/2-i[l]/2;let d;switch(a){case"top":d={x:f,y:n.y-i.height};break;case"bottom":d={x:f,y:n.y+n.height};break;case"right":d={x:n.x+n.width,y:h};break;case"left":d={x:n.x-i.width,y:h};break;default:d={x:n.x,y:n.y}}switch(M(e)){case"start":d[s]-=u*(o&&c?-1:1);break;case"end":d[s]+=u*(o&&c?-1:1);break}return d}const qt=async(t,e,o)=>{const{placement:n="bottom",strategy:i="absolute",middleware:r=[],platform:s}=o,l=r.filter(Boolean),a=await(s.isRTL==null?void 0:s.isRTL(e));let c=await s.getElementRects({reference:t,floating:e,strategy:i}),{x:f,y:h}=Ct(c,n,a),u=n,d={},m=0;for(let p=0;p<l.length;p++){const{name:w,fn:g}=l[p],{x:b,y:x,data:v,reset:y}=await g({x:f,y:h,initialPlacement:n,placement:u,strategy:i,middlewareData:d,rects:c,platform:s,elements:{reference:t,floating:e}});f=b??f,h=x??h,d={...d,[w]:{...d[w],...v}},y&&m<=50&&(m++,typeof y=="object"&&(y.placement&&(u=y.placement),y.rects&&(c=y.rects===!0?await s.getElementRects({reference:t,floating:e,strategy:i}):y.rects),{x:f,y:h}=Ct(c,u,a)),p=-1)}return{x:f,y:h,placement:u,strategy:i,middlewareData:d}};async function Xt(t,e){var o;e===void 0&&(e={});const{x:n,y:i,platform:r,rects:s,elements:l,strategy:a}=t,{boundary:c="clippingAncestors",rootBoundary:f="viewport",elementContext:h="floating",altBoundary:u=!1,padding:d=0}=tt(e,t),m=Et(d),w=l[u?h==="floating"?"reference":"floating":h],g=et(await r.getClippingRect({element:(o=await(r.isElement==null?void 0:r.isElement(w)))==null||o?w:w.contextElement||await(r.getDocumentElement==null?void 0:r.getDocumentElement(l.floating)),boundary:c,rootBoundary:f,strategy:a})),b=h==="floating"?{x:n,y:i,width:s.floating.width,height:s.floating.height}:s.reference,x=await(r.getOffsetParent==null?void 0:r.getOffsetParent(l.floating)),v=await(r.isElement==null?void 0:r.isElement(x))?await(r.getScale==null?void 0:r.getScale(x))||{x:1,y:1}:{x:1,y:1},y=et(r.convertOffsetParentRelativeRectToViewportRelativeRect?await r.convertOffsetParentRelativeRectToViewportRelativeRect({elements:l,rect:b,offsetParent:x,strategy:a}):b);return{top:(g.top-y.top+m.top)/v.y,bottom:(y.bottom-g.bottom+m.bottom)/v.y,left:(g.left-y.left+m.left)/v.x,right:(y.right-g.right+m.right)/v.x}}const Ut=t=>({name:"arrow",options:t,async fn(e){const{x:o,y:n,placement:i,rects:r,platform:s,elements:l,middlewareData:a}=e,{element:c,padding:f=0}=tt(t,e)||{};if(c==null)return{};const h=Et(f),u={x:o,y:n},d=dt(i),m=at(d),p=await s.getDimensions(c),w=d==="y",g=w?"top":"left",b=w?"bottom":"right",x=w?"clientHeight":"clientWidth",v=r.reference[m]+r.reference[d]-u[d]-r.floating[m],y=u[d]-r.reference[d],F=await(s.getOffsetParent==null?void 0:s.getOffsetParent(c));let C=F?F[x]:0;(!C||!await(s.isElement==null?void 0:s.isElement(F)))&&(C=l.floating[x]||r.floating[m]);const Bt=v/2-y/2,J=C/2-p[m]/2-1,T=V(h[g],J),D=V(h[b],J),q=T,lt=C-p[m]-D,$=C/2-p[m]/2+Bt,gt=Vt(q,$,lt),wt=!a.arrow&&M(i)!=null&&$!==gt&&r.reference[m]/2-($<q?T:D)-p[m]/2<0,bt=wt?$<q?$-q:$-lt:0;return{[d]:u[d]+bt,data:{[d]:gt,centerOffset:$-gt-bt,...wt&&{alignmentOffset:bt}},reset:wt}}});function Yt(t,e,o){return(t?[...o.filter(i=>M(i)===t),...o.filter(i=>M(i)!==t)]:o.filter(i=>U(i)===i)).filter(i=>t?M(i)===t||(e?jt(i)!==i:!1):!0)}const Kt=function(t){return t===void 0&&(t={}),{name:"autoPlacement",options:t,async fn(e){var o,n,i;const{rects:r,middlewareData:s,placement:l,platform:a,elements:c}=e,{crossAxis:f=!1,alignment:h,allowedPlacements:u=xt,autoAlignment:d=!0,...m}=tt(t,e),p=h!==void 0||u===xt?Yt(h||null,d,u):u,w=await Xt(e,m),g=((o=s.autoPlacement)==null?void 0:o.index)||0,b=p[g];if(b==null)return{};const x=Ht(b,r,await(a.isRTL==null?void 0:a.isRTL(c.floating)));if(l!==b)return{reset:{placement:p[0]}};const v=[w[U(b)],w[x[0]],w[x[1]]],y=[...((n=s.autoPlacement)==null?void 0:n.overflows)||[],{placement:b,overflows:v}],F=p[g+1];if(F)return{data:{index:g+1,overflows:y},reset:{placement:F}};const C=y.map(T=>{const D=M(T.placement);return[T.placement,D&&f?T.overflows.slice(0,2).reduce((q,lt)=>q+lt,0):T.overflows[0],T.overflows]}).sort((T,D)=>T[1]-D[1]),J=((i=C.filter(T=>T[2].slice(0,M(T[0])?2:3).every(D=>D<=0))[0])==null?void 0:i[0])||C[0][0];return J!==l?{data:{index:g+1,overflows:y},reset:{placement:J}}:{}}}},Gt=new Set(["left","top"]);async function Jt(t,e){const{placement:o,platform:n,elements:i}=t,r=await(n.isRTL==null?void 0:n.isRTL(i.floating)),s=U(o),l=M(o),a=ct(o)==="y",c=Gt.has(s)?-1:1,f=r&&a?-1:1,h=tt(e,t);let{mainAxis:u,crossAxis:d,alignmentAxis:m}=typeof h=="number"?{mainAxis:h,crossAxis:0,alignmentAxis:null}:{mainAxis:h.mainAxis||0,crossAxis:h.crossAxis||0,alignmentAxis:h.alignmentAxis};return l&&typeof m=="number"&&(d=l==="end"?m*-1:m),a?{x:d*f,y:u*c}:{x:u*c,y:d*f}}const Qt=function(t){return t===void 0&&(t=0),{name:"offset",options:t,async fn(e){var o,n;const{x:i,y:r,placement:s,middlewareData:l}=e,a=await Jt(e,t);return s===((o=l.offset)==null?void 0:o.placement)&&(n=l.arrow)!=null&&n.alignmentOffset?{}:{x:i+a.x,y:r+a.y,data:{...a,placement:s}}}}};function ot(){return typeof window<"u"}function z(t){return Tt(t)?(t.nodeName||"").toLowerCase():"#document"}function E(t){var e;return(t==null||(e=t.ownerDocument)==null?void 0:e.defaultView)||window}function S(t){var e;return(e=(Tt(t)?t.ownerDocument:t.document)||window.document)==null?void 0:e.documentElement}function Tt(t){return ot()?t instanceof Node||t instanceof E(t).Node:!1}function R(t){return ot()?t instanceof Element||t instanceof E(t).Element:!1}function A(t){return ot()?t instanceof HTMLElement||t instanceof E(t).HTMLElement:!1}function kt(t){return!ot()||typeof ShadowRoot>"u"?!1:t instanceof ShadowRoot||t instanceof E(t).ShadowRoot}const Zt=new Set(["inline","contents"]);function Y(t){const{overflow:e,overflowX:o,overflowY:n,display:i}=L(t);return/auto|scroll|overlay|hidden|clip/.test(e+n+o)&&!Zt.has(i)}const te=new Set(["table","td","th"]);function ee(t){return te.has(z(t))}const oe=[":popover-open",":modal"];function nt(t){return oe.some(e=>{try{return t.matches(e)}catch{return!1}})}const ne=["transform","translate","scale","rotate","perspective"],ie=["transform","translate","scale","rotate","perspective","filter"],se=["paint","layout","strict","content"];function ft(t){const e=ht(),o=R(t)?L(t):t;return ne.some(n=>o[n]?o[n]!=="none":!1)||(o.containerType?o.containerType!=="normal":!1)||!e&&(o.backdropFilter?o.backdropFilter!=="none":!1)||!e&&(o.filter?o.filter!=="none":!1)||ie.some(n=>(o.willChange||"").includes(n))||se.some(n=>(o.contain||"").includes(n))}function re(t){let e=N(t);for(;A(e)&&!W(e);){if(ft(e))return e;if(nt(e))return null;e=N(e)}return null}function ht(){return typeof CSS>"u"||!CSS.supports?!1:CSS.supports("-webkit-backdrop-filter","none")}const le=new Set(["html","body","#document"]);function W(t){return le.has(z(t))}function L(t){return E(t).getComputedStyle(t)}function it(t){return R(t)?{scrollLeft:t.scrollLeft,scrollTop:t.scrollTop}:{scrollLeft:t.scrollX,scrollTop:t.scrollY}}function N(t){if(z(t)==="html")return t;const e=t.assignedSlot||t.parentNode||kt(t)&&t.host||S(t);return kt(e)?e.host:e}function Rt(t){const e=N(t);return W(e)?t.ownerDocument?t.ownerDocument.body:t.body:A(e)&&Y(e)?e:Rt(e)}function K(t,e,o){var n;e===void 0&&(e=[]),o===void 0&&(o=!0);const i=Rt(t),r=i===((n=t.ownerDocument)==null?void 0:n.body),s=E(i);if(r){const l=ut(s);return e.concat(s,s.visualViewport||[],Y(i)?i:[],l&&o?K(l):[])}return e.concat(i,K(i,[],o))}function ut(t){return t.parent&&Object.getPrototypeOf(t.parent)?t.frameElement:null}function Lt(t){const e=L(t);let o=parseFloat(e.width)||0,n=parseFloat(e.height)||0;const i=A(t),r=i?t.offsetWidth:o,s=i?t.offsetHeight:n,l=Q(o)!==r||Q(n)!==s;return l&&(o=r,n=s),{width:o,height:n,$:l}}function mt(t){return R(t)?t:t.contextElement}function H(t){const e=mt(t);if(!A(e))return O(1);const o=e.getBoundingClientRect(),{width:n,height:i,$:r}=Lt(e);let s=(r?Q(o.width):o.width)/n,l=(r?Q(o.height):o.height)/i;return(!s||!Number.isFinite(s))&&(s=1),(!l||!Number.isFinite(l))&&(l=1),{x:s,y:l}}const ae=O(0);function Ot(t){const e=E(t);return!ht()||!e.visualViewport?ae:{x:e.visualViewport.offsetLeft,y:e.visualViewport.offsetTop}}function ce(t,e,o){return e===void 0&&(e=!1),!o||e&&o!==E(t)?!1:e}function _(t,e,o,n){e===void 0&&(e=!1),o===void 0&&(o=!1);const i=t.getBoundingClientRect(),r=mt(t);let s=O(1);e&&(n?R(n)&&(s=H(n)):s=H(t));const l=ce(r,o,n)?Ot(r):O(0);let a=(i.left+l.x)/s.x,c=(i.top+l.y)/s.y,f=i.width/s.x,h=i.height/s.y;if(r){const u=E(r),d=n&&R(n)?E(n):n;let m=u,p=ut(m);for(;p&&n&&d!==m;){const w=H(p),g=p.getBoundingClientRect(),b=L(p),x=g.left+(p.clientLeft+parseFloat(b.paddingLeft))*w.x,v=g.top+(p.clientTop+parseFloat(b.paddingTop))*w.y;a*=w.x,c*=w.y,f*=w.x,h*=w.y,a+=x,c+=v,m=E(p),p=ut(m)}}return et({width:f,height:h,x:a,y:c})}function st(t,e){const o=it(t).scrollLeft;return e?e.left+o:_(S(t)).left+o}function St(t,e){const o=t.getBoundingClientRect(),n=o.left+e.scrollLeft-st(t,o),i=o.top+e.scrollTop;return{x:n,y:i}}function de(t){let{elements:e,rect:o,offsetParent:n,strategy:i}=t;const r=i==="fixed",s=S(n),l=e?nt(e.floating):!1;if(n===s||l&&r)return o;let a={scrollLeft:0,scrollTop:0},c=O(1);const f=O(0),h=A(n);if((h||!h&&!r)&&((z(n)!=="body"||Y(s))&&(a=it(n)),A(n))){const d=_(n);c=H(n),f.x=d.x+n.clientLeft,f.y=d.y+n.clientTop}const u=s&&!h&&!r?St(s,a):O(0);return{width:o.width*c.x,height:o.height*c.y,x:o.x*c.x-a.scrollLeft*c.x+f.x+u.x,y:o.y*c.y-a.scrollTop*c.y+f.y+u.y}}function fe(t){return Array.from(t.getClientRects())}function he(t){const e=S(t),o=it(t),n=t.ownerDocument.body,i=B(e.scrollWidth,e.clientWidth,n.scrollWidth,n.clientWidth),r=B(e.scrollHeight,e.clientHeight,n.scrollHeight,n.clientHeight);let s=-o.scrollLeft+st(t);const l=-o.scrollTop;return L(n).direction==="rtl"&&(s+=B(e.clientWidth,n.clientWidth)-i),{width:i,height:r,x:s,y:l}}const At=25;function ue(t,e){const o=E(t),n=S(t),i=o.visualViewport;let r=n.clientWidth,s=n.clientHeight,l=0,a=0;if(i){r=i.width,s=i.height;const f=ht();(!f||f&&e==="fixed")&&(l=i.offsetLeft,a=i.offsetTop)}const c=st(n);if(c<=0){const f=n.ownerDocument,h=f.body,u=getComputedStyle(h),d=f.compatMode==="CSS1Compat"&&parseFloat(u.marginLeft)+parseFloat(u.marginRight)||0,m=Math.abs(n.clientWidth-h.clientWidth-d);m<=At&&(r-=m)}else c<=At&&(r+=c);return{width:r,height:s,x:l,y:a}}const me=new Set(["absolute","fixed"]);function pe(t,e){const o=_(t,!0,e==="fixed"),n=o.top+t.clientTop,i=o.left+t.clientLeft,r=A(t)?H(t):O(1),s=t.clientWidth*r.x,l=t.clientHeight*r.y,a=i*r.x,c=n*r.y;return{width:s,height:l,x:a,y:c}}function Mt(t,e,o){let n;if(e==="viewport")n=ue(t,o);else if(e==="document")n=he(S(t));else if(R(e))n=pe(e,o);else{const i=Ot(t);n={x:e.x-i.x,y:e.y-i.y,width:e.width,height:e.height}}return et(n)}function Pt(t,e){const o=N(t);return o===e||!R(o)||W(o)?!1:L(o).position==="fixed"||Pt(o,e)}function ge(t,e){const o=e.get(t);if(o)return o;let n=K(t,[],!1).filter(l=>R(l)&&z(l)!=="body"),i=null;const r=L(t).position==="fixed";let s=r?N(t):t;for(;R(s)&&!W(s);){const l=L(s),a=ft(s);!a&&l.position==="fixed"&&(i=null),(r?!a&&!i:!a&&l.position==="static"&&!!i&&me.has(i.position)||Y(s)&&!a&&Pt(t,s))?n=n.filter(f=>f!==s):i=l,s=N(s)}return e.set(t,n),n}function we(t){let{element:e,boundary:o,rootBoundary:n,strategy:i}=t;const s=[...o==="clippingAncestors"?nt(e)?[]:ge(e,this._c):[].concat(o),n],l=s[0],a=s.reduce((c,f)=>{const h=Mt(e,f,i);return c.top=B(h.top,c.top),c.right=V(h.right,c.right),c.bottom=V(h.bottom,c.bottom),c.left=B(h.left,c.left),c},Mt(e,l,i));return{width:a.right-a.left,height:a.bottom-a.top,x:a.left,y:a.top}}function be(t){const{width:e,height:o}=Lt(t);return{width:e,height:o}}function ye(t,e,o){const n=A(e),i=S(e),r=o==="fixed",s=_(t,!0,r,e);let l={scrollLeft:0,scrollTop:0};const a=O(0);function c(){a.x=st(i)}if(n||!n&&!r)if((z(e)!=="body"||Y(i))&&(l=it(e)),n){const d=_(e,!0,r,e);a.x=d.x+e.clientLeft,a.y=d.y+e.clientTop}else i&&c();r&&!n&&i&&c();const f=i&&!n&&!r?St(i,l):O(0),h=s.left+l.scrollLeft-a.x-f.x,u=s.top+l.scrollTop-a.y-f.y;return{x:h,y:u,width:s.width,height:s.height}}function pt(t){return L(t).position==="static"}function Ft(t,e){if(!A(t)||L(t).position==="fixed")return null;if(e)return e(t);let o=t.offsetParent;return S(t)===o&&(o=o.ownerDocument.body),o}function Nt(t,e){const o=E(t);if(nt(t))return o;if(!A(t)){let i=N(t);for(;i&&!W(i);){if(R(i)&&!pt(i))return i;i=N(i)}return o}let n=Ft(t,e);for(;n&&ee(n)&&pt(n);)n=Ft(n,e);return n&&W(n)&&pt(n)&&!ft(n)?o:n||re(t)||o}const xe=async function(t){const e=this.getOffsetParent||Nt,o=this.getDimensions,n=await o(t.floating);return{reference:ye(t.reference,await e(t.floating),t.strategy),floating:{x:0,y:0,width:n.width,height:n.height}}};function ve(t){return L(t).direction==="rtl"}const Ee={convertOffsetParentRelativeRectToViewportRelativeRect:de,getDocumentElement:S,getClippingRect:we,getOffsetParent:Nt,getElementRects:xe,getClientRects:fe,getDimensions:be,getScale:H,isElement:R,isRTL:ve};function Dt(t,e){return t.x===e.x&&t.y===e.y&&t.width===e.width&&t.height===e.height}function Ce(t,e){let o=null,n;const i=S(t);function r(){var l;clearTimeout(n),(l=o)==null||l.disconnect(),o=null}function s(l,a){l===void 0&&(l=!1),a===void 0&&(a=1),r();const c=t.getBoundingClientRect(),{left:f,top:h,width:u,height:d}=c;if(l||e(),!u||!d)return;const m=Z(h),p=Z(i.clientWidth-(f+u)),w=Z(i.clientHeight-(h+d)),g=Z(f),x={rootMargin:-m+"px "+-p+"px "+-w+"px "+-g+"px",threshold:B(0,V(1,a))||1};let v=!0;function y(F){const C=F[0].intersectionRatio;if(C!==a){if(!v)return s();C?s(!1,C):n=setTimeout(()=>{s(!1,1e-7)},1e3)}C===1&&!Dt(c,t.getBoundingClientRect())&&s(),v=!1}try{o=new IntersectionObserver(y,{...x,root:i.ownerDocument})}catch{o=new IntersectionObserver(y,x)}o.observe(t)}return s(!0),r}function Te(t,e,o,n){n===void 0&&(n={});const{ancestorScroll:i=!0,ancestorResize:r=!0,elementResize:s=typeof ResizeObserver=="function",layoutShift:l=typeof IntersectionObserver=="function",animationFrame:a=!1}=n,c=mt(t),f=i||r?[...c?K(c):[],...K(e)]:[];f.forEach(g=>{i&&g.addEventListener("scroll",o,{passive:!0}),r&&g.addEventListener("resize",o)});const h=c&&l?Ce(c,o):null;let u=-1,d=null;s&&(d=new ResizeObserver(g=>{let[b]=g;b&&b.target===c&&d&&(d.unobserve(e),cancelAnimationFrame(u),u=requestAnimationFrame(()=>{var x;(x=d)==null||x.observe(e)})),o()}),c&&!a&&d.observe(c),d.observe(e));let m,p=a?_(t):null;a&&w();function w(){const g=_(t);p&&!Dt(p,g)&&o(),p=g,m=requestAnimationFrame(w)}return o(),()=>{var g;f.forEach(b=>{i&&b.removeEventListener("scroll",o),r&&b.removeEventListener("resize",o)}),h?.(),(g=d)==null||g.disconnect(),d=null,a&&cancelAnimationFrame(m)}}const ke=Qt,Re=Kt,Le=Ut,Oe=(t,e,o)=>{const n=new Map,i={platform:Ee,...o},r={...i.platform,_c:n};return qt(t,e,{...i,platform:r})};function rt(t,e){window.bridgeCommand(t,e)}class G{options;isVisible=!1;targetElement=null;modalElement;backdropElement;arrowElement=null;shadowRoot;hostElement;cleanUpdateHandler;resizeTimeout=null;constructor(e={}){this.options={body:"",footer:"",showCloseButton:!0,closeOnBackdropClick:!1,backdrop:!0,target:null,blockTargetClick:!1,...e},this.createModal(),this.bindEvents()}createModal(){this.hostElement=document.createElement("div"),this.hostElement.style.position="fixed",this.hostElement.style.top="0",this.hostElement.style.left="0",this.hostElement.style.width="100%",this.hostElement.style.height="100%",this.hostElement.style.zIndex="10000",this.hostElement.style.pointerEvents="none",this.shadowRoot=this.hostElement.attachShadow({mode:"open"}),this.injectStyles(),this.backdropElement=document.createElement("div"),this.backdropElement.className="ah-modal-backdrop",this.modalElement=document.createElement("div"),this.modalElement.className="ah-modal-container";const e=document.createElement("div");if(e.className="ah-modal-content",this.options.showCloseButton){const n=document.createElement("div");n.className="ah-modal-header";const i=document.createElement("button");i.className="ah-modal-close-button",i.innerHTML="×",i.setAttribute("aria-label","Close modal"),n.appendChild(i),e.appendChild(n)}const o=document.createElement("div");if(o.className="ah-modal-body",typeof this.options.body=="string"?o.innerHTML=this.options.body:this.options.body instanceof Node&&o.append(this.options.body),e.appendChild(o),this.options.footer){const n=document.createElement("div");n.className="ah-modal-footer",typeof this.options.footer=="string"?n.innerHTML=this.options.footer:this.options.footer instanceof Node?n.append(this.options.footer):n.append(...this.options.footer),e.appendChild(n)}this.modalElement.appendChild(e),this.options.body&&(this.backdropElement.appendChild(this.modalElement),this.targetElement&&(this.cleanUpdateHandler=Te(this.targetElement,this.modalElement,this.positionModal.bind(this,this.targetElement)))),this.shadowRoot.appendChild(this.backdropElement)}injectStyles(){const e=document.createElement("style");e.textContent=`
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
        `,this.shadowRoot.appendChild(e)}createArrow(){this.arrowElement=document.createElement("div"),this.arrowElement.className="ah-modal-arrow",this.modalElement.appendChild(this.arrowElement)}bindEvents(){const e=this.modalElement.querySelector(".ah-modal-close-button");e&&e.addEventListener("click",()=>{this.close(),rt("ankihub_modal_closed")}),this.backdropElement.addEventListener("click",o=>{o.target===this.backdropElement&&this.options.closeOnBackdropClick&&this.close()}),this.modalElement.addEventListener("click",o=>{o.stopPropagation()})}show(){this.isVisible||(document.body.appendChild(this.hostElement),this.options.target&&(this.targetElement=typeof this.options.target=="string"?document.querySelector(this.options.target):this.options.target,this.applySpotlight()),this.targetElement&&(this.createArrow(),this.positionModal(this.targetElement)),requestAnimationFrame(()=>{this.backdropElement.classList.add("ah-modal-show")}),this.isVisible=!0)}close(){this.isVisible&&(this.cleanUpdateHandler?.(),this.removeSpotlight(),this.arrowElement&&this.arrowElement.parentNode&&(this.arrowElement.parentNode.removeChild(this.arrowElement),this.arrowElement=null),this.backdropElement.classList.remove("ah-modal-show"),this.backdropElement.classList.add("ah-modal-hide"),setTimeout(()=>{this.hostElement.parentNode&&this.hostElement.parentNode.removeChild(this.hostElement),this.backdropElement.classList.remove("ah-modal-hide")},200),this.isVisible=!1)}destroy(){this.close(),this.removeSpotlight(),this.resizeTimeout&&(clearTimeout(this.resizeTimeout),this.resizeTimeout=null),this.arrowElement&&this.arrowElement.parentNode&&(this.arrowElement.parentNode.removeChild(this.arrowElement),this.arrowElement=null)}spotlightClasses(){let e=["ah-spotlight-active"];return this.options.backdrop&&e.push("ah-with-backdrop"),e}applySpotlight(){if(this.targetElement){if(this.targetElement.classList.add(...this.spotlightClasses()),this.options.blockTargetClick){const e=this.targetElement.style.pointerEvents;this.targetElement.style.pointerEvents="none",this.targetElement.setAttribute("data-original-pointer-events",e)}this.targetElement.parentElement&&(this.targetElement.parentElement.style.backdropFilter="none")}}removeSpotlight(){if(!this.targetElement)return;this.targetElement.classList.remove(...this.spotlightClasses());const e=this.targetElement.getAttribute("data-original-pointer-events");e?(this.targetElement.style.pointerEvents=e,this.targetElement.removeAttribute("data-original-pointer-events")):this.targetElement.style.pointerEvents="",this.targetElement=null}_positionModal(e,o){this.modalElement.style.top=`${e}px`,this.modalElement.style.left=`${o}px`}async positionModal(e){const o=this.arrowElement?this.arrowElement.offsetWidth:0,n=Math.sqrt(2*o**2)/2;let i=[Re()];this.arrowElement&&(i.push(ke(n)),i.push(Le({element:this.arrowElement})));const{x:r,y:s,middlewareData:l,placement:a}=await Oe(e,this.modalElement,{middleware:i});this._positionModal(s,r);const c=a.split("-")[0],f={top:"bottom",right:"left",bottom:"top",left:"right"}[c];if(l.arrow){const{x:h,y:u}=l.arrow;Object.assign(this.arrowElement.style,{left:h!=null?`${h}px`:"",top:u!=null?`${u}px`:"",[f]:`${-o}px`,right:"",bottom:"",[f]:`${-o/2}px`,transform:"rotate(45deg)"})}}setModalPosition(e,o,n,i){let r={getBoundingClientRect(){return{x:0,y:0,top:e,left:o,bottom:i,right:n,width:n,height:i}}};this.positionModal(r)}}let P=null,j=null;function I(){P&&(P.destroy(),P=null)}function Se(){I();const t=`
<h2>📚 First time with Anki?</h2>
<p>Find your way in the app with this onboarding tour.</p>
    `,e=[],o=document.createElement("button");o.textContent="Close",o.classList.add("ah-button","ah-secondary-button"),o.addEventListener("click",I),e.push(o);const n=document.createElement("button");n.textContent="Take tour",n.classList.add("ah-button","ah-secondary-button"),n.addEventListener("click",()=>rt("ankihub_start_onboarding")),e.push(n);const i=new G({body:t,footer:e});i.show(),P=i}function Ae({body:t,currentStep:e,stepCount:o,target:n,primaryButton:i={show:!0,label:"Next"},blockTargetClick:r=!1,backdrop:s=!0}){I();const l=[],a=document.createElement("span");if(a.textContent=`${e} of ${o}`,l.push(a),i.show){const f=document.createElement("button");f.textContent=i.label,f.classList.add("ah-button","ah-primary-button"),f.addEventListener("click",()=>rt("ankihub_tutorial_primary_button_clicked")),l.push(f)}const c=new G({body:t,footer:l,target:n,blockTargetClick:r,backdrop:s});c.show(),P=c}function Me({target:t,currentStep:e,blockTargetClick:o=!1}){I();var n=new G({body:"",footer:"",target:t,blockTargetClick:o});n.show(),P=n,j&&(window.removeEventListener("resize",j),j=null),j=()=>{if(!n.targetElement)return;const{top:i,left:r,width:s,height:l}=n.targetElement.getBoundingClientRect();rt(`ankihub_tutorial_target_resize:${e}:${i}:${r}:${s}:${l}`)},window.addEventListener("resize",j),j()}function Pe({top:t,left:e,width:o,height:n}){P&&P.setModalPosition(t,e,o,n)}function Fe(){I();var t=new G({body:"",footer:""});t.show(),P=t}k.Modal=G,k.addTutorialBackdrop=Fe,k.destroyActiveTutorialModal=I,k.highlightTutorialTarget=Me,k.positionTutorialTarget=Pe,k.promptForOnboardingTour=Se,k.showTutorialModal=Ae,Object.defineProperty(k,Symbol.toStringTag,{value:"Module"})}));
