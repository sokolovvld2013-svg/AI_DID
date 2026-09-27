/** Настройки приложения: выбор модели LLM и наименование компании. */
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

    const state = { models: [], selected: '', loaded: false };
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

    function selectedRadio() {
        return modelsBody.querySelector('input[name="settingsModel"]:checked');
    }

    function modelById(id) {
        return state.models.find(m => m.id === id) || null;
    }

    function renderModels() {
        modelsBody.innerHTML = state.models
            .map(model => {
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
                const paramsNote = model.params_note
                    ? `<span class="settings-model-note">${escapeHtml(model.params_note)}</span>`
                    : '';
                const paramsLink = model.params_source
                    ? `<a class="settings-model-link" href="${escapeHtml(model.params_source)}" target="_blank" rel="noopener">параметры</a>`
                    : '';
                const priceNote = model.price_note
                    ? `<span class="settings-model-note">${escapeHtml(model.price_note)}</span>`
                    : '';
                const usdNote = model.price_usd_text
                    ? `<span class="settings-model-note">${escapeHtml(model.price_usd_text)}</span>`
                    : '';
                return (
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
                    `<td>${escapeHtml(model.params)}${paramsNote}${paramsLink}</td>` +
                    `<td>${escapeHtml(model.context)}${maxOut}</td>` +
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
            close();
        } catch (e) {
            showError(e.message || 'Не удалось сохранить настройки');
        } finally {
            saveBtn.disabled = false;
        }
    }

    function open() {
        lastFocused = document.activeElement;
        overlay.hidden = false;
        showError('');
        updateCurrentLabel();
        if (!state.loaded) {
            loadSettings().catch(e => showError(`Не удалось загрузить настройки: ${e.message}`));
        }
        const first = overlay.querySelector('input[name="settingsModel"]:checked')
            || overlay.querySelector('input[name="settingsModel"]:not(:disabled)');
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
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && !overlay.hidden) close();
    });
})();
