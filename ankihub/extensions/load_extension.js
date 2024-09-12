async function fetchManifest() {
    try {
        console.log("Fetching manifest.json...");
        const response = await fetch('manifest.json');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const manifest = await response.json();
        console.log("Manifest fetched successfully:", manifest);
        return manifest;
    } catch (error) {
        console.error("Error fetching manifest.json:", error);
    }
}

async function initExtension() {
    try {
        const manifest = await fetchManifest();
        if (!manifest) return;
        const endpoint = manifest.action.default_popup;
        const token = '51d64902dc720547edcf007fd1645a8c2128445e7fb0664529ac1f0471d62aae';
        const baseResponse = await fetch(endpoint, {
            method: 'GET', // Specify the method if needed
            headers: {
              'Authorization': `Token ${token}`,
              'Content-Type': 'application/json' // Include this if necessary
            }
          });
        if (!baseResponse.ok) {
            throw new Error(`HTTP error! status: ${baseResponse.status}`);
        }

        const responseContent = await baseResponse.text();
        // Parse the HTML returned by AnkiHub.
        const parser = new DOMParser();
        const doc = parser.parseFromString(responseContent, "text/html");
        // Get hx-headers from the doc.body and add it to the main document body in the Anki webview.
        const headers = doc.body.getAttribute('hx-headers');
        document.body.setAttribute('hx-headers', headers);
        // insert doc.body.innerHTML into the main document body
        document.body.innerHTML += doc.body.innerHTML;
        // TODO - Load the htmx.min.js file from the a local source such as the media folder if possible.
        let HTMXScript = document.createElement("script");
        HTMXScript.setAttribute("src", "https://unpkg.com/htmx.org@1.9.12")
        HTMXScript.setAttribute("type", "text/javascript");
        HTMXScript.setAttribute("async", true);
        HTMXScript.setAttribute("integrity", "sha384-ujb1lZYygJmzgSwoxRggbCHcjc0rB2XoQrxeTUQyRjrOnlCoYta87iKBWq3EsdM2")
        HTMXScript.setAttribute("crossorigin", "anonymous")
        document.head.appendChild(HTMXScript);
        HTMXScript.addEventListener("load", () => {
            console.log("File loaded")
        });

        HTMXScript.addEventListener("error", (ev) => {
            console.log("Error on loading file", ev);
        });

        let HTMXHeadersScript = document.createElement("script");
        HTMXHeadersScript.textContent = `
        document.body.addEventListener('htmx:configRequest', (event) => {
            event.detail.headers['Authorization'] = 'Token ${token}';
        })
        `;
        document.head.appendChild(HTMXHeadersScript);
        HTMXHeadersScript.addEventListener("load", () => {
            console.log("Headers script loaded")
        });


    } catch (error) {
        console.error("Error initializing extension:", error);
    }
}

// Add the function to onShownHook
onShownHook.push(() => {
    console.log("DOM fully loaded and parsed");
    initExtension();
});
