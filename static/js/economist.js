/** Модуль Экономист — таблица приходит с сервера в поле html */

const ECONOMIST_ERROR_REPLY = 'Не удалось получить ответ. Попробуйте уточнить формулировку вопроса или повторить запрос позже.';

document.addEventListener('DOMContentLoaded', () => {
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatMessages = document.getElementById('chat-messages');
    const chatSubmit = document.getElementById('chat-submit');
    const requestStatus = document.getElementById('economist-request-status');
    const exampleButtons = document.querySelectorAll('.economist-example');
    const compactExamples = document.getElementById('economist-compact-examples');
    const compactExampleButtons = compactExamples?.querySelectorAll('button') || [];

    loadChatHistory();

    function updateSubmitState() {
        if (!chatSubmit || !chatInput) return;
        chatSubmit.disabled = !chatInput.value.trim() || chatForm?.classList.contains('loading');
    }

    function setRequestStatus(text = '', type = '') {
        if (!requestStatus) return;
        requestStatus.textContent = text;
        requestStatus.className = type
            ? `economist-request-status ${type}`
            : 'economist-request-status';
    }

    chatInput?.addEventListener('input', updateSubmitState);
    updateSubmitState();

    function fillFromExample(button) {
        if (!chatInput || chatForm?.classList.contains('loading')) return;
        chatInput.value = button.dataset.query ?? button.textContent.trim();
        updateSubmitState();
        chatInput.focus();
    }

    function switchToCompactExamples() {
        document.getElementById('economist-welcome')?.remove();
        compactExamples?.classList.remove('hidden');
    }

    function historyTargetId(index) {
        return `economist-history-${index}`;
    }

    function scrollToHistory(index) {
        const target = document.getElementById(historyTargetId(index));
        if (!target) return;
        const scrollTop = target.offsetTop;
        chatMessages.scrollTo({ top: Math.max(0, scrollTop), behavior: 'smooth' });
        target.classList.remove('economist-message-highlight');
        void target.offsetWidth;
        target.classList.add('economist-message-highlight');
        window.setTimeout(() => target.classList.remove('economist-message-highlight'), 1800);
    }

    function bindHistoryLinks() {
        document.querySelectorAll('.economist-history-link').forEach(button => {
            button.onclick = () => scrollToHistory(button.dataset.historyIndex);
        });
    }

    exampleButtons.forEach(button => {
        button.addEventListener('click', () => {
            fillFromExample(button);
        });
    });

    compactExampleButtons.forEach(button => {
        button.addEventListener('click', () => fillFromExample(button));
    });
    bindHistoryLinks();

    chatForm?.addEventListener('submit', async e => {
        e.preventDefault();
        const message = chatInput.value.trim();
        if (!message) return;

        switchToCompactExamples();
        App.addMessage('chat-messages', message, 'user');
        chatInput.value = '';
        chatForm.classList.add('loading');
        updateSubmitState();
        setRequestStatus('Формирую ответ. Это может занять некоторое время…', 'loading');
        chatMessages?.setAttribute('aria-busy', 'true');

        try {
            const resp = await fetch('/economist/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message }),
            });
            let data = {};
            try {
                data = await resp.json();
            } catch (_) {}

            if (!resp.ok) {
                showErrorReply();
                setRequestStatus('Запрос не выполнен. Попробуйте еще раз.', 'error');
                return;
            }
            if (!hasReplyContent(data)) {
                showErrorReply();
                setRequestStatus('По запросу не удалось получить данные.', 'error');
                return;
            }
            showBotReply(data);
            if (data.intent === 'error') {
                setRequestStatus('Не удалось получить данные. Уточните вопрос или повторите запрос позже.', 'error');
            } else {
                setRequestStatus('Ответ готов.', 'ok');
            }
            await refreshHistory();
            await loadChatHistory();
        } catch (_) {
            showErrorReply();
            setRequestStatus('Нет связи с сервисом. Проверьте подключение и повторите запрос.', 'error');
        } finally {
            chatForm.classList.remove('loading');
            chatMessages?.removeAttribute('aria-busy');
            updateSubmitState();
            chatInput?.focus();
        }
    });

    function isEmptyAnswer(text) {
        const s = String(text ?? '').trim();
        return !s || s === '[]' || s === '{}' || s === 'null';
    }

    function hasReplyContent(data) {
        const html = typeof data?.html === 'string' ? data.html.trim() : '';
        if (html) return true;
        const answer = data?.answer ?? data?.response;
        return typeof answer === 'string' && !isEmptyAnswer(answer);
    }

    function showErrorReply() {
        App.addMessage('chat-messages', ECONOMIST_ERROR_REPLY, 'bot');
    }

    function showBotReply(data) {
        const container = chatMessages || document.getElementById('chat-messages');
        if (!container) return;

        if (!hasReplyContent(data)) {
            showErrorReply();
            return;
        }

        const div = document.createElement('div');
        div.className = 'message bot';

        const html = typeof data.html === 'string' ? data.html.trim() : '';
        if (html) {
            const wrap = document.createElement('div');
            wrap.className = 'economist-table-wrap';
            wrap.innerHTML = html;
            div.appendChild(wrap);
        } else {
            const p = document.createElement('p');
            p.className = 'message-text';
            const answer = data.answer ?? data.response;
            p.textContent = answer.trim();
            div.appendChild(p);
        }

        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
    }

    async function loadChatHistory() {
        try {
            const resp = await fetch('/economist/history');
            const data = await resp.json();
            if (!chatMessages || !data.history?.length) return;

            switchToCompactExamples();
            chatMessages.innerHTML = '';
            const items = [...data.history].reverse();
            for (const [index, h] of items.entries()) {
                const group = document.createElement('div');
                group.className = 'economist-message-group';
                group.id = historyTargetId(data.history.length - 1 - index);
                chatMessages.appendChild(group);

                const userMessage = document.createElement('div');
                userMessage.className = 'message message-text user';
                userMessage.textContent = h.query;
                group.appendChild(userMessage);
                showBotReply({ answer: h.response, html: h.html || '' });
                const botMessage = chatMessages.lastElementChild;
                if (botMessage && botMessage !== group) group.appendChild(botMessage);
            }
            bindHistoryLinks();
        } catch (_) {}
    }

    async function refreshHistory() {
        try {
            const resp = await fetch('/economist/history');
            const data = await resp.json();
            const list = document.getElementById('history-list');
            if (!list) return;
            list.innerHTML = data.history.length
                ? data.history.map((h, index) =>
                    `<li><time>${escapeHtml(h.timestamp)}</time><button type="button" class="history-query economist-history-link" data-history-index="${index}">${escapeHtml(String(h.query || '').slice(0, 80))}</button></li>`
                ).join('')
                : '<li class="muted">Нет запросов</li>';
            bindHistoryLinks();
        } catch (_) {}
    }

    function escapeHtml(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }
});
