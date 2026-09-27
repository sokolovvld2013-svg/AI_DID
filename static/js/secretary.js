<<<<<<< HEAD
/** Модуль Секретарь — фронтенд */

document.addEventListener('DOMContentLoaded', () => {

    const output = document.getElementById('protocol-output');
    const audioMeta = document.getElementById('audio-meta');
    const protocolActions = document.getElementById('protocol-actions');
    let currentProtocol = '';
    let currentFilename = '';

    const STORAGE_KEY = 'secretary_last_protocol';

    const STORAGE_FILE_KEY = 'secretary_last_filename';



    function showProtocol(protocol, filename) {

        if (!output) return;

        if (!protocol || !String(protocol).trim()) {

            output.innerHTML = '<p class="muted">Протокол пуст</p>';

            return;

        }

        output.innerHTML = App.formatMarkdownSimple(protocol);
        currentProtocol = String(protocol);
        currentFilename = filename || 'Протокол совещания';
        protocolActions?.classList.remove('hidden');

        output.scrollTop = 0;

        try {

            sessionStorage.setItem(STORAGE_KEY, protocol);

            if (filename) sessionStorage.setItem(STORAGE_FILE_KEY, filename);

        } catch (_) {}

    }



    function isPlaceholder() {

        if (!output) return true;

        const text = output.textContent.trim();

        return !text

            || text.includes('Загрузите аудиофайл')

            || text === 'Обработка...';

    }



    async function loadLatestProtocol() {

        try {

            const resp = await fetch('/secretary/history');

            if (!resp.ok) return;

            const data = await resp.json();

            const latest = data.history?.[0];

            if (latest?.response?.trim()) {

                showProtocol(latest.response, latest.filename || latest.query);
                App.setStatus(
                    'upload-status',
                    `Готов протокол: ${latest.filename || latest.query || 'запись'}`,
                    'ok',
                );
                return;

            }

        } catch (_) {}



        try {

            const cached = sessionStorage.getItem(STORAGE_KEY);

            if (cached?.trim() && isPlaceholder()) {

                showProtocol(cached, sessionStorage.getItem(STORAGE_FILE_KEY));

            }

        } catch (_) {}

    }



    async function processAudio(file) {

        document.getElementById('audio-name').textContent = file.name;
=======
/** Модуль Секретарь — фронтенд (транскрибация и обезличивание) */

document.addEventListener('DOMContentLoaded', () => {

    const output = document.getElementById('result-output');
    const resultTitle = document.getElementById('result-title');
    const resultActions = document.getElementById('result-actions');
    let currentResult = '';
    let currentFilename = '';

    const STORAGE_KEY = 'secretary_last_protocol';
    const STORAGE_FILE_KEY = 'secretary_last_filename';

    let mode = 'transcription';
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

    function setMode(nextMode) {
        mode = nextMode;
        const isTranscription = mode === 'transcription';

        if (resultTitle) {
            resultTitle.textContent = isTranscription ? 'Протокол совещания' : 'Результат обезличивания';
        }

        const transcriptionCard = document.getElementById('transcription-card');
        const anonymizeCard = document.getElementById('anonymize-card');
        if (transcriptionCard) transcriptionCard.classList.toggle('hidden', !isTranscription);
        if (anonymizeCard) anonymizeCard.classList.toggle('hidden', isTranscription);

        document.querySelectorAll('.lawyer-mode-btn').forEach(btn => {
            const isActive = btn.getAttribute('data-tab') === mode;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        // Сброс UI при смене режима
        currentResult = '';
        currentFilename = '';
        resultActions?.classList.add('hidden');
        if (output) {
            output.innerHTML = isTranscription
                ? '<p class="muted">Загрузите аудиофайл для формирования протокола</p>'
                : anonymizePlaceholderHtml();
        }
        App.setStatus('upload-status', '', '');
        App.setStatus('anonymize-status', '', '');
        setAnonymizeDocLabel(null);
        requestPending = false;
    }

    document.querySelectorAll('.lawyer-mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (!requestPending) setMode(btn.getAttribute('data-tab') || 'transcription');
        });
    });

    function showResult(result, filename) {
        if (!output) return;

        if (!result || !String(result).trim()) {
            output.innerHTML = '<p class="muted">Результат пуст</p>';
            return;
        }

        const isTranscription = mode === 'transcription';
        output.innerHTML = isTranscription
            ? App.formatMarkdownSimple(result)
            : '<pre class="anonymize-result">' + escapeHtml(result) + '</pre>';

        currentResult = String(result);
        currentFilename = filename || (isTranscription ? 'Протокол совещания' : 'Обезличенный документ');
        resultActions?.classList.remove('hidden');
        output.scrollTop = 0;

        try {
            sessionStorage.setItem(STORAGE_KEY, result);
            if (filename) sessionStorage.setItem(STORAGE_FILE_KEY, filename);
        } catch (_) {}
    }

    async function loadLatestProtocol() {
        try {
            const resp = await fetch('/secretary/history');
            if (!resp.ok) return;
            const data = await resp.json();
            const latest = data.history?.[0];
            if (latest?.response?.trim()) {
                showResult(latest.response, latest.filename || latest.query);
                App.setStatus('upload-status', `Готов протокол: ${latest.filename || latest.query || 'запись'}`, 'ok');
                return;
            }
        } catch (_) {}

        try {
            const cached = sessionStorage.getItem(STORAGE_KEY);
            if (cached?.trim()) {
                showResult(cached, sessionStorage.getItem(STORAGE_FILE_KEY));
            }
        } catch (_) {}
    }

    async function processAudio(file) {
        document.getElementById('audio-name').textContent = file.name;
        const audioMeta = document.getElementById('audio-meta');
>>>>>>> fcd02a2 (1)
        if (audioMeta) {
            const sizeMb = (file.size / (1024 * 1024)).toFixed(file.size >= 10 * 1024 * 1024 ? 1 : 2);
            const extension = file.name.includes('.') ? file.name.split('.').pop().toUpperCase() : 'Аудио';
            audioMeta.innerHTML = `<strong>${escapeHtml(file.name)}</strong><br>Формат: ${escapeHtml(extension)} · Размер: ${sizeMb} МБ`;
            audioMeta.classList.remove('hidden');
        }

<<<<<<< HEAD
=======
        requestPending = true;
>>>>>>> fcd02a2 (1)
        App.setFileProcessing({
            statusId: 'upload-status',
            progressId: 'upload-progress',
            zoneId: 'audio-drop',
            active: true,
            message: 'Транскрибация и формирование протокола… Не закрывайте страницу (5–15 мин).',
        });

        if (output) output.innerHTML = '<p class="muted">Обработка...</p>';

<<<<<<< HEAD


        try {

            const data = await App.uploadFile('/secretary/upload', file);

            if (!data.protocol?.trim()) {

                throw new Error('Сервер вернул пустой протокол');

            }

            showProtocol(data.protocol, data.filename);

            App.setStatus('upload-status', 'Готово', 'ok', { zoneId: 'audio-drop' });

            await refreshHistory();

        } catch (e) {

            const msg = e.name === 'AbortError'

                ? 'Запрос прерван. Откройте «Секретарь» снова — протокол может быть в истории слева.'

                : e.message;

=======
        try {
            const data = await App.uploadFile('/secretary/upload', file);
            if (!data.protocol?.trim()) throw new Error('Сервер вернул пустой протокол');
            showResult(data.protocol, data.filename);
            App.setStatus('upload-status', 'Готово', 'ok', { zoneId: 'audio-drop' });
            await refreshHistory();
        } catch (e) {
            const msg = e.name === 'AbortError'
                ? 'Запрос прерван. Откройте «Секретарь» снова — протокол может быть в истории слева.'
                : e.message;
>>>>>>> fcd02a2 (1)
            if (output) {
                output.innerHTML = '';
                const error = document.createElement('p');
                error.className = 'status error';
                error.textContent = msg;
                output.appendChild(error);
            }
<<<<<<< HEAD

            App.setStatus('upload-status', msg, 'error', { zoneId: 'audio-drop' });

            await loadLatestProtocol();

        } finally {
=======
            App.setStatus('upload-status', msg, 'error', { zoneId: 'audio-drop' });
            await loadLatestProtocol();
        } finally {
            requestPending = false;
>>>>>>> fcd02a2 (1)
            App.setFileProcessing({
                statusId: 'upload-status',
                progressId: 'upload-progress',
                zoneId: 'audio-drop',
                active: false,
                message: '',
            });
        }
<<<<<<< HEAD

    }



    App.setupDropZone('audio-drop', 'audio-file', processAudio);



    document.getElementById('history-list')?.addEventListener('click', async e => {

        const link = e.target.closest('.history-link');

        if (!link) return;

        e.preventDefault();

        const fileId = link.dataset.fileId;

        if (!fileId) return;

        App.setStatus('upload-status', 'Загрузка протокола…', 'loading');
        document.querySelectorAll('.history-link').forEach(item => item.classList.remove('is-active'));
        link.classList.add('is-active');

        try {

            const resp = await fetch(`/secretary/protocol/${fileId}`);

            const data = await resp.json();

            if (!resp.ok) throw new Error(data.detail || 'Протокол не найден');

            showProtocol(data.protocol, data.filename);

            App.setStatus('upload-status', 'Готово', 'ok');

        } catch (err) {

            App.setStatus('upload-status', err.message, 'error');

        }

    });

    document.getElementById('copy-protocol')?.addEventListener('click', async () => {
        if (!currentProtocol) return;
        try {
            await navigator.clipboard.writeText(currentProtocol);
            App.setStatus('upload-status', 'Протокол скопирован', 'ok');
        } catch (_) {
            App.setStatus('upload-status', 'Не удалось скопировать протокол', 'error');
        }
    });

    document.getElementById('download-protocol')?.addEventListener('click', () => {
        if (!currentProtocol) return;
        const blob = new Blob([currentProtocol], { type: 'text/plain;charset=utf-8' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        const base = currentFilename.replace(/\.[^.]+$/, '').replace(/[\\/:*?"<>|]+/g, '_') || 'protocol';
        link.download = `${base}.txt`;
        link.click();
        URL.revokeObjectURL(link.href);
    });

    document.getElementById('new-protocol')?.addEventListener('click', () => {
        currentProtocol = '';
        currentFilename = '';
        protocolActions?.classList.add('hidden');
        if (output) output.innerHTML = '<p class="muted">Загрузите аудиофайл для формирования протокола</p>';
        const audioName = document.getElementById('audio-name');
        if (audioName) audioName.textContent = '';
        audioMeta?.classList.add('hidden');
        App.setStatus('upload-status', '', '');
        document.getElementById('audio-file')?.click();
    });



    async function refreshHistory() {

        const resp = await fetch('/secretary/history');

        const data = await resp.json();

        const list = document.getElementById('history-list');

        if (!list) return;

        list.innerHTML = data.history.length

            ? data.history.map(h =>

                `<li><time>${h.timestamp}</time>

                 <a href="#" class="history-link" data-file-id="${h.file_id || ''}">${escapeHtml(h.filename || h.query)}</a></li>`,

            ).join('')

            : '<li class="muted">Нет записей</li>';

    }



    function escapeHtml(s) {

        const d = document.createElement('div');

        d.textContent = s;

        return d.innerHTML;

    }



    refreshHistory().then(loadLatestProtocol);

});


=======
    }

    const TYPE_LABELS = {
        PERSON: 'ФИО',
        ORG: 'Организация',
        INN: 'ИНН',
        EMAIL: 'Email',
        PHONE: 'Телефон',
    };

    function currentCompanyName() {
        const el = document.querySelector('.header [data-company-name]');
        return (el?.textContent || '').trim();
    }

    function anonymizePlaceholderHtml() {
        const company = currentCompanyName() || 'наименование организации';
        return '<p class="muted">Загрузите документ для обезличивания. Замена чувствительной информации осуществляется локально. Данные в интернет не уходят!</p>'
            + '<div class="anon-scope">'
            + '<h4>Какие сущности обезличиваются</h4>'
            + '<ul>'
            + '<li><span class="anon-scope-type">ФИО</span> — заменяются на [PERSON_1] и т.д.</li>'
            + `<li><span class="anon-scope-type">Организации</span> — заменяются на [ORG_1] и т.д. (юрлица: ООО/АО/ПАО «...», ${escapeHtml(company)}, Росимущество, контрагент по договору)</li>`
            + '<li><span class="anon-scope-type">ИНН</span> — заменяются на [INN_1] и т.д.</li>'
            + '<li><span class="anon-scope-type">Email</span> — заменяются на [EMAIL_1] и т.д.</li>'
            + '<li><span class="anon-scope-type">Телефон</span> — заменяются на [PHONE_1] и т.д.</li>'
            + '</ul>'
            + '</div>';
    }

    function setAnonymizeDocLabel(fileName) {
        const info = document.getElementById('anonymize-doc-info');
        if (!info) return;
        info.innerHTML = fileName
            ? `<li><span>${escapeHtml(fileName)}</span></li>`
            : '<li class="muted">Документ не загружен</li>';
    }

    function renderAnonymizeReport(report, fname) {
        const safe = escapeHtml;
        let html = '<div class="anon-report">';
        html += `<p class="anon-file">Файл сохранён: <strong>${safe(fname)}</strong></p>`;
        const total = report.entities_found ?? 0;
        html += `<p class="anon-summary">Обезличено сущностей: <strong>${total}</strong></p>`;

        const byType = report.entities_by_type || {};
        const mapping = report.mapping || {};
        const order = ['PERSON', 'ORG', 'INN', 'EMAIL', 'PHONE'];
        const types = order.filter(t => (byType[t] || 0) > 0);

        if (!types.length) {
            html += '<p class="muted">Личные данные не найдены.</p>';
        } else {
            for (const t of types) {
                const label = TYPE_LABELS[t] || safe(t);
                html += `<div class="anon-type"><h4>${label} <span class="anon-type-count">(${byType[t]})</span></h4><ul>`;
                Object.keys(mapping)
                    .filter(ph => ph.includes(`[${t}_`))
                    .forEach(ph => {
                        html += `<li><span><code>${safe(ph)}</code></span><span>← ${safe(mapping[ph])}</span></li>`;
                    });
                html += '</ul></div>';
            }
        }
        html += '</div>';
        return html;
    }

    function showAnonymizeResult(fname, resp) {
        const count = resp.headers.get('X-Anonymizer-Entities') || '?';
        const byType = JSON.parse(resp.headers.get('X-Anonymizer-By-Type') || '{}');
        try {
            const reportHeader = resp.headers.get('X-Anonymizer-Report');
            if (reportHeader) {
                const report = JSON.parse(decodeURIComponent(reportHeader));
                if (output) output.innerHTML = renderAnonymizeReport(report, fname);
                const lines = Object.entries(report.mapping || {}).map(([ph, orig]) => `${ph} ← ${orig}`);
                currentResult = `Файл: ${fname}\nОбезличено сущностей: ${report.entities_found ?? count}\n` + lines.join('\n');
                currentFilename = fname;
                resultActions?.classList.remove('hidden');
                output.scrollTop = 0;
                try {
                    sessionStorage.setItem(STORAGE_KEY, currentResult);
                    if (fname) sessionStorage.setItem(STORAGE_FILE_KEY, fname);
                } catch (_) {}
                return;
            }
        } catch (_) {}
        showResult(`Файл сохранён: ${fname}\nНайдено сущностей: ${count}\nПо типам: ${JSON.stringify(byType)}`, fname);
    }

    async function processDocument(file) {
        setAnonymizeDocLabel(file.name);

        requestPending = true;
        App.setFileProcessing({
            statusId: 'anonymize-status',
            progressId: 'anonymize-progress',
            zoneId: 'doc-drop',
            active: true,
            message: 'Обезличивание документа…',
        });

        if (output) output.innerHTML = '<p class="muted">Обработка...</p>';

        try {
            // POST /api/anonymize (multipart file)
            const form = new FormData();
            form.append('file', file);
            const resp = await fetch('/api/anonymize', { method: 'POST', body: form });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                const detail = err.detail;
                const msg = Array.isArray(detail)
                    ? detail.map(d => d.msg || String(d)).join('; ')
                    : (detail || resp.statusText);
                throw new Error(stripSiteUrls(msg));
            }

            const blob = await resp.blob();
            const text = await blob.text(); // на случай JSON-ошибки
            // Если ответ — файл (docx), blob.type будет application/vnd.openxmlformats...
            if (resp.headers.get('content-type')?.includes('application/vnd.openxmlformats')) {
                // Сохраняем файл
                let fname = 'obezlichennyi.docx';
                const cd = resp.headers.get('Content-Disposition') || '';
                const m = cd.match(/filename\*=UTF-8''([^;]+)/);
                if (m) fname = decodeURIComponent(m[1]);

                const link = document.createElement('a');
                link.href = URL.createObjectURL(blob);
                link.download = fname;
                link.click();
                URL.revokeObjectURL(link.href);

                showAnonymizeResult(fname, resp);
            } else {
                // JSON ответ (ошибка или report=json)
                try {
                    const json = JSON.parse(text);
                    if (json.detail) throw new Error(json.detail);
                    showResult(JSON.stringify(json, null, 2), file.name);
                } catch {
                    showResult(text, file.name);
                }
            }

            App.setStatus('anonymize-status', 'Готово', 'ok', { zoneId: 'doc-drop' });
        } catch (e) {
            const msg = e.name === 'AbortError'
                ? 'Запрос прерван.'
                : e.message;
            if (output) {
                output.innerHTML = '';
                const error = document.createElement('p');
                error.className = 'status error';
                error.textContent = msg;
                output.appendChild(error);
            }
            App.setStatus('anonymize-status', msg, 'error', { zoneId: 'doc-drop' });
        } finally {
            requestPending = false;
            App.setFileProcessing({
                statusId: 'anonymize-status',
                progressId: 'anonymize-progress',
                zoneId: 'doc-drop',
                active: false,
                message: '',
            });
        }
    }

    App.setupDropZone('audio-drop', 'audio-file', processAudio);
    App.setupDropZone('doc-drop', 'doc-file', processDocument);

    document.getElementById('history-list')?.addEventListener('click', async e => {
        const link = e.target.closest('.history-link');
        if (!link) return;
        e.preventDefault();
        const fileId = link.dataset.fileId;
        if (!fileId) return;
        App.setStatus('upload-status', 'Загрузка протокола…', 'loading');
        document.querySelectorAll('.history-link').forEach(item => item.classList.remove('is-active'));
        link.classList.add('is-active');
        try {
            const resp = await fetch(`/secretary/protocol/${fileId}`);
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Протокол не найден');
            showResult(data.protocol, data.filename);
            App.setStatus('upload-status', 'Готово', 'ok');
        } catch (err) {
            App.setStatus('upload-status', err.message, 'error');
        }
    });

    document.getElementById('copy-result')?.addEventListener('click', async () => {
        if (!currentResult) return;
        try {
            await navigator.clipboard.writeText(currentResult);
            App.setStatus('upload-status', 'Результат скопирован', 'ok');
        } catch (_) {
            App.setStatus('upload-status', 'Не удалось скопировать', 'error');
        }
    });

    document.getElementById('download-result')?.addEventListener('click', () => {
        if (!currentResult) return;
        const isTranscription = mode === 'transcription';
        if (isTranscription) {
            const blob = new Blob([currentResult], { type: 'text/plain;charset=utf-8' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            const base = currentFilename.replace(/\.[^.]+$/, '').replace(/[\\/:*?"<>|]+/g, '_') || 'protocol';
            link.download = `${base}.txt`;
            link.click();
            URL.revokeObjectURL(link.href);
        } else {
            // Для обезличенного docx файл уже скачался при получении — просто уведомляем
            App.setStatus('upload-status', 'Файл уже скачан при обработке', 'ok');
        }
    });

    document.getElementById('new-result')?.addEventListener('click', () => {
        currentResult = '';
        currentFilename = '';
        resultActions?.classList.add('hidden');
        if (output) {
            output.innerHTML = mode === 'transcription'
                ? '<p class="muted">Загрузите аудиофайл для формирования протокола</p>'
                : anonymizePlaceholderHtml();
        }
        const audioName = document.getElementById('audio-name');
        if (audioName) audioName.textContent = '';
        setAnonymizeDocLabel(null);
        document.getElementById('audio-meta')?.classList.add('hidden');
        App.setStatus('upload-status', '', '');
        App.setStatus('anonymize-status', '', '');
        if (mode === 'transcription') {
            document.getElementById('audio-file')?.click();
        } else {
            document.getElementById('doc-file')?.click();
        }
    });

    async function refreshHistory() {
        const resp = await fetch('/secretary/history');
        const data = await resp.json();
        const list = document.getElementById('history-list');
        if (!list) return;
        list.innerHTML = data.history.length
            ? data.history.map(h =>
                `<li><time>${h.timestamp}</time>
                 <a href="#" class="history-link" data-file-id="${h.file_id || ''}">${escapeHtml(h.filename || h.query)}</a></li>`
            ).join('')
            : '<li class="muted">Нет записей</li>';
    }

    function escapeHtml(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    refreshHistory().then(loadLatestProtocol);
    setMode('transcription');
});
>>>>>>> fcd02a2 (1)
