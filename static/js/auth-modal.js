/** Возврат в систему после разрыва сессии: окно с логином и паролем.
 *
 * Сессия живёт в подписанной cookie (AUTH_SESSION_MAX_AGE, по умолчанию 12 ч),
 * поэтому посреди работы запрос модуля теряет вход. Раньше фронтенд показывал
 * это как обычную ошибку, и окно приложения не менялось. Теперь fetch
 * перехватывается: пользователю показывается окно входа, а сорвавшийся запрос
 * повторяется после успеха — введённые данные не теряются.
 *
 * Признаков разрыва два, потому что middleware отвечает по-разному:
 *  • пути /api/* получают 401 JSON (core/auth_router.py);
 *  • остальные пути получают 303 на /login, и fetch молча следует за ним,
 *    отдавая страницу входа со статусом 200 — это ловим по response.redirected.
 *
 * Исключение — сам вход: /api/login отвечает 401 и на неверный пароль, поэтому
 * перехватывать его нельзя, иначе получится кольцо.
 */
(function () {
    'use strict';

    const LOGIN_API = '/api/login';
    const LOGIN_PAGE = '/login';
    const PASSWORD_URL = '/account/password';
    // Пути, которые сами обрабатывают вход и выход.
    const SKIP_PATHS = new Set([LOGIN_API, LOGIN_PAGE, '/api/logout', '/logout']);

    const rawFetch = typeof window.fetch === 'function' ? window.fetch.bind(window) : null;

    let elements = null;
    // Общий промис: одновременные 401 открывают одно окно, а не несколько.
    let pending = null;
    let resolveCurrent = null;

    function ui() {
        if (elements) return elements;
        const overlay = document.getElementById('authOverlay');
        const form = document.getElementById('authForm');
        if (!overlay || !form) return null;

        elements = {
            overlay,
            form,
            login: document.getElementById('authLogin'),
            password: document.getElementById('authPassword'),
            error: document.getElementById('authError'),
            cancel: document.getElementById('authCancel'),
            submit: document.getElementById('authSubmit'),
            defaultLogin: overlay.dataset.login || '',
        };

        form.addEventListener('submit', onSubmit);
        elements.cancel.addEventListener('click', () => finish('logout'));
        overlay.addEventListener('mousedown', (event) => {
            if (event.target === overlay) finish(false);
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !overlay.classList.contains('hidden')) finish(false);
        });
        return elements;
    }

    function showError(message) {
        if (!elements) return;
        elements.error.textContent = message || 'Не удалось войти';
        elements.error.hidden = false;
    }

    function clearError() {
        if (!elements) return;
        elements.error.hidden = true;
        elements.error.textContent = '';
    }

    function open() {
        const el = ui();
        if (!el) return Promise.resolve(true); // разметки нет — не мешаем работе
        clearError();
        el.login.value = el.defaultLogin;
        el.password.value = '';
        el.overlay.classList.remove('hidden');
        document.body.classList.add('auth-open');
        el.password.focus();
        return new Promise((resolve) => {
            resolveCurrent = resolve;
        });
    }

    /** result: true — вошли, false — отменили, 'logout' — уйти на страницу входа.
     *  redirectTo — адрес, куда увести вместо возврата к прерванному запросу. */
    function finish(result, redirectTo) {
        if (elements) {
            elements.overlay.classList.add('hidden');
            document.body.classList.remove('auth-open');
        }
        const resolve = resolveCurrent;
        resolveCurrent = null;
        pending = null;

        if (redirectTo) {
            window.location.assign(redirectTo);
            return;
        }
        if (result === 'logout') {
            const next = window.location.pathname + window.location.search;
            window.location.assign(`${LOGIN_PAGE}?next=${encodeURIComponent(next)}`);
            return;
        }
        if (resolve) resolve(result === true);
    }

    /** Дождаться входа пользователя: true — вошли, false — отказались. */
    function ensureAuthenticated() {
        if (!pending) pending = open();
        return pending;
    }

    async function onSubmit(event) {
        event.preventDefault();
        if (!elements || !rawFetch) return;
        const login = elements.login.value.trim();
        const password = elements.password.value;
        if (!login || !password) {
            showError('Введите логин и пароль');
            return;
        }

        clearError();
        elements.submit.disabled = true;
        try {
            const response = await rawFetch(LOGIN_API, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({
                    login,
                    password,
                    next: window.location.pathname + window.location.search,
                }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                showError(data.detail || 'Неверный логин или пароль');
                return;
            }
            // Вход по временному паролю: сервер сам решает, что сначала его сменить.
            if (data.next === PASSWORD_URL) {
                finish(true, PASSWORD_URL);
                return;
            }
            finish(true);
        } catch (error) {
            showError('Сервер недоступен. Повторите попытку.');
        } finally {
            if (elements) elements.submit.disabled = false;
        }
    }

    function pathOf(input) {
        try {
            const url = typeof input === 'string'
                ? input
                : (input && input.url ? input.url : String(input));
            return new URL(url, window.location.origin).pathname;
        } catch (error) {
            return '';
        }
    }

    function shouldIntercept(input) {
        const path = pathOf(input);
        if (!path || SKIP_PATHS.has(path)) return false;
        return !path.startsWith('/login');
    }

    /** Ответ пришёл со страницы входа — значит, fetch ушёл по 303 из middleware. */
    function redirectedToLogin(response) {
        if (!response.redirected) return false;
        try {
            return new URL(response.url).pathname === LOGIN_PAGE;
        } catch (error) {
            return false;
        }
    }

    function needsReauth(response) {
        return response.status === 401 || redirectedToLogin(response);
    }

    function install() {
        if (!rawFetch || !window.fetch) return;
        window.fetch = async function (input, init) {
            let response = await rawFetch(input, init);
            if (needsReauth(response) && shouldIntercept(input)) {
                const authenticated = await ensureAuthenticated();
                if (authenticated) response = await rawFetch(input, init);
            }
            return response;
        };
    }

    install();
})();
