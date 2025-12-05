(function(k,X){typeof exports=="object"&&typeof module<"u"?X(exports):typeof define=="function"&&define.amd?define(["exports"],X):(k=typeof globalThis<"u"?globalThis:k||self,X(k.AnkiHub={}))})(this,(function(k){"use strict";const X=["top","right","bottom","left"],yt=["start","end"],xt=X.reduce((t,e)=>t.concat(e,e+"-"+yt[0],e+"-"+yt[1]),[]),V=Math.min,B=Math.max,Q=Math.round,Z=Math.floor,O=t=>({x:t,y:t}),$t={left:"right",right:"left",bottom:"top",top:"bottom"},Vt={start:"end",end:"start"};function zt(t,e,n){return B(t,V(e,n))}function tt(t,e){return typeof t=="function"?t(e):t}function U(t){return t.split("-")[0]}function M(t){return t.split("-")[1]}function Wt(t){return t==="x"?"y":"x"}function at(t){return t==="y"?"height":"width"}const Ht=new Set(["top","bottom"]);function ct(t){return Ht.has(U(t))?"y":"x"}function dt(t){return Wt(ct(t))}function jt(t,e,n){n===void 0&&(n=!1);const o=M(t),i=dt(t),r=at(i);let s=i==="x"?o===(n?"end":"start")?"right":"left":o==="start"?"bottom":"top";return e.reference[r]>e.floating[r]&&(s=vt(s)),[s,vt(s)]}function It(t){return t.replace(/start|end/g,e=>Vt[e])}function vt(t){return t.replace(/left|right|bottom|top/g,e=>$t[e])}function qt(t){return{top:0,right:0,bottom:0,left:0,...t}}function Et(t){return typeof t!="number"?qt(t):{top:t,right:t,bottom:t,left:t}}function et(t){const{x:e,y:n,width:o,height:i}=t;return{width:o,height:i,top:n,left:e,right:e+o,bottom:n+i,x:e,y:n}}function Ct(t,e,n){let{reference:o,floating:i}=t;const r=ct(e),s=dt(e),l=at(s),a=U(e),c=r==="y",f=o.x+o.width/2-i.width/2,h=o.y+o.height/2-i.height/2,u=o[l]/2-i[l]/2;let d;switch(a){case"top":d={x:f,y:o.y-i.height};break;case"bottom":d={x:f,y:o.y+o.height};break;case"right":d={x:o.x+o.width,y:h};break;case"left":d={x:o.x-i.width,y:h};break;default:d={x:o.x,y:o.y}}switch(M(e)){case"start":d[s]-=u*(n&&c?-1:1);break;case"end":d[s]+=u*(n&&c?-1:1);break}return d}const Xt=async(t,e,n)=>{const{placement:o="bottom",strategy:i="absolute",middleware:r=[],platform:s}=n,l=r.filter(Boolean),a=await(s.isRTL==null?void 0:s.isRTL(e));let c=await s.getElementRects({reference:t,floating:e,strategy:i}),{x:f,y:h}=Ct(c,o,a),u=o,d={},m=0;for(let p=0;p<l.length;p++){const{name:w,fn:g}=l[p],{x:b,y:x,data:v,reset:y}=await g({x:f,y:h,initialPlacement:o,placement:u,strategy:i,middlewareData:d,rects:c,platform:s,elements:{reference:t,floating:e}});f=b??f,h=x??h,d={...d,[w]:{...d[w],...v}},y&&m<=50&&(m++,typeof y=="object"&&(y.placement&&(u=y.placement),y.rects&&(c=y.rects===!0?await s.getElementRects({reference:t,floating:e,strategy:i}):y.rects),{x:f,y:h}=Ct(c,u,a)),p=-1)}return{x:f,y:h,placement:u,strategy:i,middlewareData:d}};async function Ut(t,e){var n;e===void 0&&(e={});const{x:o,y:i,platform:r,rects:s,elements:l,strategy:a}=t,{boundary:c="clippingAncestors",rootBoundary:f="viewport",elementContext:h="floating",altBoundary:u=!1,padding:d=0}=tt(e,t),m=Et(d),w=l[u?h==="floating"?"reference":"floating":h],g=et(await r.getClippingRect({element:(n=await(r.isElement==null?void 0:r.isElement(w)))==null||n?w:w.contextElement||await(r.getDocumentElement==null?void 0:r.getDocumentElement(l.floating)),boundary:c,rootBoundary:f,strategy:a})),b=h==="floating"?{x:o,y:i,width:s.floating.width,height:s.floating.height}:s.reference,x=await(r.getOffsetParent==null?void 0:r.getOffsetParent(l.floating)),v=await(r.isElement==null?void 0:r.isElement(x))?await(r.getScale==null?void 0:r.getScale(x))||{x:1,y:1}:{x:1,y:1},y=et(r.convertOffsetParentRelativeRectToViewportRelativeRect?await r.convertOffsetParentRelativeRectToViewportRelativeRect({elements:l,rect:b,offsetParent:x,strategy:a}):b);return{top:(g.top-y.top+m.top)/v.y,bottom:(y.bottom-g.bottom+m.bottom)/v.y,left:(g.left-y.left+m.left)/v.x,right:(y.right-g.right+m.right)/v.x}}const Yt=t=>({name:"arrow",options:t,async fn(e){const{x:n,y:o,placement:i,rects:r,platform:s,elements:l,middlewareData:a}=e,{element:c,padding:f=0}=tt(t,e)||{};if(c==null)return{};const h=Et(f),u={x:n,y:o},d=dt(i),m=at(d),p=await s.getDimensions(c),w=d==="y",g=w?"top":"left",b=w?"bottom":"right",x=w?"clientHeight":"clientWidth",v=r.reference[m]+r.reference[d]-u[d]-r.floating[m],y=u[d]-r.reference[d],F=await(s.getOffsetParent==null?void 0:s.getOffsetParent(c));let C=F?F[x]:0;(!C||!await(s.isElement==null?void 0:s.isElement(F)))&&(C=l.floating[x]||r.floating[m]);const _t=v/2-y/2,J=C/2-p[m]/2-1,T=V(h[g],J),D=V(h[b],J),q=T,lt=C-p[m]-D,$=C/2-p[m]/2+_t,gt=zt(q,$,lt),wt=!a.arrow&&M(i)!=null&&$!==gt&&r.reference[m]/2-($<q?T:D)-p[m]/2<0,bt=wt?$<q?$-q:$-lt:0;return{[d]:u[d]+bt,data:{[d]:gt,centerOffset:$-gt-bt,...wt&&{alignmentOffset:bt}},reset:wt}}});function Kt(t,e,n){return(t?[...n.filter(i=>M(i)===t),...n.filter(i=>M(i)!==t)]:n.filter(i=>U(i)===i)).filter(i=>t?M(i)===t||(e?It(i)!==i:!1):!0)}const Gt=function(t){return t===void 0&&(t={}),{name:"autoPlacement",options:t,async fn(e){var n,o,i;const{rects:r,middlewareData:s,placement:l,platform:a,elements:c}=e,{crossAxis:f=!1,alignment:h,allowedPlacements:u=xt,autoAlignment:d=!0,...m}=tt(t,e),p=h!==void 0||u===xt?Kt(h||null,d,u):u,w=await Ut(e,m),g=((n=s.autoPlacement)==null?void 0:n.index)||0,b=p[g];if(b==null)return{};const x=jt(b,r,await(a.isRTL==null?void 0:a.isRTL(c.floating)));if(l!==b)return{reset:{placement:p[0]}};const v=[w[U(b)],w[x[0]],w[x[1]]],y=[...((o=s.autoPlacement)==null?void 0:o.overflows)||[],{placement:b,overflows:v}],F=p[g+1];if(F)return{data:{index:g+1,overflows:y},reset:{placement:F}};const C=y.map(T=>{const D=M(T.placement);return[T.placement,D&&f?T.overflows.slice(0,2).reduce((q,lt)=>q+lt,0):T.overflows[0],T.overflows]}).sort((T,D)=>T[1]-D[1]),J=((i=C.filter(T=>T[2].slice(0,M(T[0])?2:3).every(D=>D<=0))[0])==null?void 0:i[0])||C[0][0];return J!==l?{data:{index:g+1,overflows:y},reset:{placement:J}}:{}}}},Jt=new Set(["left","top"]);async function Qt(t,e){const{placement:n,platform:o,elements:i}=t,r=await(o.isRTL==null?void 0:o.isRTL(i.floating)),s=U(n),l=M(n),a=ct(n)==="y",c=Jt.has(s)?-1:1,f=r&&a?-1:1,h=tt(e,t);let{mainAxis:u,crossAxis:d,alignmentAxis:m}=typeof h=="number"?{mainAxis:h,crossAxis:0,alignmentAxis:null}:{mainAxis:h.mainAxis||0,crossAxis:h.crossAxis||0,alignmentAxis:h.alignmentAxis};return l&&typeof m=="number"&&(d=l==="end"?m*-1:m),a?{x:d*f,y:u*c}:{x:u*c,y:d*f}}const Zt=function(t){return t===void 0&&(t=0),{name:"offset",options:t,async fn(e){var n,o;const{x:i,y:r,placement:s,middlewareData:l}=e,a=await Qt(e,t);return s===((n=l.offset)==null?void 0:n.placement)&&(o=l.arrow)!=null&&o.alignmentOffset?{}:{x:i+a.x,y:r+a.y,data:{...a,placement:s}}}}};function ot(){return typeof window<"u"}function z(t){return Tt(t)?(t.nodeName||"").toLowerCase():"#document"}function E(t){var e;return(t==null||(e=t.ownerDocument)==null?void 0:e.defaultView)||window}function S(t){var e;return(e=(Tt(t)?t.ownerDocument:t.document)||window.document)==null?void 0:e.documentElement}function Tt(t){return ot()?t instanceof Node||t instanceof E(t).Node:!1}function R(t){return ot()?t instanceof Element||t instanceof E(t).Element:!1}function A(t){return ot()?t instanceof HTMLElement||t instanceof E(t).HTMLElement:!1}function kt(t){return!ot()||typeof ShadowRoot>"u"?!1:t instanceof ShadowRoot||t instanceof E(t).ShadowRoot}const te=new Set(["inline","contents"]);function Y(t){const{overflow:e,overflowX:n,overflowY:o,display:i}=L(t);return/auto|scroll|overlay|hidden|clip/.test(e+o+n)&&!te.has(i)}const ee=new Set(["table","td","th"]);function oe(t){return ee.has(z(t))}const ne=[":popover-open",":modal"];function nt(t){return ne.some(e=>{try{return t.matches(e)}catch{return!1}})}const ie=["transform","translate","scale","rotate","perspective"],se=["transform","translate","scale","rotate","perspective","filter"],re=["paint","layout","strict","content"];function ft(t){const e=ht(),n=R(t)?L(t):t;return ie.some(o=>n[o]?n[o]!=="none":!1)||(n.containerType?n.containerType!=="normal":!1)||!e&&(n.backdropFilter?n.backdropFilter!=="none":!1)||!e&&(n.filter?n.filter!=="none":!1)||se.some(o=>(n.willChange||"").includes(o))||re.some(o=>(n.contain||"").includes(o))}function le(t){let e=N(t);for(;A(e)&&!W(e);){if(ft(e))return e;if(nt(e))return null;e=N(e)}return null}function ht(){return typeof CSS>"u"||!CSS.supports?!1:CSS.supports("-webkit-backdrop-filter","none")}const ae=new Set(["html","body","#document"]);function W(t){return ae.has(z(t))}function L(t){return E(t).getComputedStyle(t)}function it(t){return R(t)?{scrollLeft:t.scrollLeft,scrollTop:t.scrollTop}:{scrollLeft:t.scrollX,scrollTop:t.scrollY}}function N(t){if(z(t)==="html")return t;const e=t.assignedSlot||t.parentNode||kt(t)&&t.host||S(t);return kt(e)?e.host:e}function Rt(t){const e=N(t);return W(e)?t.ownerDocument?t.ownerDocument.body:t.body:A(e)&&Y(e)?e:Rt(e)}function K(t,e,n){var o;e===void 0&&(e=[]),n===void 0&&(n=!0);const i=Rt(t),r=i===((o=t.ownerDocument)==null?void 0:o.body),s=E(i);if(r){const l=ut(s);return e.concat(s,s.visualViewport||[],Y(i)?i:[],l&&n?K(l):[])}return e.concat(i,K(i,[],n))}function ut(t){return t.parent&&Object.getPrototypeOf(t.parent)?t.frameElement:null}function Lt(t){const e=L(t);let n=parseFloat(e.width)||0,o=parseFloat(e.height)||0;const i=A(t),r=i?t.offsetWidth:n,s=i?t.offsetHeight:o,l=Q(n)!==r||Q(o)!==s;return l&&(n=r,o=s),{width:n,height:o,$:l}}function mt(t){return R(t)?t:t.contextElement}function H(t){const e=mt(t);if(!A(e))return O(1);const n=e.getBoundingClientRect(),{width:o,height:i,$:r}=Lt(e);let s=(r?Q(n.width):n.width)/o,l=(r?Q(n.height):n.height)/i;return(!s||!Number.isFinite(s))&&(s=1),(!l||!Number.isFinite(l))&&(l=1),{x:s,y:l}}const ce=O(0);function Ot(t){const e=E(t);return!ht()||!e.visualViewport?ce:{x:e.visualViewport.offsetLeft,y:e.visualViewport.offsetTop}}function de(t,e,n){return e===void 0&&(e=!1),!n||e&&n!==E(t)?!1:e}function _(t,e,n,o){e===void 0&&(e=!1),n===void 0&&(n=!1);const i=t.getBoundingClientRect(),r=mt(t);let s=O(1);e&&(o?R(o)&&(s=H(o)):s=H(t));const l=de(r,n,o)?Ot(r):O(0);let a=(i.left+l.x)/s.x,c=(i.top+l.y)/s.y,f=i.width/s.x,h=i.height/s.y;if(r){const u=E(r),d=o&&R(o)?E(o):o;let m=u,p=ut(m);for(;p&&o&&d!==m;){const w=H(p),g=p.getBoundingClientRect(),b=L(p),x=g.left+(p.clientLeft+parseFloat(b.paddingLeft))*w.x,v=g.top+(p.clientTop+parseFloat(b.paddingTop))*w.y;a*=w.x,c*=w.y,f*=w.x,h*=w.y,a+=x,c+=v,m=E(p),p=ut(m)}}return et({width:f,height:h,x:a,y:c})}function st(t,e){const n=it(t).scrollLeft;return e?e.left+n:_(S(t)).left+n}function St(t,e){const n=t.getBoundingClientRect(),o=n.left+e.scrollLeft-st(t,n),i=n.top+e.scrollTop;return{x:o,y:i}}function fe(t){let{elements:e,rect:n,offsetParent:o,strategy:i}=t;const r=i==="fixed",s=S(o),l=e?nt(e.floating):!1;if(o===s||l&&r)return n;let a={scrollLeft:0,scrollTop:0},c=O(1);const f=O(0),h=A(o);if((h||!h&&!r)&&((z(o)!=="body"||Y(s))&&(a=it(o)),A(o))){const d=_(o);c=H(o),f.x=d.x+o.clientLeft,f.y=d.y+o.clientTop}const u=s&&!h&&!r?St(s,a):O(0);return{width:n.width*c.x,height:n.height*c.y,x:n.x*c.x-a.scrollLeft*c.x+f.x+u.x,y:n.y*c.y-a.scrollTop*c.y+f.y+u.y}}function he(t){return Array.from(t.getClientRects())}function ue(t){const e=S(t),n=it(t),o=t.ownerDocument.body,i=B(e.scrollWidth,e.clientWidth,o.scrollWidth,o.clientWidth),r=B(e.scrollHeight,e.clientHeight,o.scrollHeight,o.clientHeight);let s=-n.scrollLeft+st(t);const l=-n.scrollTop;return L(o).direction==="rtl"&&(s+=B(e.clientWidth,o.clientWidth)-i),{width:i,height:r,x:s,y:l}}const At=25;function me(t,e){const n=E(t),o=S(t),i=n.visualViewport;let r=o.clientWidth,s=o.clientHeight,l=0,a=0;if(i){r=i.width,s=i.height;const f=ht();(!f||f&&e==="fixed")&&(l=i.offsetLeft,a=i.offsetTop)}const c=st(o);if(c<=0){const f=o.ownerDocument,h=f.body,u=getComputedStyle(h),d=f.compatMode==="CSS1Compat"&&parseFloat(u.marginLeft)+parseFloat(u.marginRight)||0,m=Math.abs(o.clientWidth-h.clientWidth-d);m<=At&&(r-=m)}else c<=At&&(r+=c);return{width:r,height:s,x:l,y:a}}const pe=new Set(["absolute","fixed"]);function ge(t,e){const n=_(t,!0,e==="fixed"),o=n.top+t.clientTop,i=n.left+t.clientLeft,r=A(t)?H(t):O(1),s=t.clientWidth*r.x,l=t.clientHeight*r.y,a=i*r.x,c=o*r.y;return{width:s,height:l,x:a,y:c}}function Mt(t,e,n){let o;if(e==="viewport")o=me(t,n);else if(e==="document")o=ue(S(t));else if(R(e))o=ge(e,n);else{const i=Ot(t);o={x:e.x-i.x,y:e.y-i.y,width:e.width,height:e.height}}return et(o)}function Pt(t,e){const n=N(t);return n===e||!R(n)||W(n)?!1:L(n).position==="fixed"||Pt(n,e)}function we(t,e){const n=e.get(t);if(n)return n;let o=K(t,[],!1).filter(l=>R(l)&&z(l)!=="body"),i=null;const r=L(t).position==="fixed";let s=r?N(t):t;for(;R(s)&&!W(s);){const l=L(s),a=ft(s);!a&&l.position==="fixed"&&(i=null),(r?!a&&!i:!a&&l.position==="static"&&!!i&&pe.has(i.position)||Y(s)&&!a&&Pt(t,s))?o=o.filter(f=>f!==s):i=l,s=N(s)}return e.set(t,o),o}function be(t){let{element:e,boundary:n,rootBoundary:o,strategy:i}=t;const s=[...n==="clippingAncestors"?nt(e)?[]:we(e,this._c):[].concat(n),o],l=s[0],a=s.reduce((c,f)=>{const h=Mt(e,f,i);return c.top=B(h.top,c.top),c.right=V(h.right,c.right),c.bottom=V(h.bottom,c.bottom),c.left=B(h.left,c.left),c},Mt(e,l,i));return{width:a.right-a.left,height:a.bottom-a.top,x:a.left,y:a.top}}function ye(t){const{width:e,height:n}=Lt(t);return{width:e,height:n}}function xe(t,e,n){const o=A(e),i=S(e),r=n==="fixed",s=_(t,!0,r,e);let l={scrollLeft:0,scrollTop:0};const a=O(0);function c(){a.x=st(i)}if(o||!o&&!r)if((z(e)!=="body"||Y(i))&&(l=it(e)),o){const d=_(e,!0,r,e);a.x=d.x+e.clientLeft,a.y=d.y+e.clientTop}else i&&c();r&&!o&&i&&c();const f=i&&!o&&!r?St(i,l):O(0),h=s.left+l.scrollLeft-a.x-f.x,u=s.top+l.scrollTop-a.y-f.y;return{x:h,y:u,width:s.width,height:s.height}}function pt(t){return L(t).position==="static"}function Ft(t,e){if(!A(t)||L(t).position==="fixed")return null;if(e)return e(t);let n=t.offsetParent;return S(t)===n&&(n=n.ownerDocument.body),n}function Nt(t,e){const n=E(t);if(nt(t))return n;if(!A(t)){let i=N(t);for(;i&&!W(i);){if(R(i)&&!pt(i))return i;i=N(i)}return n}let o=Ft(t,e);for(;o&&oe(o)&&pt(o);)o=Ft(o,e);return o&&W(o)&&pt(o)&&!ft(o)?n:o||le(t)||n}const ve=async function(t){const e=this.getOffsetParent||Nt,n=this.getDimensions,o=await n(t.floating);return{reference:xe(t.reference,await e(t.floating),t.strategy),floating:{x:0,y:0,width:o.width,height:o.height}}};function Ee(t){return L(t).direction==="rtl"}const Ce={convertOffsetParentRelativeRectToViewportRelativeRect:fe,getDocumentElement:S,getClippingRect:be,getOffsetParent:Nt,getElementRects:ve,getClientRects:he,getDimensions:ye,getScale:H,isElement:R,isRTL:Ee};function Dt(t,e){return t.x===e.x&&t.y===e.y&&t.width===e.width&&t.height===e.height}function Te(t,e){let n=null,o;const i=S(t);function r(){var l;clearTimeout(o),(l=n)==null||l.disconnect(),n=null}function s(l,a){l===void 0&&(l=!1),a===void 0&&(a=1),r();const c=t.getBoundingClientRect(),{left:f,top:h,width:u,height:d}=c;if(l||e(),!u||!d)return;const m=Z(h),p=Z(i.clientWidth-(f+u)),w=Z(i.clientHeight-(h+d)),g=Z(f),x={rootMargin:-m+"px "+-p+"px "+-w+"px "+-g+"px",threshold:B(0,V(1,a))||1};let v=!0;function y(F){const C=F[0].intersectionRatio;if(C!==a){if(!v)return s();C?s(!1,C):o=setTimeout(()=>{s(!1,1e-7)},1e3)}C===1&&!Dt(c,t.getBoundingClientRect())&&s(),v=!1}try{n=new IntersectionObserver(y,{...x,root:i.ownerDocument})}catch{n=new IntersectionObserver(y,x)}n.observe(t)}return s(!0),r}function ke(t,e,n,o){o===void 0&&(o={});const{ancestorScroll:i=!0,ancestorResize:r=!0,elementResize:s=typeof ResizeObserver=="function",layoutShift:l=typeof IntersectionObserver=="function",animationFrame:a=!1}=o,c=mt(t),f=i||r?[...c?K(c):[],...K(e)]:[];f.forEach(g=>{i&&g.addEventListener("scroll",n,{passive:!0}),r&&g.addEventListener("resize",n)});const h=c&&l?Te(c,n):null;let u=-1,d=null;s&&(d=new ResizeObserver(g=>{let[b]=g;b&&b.target===c&&d&&(d.unobserve(e),cancelAnimationFrame(u),u=requestAnimationFrame(()=>{var x;(x=d)==null||x.observe(e)})),n()}),c&&!a&&d.observe(c),d.observe(e));let m,p=a?_(t):null;a&&w();function w(){const g=_(t);p&&!Dt(p,g)&&n(),p=g,m=requestAnimationFrame(w)}return n(),()=>{var g;f.forEach(b=>{i&&b.removeEventListener("scroll",n),r&&b.removeEventListener("resize",n)}),h?.(),(g=d)==null||g.disconnect(),d=null,a&&cancelAnimationFrame(m)}}const Re=Zt,Le=Gt,Oe=Yt,Se=(t,e,n)=>{const o=new Map,i={platform:Ce,...n},r={...i.platform,_c:o};return Xt(t,e,{...i,platform:r})};function rt(t,e){window.bridgeCommand(t,e)}function Bt(t){return typeof t=="string"?document.querySelector(t):t}class G{options;isVisible=!1;targetElement=null;modalElement;backdropElement;arrowElement=null;shadowRoot;hostElement;cleanUpdateHandler;resizeTimeout=null;constructor(e={}){this.options={body:"",footer:"",showCloseButton:!0,closeOnBackdropClick:!1,backdrop:!0,target:null,blockTargetClick:!1,...e},this.createModal(),this.bindEvents()}createModal(){this.hostElement=document.createElement("div"),this.hostElement.style.position="fixed",this.hostElement.style.top="0",this.hostElement.style.left="0",this.hostElement.style.width="100%",this.hostElement.style.height="100%",this.hostElement.style.zIndex="10000",this.hostElement.style.pointerEvents="none",this.shadowRoot=this.hostElement.attachShadow({mode:"open"}),this.injectStyles(),this.backdropElement=document.createElement("div"),this.backdropElement.className="ah-modal-backdrop",this.modalElement=document.createElement("div"),this.modalElement.className="ah-modal-container";const e=document.createElement("div");if(e.className="ah-modal-content",this.options.showCloseButton){const o=document.createElement("div");o.className="ah-modal-header";const i=document.createElement("button");i.className="ah-modal-close-button",i.innerHTML="×",i.setAttribute("aria-label","Close modal"),o.appendChild(i),e.appendChild(o)}const n=document.createElement("div");if(n.className="ah-modal-body",typeof this.options.body=="string"?n.innerHTML=this.options.body:this.options.body instanceof Node&&n.append(this.options.body),e.appendChild(n),this.options.footer){const o=document.createElement("div");o.className="ah-modal-footer",typeof this.options.footer=="string"?o.innerHTML=this.options.footer:this.options.footer instanceof Node?o.append(this.options.footer):o.append(...this.options.footer),e.appendChild(o)}if(this.modalElement.appendChild(e),this.options.body){this.backdropElement.appendChild(this.modalElement);const o=this.options.target?Bt(this.options.target):null;o&&(this.cleanUpdateHandler=ke(o,this.modalElement,this.positionModal.bind(this,o)))}this.shadowRoot.appendChild(this.backdropElement)}injectStyles(){const e=document.createElement("style");e.textContent=`
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
        `,this.shadowRoot.appendChild(e)}createArrow(){this.arrowElement=document.createElement("div"),this.arrowElement.className="ah-modal-arrow",this.modalElement.appendChild(this.arrowElement)}bindEvents(){const e=this.modalElement.querySelector(".ah-modal-close-button");e&&e.addEventListener("click",()=>{this.close(),rt("ankihub_modal_closed")}),this.backdropElement.addEventListener("click",n=>{n.target===this.backdropElement&&this.options.closeOnBackdropClick&&this.close()}),this.modalElement.addEventListener("click",n=>{n.stopPropagation()})}show(){this.isVisible||(document.body.appendChild(this.hostElement),this.options.target&&(this.targetElement=Bt(this.options.target),this.applySpotlight()),this.targetElement&&(this.createArrow(),this.positionModal(this.targetElement)),requestAnimationFrame(()=>{this.backdropElement.classList.add("ah-modal-show")}),this.isVisible=!0)}close(){this.isVisible&&(this.cleanUpdateHandler?.(),this.removeSpotlight(),this.arrowElement&&this.arrowElement.parentNode&&(this.arrowElement.parentNode.removeChild(this.arrowElement),this.arrowElement=null),this.backdropElement.classList.remove("ah-modal-show"),this.backdropElement.classList.add("ah-modal-hide"),setTimeout(()=>{this.hostElement.parentNode&&this.hostElement.parentNode.removeChild(this.hostElement),this.backdropElement.classList.remove("ah-modal-hide")},200),this.isVisible=!1)}destroy(){this.close(),this.removeSpotlight(),this.resizeTimeout&&(clearTimeout(this.resizeTimeout),this.resizeTimeout=null),this.arrowElement&&this.arrowElement.parentNode&&(this.arrowElement.parentNode.removeChild(this.arrowElement),this.arrowElement=null)}spotlightClasses(){let e=["ah-spotlight-active"];return this.options.backdrop&&e.push("ah-with-backdrop"),e}applySpotlight(){if(this.targetElement){if(this.targetElement.classList.add(...this.spotlightClasses()),this.options.blockTargetClick){const e=this.targetElement.style.pointerEvents;this.targetElement.style.pointerEvents="none",this.targetElement.setAttribute("data-original-pointer-events",e)}this.targetElement.parentElement&&(this.targetElement.parentElement.style.backdropFilter="none")}}removeSpotlight(){if(!this.targetElement)return;this.targetElement.classList.remove(...this.spotlightClasses());const e=this.targetElement.getAttribute("data-original-pointer-events");e?(this.targetElement.style.pointerEvents=e,this.targetElement.removeAttribute("data-original-pointer-events")):this.targetElement.style.pointerEvents="",this.targetElement=null}_positionModal(e,n){this.modalElement.style.top=`${e}px`,this.modalElement.style.left=`${n}px`}async positionModal(e){const n=this.arrowElement?this.arrowElement.offsetWidth:0,o=Math.sqrt(2*n**2)/2;let i=[Le()];this.arrowElement&&(i.push(Re(o)),i.push(Oe({element:this.arrowElement})));const{x:r,y:s,middlewareData:l,placement:a}=await Se(e,this.modalElement,{middleware:i});this._positionModal(s,r);const c=a.split("-")[0],f={top:"bottom",right:"left",bottom:"top",left:"right"}[c];if(l.arrow){const{x:h,y:u}=l.arrow;Object.assign(this.arrowElement.style,{left:h!=null?`${h}px`:"",top:u!=null?`${u}px`:"",[f]:`${-n}px`,right:"",bottom:"",[f]:`${-n/2}px`,transform:"rotate(45deg)"})}}setModalPosition(e,n,o,i){let r={getBoundingClientRect(){return{x:0,y:0,top:e,left:n,bottom:i,right:o,width:o,height:i}}};this.positionModal(r)}}let P=null,j=null;function I(){P&&(P.destroy(),P=null)}function Ae(){I();const t=`
<h2>📚 First time with Anki?</h2>
<p>Find your way in the app with this onboarding tour.</p>
    `,e=[],n=document.createElement("button");n.textContent="Close",n.classList.add("ah-button","ah-secondary-button"),n.addEventListener("click",I),e.push(n);const o=document.createElement("button");o.textContent="Take tour",o.classList.add("ah-button","ah-secondary-button"),o.addEventListener("click",()=>rt("ankihub_start_onboarding")),e.push(o);const i=new G({body:t,footer:e});i.show(),P=i}function Me({body:t,currentStep:e,stepCount:n,target:o,primaryButton:i={show:!0,label:"Next"},blockTargetClick:r=!1,backdrop:s=!0}){I();const l=[],a=document.createElement("span");if(a.textContent=`${e} of ${n}`,l.push(a),i.show){const f=document.createElement("button");f.textContent=i.label,f.classList.add("ah-button","ah-primary-button"),f.addEventListener("click",()=>rt("ankihub_tutorial_primary_button_clicked")),l.push(f)}const c=new G({body:t,footer:l,target:o,blockTargetClick:r,backdrop:s});c.show(),P=c}function Pe({target:t,currentStep:e,blockTargetClick:n=!1}){I();var o=new G({body:"",footer:"",target:t,blockTargetClick:n});o.show(),P=o,j&&(window.removeEventListener("resize",j),j=null),j=()=>{if(!o.targetElement)return;const{top:i,left:r,width:s,height:l}=o.targetElement.getBoundingClientRect();rt(`ankihub_tutorial_target_resize:${e}:${i}:${r}:${s}:${l}`)},window.addEventListener("resize",j),j()}function Fe({top:t,left:e,width:n,height:o}){P&&P.setModalPosition(t,e,n,o)}function Ne(){I();var t=new G({body:"",footer:""});t.show(),P=t}k.Modal=G,k.addTutorialBackdrop=Ne,k.destroyActiveTutorialModal=I,k.highlightTutorialTarget=Pe,k.positionTutorialTarget=Fe,k.promptForOnboardingTour=Ae,k.showTutorialModal=Me,Object.defineProperty(k,Symbol.toStringTag,{value:"Module"})}));
