/** Настройки приложения: модель LLM, наименование компании, логотип. */
(function () {
    'use strict';

    const overlay = document.getElementById('settingsOverlay');
    const openBtn = document.getElementById('settingsOpen');
    if (!overlay || !openBtn) return;

    const closeBtn = document.getElementById('settingsClose');
    const cancelBtn = document.getElementById('settingsCancel');
    const saveBtn = document.getElementById('settingsSave');
    const modelsBody = document.getElementById('settingsModels');
    const companyInput = document.getElementById('settingsCompany');
    const errorBox = document.getElementById('settingsError');
    const currentLabel = document.getElementById('settingsCurrent');
    const pricesAt = document.getElementById('settingsPricesAt');
    const logoPreview = document.getElementById('settingsLogoPreview');
    const logoName = document.getElementById('settingsLogoName');
    const logoMeta = document.getElementById('settingsLogoMeta');
    const logoFile = document.getElementById('settingsLogoFile');
    const logoReset = document.getElementById('settingsLogoReset');
    const logoStatus = document.getElementById('settingsLogoStatus');

    // Логи пользователей
    const logsBody = document.getElementById('userLogsBody');
    const logsModule = document.getElementById('userLogsModule');
    const logsStatus = document.getElementById('userLogsStatus');
    const logsLimit = document.getElementById('userLogsLimit');
    const logsRefresh = document.getElementById('userLogsRefresh');
    const logsDownload = document.getElementById('userLogsDownload');
    const logsError = document.getElementById('userLogsError');
    const logsRetention = document.getElementById('userLogsRetention');
    const logsPath = document.getElementById('userLogsPath');
    const logsState = { loaded: false, errorCodes: {} };

    const state = { models: [], selected: '', loaded: false, logo: null };
    let lastFocused = null;

    function escapeHtml(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function showError(message) {
        if (!errorBox) return;
        errorBox.textContent = message || '';
        errorBox.hidden = !message;
    }

    function formatSize(bytes) {
        const value = Number(bytes) || 0;
        if (value < 1024) return `${value} Б`;
        if (value < 1024 * 1024) return `${(value / 1024).toFixed(1).replace('.', ',')} КБ`;
        return `${(value / (1024 * 1024)).toFixed(1).replace('.', ',')} МБ`;
    }

    function showLogoStatus(message, isError) {
        if (!logoStatus) return;
        logoStatus.textContent = message || '';
        logoStatus.hidden = !message;
        logoStatus.classList.toggle('is-error', Boolean(isError));
    }

    function renderLogo(logo) {
        state.logo = logo || null;
        const info = state.logo || {};
        if (logoPreview) {
            logoPreview.src = info.url || '';
            logoPreview.hidden = !info.url;
        }
        if (logoName) logoName.textContent = info.name || '';
        if (logoMeta) {
            const parts = [];
            if (info.size) parts.push(formatSize(info.size));
            if (info.updated_at) parts.push(info.updated_at);
            if (info.is_default) parts.push('исходный файл');
            logoMeta.textContent = parts.join(' · ');
        }
        if (logoReset) logoReset.hidden = Boolean(info.is_default);
    }

    function applyLogoToPage(url) {
        if (!url) return;
        document.querySelectorAll('.logo-img').forEach(img => { img.src = url; });
    }

    function selectedRadio() {
        return modelsBody.querySelector('input[name="settingsModel"]:checked');
    }

    function modelById(id) {
        return state.models.find(m => m.id === id) || null;
    }

    function renderModels() {
        let lastFamily = '';
        modelsBody.innerHTML = state.models
            .map(model => {
                const family = model.provider_label || model.provider || '';
                let groupRow = '';
                if (family !== lastFamily) {
                    lastFamily = family;
                    groupRow =
                        `<tr class="settings-group">` +
                        `<th colspan="5" scope="colgroup">Модели ${escapeHtml(family)}</th>` +
                        `</tr>`;
                }
                const checked = model.id === state.selected ? ' checked' : '';
                const disabled = model.available ? '' : ' disabled';
                const rowClass = [
                    model.id === state.selected ? 'is-selected' : '',
                    model.available ? '' : 'is-unavailable',
                ]
                    .filter(Boolean)
                    .join(' ');
                const badge = model.available
                    ? ''
                    : `<span class="settings-model-unavailable">${escapeHtml(model.unavailable_reason)}</span>`;
                const maxOut = model.max_output && !/^(—|-|не указано)$/.test(model.max_output)
                    ? `<span class="settings-model-note">вывод до ${escapeHtml(model.max_output)}</span>`
                    : '';
                const contextNote = model.context_note
                    ? `<span class="settings-model-note">${escapeHtml(model.context_note)}</span>`
                    : '';
                const paramsNote = model.params_note
                    ? `<span class="settings-model-note">${escapeHtml(model.params_note)}</span>`
                    : '';
                const paramsLinks = (Array.isArray(model.params_sources) ? model.params_sources : [])
                    .concat(model.params_source ? [{ label: 'параметры', url: model.params_source }] : [])
                    .map(s => `<a class="settings-model-link" href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.label)}</a>`)
                    .join('');
                const priceNote = model.price_note
                    ? `<span class="settings-model-note">${escapeHtml(model.price_note)}</span>`
                    : '';
                const usdNote = model.price_usd_text
                    ? `<span class="settings-model-note">${escapeHtml(model.price_usd_text)}</span>`
                    : '';
                return (
                    groupRow +
                    `<tr class="${rowClass}">` +
                    `<td class="settings-pick">` +
                    `<input type="radio" name="settingsModel" value="${escapeHtml(model.id)}"${checked}${disabled} ` +
                    `aria-label="${escapeHtml(model.title)}">` +
                    `</td>` +
                    `<td>` +
                    `<span class="settings-model-name">${escapeHtml(model.title)}</span>` +
                    `<span class="settings-model-id">${escapeHtml(model.provider_label)} · ${escapeHtml(model.id)}</span>` +
                    priceNote +
                    (model.source
                        ? `<a class="settings-model-link" href="${escapeHtml(model.source)}" target="_blank" rel="noopener">тарифы</a>`
                        : '') +
                    `${badge}` +
                    `</td>` +
                    `<td class="settings-model-price">${escapeHtml(model.price_text)}${usdNote}</td>` +
                    `<td>${escapeHtml(model.params)}${paramsNote}${paramsLinks}</td>` +
                    `<td>${escapeHtml(model.context)}${contextNote}${maxOut}</td>` +
                    `</tr>`
                );
            })
            .join('');
    }

    function updateCurrentLabel() {
        const radio = selectedRadio();
        const model = radio ? modelById(radio.value) : null;
        if (!model) {
            currentLabel.textContent = 'Модель не выбрана';
            return;
        }
        const price = /не опубликов/i.test(model.price_text || '') ? '' : ` · ${model.price_text}`;
        currentLabel.textContent = `Сейчас: ${model.title}${price}`;
    }

    async function loadSettings() {
        const resp = await fetch('/api/settings', { headers: { Accept: 'application/json' } });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        state.models = Array.isArray(data.models) ? data.models : [];
        state.selected = data.model || '';
        companyInput.value = data.company_name || '';
        renderLogo(data.logo);
        if (pricesAt) {
            const parts = [];
            if (data.prices_updated_at) parts.push(`Данные на ${data.prices_updated_at}`);
            const fx = data.fx || null;
            if (fx && fx.rate) {
                const rate = Number(fx.rate).toFixed(2).replace('.', ',');
                parts.push(
                    `Курс: ${rate} ₽/$ (${fx.source}${fx.as_of ? ', ' + fx.as_of : ''})`
                    + (fx.stale ? ' — не удалось обновить' : ''),
                );
            }
            const api = data.api || {};
            const failed = Object.keys(api)
                .filter(key => api[key] && api[key].error)
                .map(key => api[key].error);
            if (failed.length) {
                parts.push('список моделей в API проверить не удалось — показаны все');
            }
            pricesAt.textContent = parts.length ? parts.join('. ') + '.' : '';
        }
        state.loaded = true;
        renderModels();
        updateCurrentLabel();
    }

    function applyCompanyToPage(name) {
        document.querySelectorAll('[data-company-name]').forEach(el => {
            el.textContent = name;
        });
        const logo = document.querySelector('.logo-img');
        if (logo) logo.alt = `Логотип ${name}`;
        if (document.title.includes('ИИ-помощник')) {
            const pageTitle = document.title.split('—')[0].trim();
            document.title = `${pageTitle} — ИИ-помощник ${name}`;
        }
    }

    async function save() {
        showError('');
        const radio = selectedRadio();
        if (!radio) {
            showError('Выберите модель');
            return;
        }
        const company = companyInput.value.trim();
        if (!company) {
            showError('Укажите наименование компании');
            companyInput.focus();
            return;
        }
        saveBtn.disabled = true;
        try {
            const resp = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ company_name: company, model: radio.value }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                const detail = data.detail;
                throw new Error(
                    Array.isArray(detail)
                        ? detail.map(d => d.msg || String(d)).join('; ')
                        : (detail || `HTTP ${resp.status}`),
                );
            }
            applyCompanyToPage(data.company_name || company);
            if (data.logo) {
                renderLogo(data.logo);
                applyLogoToPage(data.logo.url);
            }
            close();
        } catch (e) {
            showError(e.message || 'Не удалось сохранить настройки');
        } finally {
            saveBtn.disabled = false;
        }
    }

    async function uploadLogo(file) {
        showError('');
        showLogoStatus('Загрузка…', false);
        const form = new FormData();
        form.append('file', file, file.name);
        try {
            const resp = await fetch('/api/settings/logo', { method: 'POST', body: form });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                const detail = data.detail;
                throw new Error(
                    Array.isArray(detail)
                        ? detail.map(d => d.msg || String(d)).join('; ')
                        : (detail || `HTTP ${resp.status}`),
                );
            }
            renderLogo(data.logo);
            applyLogoToPage(data.logo.url);
            showLogoStatus('Логотип обновлён', false);
        } catch (e) {
            showLogoStatus(`Не удалось заменить логотип: ${e.message}`, true);
        }
    }

    async function resetLogo() {
        showError('');
        showLogoStatus('Возврат исходного…', false);
        try {
            const resp = await fetch('/api/settings/logo', { method: 'DELETE' });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
            renderLogo(data.logo);
            applyLogoToPage(data.logo.url);
            showLogoStatus('Возвращён исходный логотип', false);
        } catch (e) {
            showLogoStatus(`Не удалось вернуть исходный логотип: ${e.message}`, true);
        }
    }

    const MODULE_LABELS = {
        economist: 'Экономист',
        lawyer: 'Юрист',
        procurement: 'Закупки',
        secretary: 'Секретарь',
        tenders: 'Торги',
    };

    function moduleLabel(key) {
        return MODULE_LABELS[key] || key || '—';
    }

    function showLogsError(message) {
        if (!logsError) return;
        logsError.hidden = !message;
        logsError.textContent = message || '';
    }

    function renderLogs(rows) {
        if (!logsBody) return;
        if (!rows.length) {
            logsBody.innerHTML = '<tr><td colspan="7" class="muted">Записей пока нет</td></tr>';
            return;
        }
        logsBody.innerHTML = rows.map((row) => {
            const ts = String(row.ts || '').replace('T', ' ').slice(0, 19);
            const login = row.login ? escapeHtml(row.login) : '<span class="muted">—</span>';
            const question = escapeHtml(row.question || '');
            const status = row.status === 'error'
                ? '<span class="user-logs-status is-error">Ошибка</span>'
                : '<span class="user-logs-status is-ok">Получен</span>';
            // В логе лежит код, а не текст для пользователя: расшифровка — в подсказке.
            // Записи до перехода на коды содержат поле error — показываем как есть.
            const code = row.error_code || '';
            const legacy = !code && row.error ? row.error : '';
            const codeCell = code
                ? `<span class="user-logs-code" title="${escapeHtml(logsState.errorCodes[code] || code)}">${escapeHtml(code)}</span>`
                : legacy
                    ? `<small class="user-logs-legacy">${escapeHtml(legacy)}</small>`
                    : '';
            const tokens = row.tokens == null ? '—' : escapeHtml(String(row.tokens));
            return `<tr>
                <td class="user-logs-ts">${escapeHtml(ts)}</td>
                <td>${login}</td>
                <td>${escapeHtml(row.ip || '—')}</td>
                <td>${escapeHtml(moduleLabel(row.module))}</td>
                <td class="user-logs-question">${question}</td>
                <td class="user-logs-tokens">${tokens}</td>
                <td>${status}${codeCell}</td>
            </tr>`;
        }).join('');
    }

    function fillModuleFilter(modules) {
        if (!logsModule || logsModule.dataset.filled === '1') return;
        (modules || []).forEach((m) => {
            const opt = document.createElement('option');
            opt.value = m;
            opt.textContent = moduleLabel(m);
            logsModule.appendChild(opt);
        });
        logsModule.dataset.filled = '1';
    }

    async function loadUserLogs() {
        if (!logsBody) return;
        showLogsError('');
        const params = new URLSearchParams();
        if (logsModule && logsModule.value) params.set('module', logsModule.value);
        if (logsStatus && logsStatus.value) params.set('status', logsStatus.value);
        // «Все» — не отправляем limit: сервер отдаёт всё окно хранения.
        const wantLimit = logsLimit ? logsLimit.value : '10';
        if (wantLimit && wantLimit !== 'all') params.set('limit', wantLimit);
        const qs = params.toString();
        try {
            const resp = await fetch(`/api/settings/logs?${qs}`, {
                headers: { Accept: 'application/json' },
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            if (data.error_codes) logsState.errorCodes = data.error_codes;
            renderLogs(data.logs || []);
            if (logsRetention && data.retention_days != null) {
                logsRetention.textContent = String(data.retention_days);
            }
            // Путь хранилища приходит с бэкенда, чтобы не расходиться с config.py.
            // Показываем только когда знаем значение — иначе в заголовке висят пустые скобки.
            if (logsPath && data.storage_dir) {
                logsPath.textContent = ` (${data.storage_dir})`;
                logsPath.title = data.storage_dir;
                logsPath.hidden = false;
            }
            fillModuleFilter(data.modules || []);
            if (logsDownload) logsDownload.href = `/api/settings/logs.csv`;
        } catch (e) {
            logsBody.innerHTML = '<tr><td colspan="7" class="muted">Не удалось загрузить логи</td></tr>';
            showLogsError(`Не удалось загрузить логи: ${e.message}`);
        } finally {
            logsState.loaded = true;
        }
    }

    function open() {        lastFocused = document.activeElement;
        overlay.hidden = false;
        showError('');
        showLogoStatus('', false);
        if (logoFile) logoFile.value = '';
        updateCurrentLabel();
        if (!state.loaded) {
            loadSettings().catch(e => showError(`Не удалось загрузить настройки: ${e.message}`));
        }
        loadUserLogs().catch(() => { /* ошибка уже показана в блоке логов */ });
        const first = overlay.querySelector('input[name="settingsModel"]:checked')
            || overlay.querySelector('input[name="settingsModel"]:not([disabled])');
        (first || closeBtn).focus();
    }

    function close() {
        overlay.hidden = true;
        if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
    }

    openBtn.addEventListener('click', open);
    closeBtn.addEventListener('click', close);
    cancelBtn.addEventListener('click', close);
    saveBtn.addEventListener('click', save);
    overlay.addEventListener('click', e => {
        if (e.target === overlay) close();
    });
    modelsBody.addEventListener('change', () => {
        modelsBody.querySelectorAll('tr').forEach(row => row.classList.remove('is-selected'));
        const radio = selectedRadio();
        if (radio) radio.closest('tr').classList.add('is-selected');
        updateCurrentLabel();
    });
    companyInput.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
            e.preventDefault();
            save();
        }
    });
    if (logoFile) {
        logoFile.addEventListener('change', () => {
            const file = logoFile.files && logoFile.files[0];
            if (file) uploadLogo(file);
        });
    }
    if (logoReset) logoReset.addEventListener('click', resetLogo);
    if (logsRefresh) logsRefresh.addEventListener('click', () => loadUserLogs());
    if (logsModule) logsModule.addEventListener('change', () => loadUserLogs());
    if (logsStatus) logsStatus.addEventListener('change', () => loadUserLogs());
    if (logsLimit) logsLimit.addEventListener('change', () => loadUserLogs());
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && !overlay.hidden) close();
    });
})();
