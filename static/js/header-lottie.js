/** Анимация lottie-web в шапке, сразу после названия «ИИ-помощник …». */
(function () {
    'use strict';

    const container = document.getElementById('headerLottie');
    if (!container || typeof window.lottie === 'undefined') return;

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
})();
