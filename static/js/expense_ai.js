/* M2 · IA du formulaire de dépense (Yahya).
 * 1. Catégorie suggérée à partir du titre (classifieur TF-IDF + régression logistique).
 *    Jamais d'écrasement d'un choix fait à la main : la suggestion est alors proposée, pas appliquée.
 * 2. Saisie en langage naturel : une phrase → champs pré-remplis (LLM validé côté serveur).
 * Sans JavaScript ou si l'IA échoue, le formulaire reste entièrement utilisable à la main.
 */
(function () {
    "use strict";

    const form = document.getElementById("expense-form");
    if (!form) return;

    const $ = (id) => document.getElementById(id);
    const titleInput = $("id_title");
    const categorySelect = $("id_category");
    const newCategoryInput = $("id_new_category_name");
    const box = $("ai-suggestion");
    const boxText = $("ai-suggestion-text");
    const applyBtn = $("ai-suggestion-apply");
    const dismissBtn = $("ai-suggestion-dismiss");
    const csrf = form.querySelector("[name=csrfmiddlewaretoken]");

    // Une catégorie déjà présente (édition, formulaire renvoyé avec erreurs) est un choix de l'utilisateur
    let manualChoice = Boolean(categorySelect && categorySelect.value);
    let pending = null;
    let lastSuggestion = null;

    function flash(el) {
        if (!el) return;
        el.classList.remove("ai-filled");
        void el.offsetWidth;
        el.classList.add("ai-filled");
    }

    function percent(confidence) {
        return Math.round((confidence || 0) * 100) + " %";
    }

    function hideBox() {
        if (box) box.hidden = true;
    }

    function showBox(text, { apply = false, dismiss = false } = {}) {
        if (!box) return;
        boxText.textContent = text;
        applyBtn.hidden = !apply;
        dismissBtn.hidden = !dismiss;
        box.hidden = false;
    }

    /* Applique une suggestion : catégorie existante, ou nom à créer à l'enregistrement. */
    function applySuggestion(s) {
        if (!categorySelect) return;
        if (s.exists && s.category_id) {
            categorySelect.value = String(s.category_id);
            if (newCategoryInput) newCategoryInput.value = "";
            showBox("Catégorie « " + s.category_name + " » choisie par l'IA (" + percent(s.confidence) + ").");
        } else {
            categorySelect.value = "";
            if (newCategoryInput) newCategoryInput.value = s.category_name;
            showBox("Catégorie « " + s.category_name + " » suggérée (" + percent(s.confidence)
                + ") : elle n'existe pas encore et sera créée dans le groupe à l'enregistrement.", { dismiss: true });
        }
        flash(categorySelect);
    }

    function handleSuggestion(s) {
        lastSuggestion = s;
        if (!s || !s.category_name) return hideBox();
        const alreadyChosen = s.exists && categorySelect && categorySelect.value === String(s.category_id);
        if (alreadyChosen) return hideBox();
        if (manualChoice) {
            showBox("L'IA suggère « " + s.category_name + " » (" + percent(s.confidence) + ").", { apply: true });
        } else if (s.confident) {
            applySuggestion(s);
        } else {
            showBox("Suggestion peu sûre : « " + s.category_name + " » (" + percent(s.confidence)
                + "). Choisissez la catégorie.", { apply: true });
        }
    }

    if (categorySelect) {
        // L'événement « change » n'est déclenché que par l'utilisateur, pas par l'IA
        categorySelect.addEventListener("change", function () {
            manualChoice = true;
            if (newCategoryInput) newCategoryInput.value = "";
            hideBox();
        });
    }
    if (applyBtn) {
        applyBtn.addEventListener("click", function () {
            if (lastSuggestion) applySuggestion(lastSuggestion);
        });
    }
    if (dismissBtn) {
        dismissBtn.addEventListener("click", function () {
            if (newCategoryInput) newCategoryInput.value = "";
            manualChoice = true;
            hideBox();
        });
    }

    let timer = null;
    if (titleInput && categorySelect) {
        titleInput.addEventListener("input", function () {
            clearTimeout(timer);
            const title = titleInput.value.trim();
            if (title.length < 3) {
                if (!manualChoice && newCategoryInput) newCategoryInput.value = "";
                return hideBox();
            }
            timer = setTimeout(function () {
                if (pending) pending.abort();
                pending = new AbortController();
                const url = form.dataset.categorizeUrl + "?title=" + encodeURIComponent(title);
                fetch(url, { signal: pending.signal, headers: { Accept: "application/json" } })
                    .then((res) => (res.ok ? res.json() : Promise.reject(new Error("HTTP " + res.status))))
                    .then(handleSuggestion)
                    .catch((err) => {
                        if (err.name !== "AbortError") console.debug("Suggestion de catégorie indisponible :", err);
                    });
            }, 350);
        });
    }

    /* ── Saisie en langage naturel ── */
    const nlpInput = $("nlp-input");
    const nlpBtn = $("nlp-btn");
    const feedback = $("nlp-feedback");
    if (!nlpInput || !nlpBtn) return;

    function setBusy(busy) {
        nlpBtn.disabled = busy;
        nlpBtn.setAttribute("aria-busy", busy ? "true" : "false");
        nlpBtn.querySelector(".spinner").hidden = !busy;
        nlpBtn.querySelector(".btn-label").textContent = busy ? "Analyse en cours…" : "Pré-remplir";
    }

    function say(text, kind) {
        feedback.textContent = text;
        feedback.className = "nlp-feedback" + (kind ? " is-" + kind : "");
    }

    function fill(id, value) {
        const el = $(id);
        if (!el || value === null || value === undefined || value === "") return false;
        el.value = value;
        flash(el);
        return true;
    }

    function extract() {
        const phrase = nlpInput.value.trim();
        if (!phrase) {
            say("Écrivez d'abord une phrase, par exemple « 45 dinars resto hier ».", "error");
            nlpInput.focus();
            return;
        }
        setBusy(true);
        say("Analyse en cours…");
        const body = new FormData();
        body.append("phrase", phrase);
        fetch(form.dataset.nlpUrl, {
            method: "POST",
            headers: { "X-CSRFToken": csrf ? csrf.value : "", Accept: "application/json" },
            body: body,
        })
            .then((res) => res.json().catch(() => ({})).then((data) => ({ ok: res.ok, data: data })))
            .then(({ ok, data }) => {
                if (!ok || !data.success) {
                    say((data && data.error) || "L'analyse a échoué. Remplissez le formulaire à la main.", "error");
                    return;
                }
                fill("id_title", data.title);
                fill("id_amount", data.amount);
                fill("id_currency", data.currency);
                fill("id_expense_date", data.expense_date);
                if (data.category_name) {
                    manualChoice = false;
                    handleSuggestion({ ...data, confidence: 1, confident: true });
                }
                const notes = [];
                if (data.participants && data.participants.length) notes.push("avec " + data.participants.join(", "));
                if (data.unknown_participants && data.unknown_participants.length) {
                    notes.push(data.unknown_participants.join(", ") + " : pas membre du groupe");
                }
                if (data.missing && data.missing.length) notes.push("à compléter : " + data.missing.join(", "));
                const via = data.source === "regles" ? "analyse locale (IA indisponible)" : "IA";
                say("Formulaire pré-rempli par " + via + ". Vérifiez puis enregistrez."
                    + (notes.length ? " (" + notes.join(" · ") + ")" : ""), data.missing && data.missing.length ? "warn" : "ok");
            })
            .catch(() => say("Erreur réseau : l'IA n'a pas répondu. Remplissez le formulaire à la main.", "error"))
            .finally(() => setBusy(false));
    }

    nlpBtn.addEventListener("click", extract);
    nlpInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            extract();
        }
    });
})();
