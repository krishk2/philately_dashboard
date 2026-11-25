document.addEventListener('DOMContentLoaded', function () {
    const eventSelect = document.getElementById('existingEvents');
    const deleteId = document.getElementById('deleteEventId');
    let events = [];

    // Fill the update form with the selected event's current values.
    function showSelected() {
        const selected = events.find(e => String(e.id) === eventSelect.value);
        deleteId.value = eventSelect.value;
        if (!selected) return;
        document.getElementById('updateEventName').value = selected.event_name;
        document.getElementById('updateEventDate').value = selected.date;
        document.getElementById('updateEventLocation').value = selected.location;
        document.getElementById('updateEventDescription').value = selected.description;
    }

    // Function to fetch events and populate the dropdown
    function populateEventDropdown() {
        fetch(eventSelect.dataset.source, { headers: { 'Accept': 'application/json' } })
            .then(response => response.json())
            .then(data => {
                events = data;
                eventSelect.innerHTML = ''; // Clear existing options

                data.forEach(event => {
                    const option = document.createElement('option');
                    option.value = event.id;
                    option.textContent = event.event_name + ' (' + event.date + ')';
                    eventSelect.appendChild(option);
                });
                showSelected();
            })
            .catch(error => console.error('Error fetching events:', error));
    }

    // Update hidden delete field and the form when an event is selected
    eventSelect.addEventListener('change', showSelected);

    // Don't delete by accident.
    document.getElementById('deleteEventForm').addEventListener('submit', function (e) {
        if (!deleteId.value || !confirm('Delete this event?')) {
            e.preventDefault();
        }
    });

    // Call the function to populate the dropdown when the page loads
    populateEventDropdown();
});
