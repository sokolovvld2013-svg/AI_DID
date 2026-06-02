/** Модуль Экономист — таблица приходит с сервера в поле html */

const ECONOMIST_ERROR_REPLY = 'Произошла ошибка или отсутствуют данные! Попробуйте переформулировать вопрос.';

document.addEventListener('DOMContentLoaded', () => {
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatMessages = document.getElementById('chat-messages');

    loadChatHistory();

    chatForm?.addEventListener('submit', async e => {
        e.preventDefault();
        const message = chatInput.value.trim();
        if (!message) return;

        App.addMessage('chat-messages', message, 'user');
        chatInput.value = '';
        chatForm.classList.add('loading');

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
                return;
            }
            if (!hasReplyContent(data)) {
                showErrorReply();
                return;
            }
            showBotReply(data);
            refreshHistory();
        } catch (_) {
            showErrorReply();
        } finally {
            chatForm.classList.remove('loading');
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

            chatMessages.innerHTML = '';
            const items = [...data.history].reverse();
            for (const h of items) {
                App.addMessage('chat-messages', h.query, 'user');
                showBotReply({ answer: h.response, html: h.html || '' });
            }
        } catch (_) {}
    }

    async function refreshHistory() {
        try {
            const resp = await fetch('/economist/history');
            const data = await resp.json();
            const list = document.getElementById('history-list');
            if (!list) return;
            list.innerHTML = data.history.length
                ? data.history.map(h =>
                    `<li><time>${escapeHtml(h.timestamp)}</time><p class="history-query">${escapeHtml(String(h.query || '').slice(0, 80))}</p></li>`
                ).join('')
                : '<li class="muted">Нет запросов</li>';
        } catch (_) {}
    }

    function escapeHtml(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }
});
