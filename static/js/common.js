/** Общие утилиты для фронтенда */
function stripSiteUrls(text) {
    if (!text) return '';
    const original = String(text);
    const stripped = original
        .replace(/\[([^\]]*)\]\(https?:\/\/[^)]+\)/gi, '$1')
        .replace(/https?:\/\/[^\s<>"']+/gi, '')
        .replace(/www\.[^\s<>"']+/gi, '')
        .replace(/localhost(?::\d+)?(?:\/[^\s<>"']*)?/gi, '')
        .replace(/\s{2,}/g, ' ')
        .trim();
    return stripped || original;
}

let _confirmUi = null;

function _ensureConfirmUi() {
    if (_confirmUi) return _confirmUi;

    const overlay = document.createElement('div');
    overlay.id = 'app-confirm';
    overlay.className = 'confirm-overlay hidden';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.innerHTML = `
        <div class="confirm-dialog">
            <p class="confirm-message"></p>
            <div class="confirm-actions">
                <button type="button" class="btn btn-secondary confirm-cancel">Нет</button>
                <button type="button" class="btn btn-primary confirm-ok">Да</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);

    const messageEl = overlay.querySelector('.confirm-message');
    const btnOk = overlay.querySelector('.confirm-ok');
    const btnCancel = overlay.querySelector('.confirm-cancel');

    _confirmUi = { overlay, messageEl, btnOk, btnCancel };
    return _confirmUi;
}

const App = {
    /** Свой диалог подтверждения (без «localhost» в заголовке браузера). */
    confirm(message, options = {}) {
        const ui = _ensureConfirmUi();
        const okLabel = options.okLabel || 'Да';
        const cancelLabel = options.cancelLabel || 'Нет';
        const danger = Boolean(options.danger);

        return new Promise(resolve => {
            let done = false;
            const onKey = e => {
                if (e.key === 'Escape') finish(false);
            };
            const finish = value => {
                if (done) return;
                done = true;
                document.removeEventListener('keydown', onKey);
                ui.overlay.classList.add('hidden');
                document.body.classList.remove('confirm-open');
                resolve(value);
            };

            ui.messageEl.textContent = stripSiteUrls(message);
            ui.btnOk.textContent = okLabel;
            ui.btnCancel.textContent = cancelLabel;
            ui.btnOk.className = danger
                ? 'btn btn-danger confirm-ok'
                : 'btn btn-primary confirm-ok';

            ui.btnOk.onclick = () => finish(true);
            ui.btnCancel.onclick = () => finish(false);
            ui.overlay.onclick = e => {
                if (e.target === ui.overlay) finish(false);
            };
            document.addEventListener('keydown', onKey);

            ui.overlay.classList.remove('hidden');
            document.body.classList.add('confirm-open');
            ui.btnCancel.focus();
        });
    },

    showProgress(id, show = true) {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.toggle('hidden', !show);
        el.classList.toggle('active', show);
    },

    setStatus(id, text, type = '', options = {}) {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = stripSiteUrls(text);
        el.className = type ? `status ${type}` : 'status';
        el.dataset.status = type || 'idle';

        const zoneId = options.zoneId;
        if (zoneId) {
            const zone = document.getElementById(zoneId);
            if (zone) zone.classList.toggle('is-processing', type === 'loading');
        }
    },

    /** Подсветка зоны загрузки и статуса во время обработки файла на сервере. */
    setFileProcessing({ statusId, progressId, zoneId, active, message }) {
        this.showProgress(progressId, active);
        if (active) {
            this.setStatus(statusId, message, 'loading', { zoneId });
        } else if (zoneId) {
            const zone = document.getElementById(zoneId);
            if (zone) zone.classList.remove('is-processing');
        }
    },

    addMessage(containerId, text, role = 'bot') {
        const container = document.getElementById(containerId);
        if (!container) return;
        const div = document.createElement('div');
        div.className = `message message-text ${role}`;
        div.textContent = text;
        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
    },

    formatMarkdownSimple(text) {
        if (!text || !String(text).trim()) {
            return '<p class="muted">Протокол пуст</p>';
        }
        let html = String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        html = html.replace(/(?:^\|.+\|\s*$\n?)+/gm, block => {
            const rows = block.trim().split('\n').filter(r => r.trim());
            if (rows.length < 2) return block.replace(/\n/g, '<br>');
            const isSep = row => /^\|[\s\-:|]+\|$/.test(row.trim());
            const bodyRows = rows.filter(r => !isSep(r));
            const cells = row => row.split('|').slice(1, -1).map(c => c.trim());
            const trs = bodyRows.map((row, i) => {
                const tag = i === 0 ? 'th' : 'td';
                return `<tr>${cells(row).map(c => `<${tag}>${c}</${tag}>`).join('')}</tr>`;
            }).join('');
            return `<table>${trs}</table>`;
        });

        return html
            .replace(/^## (.+)$/gm, '<h3>$1</h3>')
            .replace(/^### (.+)$/gm, '<h4>$1</h4>')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\n/g, '<br>');
    },

  setupDropZone(zoneId, inputId, onFile) {
        const zone = document.getElementById(zoneId);
        const input = document.getElementById(inputId);
        if (!zone || !input) return;

        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(evt => {
            zone.addEventListener(evt, e => { e.preventDefault(); e.stopPropagation(); });
        });
        zone.addEventListener('dragover', () => zone.classList.add('dragover'));
        zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
        zone.addEventListener('drop', e => {
            zone.classList.remove('dragover');
            if (e.dataTransfer.files.length) onFile(e.dataTransfer.files[0]);
        });
        input.addEventListener('change', () => {
            if (input.files.length) onFile(input.files[0]);
        });
    },

    async uploadFile(endpoint, file, onProgress) {
        const form = new FormData();
        form.append('file', file);
        const resp = await fetch(endpoint, { method: 'POST', body: form });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            const detail = err.detail;
            const msg = Array.isArray(detail)
                ? detail.map(d => d.msg || String(d)).join('; ')
                : (detail || resp.statusText);
            throw new Error(stripSiteUrls(msg));
        }
        return resp.json();
    },
};
