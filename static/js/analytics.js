(function () {
    "use strict";

    // Palette catégorielle validée (daltonisme) : un emplacement fixe par catégorie,
    // dans l'ordre de analytics.demo.CATEGORIES. La couleur suit la catégorie, jamais son rang.
    const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
    const INK = "#52514e";
    const GRID = "#e1e0d9";
    const SURFACE = "#ffffff";

    const currency = JSON.parse(document.getElementById("chart-currency").textContent);
    const charts = JSON.parse(document.getElementById("chart-data").textContent);
    const money = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
    const compact = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });
    const formatMoney = (value) => `${money.format(value)} ${currency}`;

    loadSummary();

    if (typeof Chart === "undefined") {
        // CDN indisponible : les tableaux de données restent disponibles.
        document.querySelectorAll(".chart-box").forEach((box) => box.remove());
        document.querySelectorAll(".chart-card details").forEach((d) => d.setAttribute("open", ""));
        return;
    }

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    Chart.defaults.font.family = '"IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif';
    Chart.defaults.color = INK;
    Chart.defaults.animation = reducedMotion ? false : Chart.defaults.animation;
    Chart.defaults.plugins.tooltip.callbacks.label = (ctx) => ` ${formatMoney(ctx.parsed.y ?? ctx.parsed)}`;

    const valueAxis = {
        beginAtZero: true,
        grid: { color: GRID },
        border: { display: false },
        ticks: { callback: (value) => compact.format(value) },
    };

    new Chart(document.getElementById("chart-category"), {
        type: "doughnut",
        data: {
            labels: charts.category.labels,
            datasets: [{
                data: charts.category.values,
                backgroundColor: charts.category.slots.map((slot) => SERIES[slot % SERIES.length]),
                borderColor: SURFACE,
                borderWidth: 2,
                hoverOffset: 6,
            }],
        },
        options: {
            maintainAspectRatio: false,
            cutout: "62%",
            plugins: {
                legend: { position: "right", labels: { boxWidth: 12, boxHeight: 12, usePointStyle: true } },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const sum = ctx.dataset.data.reduce((a, b) => a + b, 0);
                            const share = sum ? (ctx.parsed / sum) * 100 : 0;
                            return ` ${formatMoney(ctx.parsed)} (${share.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} %)`;
                        },
                    },
                },
            },
        },
    });

    new Chart(document.getElementById("chart-month"), {
        type: "line",
        data: {
            labels: charts.month.labels,
            datasets: [{
                label: "Dépenses",
                data: charts.month.values,
                borderColor: SERIES[0],
                backgroundColor: SERIES[0],
                borderWidth: 2,
                pointRadius: 4,
                pointHoverRadius: 6,
                pointBorderColor: SURFACE,
                pointBorderWidth: 2,
                tension: 0.25,
            }],
        },
        options: {
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: { legend: { display: false } },
            scales: { x: { grid: { display: false } }, y: valueAxis },
        },
    });

    new Chart(document.getElementById("chart-member"), {
        type: "bar",
        data: {
            labels: charts.member.labels,
            datasets: [{
                label: "Montant payé",
                data: charts.member.values,
                backgroundColor: SERIES[0],
                borderRadius: { topLeft: 4, topRight: 4 },
                borderSkipped: "bottom",
                maxBarThickness: 36,
            }],
        },
        options: {
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { x: { grid: { display: false } }, y: valueAxis },
        },
    });

    function loadSummary() {
        const box = document.getElementById("summary");
        if (!box) return;
        fetch(box.dataset.url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
            .then((response) => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.json();
            })
            .then((result) => {
                const label = result.source === "llm"
                    ? "Rédigé par l'IA, chiffres vérifiés"
                    : "Résumé automatique (sans IA)";
                box.replaceChildren(
                    paragraph(result.text),
                    paragraph(`${label}${result.month_label ? ` · ${result.month_label}` : ""}`, "help"),
                );
            })
            .catch(() => {
                box.replaceChildren(paragraph("Le résumé n'a pas pu être chargé. Réessayez plus tard.", "field-error"));
            })
            .finally(() => box.setAttribute("aria-busy", "false"));
    }

    function paragraph(text, className) {
        const p = document.createElement("p");
        p.textContent = text;
        if (className) p.className = className;
        return p;
    }
})();
