function renderFlashCardSelectorButtonOnCongrats() {
    if (!document.getElementById("{{ FLASHCARD_SELECTOR_OPEN_BUTTON_ID }}")) {
        const style = document.createElement("style");
        const bodyWrapper = document.querySelector("body");
        const smartSearchBgColor = "#9333ea";
        const smartSearchBgHoverColor = "#851ce8";
        const tooltipBackgroundColor = "#d1d5db";
        const tooltipColor = "black";
        const sparklesSVG = `<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"12\" height=\"12\" viewBox=\"0 0 20 20\" fill=\"currentColor\">\n                        <path d=\"M15.9806 1.80388C15.8871 1.33646 15.4767 1 15 1C14.5233 1 14.1129 1.33646 14.0194 1.80388L13.7809 2.99644C13.7017 3.3923 13.3923 3.70174 12.9964 3.78091L11.8039 4.01942C11.3365 4.1129 11 4.52332 11 5C11 5.47668 11.3365 5.8871 11.8039 5.98058L12.9964 6.21909C13.3923 6.29826 13.7017 6.6077 13.7809 7.00356L14.0194 8.19612C14.1129 8.66354 14.5233 9 15 9C15.4767 9 15.8871 8.66354 15.9806 8.19612L16.2191 7.00356C16.2983 6.6077 16.6077 6.29826 17.0036 6.21909L18.1961 5.98058C18.6635 5.8871 19 5.47668 19 5C19 4.52332 18.6635 4.1129 18.1961 4.01942L17.0036 3.78091C16.6077 3.70174 16.2983 3.3923 16.2191 2.99644L15.9806 1.80388Z\" fill=\"currentColor\"/>\n                        <path d=\"M6.94868 5.68377C6.81257 5.27543 6.43043 5 6 5C5.56957 5 5.18743 5.27543 5.05132 5.68377L4.36754 7.73509C4.26801 8.03369 4.03369 8.26801 3.73509 8.36754L1.68377 9.05132C1.27543 9.18743 1 9.56957 1 10C1 10.4304 1.27543 10.8126 1.68377 10.9487L3.73509 11.6325C4.03369 11.732 4.26801 11.9663 4.36754 12.2649L5.05132 14.3162C5.18743 14.7246 5.56957 15 6 15C6.43043 15 6.81257 14.7246 6.94868 14.3162L7.63246 12.2649C7.73199 11.9663 7.96631 11.732 8.26491 11.6325L10.3162 10.9487C10.7246 10.8126 11 10.4304 11 10C11 9.56957 10.7246 9.18743 10.3162 9.05132L8.26491 8.36754C7.96631 8.26801 7.73199 8.03369 7.63246 7.73509L6.94868 5.68377Z\" fill=\"currentColor\"/>\n                        <path d=\"M13.9487 13.6838C13.8126 13.2754 13.4304 13 13 13C12.5696 13 12.1874 13.2754 12.0513 13.6838L11.8675 14.2351C11.768 14.5337 11.5337 14.768 11.2351 14.8675L10.6838 15.0513C10.2754 15.1874 10 15.5696 10 16C10 16.4304 10.2754 16.8126 10.6838 16.9487L11.2351 17.1325C11.5337 17.232 11.768 17.4663 11.8675 17.7649L12.0513 18.3162C12.1874 18.7246 12.5696 19 13 19C13.4304 19 13.8126 18.7246 13.9487 18.3162L14.1325 17.7649C14.232 17.4663 14.4663 17.232 14.7649 17.1325L15.3162 16.9487C15.7246 16.8126 16 16.4304 16 16C16 15.5696 15.7246 15.1874 15.3162 15.0513L14.7649 14.8675C14.4663 14.768 14.232 14.5337 14.1325 14.2351L13.9487 13.6838Z\" fill=\"currentColor\"/>\n                        </svg>`;
        const wrapper = document.createElement("div");
        wrapper.className = "smart-search-button-wrapper";
        const button = document.createElement("button");
        button.id = "{{ FLASHCARD_SELECTOR_OPEN_BUTTON_ID }}";
        button.innerHTML = `${sparklesSVG} Smart Search`;
        const tooltip = document.createElement("div");
        tooltip.innerHTML = "Find relevant<br>flashcards with AI";
        const tooltipArrow = document.createElement("div");
        tooltipArrow.style.cssText = `
            position: absolute;
            width: 0;
            height: 0;
            border-left: 6px solid ${tooltipBackgroundColor};
            border-top: 6px solid transparent;
            border-bottom: 6px solid transparent;
            top: -8px;
            transform: rotate(-90deg);
            right:0;
            left: 0;
            margin: 0 auto;
        `;
        tooltip.style.cssText = `
          position: absolute;
          z-index: 1000;
          background-color: ${tooltipBackgroundColor};
          font-size: medium;
          color: ${tooltipColor};
          border-radius: 5px;
          text-align: center;
          padding: 10px;
          display: none;
          top: 35px;
          width:150px;
        `;
        style.textContent = `
            #ankihub-flashcard-selector-open-button {
                position: relative;
                background-color: ${smartSearchBgColor};
                color: white;
                display: none;
                align-items: center;
                justify-content: center;
                gap: 4px;
                cursor: pointer;
                width: fit-content;
                height: 100%;
                padding: 4px 24px;
            }
            #ankihub-flashcard-selector-open-button:hover {
                background: ${smartSearchBgHoverColor}
            }

             .smart-search-button-wrapper{
                margin: 0 auto;
                max-width: 30em;
             }

        `;

        wrapper.style.cssText = `

        `
        button.addEventListener("click", function () {
            pycmd("{{ FLASHCARD_SELECTOR_OPEN_PYCMD }}");
        });
        button.addEventListener("mouseover", function () {
            tooltip.style.display = "block";
        });
        button.addEventListener("mouseout", function () {
            tooltip.style.display = "none";
        });
        tooltip.appendChild(tooltipArrow);
        button.appendChild(tooltip);
        wrapper.appendChild(button);
        bodyWrapper.appendChild(wrapper);
        document.head.appendChild(style);
    }
}

