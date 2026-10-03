/**
 * Анимация lottie-web с роботом-помощником.
 * Запускается для каждого элемента с классом js-lottie и атрибутом data-src
 * (в шапке и на странице входа).
 */
(function () {
    'use strict';

    function mount(container) {
        const src = container.dataset.src;
        if (!src) return;

        const animation = window.lottie.loadAnimation({
            container: container,
            renderer: 'svg',
            loop: true,
            autoplay: true,
            path: src,
            rendererSettings: { progressiveLoad: false },
        });

        animation.addEventListener('DOMLoaded', function () {
            container.classList.add('is-ready');
        });

        animation.addEventListener('data_failed', function () {
            container.remove();
        });

        // В фоне анимацию не крутим — экономим CPU на скрытой вкладке.
        document.addEventListener('visibilitychange', function () {
            if (document.hidden) animation.pause();
            else animation.play();
        });
    }

    function init() {
        if (typeof window.lottie === 'undefined') return;
        document.querySelectorAll('.js-lottie').forEach(mount);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();