/** Модуль Закупка 223-ФЗ — фронтенд (загрузка по образцу lawyer.js) */
document.addEventListener("DOMContentLoaded", () => {
    const chatForm = document.getElementById("chat-form");
    const chatInput = document.getElementById("chat-input");
    const chatSubmit = document.getElementById("chat-submit");
    const chatMessages = document.getElementById("chat-messages");
    const chatTitle = document.getElementById("chat-title");
    const docFileInput = document.getElementById("doc-file");
    const policyFileInput = document.getElementById("policy-file");
    const docInfo = document.getElementById("doc-info");
    const policyList = document.getElementById("policy-list");
    const actionHint = document.getElementById("chat-action-hint");
    const guidance = document.getElementById("module-guidance");

    const CHECK_QUESTION =
        "Проверь закупочную документацию и сформируй отчёт о проверке с замечаниями.";
    const CHECK_USER_LABEL = "Проверка документации";

    let mode = "check";
    let docLoaded = false;
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
        const disabled = requestPending || (checkMode ? !docLoaded : !chatInput?.value.trim());

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
                chatInput.placeholder = "Задайте вопрос по законодательству о закупках…";
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
            } else if (checkMode && !docLoaded) {
                actionHint.textContent = "Сначала загрузите закупочную документацию.";
            } else if (checkMode) {
                actionHint.textContent = "Документация готова к проверке.";
                actionHint.classList.add("is-ready");
            } else {
                actionHint.textContent = chatInput?.value.trim() ? "Вопрос готов к отправке." : "Введите вопрос по законодательству о закупках.";
            }
        }
    }

    function setMode(nextMode) {
        mode = nextMode;
        if (chatTitle) {
            chatTitle.textContent =
                mode === "expert"
                    ? "Экспертное мнение"
                    : "Проверка закупочной документации";
        }
        document.querySelectorAll(".procurement-mode-btn").forEach((btn) => {
            const isActive = btn.getAttribute("data-tab") === mode;
            btn.classList.toggle("active", isActive);
            btn.setAttribute("aria-selected", isActive ? "true" : "false");
        });
        updateChatAvailability();
        if (guidance) guidance.textContent = mode === "check"
            ? "1. Загрузите закупочную документацию. 2. Дождитесь обработки. 3. Запустите проверку."
            : "Задайте вопрос по законодательству и загруженному Положению о закупке.";
    }

    document.querySelectorAll(".procurement-mode-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            setMode(btn.getAttribute("data-tab") || "check");
        });
    });
    chatInput?.addEventListener("input", updateChatAvailability);

    function removeEmptyState() { document.getElementById("chat-empty-state")?.remove(); }
    function historyTargetId(index) { return `procurement-history-${index}`; }
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

    async function refreshDocStatus() {
        try {
            const resp = await fetch("/procurement/documentation/status");
            const data = await resp.json().catch(() => ({}));
            docLoaded = Boolean(data.loaded);
            if (!docInfo) return;
            if (docLoaded) {
                const name = safeText(data.filename) || "Документ";
                docInfo.innerHTML = `<li><span>${escapeHtml(name)}</span></li>`;
            } else {
                docInfo.innerHTML = '<li class="muted">Документ не загружен</li>';
            }
            updateChatAvailability();
        } catch (e) {
            console.error("refreshDocStatus", e);
        }
    }

    async function uploadDocumentation(file) {
        if (!file) return;
        App.setFileProcessing({
            statusId: "doc-status",
            progressId: "doc-progress",
            zoneId: "doc-drop",
            active: true,
            message: `Обработка файла: ${file.name}… (разбор разделов)`,
        });
        try {
            const data = await App.uploadFile("/procurement/documentation/upload", file);
            const name = safeText(data.filename) || file.name;
            const sections = (data.sections_detected || []).join(", ") || "не найдены";
            App.setStatus(
                "doc-status",
                `✓ Загружен: ${name}. Разделы: ${sections}`,
                "ok",
                { zoneId: "doc-drop" },
            );
            docLoaded = true;
            await refreshDocStatus();
        } catch (e) {
            App.setStatus(
                "doc-status",
                safeText(e.message) || "Ошибка загрузки",
                "error",
                { zoneId: "doc-drop" },
            );
        } finally {
            App.setFileProcessing({
                statusId: "doc-status",
                progressId: "doc-progress",
                zoneId: "doc-drop",
                active: false,
                message: "",
            });
            if (docFileInput) docFileInput.value = "";
        }
    }

    async function uploadPolicy(file) {
        if (!file) return;
        App.setFileProcessing({
            statusId: "policy-status",
            progressId: "policy-progress",
            zoneId: "policy-drop",
            active: true,
            message: `Обработка файла: ${file.name}… (извлечение текста и индексация)`,
        });
        try {
            const data = await App.uploadFile("/procurement/upload", file);
            const name = safeText(data.filename) || file.name;
            App.setStatus(
                "policy-status",
                `✓ Загружен: ${name} (${data.chunks} фрагментов в базе)`,
                "ok",
                { zoneId: "policy-drop" },
            );
            await refreshPolicyFiles();
        } catch (e) {
            App.setStatus(
                "policy-status",
                safeText(e.message) || "Ошибка загрузки",
                "error",
                { zoneId: "policy-drop" },
            );
        } finally {
            App.setFileProcessing({
                statusId: "policy-status",
                progressId: "policy-progress",
                zoneId: "policy-drop",
                active: false,
                message: "",
            });
            if (policyFileInput) policyFileInput.value = "";
        }
    }

    App.setupDropZone("doc-drop", "doc-file", uploadDocumentation);
    App.setupDropZone("policy-drop", "policy-file", uploadPolicy);

    policyList?.addEventListener("click", async (e) => {
        const btn = e.target.closest(".delete-policy");
        if (!btn) return;
        const li = btn.closest("li");
        const fileId = li?.dataset.fileId;
        if (!fileId || !(await App.confirm("Удалить Положение из базы?", { danger: true }))) return;

        try {
            const resp = await fetch(`/procurement/policy/files/${fileId}`, { method: "DELETE" });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                App.setStatus("policy-status", safeText(err.detail) || "Не удалось удалить", "error");
                return;
            }
            App.setStatus("policy-status", "✓ Документ удалён из базы", "ok");
            await refreshPolicyFiles();
        } catch (e) {
            App.setStatus("policy-status", "Ошибка при удалении", "error");
        }
    });

    function formatAnswer(text, replyMode) {
        const useMode = replyMode || mode;
        return useMode === "check"
            ? App.formatCheckReportMarkdown(text)
            : App.formatChatMarkdown(text);
    }

    function showBotReply(answer, citations, replyMode) {
        const text = safeText(answer) || "Ответ пуст. Попробуйте переформулировать вопрос.";
        const useMode = replyMode || mode;
        const msgDiv = document.createElement("div");
        msgDiv.className = "message bot";

        const body = document.createElement("div");
        body.className = "message-text";
        body.innerHTML = formatAnswer(text, replyMode);
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
                const pageLabel = String(page).startsWith("разд.") ? page : `стр. ${page}`;
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

    chatForm?.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (mode === "check" && !docLoaded) return;

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
            const resp = await fetch("/procurement/query", {
                method: "POST",
                headers: { "Content-Type": "application/json; charset=utf-8" },
                body: JSON.stringify({ question, mode }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                throw new Error(data.detail || resp.statusText || "Ошибка запроса");
            }
            showBotReply(data.answer, data.citations || [], mode);
            await refreshHistory();
            await loadChatHistory();
        } catch (err) {
            showBotReply(safeText(err.message) || "Ошибка запроса", [], mode);
        } finally {
            chatForm.classList.remove("loading");
            requestPending = false;
            updateChatAvailability();
        }
    });

    async function refreshPolicyFiles() {
        try {
            const resp = await fetch("/procurement/policy/files");
            const data = await resp.json();
            if (!policyList) return;
            if (!data.files || !data.files.length) {
                policyList.innerHTML = '<li class="muted">Нет загруженных документов</li>';
                return;
            }
            policyList.innerHTML = data.files
                .map(
                    (f) =>
                        `<li data-file-id="${f.file_id}">
                            <span>${escapeHtml(safeText(f.filename))}</span>
                            <button type="button" class="btn-icon delete-policy" title="Удалить">×</button>
                         </li>`,
                )
                .join("");
        } catch (e) {
            console.error("refreshPolicyFiles", e);
        }
    }

    async function refreshHistory() {
        try {
            const resp = await fetch("/procurement/history");
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
            const resp = await fetch("/procurement/history");
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
                showBotReply(h.response, h.citations || [], h.mode || "check");
                const bot = chatMessages.lastElementChild;
                if (bot && bot !== group) group.appendChild(bot);
            }
            bindHistoryLinks();
        } catch (e) {
            console.error("loadChatHistory", e);
        }
    }

    refreshDocStatus();
    refreshPolicyFiles();
    loadChatHistory();
    bindHistoryLinks();
    setMode("check");
});
