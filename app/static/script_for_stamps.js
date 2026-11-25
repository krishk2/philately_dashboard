// Manage Stamps: prefill the update form from the selected stamp and keep the delete form in sync.
document.addEventListener('DOMContentLoaded', function () {
    const select = document.getElementById('existingStamps');
    const deleteId = document.getElementById('deleteStampId');

    function showSelected() {
        const option = select.options[select.selectedIndex];
        deleteId.value = select.value;
        if (!option || !option.dataset.name) return;
        document.getElementById('updateStampName').value = option.dataset.name;
        document.getElementById('updateStampDate').value = option.dataset.date;
        document.getElementById('updateStampPrice').value = option.dataset.price;
    }

    select.addEventListener('change', showSelected);
    showSelected();

    document.getElementById('deleteStampForm').addEventListener('submit', function (e) {
        if (!deleteId.value || !confirm('Delete this stamp?')) {
            e.preventDefault();
        }
    });
});
