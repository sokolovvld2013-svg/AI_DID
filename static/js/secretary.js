/** Модуль Секретарь — фронтенд */

document.addEventListener('DOMContentLoaded', () => {

    const output = document.getElementById('protocol-output');

    const STORAGE_KEY = 'secretary_last_protocol';

    const STORAGE_FILE_KEY = 'secretary_last_filename';



    function showProtocol(protocol, filename) {

        if (!output) return;

        if (!protocol || !String(protocol).trim()) {

            output.innerHTML = '<p class="muted">Протокол пуст</p>';

            return;

        }

        output.innerHTML = App.formatMarkdownSimple(protocol);

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

        App.showProgress('upload-progress', true);

        App.setStatus(

            'upload-status',

            'Транскрибация и формирование протокола... Не закрывайте страницу (5–15 мин).',

        );

        if (output) output.innerHTML = '<p class="muted">Обработка...</p>';



        try {

            const data = await App.uploadFile('/secretary/upload', file);

            if (!data.protocol?.trim()) {

                throw new Error('Сервер вернул пустой протокол');

            }

            showProtocol(data.protocol, data.filename);

            App.setStatus('upload-status', 'Готово', 'ok');

            await refreshHistory();

        } catch (e) {

            const msg = e.name === 'AbortError'

                ? 'Запрос прерван. Откройте «Секретарь» снова — протокол может быть в истории слева.'

                : e.message;

            if (output) output.innerHTML = `<p class="status error">${msg}</p>`;

            App.setStatus('upload-status', msg, 'error');

            await loadLatestProtocol();

        } finally {

            App.showProgress('upload-progress', false);

        }

    }



    App.setupDropZone('audio-drop', 'audio-file', processAudio);



    document.getElementById('history-list')?.addEventListener('click', async e => {

        const link = e.target.closest('.history-link');

        if (!link) return;

        e.preventDefault();

        const fileId = link.dataset.fileId;

        if (!fileId) return;

        App.setStatus('upload-status', 'Загрузка протокола...');

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


