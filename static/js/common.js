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
let _processingBanner = null;

function _ensureProcessingBanner() {
    if (_processingBanner) return _processingBanner;

    const el = document.createElement('div');
    el.id = 'app-file-processing-banner';
    el.className = 'file-processing-banner hidden';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.innerHTML =
        '<span class="file-processing-banner__spinner" aria-hidden="true"></span>' +
        '<span class="file-processing-banner__text"></span>';

    const main = document.querySelector('.main');
    if (main) {
        main.insertBefore(el, main.firstChild);
    } else {
        document.body.appendChild(el);
    }

    _processingBanner = {
        el,
        textEl: el.querySelector('.file-processing-banner__text'),
    };
    return _processingBanner;
}

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
        const zone = zoneId ? document.getElementById(zoneId) : null;
        const card = zone?.closest('.card') || null;
        const banner = _ensureProcessingBanner();

        if (active) {
            this.setStatus(statusId, message, 'loading', { zoneId });
            if (card) card.classList.add('is-file-processing');
            document.body.classList.add('app-file-processing');

            banner.textEl.textContent = stripSiteUrls(message) || 'Идёт обработка файла…';
            banner.el.classList.remove('hidden');

            const statusEl = statusId ? document.getElementById(statusId) : null;
            statusEl?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } else {
            if (zone) zone.classList.remove('is-processing');
            if (card) card.classList.remove('is-file-processing');
            document.body.classList.remove('app-file-processing');
            banner.el.classList.add('hidden');
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

    _escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    },

    _normalizeListBreaks(text) {
        return String(text)
            .replace(/([:;—–])\s*([-*•]\s+\S)/g, '$1\n$2')
            .replace(/(\*\*[^*]+\*\*)\s*([-*•]\s+\S)/g, '$1\n$2')
            .replace(/([^\n])\s+([-*•]\s+\S)/g, '$1\n$2')
            .replace(/([:;—–])\s*(\d+\.\s+\S)/g, '$1\n$2')
            .replace(/(\*\*[^*]+\*\*)\s*(\d+\.\s+\S)/g, '$1\n$2')
            .replace(/([^\n])\s+(\d+\.\s+\S)/g, '$1\n$2')
            .replace(/([^\n])\s*(🔴\s)/g, '$1\n$2')
            .replace(/([^\n])\s*(🟡\s)/g, '$1\n$2');
    },

    _cleanMarkdownArtifacts(text) {
        return String(text)
            .replace(/^\s*[-*_]{3,}\s*$/gm, '')
            .replace(/^\s*[-*_]{3,}\s+/gm, '')
            .replace(/^\s*#{1,6}\s*$/gm, '')
            .replace(/[-*_]{3,}\s*#{1,6}\s+/g, '')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    },

    _peekNextNonemptyLine(lines, startIndex) {
        for (let i = startIndex; i < lines.length; i++) {
            const trimmed = lines[i].trim();
            if (trimmed) return trimmed;
        }
        return null;
    },

    _isNumberedListLine(trimmed) {
        return /^\d+\.\s+/.test(trimmed);
    },

    _isBulletListLine(trimmed) {
        return /^[-*•]\s+/.test(trimmed);
    },

    _linesToHtml(text) {
        const lines = String(text).split('\n');
        const blocks = [];
        let paraLines = [];
        let listItems = [];
        let listType = null;

        const flushPara = () => {
            if (paraLines.length) {
                blocks.push(paraLines.join('<br>'));
                paraLines = [];
            }
        };

        const flushList = () => {
            if (!listItems.length) return;
            const tag = listType === 'ol' ? 'ol' : 'ul';
            blocks.push(`<${tag}>${listItems.join('')}</${tag}>`);
            listItems = [];
            listType = null;
        };

        for (let lineIndex = 0; lineIndex < lines.length; lineIndex++) {
            const trimmed = lines[lineIndex].trim();
            if (!trimmed) {
                if (listType === 'ol') {
                    const next = this._peekNextNonemptyLine(lines, lineIndex + 1);
                    if (next && this._isNumberedListLine(next)) continue;
                } else if (listType === 'ul') {
                    const next = this._peekNextNonemptyLine(lines, lineIndex + 1);
                    if (next && this._isBulletListLine(next)) continue;
                }
                flushList();
                flushPara();
                continue;
            }
            const bullet = trimmed.match(/^[-*•]\s+(.+)$/);
            const numbered = trimmed.match(/^\d+\.\s+(.+)$/);
            if (bullet) {
                flushPara();
                if (listType === 'ol') flushList();
                listType = 'ul';
                listItems.push(`<li>${bullet[1]}</li>`);
            } else if (numbered) {
                flushPara();
                if (listType === 'ul') flushList();
                listType = 'ol';
                listItems.push(`<li>${numbered[1]}</li>`);
            } else {
                flushList();
                paraLines.push(trimmed);
            }
        }
        flushList();
        flushPara();
        return blocks.join('');
    },

    _formatInlineMarkdown(html) {
        return String(html).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    },

    _formatSegmentMarkdown(text, options = {}) {
        if (!text || !String(text).trim()) return '';
        let html = this._escapeHtml(this._cleanMarkdownArtifacts(text));
        if (options.auditReport) {
            html = html
                .replace(/^(\*\*)?Общий вывод:(\*\*)?$/gm, '<h4 class="audit-summary-heading">Общий вывод</h4>')
                .replace(/^(\*\*)?Замечания:(\*\*)?$/gm, '<h4 class="audit-remarks-heading">Замечания</h4>');
        }
        html = html
            .replace(/^## (.+)$/gm, '<h3>$1</h3>')
            .replace(/^### (.+)$/gm, '<h4>$1</h4>')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        return this._linesToHtml(html);
    },

    _splitAuditSections(text) {
        const lines = String(text).split('\n');
        const segments = [];
        let current = { type: 'text', lines: [] };

        const pushCurrent = () => {
            if (current.type === 'text') {
                if (current.lines.some((l) => l.trim())) segments.push(current);
            } else {
                segments.push(current);
            }
        };

        for (const line of lines) {
            const crit = line.match(/^🔴\s*(.+)$/);
            const imp = line.match(/^🟡\s*(.+)$/);
            if (crit) {
                pushCurrent();
                current = { type: 'critical', title: crit[1], lines: [] };
                continue;
            }
            if (imp) {
                pushCurrent();
                current = { type: 'important', title: imp[1], lines: [] };
                continue;
            }
            current.lines.push(line);
        }
        pushCurrent();
        return segments;
    },

    _applyChatMarkdown(text) {
        return this._formatSegmentMarkdown(
            this._normalizeListBreaks(this._cleanMarkdownArtifacts(text)),
        );
    },

    formatChatMarkdown(text) {
        if (!text || !String(text).trim()) return '';
        return this._applyChatMarkdown(text);
    },

    formatCheckReportMarkdown(text) {
        if (!text || !String(text).trim()) return '';
        const normalized = this._normalizeListBreaks(this._cleanMarkdownArtifacts(text));
        const segments = this._splitAuditSections(normalized);
        const hasAuditBlocks = segments.some((s) => s.type !== 'text');
        if (!hasAuditBlocks) {
            return this._formatSegmentMarkdown(normalized, { auditReport: true });
        }

        return segments
            .map((seg) => {
                if (seg.type === 'text') {
                    return this._formatSegmentMarkdown(seg.lines.join('\n'), { auditReport: true });
                }
                const body = this._formatSegmentMarkdown(seg.lines.join('\n'));
                const cls =
                    seg.type === 'critical' ? 'audit-block-critical' : 'audit-block-important';
                const emoji = seg.type === 'critical' ? '🔴' : '🟡';
                const title = this._formatInlineMarkdown(this._escapeHtml(seg.title));
                const content = body || '<p class="muted">Не выявлено</p>';
                return (
                    `<div class="audit-block ${cls}">` +
                    `<div class="audit-block-title">${emoji} ${title}</div>` +
                    `<div class="audit-block-body">${content}</div>` +
                    '</div>'
                );
            })
            .join('');
    },

    formatMarkdownSimple(text) {
        if (!text || !String(text).trim()) {
            return '<p class="muted">Протокол пуст</p>';
        }
        let html = this._escapeHtml(text);

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

        html = html
            .replace(/^## (.+)$/gm, '<h3>$1</h3>')
            .replace(/^### (.+)$/gm, '<h4>$1</h4>')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        return this._linesToHtml(html);
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
