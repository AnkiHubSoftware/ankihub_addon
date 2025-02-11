(() => {
    const button = document.getElementById('{{ button_id }}');
    const rect = button.getBoundingClientRect();

    // Remove existing dropdown if any
    const existingDropdown = document.getElementById('template-dropdown');
    if (existingDropdown) {
        existingDropdown.remove();
        return;
    }

    // Create dropdown
    const dropdown = document.createElement('div');
    dropdown.id = 'template-dropdown';
    dropdown.style.position = 'absolute';
    dropdown.style.left = rect.left + 'px';
    dropdown.style.top = (rect.bottom + 2) + 'px';
    dropdown.style.backgroundColor = 'white';
    dropdown.style.border = '1px solid #ccc';
    dropdown.style.borderRadius = '3px';
    dropdown.style.boxShadow = '0 2px 4px rgba(0,0,0,0.2)';
    dropdown.style.zIndex = '1000';
    dropdown.style.maxHeight = '300px';
    dropdown.style.overflowY = 'auto';

    // Parse the options JSON string
    const templateOptions = JSON.parse('{{ options }}');

    if (templateOptions.length === 0) {
        const item = document.createElement('div');
        item.textContent = 'No templates found';
        item.style.padding = '8px 12px';
        item.style.color = '#666';
        item.style.fontStyle = 'italic';
        dropdown.appendChild(item);
    } else {
        templateOptions.forEach(opt => {
            const item = document.createElement('div');
            item.textContent = opt;
            item.style.padding = '8px 12px';
            item.style.cursor = 'pointer';
            item.style.whiteSpace = 'nowrap';

            item.addEventListener('mouseover', () => {
                item.style.backgroundColor = '#f0f0f0';
            });

            item.addEventListener('mouseout', () => {
                item.style.backgroundColor = 'white';
            });

            item.addEventListener('click', () => {
                pycmd(`template-select:${opt}`);
                dropdown.remove();
            });

            dropdown.appendChild(item);
        });
    }

    // Close dropdown when clicking outside
    document.addEventListener('click', function closeDropdown(e) {
        if (!dropdown.contains(e.target) && e.target !== button) {
            dropdown.remove();
            document.removeEventListener('click', closeDropdown);
        }
    });

    document.body.appendChild(dropdown);
})();
