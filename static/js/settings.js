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

    // Управление пользователями. Тот же раздел настроек, поэтому администратор
    // заводит учётки здесь, а не через scripts.manage_users на сервере.
    const usersBody = document.getElementById('usersBody');
    const usersError = document.getElementById('usersError');
    const usersStatus = document.getElementById('usersStatus');
    const usersPath = document.getElementById('usersPath');
    const addForm = document.getElementById('usersAddForm');
    const newLogin = document.getElementById('usersNewLogin');
    const newPassword = document.getElementById('usersNewPassword');
    const newPassword2 = document.getElementById('usersNewPassword2');
    const newRole = document.getElementById('usersNewRole');
    const newMustChange = document.getElementById('usersNewMustChange');
    const genPassBtn = document.getElementById('usersGenPass');
    const addBtn = document.getElementById('usersAddBtn');
    const usersState = { me: '' };

    const ROLE_LABELS = { user: 'Пользователь', admin: 'Администратор' };

    // Генерация пароля для нового сотрудника. Берём символы из crypto, а не
    // Math.random: пароль это единственное, что защищает учётку, и Math.random
    // предсказуем. Из алфавита убраны 0/O, 1/l/I — администратор передаёт пароль
    // сотруднику устно или в письме, и разница между «О» и «0» стоит лишнего
    // звонка в поддержку.
    const PASS_LOWER = 'abcdefghijkmnopqrstuvwxyz';
    const PASS_UPPER = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
    const PASS_DIGIT = '23456789';
    const PASS_SYMBOL = '!@#$%^&*()-_=+[]{}:,.?';
    const PASS_LENGTH = 16;

    // Случайный индекс без смещения по модулю: значения из хвоста диапазона
    // отбрасываем, иначе первые символы алфавита выпадали бы чаще.
    function randomIndex(max) {
        const limit = Math.floor(0xFFFFFFFF / max) * max;
        const buf = new Uint32Array(1);
        let value;
        do {
            crypto.getRandomValues(buf);
            value = buf[0];
        } while (value >= limit);
        return value % max;
    }

    function randomChar(alphabet) {
        return alphabet.charAt(randomIndex(alphabet.length));
    }

    function generatePassword(length) {
        // По символу из каждого класса — иначе пароль может оказаться без цифр
        // или без знака, и такой его отвергнет внешняя политика паролей.
        const classes = [PASS_LOWER, PASS_UPPER, PASS_DIGIT, PASS_SYMBOL];
        const all = classes.join('');
        const chars = classes.map(randomChar);
        while (chars.length < length) chars.push(randomChar(all));
        for (let i = chars.length - 1; i > 0; i -= 1) {
            const j = randomIndex(i + 1);
            const swap = chars[i];
            chars[i] = chars[j];
            chars[j] = swap;
        }
        return chars.join('');
    }

    function showGeneratedPassword() {
        if (!newPassword) return;
        showUsersError('');
        let pass;
        try {
            pass = generatePassword(PASS_LENGTH);
        } catch (e) {
            showUsersError('Браузер не даёт сгенерировать пароль — введите его вручную');
            newPassword.focus();
            return;
        }
        newPassword.value = pass;
        newPassword2.value = pass;
        // Показываем текстом: пароль нужно передать сотруднику, из точек его
        // не прочитать. Поле повтора остаётся замаскированным — это проверка
        // совпадения, читать его не нужно. После сохранения поля маскируются.
        newPassword.type = 'text';
        newMustChange.checked = true;
        showUsersStatus(`Готов пароль на ${PASS_LENGTH} символов — передайте его сотруднику.`);
        newPassword.focus();
        newPassword.select();
    }

    function hideGeneratedPassword() {
        if (newPassword) newPassword.type = 'password';
        if (newPassword2) newPassword2.type = 'password';
    }

    function apiDetail(data, status) {
        const detail = data && data.detail;
        if (Array.isArray(detail)) return detail.map(d => d.msg || String(d)).join('; ');
        return detail || `HTTP ${status}`;
    }

    function showUsersError(message) {
        if (!usersError) return;
        usersError.textContent = message || '';
        usersError.hidden = !message;
    }

    function showUsersStatus(message) {
        if (!usersStatus) return;
        usersStatus.textContent = message || '';
        usersStatus.hidden = !message;
    }

    function currentLogin() {
        const el = document.querySelector('.nav-user');
        return el ? el.textContent.trim() : '';
    }

    function roleOptions(role, roles) {
        const list = roles && roles.length ? roles : ['user', 'admin'];
        return list
            .map(r => `<option value="${escapeHtml(r)}"${r === role ? ' selected' : ''}>`
                + `${escapeHtml(ROLE_LABELS[r] || r)}</option>`)
            .join('');
    }

    function stateBadge(user) {
        if (user.disabled) return '<span class="users-badge is-off">заблокирован</span>';
        if (user.must_change_password) {
            return '<span class="users-badge is-temp">временный пароль</span>';
        }
        return '<span class="users-badge is-on">активен</span>';
    }

    function changedAt(value) {
        const raw = String(value || '');
        if (!raw) return '<span class="muted">—</span>';
        return escapeHtml(raw.replace('T', ' ').slice(0, 16));
    }

    function actButton(act, login, label, extra) {
        return `<button type="button" class="btn btn-sm ${extra} users-act"`
            + ` data-act="${act}" data-login="${escapeHtml(login)}">${escapeHtml(label)}</button>`;
    }

    function renderUsers(rows, roles) {
        if (!usersBody) return;
        if (!rows.length) {
            usersBody.innerHTML = '<tr><td colspan="5" class="muted">'
                + 'Пользователей нет — заведите администратора формой ниже</td></tr>';
            return;
        }
        usersBody.innerHTML = rows.map(user => {
            const login = user.login || '';
            const self = login === usersState.me;
            const loginCell = escapeHtml(login)
                + (self ? '<span class="users-self" title="Это вы">вы</span>' : '');
            const toggle = user.disabled
                ? actButton('enable', login, 'Разблокировать', 'btn-secondary')
                : actButton('disable', login, 'Заблокировать', 'btn-secondary');
            return `<tr>
                <td class="users-login">${loginCell}</td>
                <td>
                    <select class="settings-input users-role" data-login="${escapeHtml(login)}"
                            aria-label="Роль ${escapeHtml(login)}">${roleOptions(user.role, roles)}</select>
                </td>
                <td>${stateBadge(user)}</td>
                <td class="users-changed">${changedAt(user.password_changed_at)}</td>
                <td class="users-actions">
                    ${actButton('password', login, 'Сменить пароль', 'btn-secondary')}
                    ${toggle}
                    ${actButton('delete', login, 'Удалить', 'btn-danger')}
                </td>
            </tr>`;
        }).join('');
    }

    function closeEditRows() {
        if (usersBody) usersBody.querySelectorAll('.users-edit-row').forEach(row => row.remove());
    }

    function generateEditPassword() {
        const row = document.querySelector('.users-edit-row');
        if (!row) return;
        showUsersError('');
        const pass = row.querySelector('.users-edit-pass');
        const pass2 = row.querySelector('.users-edit-pass2');
        let value;
        try {
            value = generatePassword(PASS_LENGTH);
        } catch (e) {
            showUsersError('Браузер не даёт сгенерировать пароль — введите его вручную');
            pass.focus();
            return;
        }
        pass.value = value;
        pass2.value = value;
        // Показываем текстом: пароль сбрасывают по запросу сотрудника, и его
        // нужно передать. Поле «Ещё раз» остаётся замаскированным.
        pass.type = 'text';
        row.querySelector('.users-edit-must').checked = true;
        showUsersStatus(`Готов пароль на ${PASS_LENGTH} символов — передайте сотруднику.`);
        pass.focus();
        pass.select();
    }

    function openEditRow(button) {
        const row = button.closest('tr');
        const existing = row.nextElementSibling;
        if (existing && existing.classList.contains('users-edit-row')) {
            existing.remove();
            return;
        }
        closeEditRows();
        const login = button.dataset.login || '';
        row.insertAdjacentHTML('afterend', `<tr class="users-edit-row">
            <td colspan="5">
                <div class="users-edit">
                    <span class="users-edit-login">${escapeHtml(login)}</span>
                    <div class="users-field users-edit-field">
                        <div class="users-field-head">
                            <label for="usersEditPass">Новый пароль</label>
                            <button type="button" class="users-gen users-edit-gen"
                                    title="Подставить надёжный пароль">Сгенерировать</button>
                        </div>
                        <input type="password" id="usersEditPass"
                               class="settings-input users-edit-pass" autocomplete="new-password">
                    </div>
                    <div class="users-field users-edit-field">
                        <div class="users-field-head">
                            <label for="usersEditPass2">Ещё раз</label>
                        </div>
                        <input type="password" id="usersEditPass2"
                               class="settings-input users-edit-pass2" autocomplete="new-password">
                    </div>
                    <label class="users-check">
                        <input type="checkbox" class="users-edit-must" checked>
                        <span>потребовать смену при входе</span>
                    </label>
                    <button type="button" class="btn btn-primary btn-sm users-edit-save">Сохранить</button>
                    <button type="button" class="btn btn-secondary btn-sm users-edit-cancel">Отмена</button>
                </div>
            </td>
        </tr>`);
        const edit = row.nextElementSibling;
        const first = edit.querySelector('.users-edit-pass');
        if (first) first.focus();
    }

    async function usersRequest(url, options) {
        const resp = await fetch(url, options);
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(apiDetail(data, resp.status));
        return data;
    }

    function applyUsers(data) {
        renderUsers(data.users || [], data.roles);
        showUsersStatus(data.message || '');
        // Путь хранилища приходит с бэкенда, чтобы не расходиться с config.py.
        if (usersPath && data.storage_file) {
            usersPath.textContent = ` (${data.storage_file})`;
            usersPath.title = data.storage_file;
            usersPath.hidden = false;
        }
    }

    async function loadUsers() {
        if (!usersBody) return;
        showUsersError('');
        try {
            applyUsers(await usersRequest('/api/settings/users', {
                headers: { Accept: 'application/json' },
            }));
        } catch (e) {
            usersBody.innerHTML = '<tr><td colspan="5" class="muted">'
                + 'Не удалось загрузить пользователей</td></tr>';
            showUsersError(`Не удалось загрузить пользователей: ${e.message}`);
        }
    }

    function userUrl(login, action) {
        return `/api/settings/users/${encodeURIComponent(login)}${action ? `/${action}` : ''}`;
    }

    async function changeRole(select) {
        const login = select.dataset.login || '';
        const role = select.value;
        showUsersError('');
        showUsersStatus('');
        select.disabled = true;
        try {
            applyUsers(await usersRequest(userUrl(login, 'role'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ role }),
            }));
        } catch (e) {
            showUsersError(e.message);
            loadUsers();
        }
    }

    async function toggleBlock(login, disable) {
        const self = login === usersState.me;
        const question = disable
            ? (self
                ? `Заблокировать собственный доступ (${login})? Придётся войти заново.`
                : `Заблокировать доступ для ${login}? Логи сохранятся.`)
            : `Разблокировать ${login}?`;
        const ok = await App.confirm(question, {
            danger: disable,
            okLabel: disable ? 'Заблокировать' : 'Разблокировать',
        });
        if (!ok) return;
        showUsersError('');
        showUsersStatus('');
        try {
            applyUsers(await usersRequest(userUrl(login, 'disable'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ disabled: disable }),
            }));
        } catch (e) {
            showUsersError(e.message);
        }
    }

    async function removeUser(login) {
        const self = login === usersState.me;
        const question = self
            ? `Удалить собственную учётку ${login}? Сессия закончится немедленно.`
            : `Удалить пользователя ${login}? Вход будет закрыт, логи сохранятся.`;
        const ok = await App.confirm(question, { danger: true, okLabel: 'Удалить' });
        if (!ok) return;
        showUsersError('');
        showUsersStatus('');
        try {
            applyUsers(await usersRequest(userUrl(login), { method: 'DELETE' }));
        } catch (e) {
            showUsersError(e.message);
        }
    }

    async function savePassword(button) {
        const edit = button.closest('.users-edit-row');
        if (!edit) return;
        const login = edit.querySelector('.users-edit-login').textContent.trim();
        const pass = edit.querySelector('.users-edit-pass').value;
        const pass2 = edit.querySelector('.users-edit-pass2').value;
        if (!pass) {
            showUsersError('Введите новый пароль');
            return;
        }
        if (pass !== pass2) {
            showUsersError('Пароли не совпадают');
            return;
        }
        showUsersError('');
        showUsersStatus('');
        button.disabled = true;
        try {
            applyUsers(await usersRequest(userUrl(login, 'password'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    password: pass,
                    must_change_password: edit.querySelector('.users-edit-must').checked,
                }),
            }));
        } catch (e) {
            showUsersError(e.message);
            button.disabled = false;
        }
    }

    async function submitNewUser(event) {
        event.preventDefault();
        showUsersError('');
        showUsersStatus('');
        const login = newLogin.value.trim();
        const pass = newPassword.value;
        if (!login) {
            showUsersError('Укажите логин');
            newLogin.focus();
            return;
        }
        if (!pass) {
            showUsersError('Укажите пароль');
            newPassword.focus();
            return;
        }
        if (pass !== newPassword2.value) {
            showUsersError('Пароли не совпадают');
            newPassword2.focus();
            return;
        }
        addBtn.disabled = true;
        try {
            const data = await usersRequest('/api/settings/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    login: login,
                    password: pass,
                    role: newRole.value,
                    must_change_password: newMustChange.checked,
                }),
            });
            // Пароль из формы убираем из DOM, чтобы не остался в памяти вкладки.
            newPassword.value = '';
            newPassword2.value = '';
            newLogin.value = '';
            hideGeneratedPassword();
            // Следующего сотрудника заводим без требования смены пароля.
            newMustChange.checked = false;
            applyUsers(data);
            newLogin.focus();
        } catch (e) {
            showUsersError(e.message);
        } finally {
            addBtn.disabled = false;
        }
    }

    function open() {        lastFocused = document.activeElement;
        overlay.hidden = false;
        showError('');
        showLogoStatus('', false);
        showUsersError('');
        showUsersStatus('');
        closeEditRows();
        if (logoFile) logoFile.value = '';
        if (newPassword) newPassword.value = '';
        if (newPassword2) newPassword2.value = '';
        hideGeneratedPassword();
        updateCurrentLabel();
        if (!state.loaded) {
            loadSettings().catch(e => showError(`Не удалось загрузить настройки: ${e.message}`));
        }
        loadUserLogs().catch(() => { /* ошибка уже показана в блоке логов */ });
        if (usersBody) {
            usersState.me = currentLogin();
            loadUsers().catch(() => { /* ошибка уже показана в блоке пользователей */ });
        }
        // Пункты свёрнуты, поэтому фокус берём с первого заголовка раздела.
        const first = overlay.querySelector('.settings-section[open] input[name="settingsModel"]:checked')
            || overlay.querySelector('.settings-section[open] input[name="settingsModel"]:not([disabled])')
            || overlay.querySelector('.settings-section-head')
            || closeBtn;
        (first || closeBtn).focus();
    }

    function close() {
        closeEditRows();
        overlay.hidden = true;
        if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
    }

    if (usersBody) {
        usersBody.addEventListener('change', e => {
            const select = e.target.closest('.users-role');
            if (select) changeRole(select);
        });
        usersBody.addEventListener('click', e => {
            const save = e.target.closest('.users-edit-save');
            if (save) {
                savePassword(save);
                return;
            }
            if (e.target.closest('.users-edit-gen')) {
                generateEditPassword();
                return;
            }
            if (e.target.closest('.users-edit-cancel')) {
                closeEditRows();
                return;
            }
            const button = e.target.closest('.users-act');
            if (!button) return;
            const act = button.dataset.act;
            const login = button.dataset.login || '';
            if (act === 'password') openEditRow(button);
            else if (act === 'disable') toggleBlock(login, true);
            else if (act === 'enable') toggleBlock(login, false);
            else if (act === 'delete') removeUser(login);
        });
    }
    if (addForm) addForm.addEventListener('submit', submitNewUser);
    if (genPassBtn) genPassBtn.addEventListener('click', showGeneratedPassword);

    // Раскрытый пункт подводим к видимой части окна, иначе содержимое
    // раскрывается за нижним краем и кажется, что до конца не прокрутить.
    overlay.querySelectorAll('.settings-section').forEach(section => {
        section.addEventListener('toggle', () => {
            if (!section.open) return;
            section.scrollIntoView({ block: 'nearest' });
        });
    });

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
