/** Модуль Юрист — фронтенд */
document.addEventListener('DOMContentLoaded', () => {
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatMessages = document.getElementById('chat-messages');
    const fileList = document.getElementById('file-list');

    refreshFiles();
    loadChatHistory();

    async function uploadDoc(file) {
        App.setFileProcessing({
            statusId: 'upload-status',
            progressId: 'upload-progress',
            zoneId: 'doc-drop',
            active: true,
            message: `Обработка файла: ${file.name}… (извлечение текста и индексация)`,
        });
        try {
            const data = await App.uploadFile('/lawyer/upload', file);
            const name = safeText(data.filename) || file.name;
            App.setStatus(
                'upload-status',
                `✓ Загружен: ${name} (${data.chunks} фрагментов в базе)`,
                'ok',
                { zoneId: 'doc-drop' },
            );
            await refreshFiles();
        } catch (e) {
            App.setStatus(
                'upload-status',
                safeText(e.message) || 'Ошибка загрузки',
                'error',
                { zoneId: 'doc-drop' },
            );
        } finally {
            App.setFileProcessing({
                statusId: 'upload-status',
                progressId: 'upload-progress',
                zoneId: 'doc-drop',
                active: false,
                message: '',
            });
        }
    }

    App.setupDropZone('doc-drop', 'doc-file', uploadDoc);

    fileList?.addEventListener('click', async e => {
        const btn = e.target.closest('.delete-file');
        if (!btn) return;
        const li = btn.closest('li');
        const fileId = li?.dataset.fileId;
        if (!fileId || !(await App.confirm('Удалить документ из базы?', { danger: true }))) return;

        try {
            const resp = await fetch(`/lawyer/files/${fileId}`, { method: 'DELETE' });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                App.setStatus('upload-status', safeText(err.detail) || 'Не удалось удалить', 'error');
                return;
            }
            App.setStatus('upload-status', '✓ Документ удалён из базы', 'ok');
            await refreshFiles();
        } catch (e) {
            App.setStatus('upload-status', 'Ошибка при удалении', 'error');
        }
    });

    document.getElementById('clear-index')?.addEventListener('click', async () => {
        if (!(await App.confirm('Очистить всю базу знаний?', { danger: true }))) return;
        try {
            const resp = await fetch('/lawyer/index', { method: 'DELETE' });
            if (!resp.ok) {
                App.setStatus('upload-status', 'Не удалось очистить базу', 'error');
                return;
            }
            App.setStatus('upload-status', '✓ База знаний очищена', 'ok');
            await refreshFiles();
        } catch (e) {
            App.setStatus('upload-status', 'Ошибка при очистке', 'error');
        }
    });

    chatForm?.addEventListener('submit', async e => {
        e.preventDefault();
        const question = chatInput.value.trim();
        if (!question) return;

        App.addMessage('chat-messages', question, 'user');
        chatInput.value = '';
        chatForm.classList.add('loading');

        try {
            const resp = await fetch('/lawyer/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json; charset=utf-8' },
                body: JSON.stringify({ question }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                throw new Error(data.detail || resp.statusText || 'Ошибка запроса');
            }

            showBotReply(data.answer, data.citations || []);
            refreshHistory();
        } catch (err) {
            showBotReply(safeText(err.message) || 'Ошибка запроса', []);
        } finally {
            chatForm.classList.remove('loading');
        }
    });

    function safeText(s) {
        const t = stripSiteUrls(s || '');
        return t || String(s || '').trim();
    }

    function showBotReply(answer, citations) {
        const text = safeText(answer) || 'Ответ пуст. Попробуйте переформулировать вопрос.';
        const msgDiv = document.createElement('div');
        msgDiv.className = 'message bot';

        const p = document.createElement('p');
        p.className = 'message-text';
        p.textContent = text;
        msgDiv.appendChild(p);

        if (citations && citations.length) {
            const box = document.createElement('div');
            box.className = 'citations';
            const title = document.createElement('strong');
            title.textContent = 'Источники:';
            box.appendChild(title);

            citations.forEach(c => {
                const block = document.createElement('div');
                block.className = 'citation-block';
                const src = document.createElement('div');
                src.className = 'citation-source';
                src.textContent = `${safeText(c.filename)}, стр. ${c.page}`;
                const mark = document.createElement('mark');
                mark.textContent = safeText(c.text);
                block.appendChild(src);
                block.appendChild(mark);
                box.appendChild(block);
            });
            msgDiv.appendChild(box);
        }

        if (chatMessages) {
            chatMessages.appendChild(msgDiv);
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }
    }

    function escapeHtml(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    async function refreshFiles() {
        try {
            const resp = await fetch('/lawyer/files');
            const data = await resp.json();
            if (!fileList) return;
            if (!data.files || !data.files.length) {
                fileList.innerHTML = '<li class="muted">Нет загруженных документов</li>';
                return;
            }
            fileList.innerHTML = data.files.map(f =>
                `<li data-file-id="${f.file_id}">
                    <span>${escapeHtml(safeText(f.filename))}</span>
                    <button type="button" class="btn-icon delete-file" title="Удалить">×</button>
                 </li>`
            ).join('');
        } catch (e) {
            console.error('refreshFiles', e);
        }
    }

    async function refreshHistory() {
        try {
            const resp = await fetch('/lawyer/history');
            const data = await resp.json();
            const list = document.getElementById('history-list');
            if (!list) return;
            list.innerHTML = data.history.length
                ? data.history.map(h => {
                    const q = safeText(h.query);
                    return `<li><time>${h.timestamp}</time><p class="history-query">${escapeHtml(q.length > 60 ? q.slice(0, 60) + '…' : q)}</p></li>`;
                }).join('')
                : '<li class="muted">Нет вопросов</li>';
        } catch (e) {
            console.error('refreshHistory', e);
        }
    }

    async function loadChatHistory() {
        try {
            const resp = await fetch('/lawyer/history');
            const data = await resp.json();
            if (!chatMessages || !data.history || !data.history.length) return;
            chatMessages.innerHTML = '';
            const items = [...data.history].reverse();
            for (const h of items) {
                App.addMessage('chat-messages', safeText(h.query), 'user');
                showBotReply(h.response, h.citations || []);
            }
        } catch (e) {
            console.error('loadChatHistory', e);
        }
    }
});
