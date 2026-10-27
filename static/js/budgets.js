/* M4 · Budgets : recommandation IA et simulateur sans rechargement. */
(function () {
    "use strict";

    function formatMoney(value, currency) {
        if (value === null || value === undefined) {
            return "—";
        }
        const amount = Number(value).toLocaleString("fr-FR", {
            minimumFractionDigits: 3,
            maximumFractionDigits: 3,
        });
        return currency ? amount + " " + currency : amount;
    }

    function formatMonth(value) {
        if (!value) {
            return "";
        }
        const [year, month] = value.split("-").map(Number);
        return new Date(year, month - 1, 1).toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
    }

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) {
            node.className = className;
        }
        if (text !== undefined) {
            node.textContent = text;
        }
        return node;
    }

    async function getJson(url) {
        const response = await fetch(url, {
            headers: { Accept: "application/json", "X-Requested-With": "fetch" },
            credentials: "same-origin",
        });
        const data = await response.json().catch(() => ({ ok: false }));
        return { response, data };
    }

    /* --- Recommandation de budget (formulaire de budget) --- */
    function initRecommendation() {
        const button = document.getElementById("recommend-button");
        const output = document.getElementById("recommend-output");
        const form = document.getElementById("budget-form");
        if (!button || !output || !form) {
            return;
        }
        button.hidden = false;

        button.addEventListener("click", async function () {
            const category = form.elements.category.value;
            const month = form.elements.month.value;
            output.replaceChildren();
            if (!category) {
                output.textContent = "Choisissez d'abord une catégorie.";
                form.elements.category.focus();
                return;
            }

            button.disabled = true;
            output.setAttribute("aria-busy", "true");
            output.textContent = "Analyse en cours…";
            const params = new URLSearchParams({ category: category, month: month });

            try {
                const { response, data } = await getJson(button.dataset.url + "?" + params);
                output.replaceChildren();
                if (!response.ok || !data.ok) {
                    output.textContent = data.error || "La recommandation est indisponible. Saisissez la limite manuellement.";
                    return;
                }
                if (data.suggested_limit) {
                    const strong = element("strong", "", "Limite suggérée : " + formatMoney(data.suggested_limit));
                    const apply = element("button", "btn btn-ghost", "Utiliser cette limite");
                    apply.type = "button";
                    apply.style.marginLeft = "0.75rem";
                    apply.addEventListener("click", function () {
                        form.elements.limit_amount.value = data.suggested_limit;
                        form.elements.limit_amount.focus();
                    });
                    output.append(strong, apply);
                }
                output.append(element("p", "", data.explanation));
                const source = data.source === "llm" ? "Explication rédigée par l'IA." : "Explication standard (IA indisponible).";
                if (data.source !== "none") {
                    output.append(element("span", "ai-source", source));
                }
            } catch (error) {
                output.textContent = "Erreur réseau : saisissez la limite manuellement.";
            } finally {
                output.setAttribute("aria-busy", "false");
                button.disabled = false;
            }
        });
    }

    /* --- Simulateur « et si ? » --- */
    function renderSimulation(container, result, currency) {
        container.replaceChildren();
        if (result.months_needed) {
            container.append(element("p", "big-figure", result.months_needed + " mois"));
            container.append(
                element(
                    "p",
                    "",
                    "pour atteindre " + formatMoney(result.savings_goal, currency) + ", vers " + formatMonth(result.reached_on) + "."
                )
            );
        } else {
            container.append(element("p", "big-figure", "—"));
            container.append(
                element("p", "", "Pas d'économie possible : aucune dépense connue dans cette catégorie, ou réduction nulle.")
            );
        }
        const list = element("dl", "result-list");
        [
            ["Dépense moyenne actuelle", formatMoney(result.monthly_average, currency) + " / mois"],
            ["Après réduction de " + Number(result.reduction_percent).toLocaleString("fr-FR") + " %", formatMoney(result.new_monthly_spend, currency) + " / mois"],
            ["Économie mensuelle", formatMoney(result.monthly_saving, currency)],
        ].forEach(function ([term, value]) {
            const row = element("div");
            row.append(element("dt", "", term), element("dd", "", value));
            list.append(row);
        });
        container.append(list);
        container.append(element("p", "help", "Moyenne calculée sur " + result.months_of_history + " mois d'historique."));
    }

    function showFieldErrors(form, errors) {
        form.querySelectorAll(".js-error").forEach((node) => node.remove());
        form.querySelectorAll("[aria-invalid]").forEach((node) => node.removeAttribute("aria-invalid"));
        let first = null;
        Object.entries(errors || {}).forEach(function ([name, list]) {
            const field = form.elements[name];
            if (!field) {
                return;
            }
            field.setAttribute("aria-invalid", "true");
            const message = element("p", "field-error js-error", list[0].message);
            message.id = field.id + "-js-error";
            message.setAttribute("role", "alert");
            field.setAttribute("aria-describedby", message.id);
            field.after(message);
            first = first || field;
        });
        if (first) {
            first.focus();
        }
    }

    function initSimulator() {
        const form = document.getElementById("simulate-form");
        const section = document.getElementById("simulate-result");
        const body = document.getElementById("simulate-result-body");
        if (!form || !section || !body) {
            return;
        }
        form.addEventListener("submit", async function (event) {
            event.preventDefault();
            const params = new URLSearchParams(new FormData(form));
            section.setAttribute("aria-busy", "true");
            try {
                const { data } = await getJson(form.dataset.url + "?" + params);
                if (!data.ok) {
                    showFieldErrors(form, data.errors);
                    return;
                }
                showFieldErrors(form, {});
                renderSimulation(body, data.result, form.dataset.currency);
                history.replaceState(null, "", "?" + params);
            } catch (error) {
                body.textContent = "Erreur réseau : réessayez.";
            } finally {
                section.setAttribute("aria-busy", "false");
            }
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        initRecommendation();
        initSimulator();
    });
})();
