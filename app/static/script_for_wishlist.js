// The ❤️ button on stamp cards. It was referenced by the original pages but never defined.
// Toggles the stamp in the wishlist on the server and flips the heart.
function likeStamp(stampId, el) {
    const token = document.querySelector('meta[name="csrf-token"]').content;
    fetch('/wishlist/' + stampId, {
        method: 'POST',
        headers: { 'X-CSRFToken': token, 'Accept': 'application/json' },
    })
    .then(response => {
        // Not logged in (or a postal circle account): the server redirects to an HTML page.
        if (response.redirected || !response.ok ||
            !(response.headers.get('Content-Type') || '').includes('application/json')) {
            window.location = '/login?next=' + encodeURIComponent(window.location.pathname);
            return null;
        }
        return response.json();
    })
    .then(data => {
        if (!data || !el) return;
        if (el.tagName === 'BUTTON') {
            el.textContent = data.liked ? '❤️ Remove from Wishlist' : '🤍 Add to Wishlist';
        } else {
            el.textContent = data.liked ? '❤️' : '🤍';
        }
    })
    .catch(error => console.error('Wishlist error:', error));
}
