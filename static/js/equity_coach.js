// M1 · Coach d'équité interactif (Ala) : comparaison égale / par poids en direct, puis avis du coach.
(() => {
    const dataNode = document.getElementById("coach-data");
    if (!dataNode) return;
    const cfg = JSON.parse(dataNode.textContent);
    const $ = (id) => document.getElementById(id);
    const amountInput = $("coach-amount");
    const membersBody = $("coach-members");
    const fr = (millimes) => (millimes / 1000).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
    let members = [];

    // Même arrondi que core/rounding.py (plus grand reste, au millime) : la somme des parts est exacte
    function split(totalMillimes, weights) {
        const sum = weights.reduce((a, b) => a + b, 0);
        const raw = weights.map((w) => (totalMillimes * w) / sum);
        const rounded = raw.map((r) => Math.round(r));
        let remainder = totalMillimes - rounded.reduce((a, b) => a + b, 0);
        const order = raw.map((r, i) => i).sort((a, b) => {
            const diff = (raw[a] - rounded[a]) - (raw[b] - rounded[b]);
            return remainder > 0 ? -diff || String(b).localeCompare(String(a)) : diff || String(a).localeCompare(String(b));
        });
        for (let k = 0; remainder !== 0 && k < order.length; k += 1) {
            rounded[order[k]] += remainder > 0 ? 1 : -1;
            remainder += remainder > 0 ? -1 : 1;
        }
        return rounded;
    }

    function el(tag, attrs = {}, text) {
        const node = document.createElement(tag);
        Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function renderMembers() {
        membersBody.replaceChildren(...members.map((m, index) => {
            const tr = el("tr");
            const name = el("input", { type: "text", class: "input", maxlength: "40", "aria-label": `Nom de la personne ${index + 1}` });
            name.value = m.name;
            name.addEventListener("input", () => { m.name = name.value; compute(); });
            const weight = el("input", { type: "number", class: "input coach-weight", min: "0.001", max: "100", step: "0.5",
                "aria-label": `Poids de ${m.name || "la personne " + (index + 1)}` });
            weight.value = m.weight;
            weight.addEventListener("input", () => { m.weight = Number(weight.value); compute(); });
            const remove = el("button", { type: "button", class: "btn-icon", "aria-label": `Retirer ${m.name}` }, "×");
            remove.disabled = members.length <= 1;
            remove.addEventListener("click", () => { members.splice(index, 1); renderMembers(); compute(); });
            const tdName = el("td"); tdName.append(name);
            const tdWeight = el("td"); tdWeight.append(weight);
            const tdRemove = el("td"); tdRemove.append(remove);
            tr.append(tdName, tdWeight, tdRemove);
            return tr;
        }));
    }

    function valid() {
        const amount = Number(amountInput.value);
        return amount > 0 && members.length > 0 && members.every((m) => m.weight >= 0.001 && m.weight <= 100);
    }

    function compute() {
        const tbody = $("coach-table");
        if (!valid()) {
            tbody.replaceChildren(el("tr", {}, ""));
            tbody.firstChild.append(el("td", { colspan: "4", class: "muted" }, "Montant ou poids invalide."));
            $("coach-summary").textContent = "";
            return;
        }
        const total = Math.round(Number(amountInput.value) * 1000);
        const equal = split(total, members.map(() => 1));
        const weighted = split(total, members.map((m) => m.weight));
        tbody.replaceChildren(...members.map((m, i) => {
            const diff = weighted[i] - equal[i];
            const tr = el("tr");
            tr.append(
                el("th", { scope: "row" }, `${m.name || "Personne " + (i + 1)} (poids ${String(m.weight).replace(".", ",")})`),
                el("td", { class: "num" }, `${fr(equal[i])} ${cfg.currency}`),
                el("td", { class: "num" }, `${fr(weighted[i])} ${cfg.currency}`),
                el("td", { class: `num ${diff > 0 ? "coach-more" : diff < 0 ? "coach-less" : ""}` },
                   diff === 0 ? "=" : `${diff > 0 ? "+" : "−"}${fr(Math.abs(diff))}`),
            );
            return tr;
        }));
        $("coach-total-equal").textContent = `${fr(total)} ${cfg.currency}`;
        $("coach-total-weight").textContent = `${fr(total)} ${cfg.currency}`;
        const diffs = members.map((m, i) => ({ name: m.name || `Personne ${i + 1}`, diff: weighted[i] - equal[i] }));
        const saver = diffs.reduce((a, b) => (b.diff < a.diff ? b : a));
        $("coach-summary").textContent = saver.diff < 0
            ? `Avec la règle par poids, ${saver.name} paie ${fr(-saver.diff)} ${cfg.currency} de moins qu'avec la règle égale.`
            : "Tous les poids sont égaux : les deux règles donnent les mêmes parts.";
    }

    function reset() {
        members = cfg.members.map((m) => ({ ...m }));
        amountInput.value = cfg.amount;
        renderMembers();
        compute();
    }

    cfg.profiles.forEach((profile) => {
        const button = el("button", { type: "button", class: "btn btn-inline btn-sm" }, `+ ${profile.label} (${profile.weight})`);
        button.addEventListener("click", () => {
            if (members.length >= 12) return;
            members.push({ name: `${profile.label} ${members.length + 1}`, weight: profile.weight });
            renderMembers();
            compute();
        });
        $("coach-presets").append(button, " ");
    });
    amountInput.addEventListener("input", compute);
    $("coach-reset").addEventListener("click", reset);

    // Avis du coach : chiffres et recommandation calculés par le serveur, explication par l'IA
    const form = $("coach-form");
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!valid()) {
            $("coach-status").textContent = "Corrigez d'abord le montant ou les poids.";
            return;
        }
        const button = $("coach-submit");
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.querySelector(".spinner").hidden = false;
        $("coach-status").textContent = "Le coach analyse le scénario…";
        const body = new FormData();
        body.append("amount", amountInput.value);
        body.append("members", JSON.stringify(members));
        body.append("question", $("coach-question").value);
        try {
            const response = await fetch(form.action, {
                method: "POST",
                headers: { "X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value },
                body,
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Erreur serveur.");
            $("coach-reco-label").textContent = data.recommendation.label;
            $("coach-explanation").innerHTML = data.explanation_html;  // HTML échappé et produit par le serveur
            const apply = $("coach-apply");
            if (apply) {
                $("coach-apply-rule").value = data.recommendation.rule;
                $("coach-apply-btn").textContent = `Appliquer « ${data.recommendation.label.toLowerCase()} » au groupe`;
                apply.hidden = data.recommendation.rule === cfg.current_rule;
            }
            $("coach-answer").hidden = false;
            const source = data.source === "llm" ? "Explication rédigée par l'IA" : "Explication issue du modèle (IA indisponible)";
            $("coach-status").textContent = `${source} en ${(data.latency_ms / 1000).toFixed(1).replace(".", ",")} s.`;
        } catch (error) {
            $("coach-status").textContent = `Le coach est indisponible : ${error.message}`;
        } finally {
            button.disabled = false;
            button.removeAttribute("aria-busy");
            button.querySelector(".spinner").hidden = true;
        }
    });

    reset();
})();
