// Function to fetch the manifest.json file and read its content
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

// Function to create a button with the given icon and popup content
async function createButton() {
    try {
        console.log("createButton function called");
        const manifest = await fetchManifest();
        if (!manifest) return;

        const popupUrl = manifest.action.default_popup;
        const iconUrl = manifest.action.default_icon;
        console.log("Popup URL:", popupUrl);
        console.log("Icon URL:", iconUrl);

        // Create the button element
        const button = document.createElement('button');
        button.style.backgroundImage = `url(${iconUrl})`;
        button.style.width = '50px';
        button.style.height = '50px';
        button.style.backgroundSize = 'cover';
        button.style.border = 'none';
        button.style.cursor = 'pointer';
        console.log("Button created");

        // Create the popup element
        const popup = document.createElement('div');
        popup.style.display = 'none';
        popup.style.position = 'absolute';
        popup.style.top = '60px';
        popup.style.left = '10px';
        popup.style.border = '1px solid #ccc';
        popup.style.backgroundColor = '#fff';
        popup.style.boxShadow = '0 0 10px rgba(0, 0, 0, 0.1)';
        popup.style.padding = '10px';
        popup.style.zIndex = '1000';
        console.log("Popup created");

        // Fetch the content for the popup
        console.log("Fetching popup content from:", popupUrl);
        const popupResponse = await fetch(popupUrl);
        if (!popupResponse.ok) {
            throw new Error(`HTTP error! status: ${popupResponse.status}`);
        }
        const popupContent = await popupResponse.text();
        popup.innerHTML = popupContent;
        console.log("Popup content fetched and set");

        // Append the button and popup to the body
        document.body.appendChild(button);
        document.body.appendChild(popup);
        console.log("Button and popup appended to the body");

        // Show/hide the popup when the button is clicked
        button.addEventListener('click', () => {
            popup.style.display = popup.style.display === 'none' ? 'block' : 'none';
            console.log("Button clicked, popup visibility:", popup.style.display);
        });

        // Hide the popup when clicking outside of it
        document.addEventListener('click', (event) => {
            if (!popup.contains(event.target) && !button.contains(event.target)) {
                popup.style.display = 'none';
                console.log("Clicked outside, popup hidden");
            }
        });
    } catch (error) {
        console.error("Error creating button and popup:", error);
    }
}

// Add the function to onShownHook
onShownHook.push(() => {
    console.log("DOM fully loaded and parsed");
    createButton();
});
