// Postal circle dashboard. Both forms now post to the server (they used to only show an alert),
// so this script just shows the selected item's current stock next to the quantity field.
document.addEventListener('DOMContentLoaded', () => {
    const item = document.getElementById('item');
    const quantity = document.getElementById('quantity');
    if (!item || !quantity) return;

    function showCurrentStock() {
        const option = item.options[item.selectedIndex];
        if (option && option.dataset.stock !== undefined) {
            quantity.placeholder = 'Currently ' + option.dataset.stock + ' available';
        }
    }
    item.addEventListener('change', showCurrentStock);
    showCurrentStock();
});
