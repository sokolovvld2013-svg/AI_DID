/** Модуль Секретарь — фронтенд (транскрибация и обезличивание) */

document.addEventListener('DOMContentLoaded', () => {

    const output = document.getElementById('result-output');
    const resultTitle = document.getElementById('result-title');
    const resultActions = document.getElementById('result-actions');
    let currentResult = '';
    let currentFilename = '';

    const STORAGE_KEY = 'secretary_last_protocol_v2';
    const STORAGE_FILE_KEY = 'secretary_last_filename_v2';
    const LEGACY_STORAGE_KEYS = ['secretary_last_protocol', 'secretary_last_filename'];

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

        if (isTranscription) {
            try {
                sessionStorage.setItem(STORAGE_KEY, result);
                if (filename) sessionStorage.setItem(STORAGE_FILE_KEY, filename);
            } catch (_) {}
        }
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
        if (audioMeta) {
            const sizeMb = (file.size / (1024 * 1024)).toFixed(file.size >= 10 * 1024 * 1024 ? 1 : 2);
            const extension = file.name.includes('.') ? file.name.split('.').pop().toUpperCase() : 'Аудио';
            audioMeta.innerHTML = `<strong>${escapeHtml(file.name)}</strong><br>Формат: ${escapeHtml(extension)} · Размер: ${sizeMb} МБ`;
            audioMeta.classList.remove('hidden');
        }

        requestPending = true;
        App.setFileProcessing({
            statusId: 'upload-status',
            progressId: 'upload-progress',
            zoneId: 'audio-drop',
            active: true,
            message: 'Транскрибация и формирование протокола… Не закрывайте страницу (5–15 мин).',
        });

        if (output) output.innerHTML = '<p class="muted">Обработка...</p>';

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
            if (output) {
                output.innerHTML = '';
                const error = document.createElement('p');
                error.className = 'status error';
                error.textContent = msg;
                output.appendChild(error);
            }
            App.setStatus('upload-status', msg, 'error', { zoneId: 'audio-drop' });
            await loadLatestProtocol();
        } finally {
            requestPending = false;
            App.setFileProcessing({
                statusId: 'upload-status',
                progressId: 'upload-progress',
                zoneId: 'audio-drop',
                active: false,
                message: '',
            });
        }
    }

    const TYPE_LABELS = {
        PERSON: 'ФИО',
        ORG: 'Организация',
        INN: 'ИНН',
        KPP: 'КПП',
        OGRN: 'ОГРН / ОГРНИП',
        OKPO: 'ОКПО',
        SNILS: 'СНИЛС',
        BIK: 'БИК',
        ACCOUNT: 'Расчётный счёт',
        PASSPORT: 'Паспорт',
        PERSNUM: 'Персональный номер',
        BIRTHDATE: 'Дата рождения',
        ADDRESS: 'Адрес',
        EMAIL: 'Email',
        PHONE: 'Телефон',
    };

    const TYPE_ORDER = [
        'PERSON', 'ORG', 'INN', 'KPP', 'OGRN', 'OKPO', 'SNILS', 'BIK',
        'ACCOUNT', 'PASSPORT', 'PERSNUM', 'BIRTHDATE', 'ADDRESS', 'EMAIL', 'PHONE',
    ];

    // Режим обезличивания: 'local' — файл не покидает браузер,
    // 'server' — старая отправка на /api/anonymize.
    let anonymizeMode = 'local';
    const ANON_MODE_KEY = 'secretary_anonymize_mode';

    function currentCompanyName() {
        const el = document.querySelector('.header [data-company-name]');
        return (el?.textContent || '').trim();
    }

    function anonymizePlaceholderHtml() {
        const company = currentCompanyName() || 'наименование организации';
        const where = anonymizeMode === 'local'
            ? 'Файл обрабатывается прямо в браузере и не отправляется на сервер.'
            : 'Файл отправляется на сервер и обрабатывается локально на VPS.';
        return `<p class="muted">Загрузите документ для обезличивания. ${where}</p>`
            + '<div class="anon-scope">'
            + '<h4>Какие сущности обезличиваются</h4>'
            + '<ul>'
            + '<li><span class="anon-scope-type">ФИО</span> — заменяются на [PERSON_1] и т.д.</li>'
            + `<li><span class="anon-scope-type">Организации</span> — заменяются на [ORG_1] и т.д. (юрлица: ООО/АО/ПАО/ФГБУ — как в кавычках, так и без них, а также полные формы ОПФ; ${escapeHtml(company)}, Росимущество, контрагент по договору)</li>`
            + '<li><span class="anon-scope-type">Реквизиты</span> — ИНН: [INN_1] и т.д.</li>'
            + '<li><span class="anon-scope-type">Прочее</span> — email: [EMAIL_1], телефон: [PHONE_1], паспорт: [PASSPORT_1] (только цифры, подпись поля остаётся), персональный номер: [PERSNUM_1] (только цифры)</li>'
            + '</ul>'
            + '</div>';
    }

    function applyAnonymizeMode(mode) {
        anonymizeMode = mode === 'server' ? 'server' : 'local';
        try { localStorage.setItem(ANON_MODE_KEY, anonymizeMode); } catch (_) {}

        document.querySelectorAll('[data-anon-mode]').forEach(btn => {
            const active = btn.dataset.anonMode === anonymizeMode;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-pressed', String(active));
        });

        const input = document.getElementById('doc-file');
        if (input) {
            input.accept = anonymizeMode === 'local' ? '.docx,.txt,.md' : '.docx,.doc';
        }
        const hint = document.getElementById('doc-formats-hint');
        if (hint) {
            hint.textContent = anonymizeMode === 'local'
                ? '.docx, .txt, .md (до 50 МБ)'
                : '.docx, .doc (до 50 МБ)';
        }
        const note = document.getElementById('doc-mode-note');
        if (note) {
            note.textContent = anonymizeMode === 'local'
                ? 'Файл не покидает ваш компьютер: обработка идёт в браузере.'
                : 'Файл отправляется на сервер. Используйте, если браузер не поддерживает разбор DOCX.';
        }
        if (mode === 'anonymize' && output) output.innerHTML = anonymizePlaceholderHtml();
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
        const types = TYPE_ORDER.filter(t => (byType[t] || 0) > 0);

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
                return;
            }
        } catch (_) {}
        showResult(`Файл сохранён: ${fname}\nНайдено сущностей: ${count}\nПо типам: ${JSON.stringify(byType)}`, fname);
    }

    function downloadBlob(blob, fname) {
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = fname;
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }

    function maskedName(name) {
        return name.replace(/(\.[^.\\/]+)$/, '_obezlichennyi$1');
    }

    async function anonymizeTextFile(file) {
        const text = new TextDecoder('utf-8').decode(await file.arrayBuffer());
        const engine = window.AnonymizeCore.createEngine({
            companyName: currentCompanyName(),
            serverParity: true,
        });
        const masked = engine.applySpans(text, engine.assign(engine.analyze(text)));
        return {
            blob: new Blob([masked], { type: 'text/plain;charset=utf-8' }),
            report: engine.report(),
        };
    }

    async function anonymizeDocxFile(file) {
        const buffer = await file.arrayBuffer();
        const engine = window.AnonymizeCore.createEngine({
            companyName: currentCompanyName(),
            serverParity: true,
        });
        return window.AnonymizeDocx.anonymizeDocx(buffer, engine);
    }

    async function processDocumentLocal(file) {
        const name = file.name.toLowerCase();
        if (name.endsWith('.txt') || name.endsWith('.md')) {
            return anonymizeTextFile(file);
        }
        if (name.endsWith('.docx')) {
            return anonymizeDocxFile(file);
        }
        if (name.endsWith('.doc')) {
            throw new Error('Формат .doc (старый Word) поддерживается только серверным режимом. '
                + 'Сохраните документ как .docx или переключитесь на серверный режим.');
        }
        throw new Error('Поддерживаются файлы .docx, .txt и .md.');
    }

    async function processDocument(file) {
        setAnonymizeDocLabel(file.name);

        const local = anonymizeMode === 'local';
        requestPending = true;
        App.setFileProcessing({
            statusId: 'anonymize-status',
            progressId: 'anonymize-progress',
            zoneId: 'doc-drop',
            active: true,
            message: local ? 'Обезличивание в браузере…' : 'Обезличивание документа…',
        });

        if (output) output.innerHTML = '<p class="muted">Обработка...</p>';

        try {
            if (local) {
                const { blob, report } = await processDocumentLocal(file);
                const fname = maskedName(file.name);
                downloadBlob(blob, fname);
                if (output) output.innerHTML = renderAnonymizeReport(report, fname);
                const lines = Object.entries(report.mapping || {})
                    .map(([ph, orig]) => `${ph} ← ${orig}`);
                currentResult = `Файл: ${fname}\nОбезличено сущностей: ${report.entities_found ?? 0}\n`
                    + lines.join('\n');
                currentFilename = fname;
                resultActions?.classList.remove('hidden');
                output.scrollTop = 0;
            } else {
                await processDocumentOnServer(file);
            }

            App.setStatus('anonymize-status', 'Готово', 'ok', { zoneId: 'doc-drop' });
        } catch (e) {
            const msg = e.name === 'AbortError'
                ? 'Запрос прерван.'
                : (e.message || String(e));
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

    async function processDocumentOnServer(file) {
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

            downloadBlob(blob, fname);

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
    }

    App.setupDropZone('audio-drop', 'audio-file', processAudio);
    App.setupDropZone('doc-drop', 'doc-file', processDocument);

    let savedAnonymizeMode = 'local';
    try { savedAnonymizeMode = localStorage.getItem(ANON_MODE_KEY) || 'local'; } catch (_) {}
    applyAnonymizeMode(savedAnonymizeMode);
    document.querySelectorAll('[data-anon-mode]').forEach(btn => {
        btn.addEventListener('click', () => applyAnonymizeMode(btn.dataset.anonMode));
    });

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
            try {
                sessionStorage.removeItem(STORAGE_KEY);
                sessionStorage.removeItem(STORAGE_FILE_KEY);
            } catch (_) {}
        }
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

    try {
        LEGACY_STORAGE_KEYS.forEach(key => sessionStorage.removeItem(key));
    } catch (_) {}

    refreshHistory().then(loadLatestProtocol);
    setMode('transcription');
});
