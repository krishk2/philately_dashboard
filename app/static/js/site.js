// Site-wide behaviour. Each feature only runs when its elements are on the page.
(function () {
    'use strict';

    const csrfToken = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ---------- profile menu: close when clicking elsewhere or pressing Escape ----------
    document.addEventListener('click', (e) => {
        document.querySelectorAll('details.profile-menu[open]').forEach((menu) => {
            if (!menu.contains(e.target)) menu.removeAttribute('open');
        });
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            document.querySelectorAll('details.profile-menu[open]').forEach((m) => m.removeAttribute('open'));
        }
    });

    // ---------- carousel ----------
    // Scroll-snap track with prev/next, dots, swipe (native) and a gentle autoplay that pauses on
    // hover/focus, when the tab is hidden, and entirely for reduced-motion users.
    document.querySelectorAll('[data-carousel]').forEach((root) => {
        const track = root.querySelector('.carousel-track');
        const slides = Array.from(track.children);
        if (!slides.length) return;
        const prev = root.querySelector('.carousel-btn.prev');
        const next = root.querySelector('.carousel-btn.next');
        const dotsBox = root.querySelector('.carousel-dots');

        const step = () => slides[0].getBoundingClientRect().width + parseFloat(getComputedStyle(track).columnGap || 20);
        const perView = () => Math.max(1, Math.round(track.clientWidth / step()));
        const pages = () => Math.max(1, Math.ceil(slides.length / perView()));
        const page = () => Math.round(track.scrollLeft / (step() * perView()));
        const atEnd = () => track.scrollLeft + track.clientWidth >= track.scrollWidth - 4;

        function goTo(p) {
            const last = pages() - 1;
            const target = p > last ? 0 : p < 0 ? last : p;
            track.scrollTo({ left: target * step() * perView(), behavior: reducedMotion ? 'auto' : 'smooth' });
        }

        function renderDots() {
            if (!dotsBox) return;
            dotsBox.innerHTML = '';
            for (let i = 0; i < pages(); i++) {
                const b = document.createElement('button');
                b.type = 'button';
                b.setAttribute('aria-label', 'Go to slide group ' + (i + 1));
                b.addEventListener('click', () => goTo(i));
                dotsBox.appendChild(b);
            }
            updateDots();
        }
        function updateDots() {
            if (!dotsBox) return;
            const current = atEnd() ? pages() - 1 : page();
            Array.from(dotsBox.children).forEach((d, i) => d.setAttribute('aria-current', i === current ? 'true' : 'false'));
        }

        prev && prev.addEventListener('click', () => goTo(page() - 1));
        next && next.addEventListener('click', () => goTo(atEnd() ? pages() : page() + 1));
        let scrollTimer;
        track.addEventListener('scroll', () => { clearTimeout(scrollTimer); scrollTimer = setTimeout(updateDots, 80); });
        window.addEventListener('resize', renderDots);
        renderDots();

        if (!reducedMotion && slides.length > perView()) {
            let paused = false;
            ['mouseenter', 'focusin', 'touchstart'].forEach((ev) => root.addEventListener(ev, () => { paused = true; }, { passive: true }));
            ['mouseleave', 'focusout'].forEach((ev) => root.addEventListener(ev, () => { paused = false; }));
            setInterval(() => {
                if (!paused && !document.hidden) goTo(atEnd() ? pages() : page() + 1);
            }, 5000);
        }
    });

    // ---------- wishlist hearts ----------
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-like]');
        if (!btn) return;
        e.preventDefault();
        fetch('/wishlist/' + btn.dataset.like, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken(), 'Accept': 'application/json' },
        }).then((r) => {
            if (r.redirected || !r.ok || !(r.headers.get('Content-Type') || '').includes('application/json')) {
                window.location = '/login?next=' + encodeURIComponent(window.location.pathname);
                return null;
            }
            return r.json();
        }).then((data) => {
            if (!data) return;
            const liked = data.liked;
            btn.setAttribute('aria-pressed', liked ? 'true' : 'false');
            if (btn.dataset.labelled !== undefined) {
                btn.textContent = liked ? '❤️ Saved to wishlist' : '🤍 Add to wishlist';
            } else {
                btn.textContent = liked ? '❤️' : '🤍';
            }
            btn.title = liked ? 'Remove from wishlist' : 'Add to wishlist';
        }).catch((err) => console.error('Wishlist error', err));
    });

    // ---------- role tabs (login / register) ----------
    document.querySelectorAll('[data-role-switch]').forEach((sw) => {
        const buttons = sw.querySelectorAll('button[data-target]');
        const show = (id) => {
            buttons.forEach((b) => {
                const on = b.dataset.target === id;
                b.setAttribute('aria-selected', on ? 'true' : 'false');
                document.getElementById(b.dataset.target).hidden = !on;
            });
        };
        buttons.forEach((b) => b.addEventListener('click', () => show(b.dataset.target)));
        const initial = sw.querySelector('button[aria-selected="true"]') || buttons[0];
        show(initial.dataset.target);
    });

    // ---------- image preview before upload ----------
    document.querySelectorAll('input[type=file][data-preview]').forEach((input) => {
        const img = document.getElementById(input.dataset.preview);
        input.addEventListener('change', () => {
            const file = input.files[0];
            if (!file || !img) return;
            const reader = new FileReader();
            reader.onload = () => { img.src = reader.result; };
            reader.readAsDataURL(file);
        });
    });

    // ---------- select that pre-fills other fields (Manage stamps, Events, Stock) ----------
    document.querySelectorAll('select[data-prefill]').forEach((select) => {
        const fill = () => {
            const opt = select.options[select.selectedIndex];
            if (!opt) return;
            Object.entries(opt.dataset).forEach(([key, value]) => {
                const field = document.querySelector('[data-field="' + key + '"]');
                if (!field) return;
                if (field.tagName === 'INPUT' && field.dataset.mode === 'placeholder') field.placeholder = value;
                else field.value = value;
            });
        };
        select.addEventListener('change', fill);
        fill();
    });

    // ---------- confirm before destructive actions ----------
    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) e.preventDefault();
        const pw = form.querySelector('[name=newPassword]');
        const confirmPw = form.querySelector('[name=confirmPassword]');
        if (pw && confirmPw && pw.value !== confirmPw.value) {
            e.preventDefault();
            confirmPw.setCustomValidity('Passwords do not match');
            confirmPw.reportValidity();
            confirmPw.addEventListener('input', () => confirmPw.setCustomValidity(''), { once: true });
        }
    });

    // ---------- chatbot ----------
    const fab = document.getElementById('chatbotIcon');
    const panel = document.getElementById('chatbotInterface');
    if (fab && panel) {
        const log = document.getElementById('chat-log');
        const input = document.getElementById('user-input');
        fab.addEventListener('click', () => { panel.classList.toggle('open'); if (panel.classList.contains('open')) input.focus(); });
        document.getElementById('closeChatbot').addEventListener('click', () => panel.classList.remove('open'));

        // textContent (never innerHTML) so neither the user nor the model can inject markup.
        const line = (who, text) => {
            const p = document.createElement('p');
            const b = document.createElement('strong');
            b.textContent = who + ': ';
            p.append(b, document.createTextNode(text));
            log.appendChild(p);
            log.scrollTop = log.scrollHeight;
        };

        document.getElementById('chat-form').addEventListener('submit', (e) => {
            e.preventDefault();
            const text = input.value.trim();
            if (!text) return;
            line('You', text);
            input.value = '';
            fetch('/ask', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text }),
            }).then((r) => r.json())
              .then((data) => line('Bot', data.response || 'Sorry, something went wrong.'))
              .catch(() => line('Bot', 'Sorry, something went wrong.'));
        });
    }
})();
