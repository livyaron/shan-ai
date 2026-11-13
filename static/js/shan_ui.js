/* Shan-AI shared motion layer (with static/css/shan_ui.css). Decoration only:
   no fetch, no numbers, no text — the page is complete without this file. */
(function () {
    'use strict';
    var nav = document.querySelector('.shan-nav');
    if (!nav || nav.dataset.shanUi) return;
    nav.dataset.shanUi = '1';
    var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ── The marker under the links: rests on the current page, follows the pointer.
    var links = nav.querySelector('.shan-nav-links');
    if (links) {
        var ink = document.createElement('span');
        ink.className = 'shan-nav-ink';
        ink.setAttribute('aria-hidden', 'true');
        links.appendChild(ink);
        var tops = Array.prototype.filter.call(links.children, function (el) {
            return el.classList.contains('shan-nav-link') || el.classList.contains('shan-nav-dd');
        });
        var home = tops.filter(function (el) {
            return el.classList.contains('active') || (el.firstElementChild && el.firstElementChild.classList.contains('active'));
        })[0] || null;
        var place = function (el) {
            if (!el) { ink.classList.remove('on'); return; }
            ink.style.width = el.offsetWidth + 'px';
            ink.style.transform = 'translate(' + el.offsetLeft + 'px,' + (el.offsetTop + el.offsetHeight + 2) + 'px)';
            ink.classList.add('on');
        };
        tops.forEach(function (el) {
            el.addEventListener('pointerenter', function () { place(el); });
            el.addEventListener('focusin', function () { place(el); });
        });
        links.addEventListener('pointerleave', function () { place(home); });
        links.addEventListener('focusout', function () { place(home); });
        window.addEventListener('resize', function () { place(home); });
        ink.style.transition = 'none';          // first placement: no slide from the corner
        place(home);
        requestAnimationFrame(function () { requestAnimationFrame(function () { ink.style.transition = ''; }); });
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { place(home); });
    }

    // ── Loading line: from the click until the next page arrives.
    var bar = document.createElement('span');
    bar.className = 'shan-nav-progress';
    bar.setAttribute('aria-hidden', 'true');
    nav.appendChild(bar);
    var start = function () { if (!still) bar.classList.add('go'); };
    var stop = function () { bar.classList.remove('go'); };
    document.addEventListener('click', function (e) {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var a = e.target.closest && e.target.closest('a[href]');
        if (!a || a.target === '_blank' || a.hasAttribute('download')) return;
        var url;
        try { url = new URL(a.href, location.href); } catch (err) { return; }
        if (url.origin !== location.origin) return;
        if (url.pathname === location.pathname && url.search === location.search) return;   // #anchor on this page
        if (/\.(xlsx?|csv|pdf|docx?|pptx?|zip|png|jpe?g|mp4)$/i.test(url.pathname)) return;    // a download never "arrives"
        start();
        setTimeout(stop, 12000);
    });
    window.addEventListener('pageshow', stop);   // back/forward cache restores the page mid-line

    // ── Cards rise one after another, in reading order (CSS alone raises them together).
    var cards = document.querySelectorAll('.card, .card-x, .stat-card, .section-card, .kb-card');
    for (var i = 0; i < cards.length && i < 14; i++) cards[i].style.setProperty('--shan-i', i);
})();