function renderFlashCardSelectorButtonOnDecks() {
    if (!document.getElementById("{{ FLASHCARD_SELECTOR_OPEN_BUTTON_ID }}")) {
        const style = document.createElement("style");
        const studyButton = document.querySelector("button#study");
        const studyButtonWrapper = studyButton.parentElement;
        const trElement = studyButtonWrapper.parentElement;
        const smartSearchBgColor = "#9333ea";
        const smartSearchBgHoverColor = "#851ce8";
        const tooltipBackgroundColor = "#d1d5db";
        const tooltipColor = "black";
        const sparklesSVG = `<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"12\" height=\"12\" viewBox=\"0 0 20 20\" fill=\"currentColor\">\n                        <path d=\"M15.9806 1.80388C15.8871 1.33646 15.4767 1 15 1C14.5233 1 14.1129 1.33646 14.0194 1.80388L13.7809 2.99644C13.7017 3.3923 13.3923 3.70174 12.9964 3.78091L11.8039 4.01942C11.3365 4.1129 11 4.52332 11 5C11 5.47668 11.3365 5.8871 11.8039 5.98058L12.9964 6.21909C13.3923 6.29826 13.7017 6.6077 13.7809 7.00356L14.0194 8.19612C14.1129 8.66354 14.5233 9 15 9C15.4767 9 15.8871 8.66354 15.9806 8.19612L16.2191 7.00356C16.2983 6.6077 16.6077 6.29826 17.0036 6.21909L18.1961 5.98058C18.6635 5.8871 19 5.47668 19 5C19 4.52332 18.6635 4.1129 18.1961 4.01942L17.0036 3.78091C16.6077 3.70174 16.2983 3.3923 16.2191 2.99644L15.9806 1.80388Z\" fill=\"currentColor\"/>\n                        <path d=\"M6.94868 5.68377C6.81257 5.27543 6.43043 5 6 5C5.56957 5 5.18743 5.27543 5.05132 5.68377L4.36754 7.73509C4.26801 8.03369 4.03369 8.26801 3.73509 8.36754L1.68377 9.05132C1.27543 9.18743 1 9.56957 1 10C1 10.4304 1.27543 10.8126 1.68377 10.9487L3.73509 11.6325C4.03369 11.732 4.26801 11.9663 4.36754 12.2649L5.05132 14.3162C5.18743 14.7246 5.56957 15 6 15C6.43043 15 6.81257 14.7246 6.94868 14.3162L7.63246 12.2649C7.73199 11.9663 7.96631 11.732 8.26491 11.6325L10.3162 10.9487C10.7246 10.8126 11 10.4304 11 10C11 9.56957 10.7246 9.18743 10.3162 9.05132L8.26491 8.36754C7.96631 8.26801 7.73199 8.03369 7.63246 7.73509L6.94868 5.68377Z\" fill=\"currentColor\"/>\n                        <path d=\"M13.9487 13.6838C13.8126 13.2754 13.4304 13 13 13C12.5696 13 12.1874 13.2754 12.0513 13.6838L11.8675 14.2351C11.768 14.5337 11.5337 14.768 11.2351 14.8675L10.6838 15.0513C10.2754 15.1874 10 15.5696 10 16C10 16.4304 10.2754 16.8126 10.6838 16.9487L11.2351 17.1325C11.5337 17.232 11.768 17.4663 11.8675 17.7649L12.0513 18.3162C12.1874 18.7246 12.5696 19 13 19C13.4304 19 13.8126 18.7246 13.9487 18.3162L14.1325 17.7649C14.232 17.4663 14.4663 17.232 14.7649 17.1325L15.3162 16.9487C15.7246 16.8126 16 16.4304 16 16C16 15.5696 15.7246 15.1874 15.3162 15.0513L14.7649 14.8675C14.4663 14.768 14.232 14.5337 14.1325 14.2351L13.9487 13.6838Z\" fill=\"currentColor\"/>\n                        </svg>`;
        const button = document.createElement("button");
        button.id = "{{ FLASHCARD_SELECTOR_OPEN_BUTTON_ID }}";
        button.innerHTML = `${sparklesSVG} Smart Search`;
        const tooltip = document.createElement("div");
        tooltip.innerHTML = "Find relevant<br>flashcards with AI";
        const tooltipArrow = document.createElement("div");
        tooltipArrow.style.cssText = `
            position: absolute;
            width: 0;
            height: 0;
            border-left: 6px solid ${tooltipBackgroundColor};
            border-top: 6px solid transparent;
            border-bottom: 6px solid transparent;
            top: -8px;
            transform: rotate(-90deg);
            right:0;
            left: 0;
            margin: 0 auto;
        `;
        tooltip.style.cssText = `
          position: absolute;
          z-index: 1000;
          background-color: ${tooltipBackgroundColor};
          font-size: medium;
          color: ${tooltipColor};
          border-radius: 5px;
          text-align: center;
          padding: 10px;
          display: none;
          top: 32px;
          width:140px;
        `;
        studyButtonWrapper.style.cssText = `
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 8px;
        `;
        trElement.style.cssText = `
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        `;
        style.textContent = `
        .fancy button {
            width: 100px;
            height: 100%;
            padding: 4px 24px;
        }
            #ankihub-flashcard-selector-open-button {
                position: relative;
                background-color: ${smartSearchBgColor};
                color: white;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 4px;
                cursor: pointer;
                width: fit-content;
                height: 100%;
                padding: 4px 24px;
            }
            #ankihub-flashcard-selector-open-button:hover {
                background: ${smartSearchBgHoverColor}
            }
        `;
        button.addEventListener("click", function () {
            pycmd("{{ FLASHCARD_SELECTOR_OPEN_PYCMD }}");
        });
        button.addEventListener("mouseover", function () {
            tooltip.style.display = "block";
        });
        button.addEventListener("mouseout", function () {
            tooltip.style.display = "none";
        });
        tooltip.appendChild(tooltipArrow);
        button.appendChild(tooltip);
        studyButtonWrapper.appendChild(button);
        document.head.appendChild(style);
    }
}




function waitForElm(selector) {
    return new Promise(resolve => {
        if (document.querySelector(selector)) {
            return resolve(document.querySelector(selector));
        }

        const observer = new MutationObserver(mutations => {
            if (document.querySelector(selector)) {
                observer.disconnect();
                resolve(document.querySelector(selector));
            }
        });

        // If you get "parameter 1 is not of type 'Node'" error, see https://stackoverflow.com/a/77855838/492336
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    });
}


if (window.location.href.includes("congrats")) {
    renderFlashCardSelectorButtonOnCongrats();
    waitForElm(".congrats").then(() => {
        document.getElementById("{{ FLASHCARD_SELECTOR_OPEN_BUTTON_ID }}").style.display = "flex";
    })
} else {
    renderFlashCardSelectorButtonOnDecks();
}
