// Assistant Finova (M6) : conversation en bulles, envoi en fetch, réponse Markdown rendue côté serveur.
(() => {
    const form = document.getElementById("asst-form");
    if (!form) return;
    const log = document.getElementById("asst-log");
    const textarea = document.getElementById("asst-question");
    const sendButton = document.getElementById("asst-send");
    const typing = document.getElementById("asst-typing");
    const counter = document.getElementById("asst-counter");
    const csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
    const MAX = Number(textarea.maxLength) || 2000;

    const scrollToBottom = () => { log.scrollTop = log.scrollHeight; };

    function autosize() {
        textarea.style.height = "auto";
        textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
        counter.textContent = `${textarea.value.length} / ${MAX}`;
        sendButton.disabled = !textarea.value.trim();
    }

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function addUserMessage(question) {
        const article = element("article", "asst-msg asst-msg-user");
        article.setAttribute("aria-label", "Vous");
        const bubble = element("div", "asst-bubble", question);  // textContent : jamais interprété en HTML
        article.append(bubble, element("time", "asst-time", "à l'instant"));
        log.insertBefore(article, typing);
    }

    function addBotMessage({ html, text, meta, isError = false, retry }) {
        const article = element("article", `asst-msg asst-msg-bot${isError ? " asst-msg-error" : ""}`);
        article.setAttribute("aria-label", isError ? "Erreur de l'assistant" : "Assistant");
        const avatar = element("span", "asst-avatar", isError ? "!" : "✦");
        avatar.setAttribute("aria-hidden", "true");
        const body = element("div", "asst-body");
        const bubble = element("div", "asst-bubble asst-md");
        if (html !== undefined) bubble.innerHTML = html;  // HTML produit et échappé par le serveur
        else bubble.textContent = text;
        const metaRow = element("div", "asst-meta");
        if (meta) metaRow.append(element("span", "", meta));
        if (!isError) {
            const copy = element("button", "asst-copy", "Copier");
            copy.type = "button";
            copy.setAttribute("aria-label", "Copier la réponse");
            metaRow.append(copy);
        }
        if (retry) {
            const again = element("button", "asst-copy", "Réessayer");
            again.type = "button";
            again.addEventListener("click", () => { article.remove(); ask(retry); });
            metaRow.append(again);
        }
        body.append(bubble, metaRow);
        article.append(avatar, body);
        log.insertBefore(article, typing);
    }

    async function ask(question) {
        document.getElementById("asst-empty")?.remove();
        addUserMessage(question);
        textarea.value = "";
        autosize();
        typing.hidden = false;
        log.setAttribute("aria-busy", "true");
        textarea.disabled = true;
        sendButton.disabled = true;
        scrollToBottom();

        const started = performance.now();
        const body = new FormData();
        body.append("question", question);
        try {
            const response = await fetch(form.action, {
                method: "POST",
                headers: { "X-CSRFToken": csrf },
                body,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || "L'assistant est momentanément indisponible.");
            const seconds = ((performance.now() - started) / 1000).toFixed(1).replace(".", ",");
            addBotMessage({ html: payload.answer_html, meta: `Répondu en ${seconds} s` });
        } catch (error) {
            const message = error instanceof TypeError ? "Connexion impossible. Vérifiez votre réseau." : error.message;
            addBotMessage({ text: message, isError: true, retry: question });
        } finally {
            typing.hidden = true;
            log.removeAttribute("aria-busy");
            textarea.disabled = false;
            autosize();
            textarea.focus();
            scrollToBottom();
        }
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const question = textarea.value.trim();
        if (question) ask(question);
    });

    textarea.addEventListener("input", autosize);
    textarea.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    document.querySelectorAll(".asst-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
            if (!textarea.disabled) ask(chip.dataset.question);
        });
    });

    // Copie du texte d'une réponse (délégation : fonctionne aussi pour les nouveaux messages)
    log.addEventListener("click", async (event) => {
        const button = event.target.closest(".asst-copy");
        if (!button || button.textContent === "Réessayer") return;
        const bubble = button.closest(".asst-body").querySelector(".asst-bubble");
        try {
            await navigator.clipboard.writeText(bubble.innerText.trim());
            button.textContent = "Copié ✓";
        } catch {
            button.textContent = "Copie impossible";
        }
        setTimeout(() => { button.textContent = "Copier"; }, 1800);
    });

    autosize();
    scrollToBottom();
})();
