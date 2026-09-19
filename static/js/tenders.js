/** Модуль «Торги» — проверка торговой документации и экспертное мнение */
document.addEventListener("DOMContentLoaded", () => {
    const chatForm = document.getElementById("chat-form");
    const chatInput = document.getElementById("chat-input");
    const chatSubmit = document.getElementById("chat-submit");
    const chatMessages = document.getElementById("chat-messages");
    const chatTitle = document.getElementById("chat-title");
    const verificationBanner = document.getElementById("verification-banner");
    const actionHint = document.getElementById("chat-action-hint");
    const guidance = document.getElementById("module-guidance");
    const progressSummary = document.getElementById("tenders-progress");

    const CHECK_QUESTION =
        "Проверь торговую документацию на соответствие требованиям торгов (135-ФЗ, Приказ ФАС №147/23) и сформируй отчёт о проверке с замечаниями.";
    const CHECK_USER_LABEL = "Проверка документации";

    const ZONES = [
        {
            key: "auction",
            dropId: "auction-drop",
            fileId: "auction-file",
            statusId: "auction-status",
            progressId: "auction-progress",
            infoId: "auction-info",
            endpoint: "/tenders/auction/upload",
            processing: "разбор торговой документации",
        },
        {
            key: "egrn",
            dropId: "egrn-drop",
            fileId: "egrn-file",
            statusId: "egrn-status",
            progressId: "egrn-progress",
            infoId: "egrn-info",
            endpoint: "/tenders/egrn/upload",
            processing: "разбор выписки ЕГРН",
        },
        {
            key: "approval",
            dropId: "approval-drop",
            fileId: "approval-file",
            statusId: "approval-status",
            progressId: "approval-progress",
            infoId: "approval-info",
            endpoint: "/tenders/approval/upload",
            processing: "разбор согласования сделки",
        },
    ];

    let mode = "check";
    let allLoaded = false;
    let loadedCount = 0;
    let requestPending = false;

    function safeText(s) {
        const t = stripSiteUrls(s || "");
        return t || String(s || "").trim();
    }

    function escapeHtml(s) {
        const d = document.createElement("div");
        d.textContent = s;
        return d.innerHTML;
    }

    function updateChatAvailability() {
        const checkMode = mode === "check";
        const disabled = requestPending || (checkMode ? !allLoaded : !chatInput?.value.trim());

        if (chatForm) {
            chatForm.classList.toggle("procurement-check-form", checkMode);
            chatForm.classList.toggle("procurement-expert-form", !checkMode);
        }
        if (chatInput) {
            chatInput.classList.toggle("hidden", checkMode);
            if (checkMode) {
                chatInput.disabled = true;
                chatInput.value = "";
            } else {
                chatInput.disabled = false;
                chatInput.placeholder =
                    "Задайте вопрос по законодательству об аренде (135-ФЗ, Приказ ФАС №147)…";
            }
        }
        if (chatSubmit) {
            chatSubmit.disabled = disabled;
            chatSubmit.textContent = checkMode ? "Проверить" : "Спросить";
        }
        if (actionHint) {
            actionHint.className = "module-action-hint";
            if (requestPending) {
                actionHint.textContent = "Формирую ответ. Это может занять некоторое время…";
                actionHint.classList.add("is-loading");
            } else if (checkMode && !allLoaded) {
                actionHint.textContent = "Загрузите торговую документацию — единственный обязательный документ.";
            } else if (checkMode) {
                actionHint.textContent = "Торговая документация загружена. Можно запускать проверку.";
                actionHint.classList.add("is-ready");
            } else {
                actionHint.textContent = chatInput?.value.trim() ? "Вопрос готов к отправке." : "Введите вопрос по законодательству об аренде.";
            }
        }
    }

    function setMode(nextMode) {
        mode = nextMode;
        if (chatTitle) {
            chatTitle.textContent =
                mode === "expert"
                    ? "Экспертное мнение"
                    : "Проверка торговой документации";
        }
        document.querySelectorAll(".procurement-mode-btn").forEach((btn) => {
            const isActive = btn.getAttribute("data-tab") === mode;
            btn.classList.toggle("active", isActive);
            btn.setAttribute("aria-selected", isActive ? "true" : "false");
        });
        if (mode === "expert") {
            showVerificationBanner(null);
        }
        updateChatAvailability();
        if (guidance) guidance.textContent = mode === "check"
            ? "Загрузите торговую документацию — единственный обязательный документ. Выписку ЕГРН и согласование сделки можно добавить по желанию."
            : "Задайте вопрос по законодательству об аренде государственного имущества.";
    }

    document.querySelectorAll(".procurement-mode-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            setMode(btn.getAttribute("data-tab") || "check");
        });
    });
    chatInput?.addEventListener("input", updateChatAvailability);

    function removeEmptyState() { document.getElementById("chat-empty-state")?.remove(); }
    function historyTargetId(index) { return `tenders-history-${index}`; }
    function scrollToHistory(index) {
        const target = document.getElementById(historyTargetId(index));
        if (!target || !chatMessages) return;
        const top = target.offsetTop;
        chatMessages.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
        target.classList.remove("module-message-highlight");
        void target.offsetWidth;
        target.classList.add("module-message-highlight");
        setTimeout(() => target.classList.remove("module-message-highlight"), 1800);
    }
    function bindHistoryLinks() {
        document.querySelectorAll(".module-history-link").forEach(button => {
            button.onclick = () => scrollToHistory(button.dataset.historyIndex);
        });
    }

    function renderFileInfo(infoId, name, cadastral) {
        const el = document.getElementById(infoId);
        if (!el) return;
        if (!name) {
            el.innerHTML = '<li class="muted">Документ не загружен</li>';
            return;
        }
        const cad = cadastral ? `<small class="muted">КН: ${escapeHtml(cadastral)}</small>` : "";
        el.innerHTML = `<li><span>${escapeHtml(name)}</span>${cad ? `<br>${cad}` : ""}</li>`;
    }

    async function refreshStatus() {
        try {
            const resp = await fetch("/tenders/status");
            const data = await resp.json().catch(() => ({}));
            allLoaded = Boolean(data.all_loaded);
            loadedCount = 0;
            for (const z of ZONES) {
                const zone = (data.zones || {})[z.key] || {};
                if (zone.loaded) {
                    loadedCount += 1;
                    renderFileInfo(z.infoId, safeText(zone.filename) || "Документ", zone.cadastral);
                } else {
                    renderFileInfo(z.infoId, null);
                }
            }
            if (progressSummary) {
                progressSummary.classList.toggle("is-ready", allLoaded);
                progressSummary.innerHTML = allLoaded
                    ? `<strong>Комплект документов: ${loadedCount} из 3</strong><span>Торговая документация загружена — можно запускать проверку. Остальное — по желанию.</span>`
                    : `<strong>Комплект документов: ${loadedCount} из 3</strong><span>Обязательна только торговая документация. Выписка ЕГРН и согласование сделки — по желанию.</span>`;
            }
            updateChatAvailability();
        } catch (e) {
            console.error("refreshStatus", e);
        }
    }

    async function uploadZone(zone, file) {
        if (!file) return;
        App.setFileProcessing({
            statusId: zone.statusId,
            progressId: zone.progressId,
            zoneId: zone.dropId,
            active: true,
            message: `Обработка файла: ${file.name}… (${zone.processing})`,
        });
        try {
            const data = await App.uploadFile(zone.endpoint, file);
            const name = safeText(data.filename) || file.name;
            App.setStatus(
                zone.statusId,
                `✓ Загружен: ${name}${data.cadastral ? ` (КН ${data.cadastral})` : ""}`,
                "ok",
                { zoneId: zone.dropId },
            );
            await refreshStatus();
        } catch (e) {
            App.setStatus(
                zone.statusId,
                safeText(e.message) || "Ошибка загрузки",
                "error",
                { zoneId: zone.dropId },
            );
        } finally {
            App.setFileProcessing({
                statusId: zone.statusId,
                progressId: zone.progressId,
                zoneId: zone.dropId,
                active: false,
                message: "",
            });
            const input = document.getElementById(zone.fileId);
            if (input) input.value = "";
        }
    }

    for (const zone of ZONES) {
        App.setupDropZone(zone.dropId, zone.fileId, (file) => uploadZone(zone, file));
    }

    function showVerificationBanner(verification) {
        if (!verificationBanner) return;
        if (!verification) {
            verificationBanner.classList.add("hidden");
            verificationBanner.textContent = "";
            return;
        }
        const score = verification.score != null ? verification.score : "—";
        const status = verification.status || "unknown";
        const labels = { passed: "Пройдено", warnings: "Есть предупреждения", failed: "Есть ошибки" };
        verificationBanner.className = `tenders-verification-banner tenders-verification-${status}`;
        verificationBanner.textContent =
            `Автопроверка: ${labels[status] || status} · оценка ${score}/100 · ` +
            `ошибок: ${verification.errors_count || 0}, предупреждений: ${verification.warnings_count || 0}`;
    }

    function formatAnswer(text, replyMode) {
        const useMode = replyMode || mode;
        return useMode === "check"
            ? App.formatCheckReportMarkdown(text)
            : App.formatChatMarkdown(text);
    }

    function showBotReply(answer, citations, verification, replyMode) {
        const text = safeText(answer) || "Ответ пуст. Попробуйте переформулировать вопрос.";
        const useMode = replyMode || mode;
        if (useMode === "check") {
            showVerificationBanner(verification);
        } else {
            showVerificationBanner(null);
        }

        const msgDiv = document.createElement("div");
        msgDiv.className = "message bot";

        const body = document.createElement("div");
        body.className = "message-text";
        body.innerHTML = formatAnswer(text, useMode);
        msgDiv.appendChild(body);

        if (citations && citations.length && useMode !== "expert") {
            const box = document.createElement("div");
            box.className = "citations";
            const title = document.createElement("strong");
            title.textContent = "Источники:";
            box.appendChild(title);

            const list = document.createElement("ul");
            list.className = "citation-list";
            citations.forEach((c) => {
                const li = document.createElement("li");
                li.className = "citation-source";
                const label = safeText(c.filename) || "Документ";
                const page = c.page != null ? c.page : "—";
                const ref = c.id != null ? `[${c.id}] ` : "";
                li.textContent = `${ref}${label}, ${page}`;
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

    chatForm?.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (mode === "check" && !allLoaded) return;

        const question = mode === "check" ? CHECK_QUESTION : chatInput.value.trim();
        if (!question) return;

        const userLabel = mode === "check" ? CHECK_USER_LABEL : question;
        removeEmptyState();
        App.addMessage("chat-messages", userLabel, "user");
        if (mode !== "check") chatInput.value = "";
        chatForm.classList.add("loading");
        requestPending = true;
        updateChatAvailability();

        try {
            const endpoint = mode === "expert" ? "/tenders/expert/query" : "/tenders/query";
            const payload =
                mode === "expert"
                    ? { question }
                    : { question, mode: "check" };
            const resp = await fetch(endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json; charset=utf-8" },
                body: JSON.stringify(payload),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                throw new Error(data.detail || resp.statusText || "Ошибка запроса");
            }
            showBotReply(data.answer, data.citations || [], data.verification, mode);
            await refreshHistory();
            await loadChatHistory();
        } catch (err) {
            showBotReply(safeText(err.message) || "Ошибка запроса", [], null, mode);
        } finally {
            chatForm.classList.remove("loading");
            requestPending = false;
            updateChatAvailability();
        }
    });

    async function refreshHistory() {
        try {
            const resp = await fetch("/tenders/history");
            const data = await resp.json();
            const list = document.getElementById("history-list");
            if (!list) return;
            list.innerHTML = data.history.length
                ? data.history
                      .map((h, index) => {
                          const isCheck = (h.mode || "check") === "check";
                          const q = isCheck ? CHECK_USER_LABEL : safeText(h.query);
                           return `<li><time>${h.timestamp}</time><button type="button" class="history-query module-history-link" data-history-index="${index}">${escapeHtml(q.length > 60 ? q.slice(0, 60) + "…" : q)}</button></li>`;
                      })
                      .join("")
                : '<li class="muted">Нет вопросов</li>';
            bindHistoryLinks();
        } catch (e) {
            console.error("refreshHistory", e);
        }
    }

    async function loadChatHistory() {
        try {
            const resp = await fetch("/tenders/history");
            const data = await resp.json();
            if (!chatMessages || !data.history || !data.history.length) return;
            chatMessages.innerHTML = "";
            const items = [...data.history].reverse();
            for (const [index, h] of items.entries()) {
                const isCheck = (h.mode || "check") === "check";
                const label = isCheck ? CHECK_USER_LABEL : safeText(h.query);
                const group = document.createElement("div");
                group.className = "module-message-group";
                group.id = historyTargetId(data.history.length - 1 - index);
                chatMessages.appendChild(group);
                const user = document.createElement("div");
                user.className = "message message-text user";
                user.textContent = label;
                group.appendChild(user);
                showBotReply(
                    h.response,
                    h.citations || [],
                    h.verification || null,
                    h.mode || "check",
                );
                const bot = chatMessages.lastElementChild;
                if (bot && bot !== group) group.appendChild(bot);
            }
            bindHistoryLinks();
        } catch (e) {
            console.error("loadChatHistory", e);
        }
    }

    refreshStatus();
    loadChatHistory();
    bindHistoryLinks();
    setMode("check");
});
