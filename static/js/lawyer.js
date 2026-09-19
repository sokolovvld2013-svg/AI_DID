/** Модуль Юрист — фронтенд (проверка договора и внутренние нормативные документы) */
document.addEventListener('DOMContentLoaded', () => {
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatSubmit = document.getElementById('chat-submit');
    const chatMessages = document.getElementById('chat-messages');
    const chatTitle = document.getElementById('chat-title');
    const contractFileInput = document.getElementById('contract-file');
    const contractCard = document.getElementById('contract-card');
    const kbCard = document.getElementById('kb-card');
    const contractInfo = document.getElementById('contract-info');
    const fileList = document.getElementById('file-list');
    const actionHint = document.getElementById('chat-action-hint');
    const guidance = document.getElementById('module-guidance');

    const CHECK_QUESTION =
        'Проверь договор и сформируй отчёт о проверке с замечаниями.';
    const CHECK_USER_LABEL = 'Проверка договора';

    let mode = 'contract';
    let contractLoaded = false;
    let requestPending = false;

    function safeText(s) {
        const t = stripSiteUrls(s || '');
        return t || String(s || '').trim();
    }

    function escapeHtml(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    function updateChatAvailability() {
        const checkMode = mode === 'contract';
        const disabled = requestPending || (checkMode ? !contractLoaded : !chatInput?.value.trim());

        if (chatForm) {
            chatForm.classList.toggle('lawyer-check-form', checkMode);
            chatForm.classList.toggle('lawyer-kb-form', !checkMode);
        }
        if (chatInput) {
            chatInput.classList.toggle('hidden', checkMode);
            if (checkMode) {
                chatInput.disabled = true;
                chatInput.value = '';
            } else {
                chatInput.disabled = false;
                chatInput.placeholder = 'Задайте вопрос по внутренним нормативным документам…';
            }
        }
        if (chatSubmit) {
            chatSubmit.disabled = disabled;
            chatSubmit.textContent = checkMode ? 'Проверить' : 'Спросить';
        }
        if (actionHint) {
            actionHint.className = 'module-action-hint';
            if (requestPending) {
                actionHint.textContent = checkMode ? 'Проверяю договор…' : 'Формирую ответ…';
                actionHint.classList.add('is-loading');
            } else if (checkMode && !contractLoaded) {
                actionHint.textContent = 'Сначала загрузите договор.';
            } else if (checkMode) {
                actionHint.textContent = 'Договор готов к проверке.';
                actionHint.classList.add('is-ready');
            } else {
                actionHint.textContent = chatInput?.value.trim() ? 'Вопрос готов к отправке.' : 'Введите вопрос по внутренним нормативным документам.';
            }
        }
    }

    function setMode(nextMode) {
        mode = nextMode;
        if (chatTitle) {
            chatTitle.textContent =
                mode === 'contract'
                    ? 'Проверка договора'
                    : 'Внутренние нормативные документы';
        }
        if (contractCard) contractCard.classList.toggle('hidden', mode !== 'contract');
        if (kbCard) kbCard.classList.toggle('hidden', mode !== 'kb');
        document.querySelectorAll('.lawyer-mode-btn').forEach(btn => {
            const isActive = btn.getAttribute('data-tab') === mode;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
        updateChatAvailability();
        if (guidance) guidance.textContent = mode === 'contract'
            ? '1. Загрузите договор. 2. Дождитесь обработки. 3. Запустите проверку.'
            : 'Задайте вопрос по загруженным внутренним нормативным документам.';
    }

    document.querySelectorAll('.lawyer-mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            setMode(btn.getAttribute('data-tab') || 'contract');
        });
    });
    chatInput?.addEventListener('input', updateChatAvailability);

    function removeEmptyState() {
        document.getElementById('chat-empty-state')?.remove();
    }

    function historyTargetId(index) { return `lawyer-history-${index}`; }
    function scrollToHistory(index) {
        const target = document.getElementById(historyTargetId(index));
        if (!target || !chatMessages) return;
        const top = target.offsetTop;
        chatMessages.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
        target.classList.remove('module-message-highlight');
        void target.offsetWidth;
        target.classList.add('module-message-highlight');
        setTimeout(() => target.classList.remove('module-message-highlight'), 1800);
    }
    function bindHistoryLinks() {
        document.querySelectorAll('.module-history-link').forEach(button => {
            button.onclick = () => scrollToHistory(button.dataset.historyIndex);
        });
    }

    async function refreshContractStatus() {
        try {
            const resp = await fetch('/lawyer/contract/status');
            const data = await resp.json().catch(() => ({}));
            contractLoaded = Boolean(data.loaded);
            if (!contractInfo) return;
            if (contractLoaded) {
                const name = safeText(data.filename) || 'Договор';
                const frags = data.fragments ? ` (${data.fragments} фрагментов)` : '';
                contractInfo.innerHTML = `<li><span>${escapeHtml(name + frags)}</span></li>`;
            } else {
                contractInfo.innerHTML = '<li class="muted">Договор не загружен</li>';
            }
            updateChatAvailability();
        } catch (e) {
            console.error('refreshContractStatus', e);
        }
    }

    async function uploadContract(file) {
        if (!file) return;
        App.setFileProcessing({
            statusId: 'contract-status',
            progressId: 'contract-progress',
            zoneId: 'contract-drop',
            active: true,
            message: `Обработка файла: ${file.name}… (разбор пунктов договора)`,
        });
        try {
            const data = await App.uploadFile('/lawyer/contract/upload', file);
            const name = safeText(data.filename) || file.name;
            const frags = data.fragments ? `, ${data.fragments} фрагментов` : '';
            App.setStatus(
                'contract-status',
                `✓ Загружен: ${name}${frags}`,
                'ok',
                { zoneId: 'contract-drop' },
            );
            contractLoaded = true;
            await refreshContractStatus();
        } catch (e) {
            App.setStatus(
                'contract-status',
                safeText(e.message) || 'Ошибка загрузки',
                'error',
                { zoneId: 'contract-drop' },
            );
        } finally {
            App.setFileProcessing({
                statusId: 'contract-status',
                progressId: 'contract-progress',
                zoneId: 'contract-drop',
                active: false,
                message: '',
            });
            if (contractFileInput) contractFileInput.value = '';
        }
    }

    App.setupDropZone('contract-drop', 'contract-file', uploadContract);

    contractInfo?.addEventListener('click', async e => {
        const btn = e.target.closest('.remove-contract');
        if (!btn) return;
        if (!(await App.confirm('Удалить загруженный договор?', { danger: true }))) return;
        try {
            const resp = await fetch('/lawyer/contract', { method: 'DELETE' });
            if (!resp.ok) return;
            App.setStatus('contract-status', '✓ Договор удалён', 'ok');
            await refreshContractStatus();
        } catch (e) {
            App.setStatus('contract-status', 'Ошибка при удалении договора', 'error');
        }
    });

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

    function formatAnswer(text, replyMode) {
        const useMode = replyMode || mode;
        return useMode === 'contract'
            ? App.formatCheckReportMarkdown(text)
            : App.formatChatMarkdown(text);
    }

    function showBotReply(answer, citations, replyMode) {
        const text = safeText(answer) || 'Ответ пуст. Попробуйте переформулировать вопрос.';
        const useMode = replyMode || mode;
        const msgDiv = document.createElement('div');
        msgDiv.className = 'message bot';

        const body = document.createElement('div');
        body.className = 'message-text';
        body.innerHTML = formatAnswer(text, replyMode);
        msgDiv.appendChild(body);

        if (citations && citations.length) {
            const box = document.createElement('div');
            box.className = 'citations';
            const title = document.createElement('strong');
            title.textContent = 'Источники:';
            box.appendChild(title);

            const list = document.createElement('ul');
            list.className = 'citation-list';
            citations.forEach(c => {
                const li = document.createElement('li');
                li.className = 'citation-source';
                const label = safeText(c.filename) || 'Документ';
                const page = c.page != null ? c.page : '—';
                const ref = c.id != null ? `[${c.id}] ` : '';
                const pageLabel = /^(п\.|фрагм\.|разд\.)/.test(String(page))
                    ? page
                    : `стр. ${page}`;
                li.textContent = `${ref}${label}, ${pageLabel}`;
                list.appendChild(li);
            });
            box.appendChild(list);
            msgDiv.appendChild(box);
        }

        if (chatMessages) {
            chatMessages.appendChild(msgDiv);
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }
    }

    chatForm?.addEventListener('submit', async e => {
        e.preventDefault();
        if (mode === 'contract' && !contractLoaded) return;

        const question = mode === 'contract' ? CHECK_QUESTION : chatInput.value.trim();
        if (!question) return;

        const userLabel = mode === 'contract' ? CHECK_USER_LABEL : question;
        removeEmptyState();
        App.addMessage('chat-messages', userLabel, 'user');
        if (mode !== 'contract') chatInput.value = '';
        chatForm.classList.add('loading');
        requestPending = true;
        updateChatAvailability();

        try {
            const resp = await fetch('/lawyer/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json; charset=utf-8' },
                body: JSON.stringify({ question, mode }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                throw new Error(data.detail || resp.statusText || 'Ошибка запроса');
            }

            showBotReply(data.answer, data.citations || [], mode);
            await refreshHistory();
            await loadChatHistory();
        } catch (err) {
            showBotReply(safeText(err.message) || 'Ошибка запроса', [], mode);
        } finally {
            chatForm.classList.remove('loading');
            requestPending = false;
            updateChatAvailability();
        }
    });

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
                ? data.history.map((h, index) => {
                    const isCheck = (h.mode || 'contract') === 'contract';
                    const q = isCheck ? CHECK_USER_LABEL : safeText(h.query);
                    return `<li><time>${h.timestamp}</time><button type="button" class="history-query module-history-link" data-history-index="${index}">${escapeHtml(q.length > 60 ? q.slice(0, 60) + '…' : q)}</button></li>`;
                }).join('')
                : '<li class="muted">Нет вопросов</li>';
            bindHistoryLinks();
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
            for (const [index, h] of items.entries()) {
                const isCheck = (h.mode || 'contract') === 'contract';
                const label = isCheck ? CHECK_USER_LABEL : safeText(h.query);
                const group = document.createElement('div');
                group.className = 'module-message-group';
                group.id = historyTargetId(data.history.length - 1 - index);
                chatMessages.appendChild(group);
                const user = document.createElement('div');
                user.className = 'message message-text user';
                user.textContent = label;
                group.appendChild(user);
                showBotReply(h.response, h.citations || [], h.mode || 'contract');
                const bot = chatMessages.lastElementChild;
                if (bot && bot !== group) group.appendChild(bot);
            }
            bindHistoryLinks();
        } catch (e) {
            console.error('loadChatHistory', e);
        }
    }

    refreshContractStatus();
    refreshFiles();
    loadChatHistory();
    bindHistoryLinks();
    setMode('contract');
});
