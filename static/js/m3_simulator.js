// M3 · Simulateur interactif du risque de retard (Imem).
// Le modèle (StandardScaler + régression logistique) est recalculé dans le navigateur à partir
// des paramètres exportés par settlements.risk.model_params() : aucune requête réseau.
(() => {
    const dataNode = document.getElementById("simulator-data");
    if (!dataNode) return;
    const cfg = JSON.parse(dataNode.textContent);
    const P = cfg.params;
    const $ = (id) => document.getElementById(id);
    const fr = (value, digits) =>
        Number(value).toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits });

    const inputs = {
        amount: $("sim-amount"),
        installments_count: $("sim-count"),
        past_late_count: $("sim-late"),
        avg_confirmation_delay_days: $("sim-delay"),
        days_since_creation: $("sim-age"),
    };
    const outputs = {
        amount: [$("sim-amount-out"), (v) => `${fr(v, 3)} ${cfg.currency}`],
        installments_count: [$("sim-count-out"), (v) => `${v} échéances`],
        past_late_count: [$("sim-late-out"), (v) => `${v} retard${v > 1 ? "s" : ""}`],
        avg_confirmation_delay_days: [$("sim-delay-out"), (v) => `${fr(v, 1)} jour${v >= 2 ? "s" : ""}`],
        days_since_creation: [$("sim-age-out"), (v) => `${v} jour${v >= 2 ? "s" : ""}`],
    };
    const frequencies = [...document.querySelectorAll('input[name="sim-frequency"]')];
    const frequencyLabels = { weekly: "semaine", biweekly: "quinzaine", monthly: "mois" };

    // --- Modèle --------------------------------------------------------------
    function currentValues() {
        const v = {};
        for (const [name, input] of Object.entries(inputs)) v[name] = Number(input.value);
        v.amount_weight_ratio = v.amount / cfg.weight;
        return v;
    }

    function contributions(v) {
        return P.features.map((feature, i) => ({
            feature,
            label: P.labels[feature],
            value: v[feature],
            contribution: (P.coef[i] * (v[feature] - P.mean[i])) / P.scale[i],
        }));
    }

    function probability(v) {
        const z = contributions(v).reduce((sum, item) => sum + item.contribution, P.intercept);
        return 1 / (1 + Math.exp(-z));
    }

    // --- Rendu ---------------------------------------------------------------
    function renderExplanation(items) {
        const list = $("sim-explain");
        const sorted = [...items].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
        const largest = Math.max(...sorted.map((i) => Math.abs(i.contribution)), 1e-9);
        list.replaceChildren(
            ...sorted.map((item) => {
                const li = document.createElement("li");
                const raises = item.contribution > 0;
                const label = document.createElement("span");
                label.className = "explain-label";
                label.textContent = item.label;
                const bar = document.createElement("span");
                bar.className = `explain-bar ${raises ? "explain-up" : "explain-down"}`;
                bar.style.setProperty("--share", `${Math.round((Math.abs(item.contribution) / largest) * 100)}%`);
                bar.setAttribute("aria-hidden", "true");
                const effect = document.createElement("span");
                effect.className = "explain-effect";
                effect.textContent = raises ? "▲ augmente le risque" : "▼ diminue le risque";
                li.append(label, bar, effect);
                return li;
            })
        );
    }

    function renderCurve(v) {
        const svg = $("sim-curve");
        const ns = "http://www.w3.org/2000/svg";
        const x = (count) => 30 + ((count - 2) / 10) * 275;
        const y = (p) => 120 - p * 105;
        const points = [];
        for (let count = 2; count <= 12; count += 1) {
            points.push([count, probability({ ...v, installments_count: count })]);
        }
        const make = (tag, attrs) => {
            const node = document.createElementNS(ns, tag);
            for (const [k, val] of Object.entries(attrs)) node.setAttribute(k, val);
            return node;
        };
        const children = [
            make("line", { x1: 30, x2: 305, y1: y(P.threshold), y2: y(P.threshold), class: "curve-threshold" }),
            make("text", { x: 2, y: y(P.threshold) + 4, class: "curve-text" }),
            make("polyline", { points: points.map(([c, p]) => `${x(c)},${y(p)}`).join(" "), class: "curve-line" }),
        ];
        children[1].textContent = "50 %";
        for (const [count, p] of points) {
            const dot = make("circle", {
                cx: x(count), cy: y(p), r: count === v.installments_count ? 6 : 3.5,
                class: count === v.installments_count ? "curve-dot curve-current" : "curve-dot",
            });
            const title = make("title", {});
            title.textContent = `${count} échéances : ${Math.round(p * 100)} %`;
            dot.append(title);
            dot.addEventListener("click", () => {
                inputs.installments_count.value = count;
                update();
            });
            children.push(dot);
            const tick = make("text", { x: x(count), y: 136, class: "curve-text", "text-anchor": "middle" });
            tick.textContent = count;
            children.push(tick);
        }
        svg.replaceChildren(...children);
    }

    let announceTimer;
    function update() {
        const v = currentValues();
        for (const [name, [output, format]] of Object.entries(outputs)) {
            output.value = format(v[name]);
            inputs[name].setAttribute("aria-valuetext", format(v[name]));
        }
        const p = probability(v);
        const percent = Math.round(p * 100);
        const high = p >= P.threshold;
        const gauge = $("sim-gauge");
        gauge.style.setProperty("--p", percent);
        gauge.classList.toggle("gauge-high", high);
        $("sim-percent").textContent = `${percent} %`;
        $("sim-verdict").textContent = high
            ? "Risque élevé : une échéance a de fortes chances d'être payée avec plus de 3 jours de retard."
            : "Risque faible : l'échéancier devrait être tenu.";
        $("sim-verdict").className = `sim-verdict ${high ? "risk-high" : "risk-low"}`;

        if (cfg.saved_percent !== null) {
            const diff = percent - cfg.saved_percent;
            $("sim-delta").textContent = diff === 0
                ? `Identique au risque enregistré (${cfg.saved_percent} %).`
                : `${diff > 0 ? "+" : "−"}${Math.abs(diff)} pts par rapport au risque enregistré (${cfg.saved_percent} %).`;
        }

        const frequency = frequencies.find((input) => input.checked).value;
        const installment = v.amount / v.installments_count;
        const monthly = installment * cfg.periods_per_month[frequency];
        $("sim-load").textContent =
            `${v.installments_count} × ${fr(installment, 3)} ${cfg.currency} par ${frequencyLabels[frequency]}, ` +
            `soit environ ${fr(monthly, 3)} ${cfg.currency} par mois.`;

        renderExplanation(contributions(v));
        renderCurve(v);

        const apply = $("sim-apply");
        if (apply) {
            const url = new URL(apply.href, window.location.origin);
            url.searchParams.set("installments_count", v.installments_count);
            url.searchParams.set("frequency", frequency);
            apply.href = url.pathname + url.search;
        }

        clearTimeout(announceTimer);
        announceTimer = setTimeout(() => {
            $("sim-live").textContent = `Risque ${percent} %, ${high ? "élevé" : "faible"}.`;
        }, 500);
    }

    function reset() {
        const initial = cfg.initial;
        inputs.amount.max = Math.max(initial.amount * 2, 500);
        for (const [name, input] of Object.entries(inputs)) input.value = initial[name];
        for (const input of frequencies) input.checked = input.value === cfg.frequency;
        update();
    }

    for (const input of [...Object.values(inputs), ...frequencies]) input.addEventListener("input", update);
    $("sim-reset").addEventListener("click", reset);
    reset();
})();
