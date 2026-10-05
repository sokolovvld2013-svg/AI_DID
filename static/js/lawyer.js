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
    const clearIndexButton = document.getElementById('clear-index');
    const chatSection = document.getElementById('lawyer-chat');
    const historyCard = document.getElementById('lawyer-history-card');
    const arbitrCard = document.getElementById('arbitr-card');
    const arbitrResults = document.getElementById('arbitr-results');

    const CHECK_QUESTION =
        'Проверь договор и сформируй отчёт о проверке с замечаниями.';
    const CHECK_USER_LABEL = "Проверка документации... ⚠️ Ответ носит информационный характер и не заменяет специалиста.";
    const CHECK_HISTORY_LABEL = 'Проверка договора';

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
                actionHint.textContent = 'Формирую ответ. Это может занять некоторое время…';
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
                    ? 'Результат проверки'
                    : 'Вопрос по документам';
        }
        if (contractCard) contractCard.classList.toggle('hidden', mode !== 'contract');
        if (kbCard) kbCard.classList.toggle('hidden', mode !== 'kb');
        // Вкладка КАД - разовый инструмент, а не разговор с LLM. Диалог и
        // история вопросов на ней бессмысленны, поэтому прячем их целиком.
        const isArbitr = mode === 'arbitr';
        if (arbitrCard) arbitrCard.classList.toggle('hidden', !isArbitr);
        if (arbitrResults) arbitrResults.classList.toggle('hidden', !isArbitr);
        if (chatSection) chatSection.classList.toggle('hidden', isArbitr);
        if (historyCard) historyCard.classList.toggle('hidden', isArbitr);
        if (isArbitr) startArbitrPolling(); else stopArbitrPolling();
        document.querySelectorAll('.lawyer-mode-btn').forEach(btn => {
            const isActive = btn.getAttribute('data-tab') === mode;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
        updateChatAvailability();
        const guidanceText = guidance?.querySelector('.guidance-text');
        if (guidanceText) guidanceText.textContent = mode === 'contract'
            ? '1. Загрузите договор. 2. Дождитесь обработки. 3. Запустите проверку.'
            : isArbitr
                ? 'Укажите ИНН и запустите поиск. Результат сохранится, даже если закрыть страницу.'
                : 'Задайте вопрос по загруженным внутренним нормативным документам.';
        const emptyState = document.getElementById('chat-empty-state');
        if (emptyState) {
            const title = emptyState.querySelector('strong');
            const description = emptyState.querySelector('span');
            if (title) title.textContent = mode === 'contract'
                ? 'Результат проверки появится здесь'
                : 'Ответ появится здесь';
            if (description) description.textContent = mode === 'contract'
                ? 'Загрузите договор и запустите проверку.'
                : 'Загрузите внутренние нормативные документы, затем задайте вопрос.';
        }
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

    clearIndexButton?.addEventListener('click', async () => {
        if (!(await App.confirm(
            'Удалить все загруженные внутренние нормативные документы? Отменить это действие нельзя.',
            { danger: true, okLabel: 'Удалить все', cancelLabel: 'Отмена' },
        ))) return;
        try {
            const resp = await fetch('/lawyer/index', { method: 'DELETE' });
            if (!resp.ok) {
                App.setStatus('upload-status', 'Не удалось удалить документы', 'error');
                return;
            }
            App.setStatus('upload-status', '✓ Все документы удалены', 'ok');
            await refreshFiles();
        } catch (e) {
            App.setStatus('upload-status', 'Ошибка при удалении документов', 'error');
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
            const hasFiles = Boolean(data.files?.length);
            if (clearIndexButton) clearIndexButton.disabled = !hasFiles;
            if (!hasFiles) {
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
                    const isCheck = (h.mode || 'contract') === 'contract'
                        || safeText(h.query) === CHECK_QUESTION;
                    const savedLabel = safeText(h.label);
                    const q = isCheck
                        ? (savedLabel && savedLabel !== CHECK_QUESTION ? savedLabel : CHECK_HISTORY_LABEL)
                        : safeText(h.query);
                    return `<li><time>${h.timestamp}</time><button type="button" class="history-query module-history-link" data-history-index="${index}">${escapeHtml(App.shortSummary(q))}</button></li>`;
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
                const isCheck = (h.mode || 'contract') === 'contract'
                    || safeText(h.query) === CHECK_QUESTION;
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

// ── Вкладка «Арбитражные дела» ──────────────────────────────────
    // Здесь только постановка задания и просмотр результата. Сам обход КАД
    // выполняет локальный агент сотрудника, поэтому вкладке важно различать
    // «ждём машину», «машина работает» и «выгрузка готова».
    const ARBITR_PAGE = 50;
    let arbitrTimer = null;
    let arbitrRecordsJob = null;

    function stopArbitrPolling() {
        if (arbitrTimer) {
            clearInterval(arbitrTimer);
            arbitrTimer = null;
        }
    }

    async function refreshArbitrStatus() {
        if (document.hidden) return;
        try {
            const resp = await fetch('/lawyer/arbitr/status');
            if (!resp.ok) return;
            const data = await resp.json();
            renderArbitrMachines(data);
            renderArbitrJobs(data.jobs || []);
        } catch (e) {
            console.error('refreshArbitrStatus', e);
        }
    }

    function startArbitrPolling() {
        if (arbitrTimer) return;
        refreshArbitrStatus();
        // Пять секунд: состояние «идёт сбор» меняется медленно, но пользователь
        // должен увидеть его появление без ручного обновления страницы.
        arbitrTimer = setInterval(refreshArbitrStatus, 5000);
    }

    function arbitrTime(ts) {
        if (!ts) return '';
        const d = new Date(ts * 1000);
        if (Number.isNaN(d.getTime())) return '';
        return d.toLocaleString('ru-RU', {
            day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
        });
    }

    function renderArbitrMachines(data) {
        const list = document.getElementById('arbitr-machines');
        if (!list) return;
        // Сервер отдаёт только живые машины: молчавшие из списка убраны.
        const rows = (data.agents || []).map(a => {
            const owner = a.owner ? ' · ' + escapeHtml(a.owner) : '';
            return `<li>${escapeHtml(a.name)}${owner} — ${a.busy ? 'выполняет другое задание' : 'готов к сбору'}</li>`;
        });
        if (!rows.length) {
            rows.push('<li class="muted">Сборщик сейчас недоступен. Задание подождёт в очереди и запустится автоматически.</li>');
        }
        list.innerHTML = rows.join('');
    }

    function arbitrJobCard(job) {
        const meta = [];
        if (job.requested_by) meta.push('заказал ' + job.requested_by);
        if (job.agent) meta.push('машина ' + job.agent);
        const when = arbitrTime(job.started_at || job.created_at);
        if (when) meta.push(when);

        const facts = [];
        if (job.enrich) facts.push('с карточками дел');
        if (job.limit) facts.push('лимит ' + job.limit);
        if (job.records_count) facts.push('записей: ' + job.records_count);
        if (job.pages_visited) facts.push('страниц: ' + job.pages_visited);
        if (job.captcha_hits) facts.push('капч: ' + job.captcha_hits);
        if (job.elapsed) facts.push('время: ' + job.elapsed + ' с');
        if (job.finish_label) facts.push(job.finish_label);

        const warn = (job.warnings || [])
            .map(w => `<div class="arbitr-warn">${escapeHtml(w)}</div>`).join('');
        const err = job.error ? `<div class="arbitr-error">${escapeHtml(job.error)}</div>` : '';

        const actions = ['<button type="button" class="btn btn-sm" data-action="records">Записи</button>'];
        if (job.has_file) {
            actions.push(
                `<a class="btn btn-sm btn-secondary" href="/lawyer/arbitr/jobs/`
                + `${encodeURIComponent(job.id)}/download">Скачать XLSX</a>`
            );
        }
        actions.push('<button type="button" class="btn btn-sm btn-danger" data-action="delete">Удалить</button>');

        const head = `<div class="arbitr-job-head"><strong>ИНН ${escapeHtml(job.inn)}</strong>`
            + `<span class="arbitr-badge" data-state="${escapeHtml(job.state)}">`
            + `${escapeHtml(job.state_label)}</span></div>`;
        const lines = (meta.length ? `<div class="arbitr-job-meta">${meta.map(escapeHtml).join(' · ')}</div>` : '')
            + (facts.length ? `<div class="arbitr-job-meta">${facts.map(escapeHtml).join(' · ')}</div>` : '');

        return `<div class="arbitr-job" data-job-id="${escapeHtml(job.id)}">`
            + head + lines + err + warn
            + `<div class="arbitr-job-actions">${actions.join('')}</div>`
            + '</div>';
    }

    function renderArbitrJobs(jobs) {
        const box = document.getElementById('arbitr-jobs');
        if (!box) return;
        if (!jobs.length) {
            box.innerHTML = '<div class="module-empty-state"><strong>Заданий пока нет</strong>'
                + '<span>Укажите ИНН и нажмите «Найти арбитражные дела».</span></div>';
            return;
        }
        box.innerHTML = jobs.map(arbitrJobCard).join('');
    }

    function renderArbitrRecords(data, box) {
        // Эти технические поля не нужны в экранной таблице: номер дела по
        // инстанции дублирует основное дело, а адреса участников перегружают
        // просмотр. В полной XLSX-выгрузке данные сохраняются.
        const hiddenColumns = new Set([
            'Номер дела по инстанции',
            'Участники (адреса)',
        ]);
        const columns = (data.columns || []).filter(column => !hiddenColumns.has(column));
        const records = data.records || [];
        if (!columns.length) {
            box.innerHTML = '<div class="module-empty-state"><strong>Записей нет</strong>'
                + '<span>Агент не прислал данных по этому заданию.</span></div>';
            box.classList.remove('hidden');
            return;
        }
        const head = columns.map(c => `<th>${escapeHtml(c)}</th>`).join('');
        const rows = records.map(r => '<tr>' + columns.map(c => {
            const v = r[c];
            return `<td>${escapeHtml(Array.isArray(v) ? v.join(', ') : String(v ?? ''))}</td>`;
        }).join('') + '</tr>').join('');

        const offset = data.offset || 0;
        const nav = [];
        if (offset > 0) {
            nav.push(`<button type="button" class="btn btn-sm" data-offset="${offset - ARBITR_PAGE}">← Назад</button>`);
        }
        nav.push(`<span class="muted">с ${offset + 1} по ${offset + records.length}</span>`);
        if (data.has_more) {
            nav.push(`<button type="button" class="btn btn-sm" data-offset="${offset + ARBITR_PAGE}">Вперёд →</button>`);
        }

        const total = data.total ?? null;
        const totalLabel = total === null ? '' : ` из ${total}`;
        box.innerHTML = `<div class="arbitr-records-head"><strong>Найденные дела</strong><span class="muted">Показаны записи ${offset + 1}–${offset + records.length}${totalLabel}</span>`
            + '<button type="button" class="btn btn-sm" data-close-records="1">Свернуть</button></div>'
            + `<div class="economist-table-wrap"><table class="economist-table"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`
            + `<div class="arbitr-records-nav">${nav.join('')}</div>`;
        box.classList.remove('hidden');
    }

    async function loadArbitrRecords(jobId, offset) {
        const box = document.getElementById('arbitr-records');
        if (!box) return;
        try {
            const resp = await fetch(
                `/lawyer/arbitr/jobs/${encodeURIComponent(jobId)}/records?offset=${offset}`
            );
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                box.innerHTML = `<div class="arbitr-error">${
                    escapeHtml(safeText(err.detail) || 'Не удалось загрузить записи')}</div>`;
                box.classList.remove('hidden');
                return;
            }
            arbitrRecordsJob = jobId;
            renderArbitrRecords(await resp.json(), box);
        } catch (e) {
            console.error('loadArbitrRecords', e);
        }
    }

    document.getElementById('arbitr-body')?.addEventListener('click', async e => {
        const pageBtn = e.target.closest('[data-offset]');
        if (pageBtn && arbitrRecordsJob) {
            loadArbitrRecords(arbitrRecordsJob, Number(pageBtn.dataset.offset) || 0);
            return;
        }
        if (e.target.closest('[data-close-records]')) {
            arbitrRecordsJob = null;
            document.getElementById('arbitr-records')?.classList.add('hidden');
            return;
        }
        const card = e.target.closest('.arbitr-job');
        const action = e.target.closest('[data-action]')?.dataset.action;
        if (!card || !action) return;
        const jobId = card.dataset.jobId;

        if (action === 'records') {
            loadArbitrRecords(jobId, 0);
            return;
        }
        if (action === 'delete') {
            if (!(await App.confirm('Удалить задание вместе с выгрузкой?', { danger: true }))) return;
            try {
                const resp = await fetch(`/lawyer/arbitr/jobs/${encodeURIComponent(jobId)}`, {
                    method: 'DELETE',
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    App.setStatus('arbitr-status', safeText(err.detail) || 'Не удалось удалить', 'error');
                    return;
                }
                if (arbitrRecordsJob === jobId) {
                    arbitrRecordsJob = null;
                    document.getElementById('arbitr-records')?.classList.add('hidden');
                }
                App.setStatus('arbitr-status', '✓ Задание удалено', 'ok');
                await refreshArbitrStatus();
            } catch (err) {
                App.setStatus('arbitr-status', 'Ошибка при удалении', 'error');
            }
        }
    });

    document.getElementById('arbitr-run')?.addEventListener('click', async () => {
        const innField = document.getElementById('arbitr-inn');
        const limitField = document.getElementById('arbitr-limit');
        const inn = (innField?.value || '').replace(/\D/g, '');
        if (inn.length < 10 || inn.length > 12) {
            App.setStatus(
                'arbitr-status',
                'Укажите ИНН: 10 цифр у организации или 12 у физического лица.',
                'error'
            );
            return;
        }
        // Полный сбор подробностей включён всегда: переключателя в UI нет.
        const payload = { inn: inn, enrich: true };
        const limit = (limitField?.value || '').trim();
        if (limit) payload.limit = Number(limit);

        const button = document.getElementById('arbitr-run');
        button.disabled = true;
        App.setStatus('arbitr-status', 'Ставлю задание в очередь…', 'loading');
        try {
            const resp = await fetch('/lawyer/arbitr/jobs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                App.setStatus(
                    'arbitr-status',
                    safeText(data.detail) || 'Не удалось поставить задание',
                    'error'
                );
                return;
            }
            const online = data.agents_online || 0;
            App.setStatus(
                'arbitr-status',
                online
                    ? `✓ Задание принято. Доступных сборщиков: ${online}. Обычно сбор занимает от 2 до 15 минут.`
                    : '✓ Задание принято. Сбор начнётся автоматически, когда будет доступен сборщик. Страницу можно закрыть.',
                'ok'
            );
            await refreshArbitrStatus();
        } catch (e) {
            App.setStatus('arbitr-status', 'Ошибка сети', 'error');
        } finally {
            button.disabled = false;
        }
    });

    document.getElementById('arbitr-refresh')?.addEventListener('click', async () => {
        const button = document.getElementById('arbitr-refresh');
        button.disabled = true;
        try { await refreshArbitrStatus(); }
        finally { button.disabled = false; }
    });

    refreshContractStatus();
    refreshFiles();
    loadChatHistory();
    bindHistoryLinks();
    setMode('contract');
});
