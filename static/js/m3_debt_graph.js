// M3 · Graphe interactif des dettes (Imem) : dettes brutes ↔ virements simplifiés (≤ n − 1).
// SVG dessiné à la main (aucune bibliothèque) ; membres sur un cercle, flèche = « doit à ».
(() => {
    const dataNode = document.getElementById("graph-data");
    const svg = document.getElementById("debt-graph");
    if (!dataNode || !svg) return;
    const data = JSON.parse(dataNode.textContent);
    const ns = "http://www.w3.org/2000/svg";
    const details = document.getElementById("graph-details");
    const list = document.getElementById("graph-list");
    const summary = document.getElementById("graph-summary");
    const radios = [...document.querySelectorAll('input[name="graph-mode"]')];
    const fr = (v) => Number(v).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
    const money = (v) => `${fr(v)} ${data.currency}`;
    const byId = Object.fromEntries(data.nodes.map((n) => [n.id, n]));
    const R = 34;
    let selected = null;

    const n = data.nodes.length;
    data.nodes.forEach((node, i) => {
        const angle = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(n, 1);
        node.x = 300 + (n > 1 ? 160 : 0) * Math.cos(angle);
        node.y = 215 + (n > 1 ? 160 : 0) * Math.sin(angle);
    });

    document.getElementById("graph-count-before").textContent = `(${data.before.length})`;
    document.getElementById("graph-count-after").textContent = `(${data.after.length})`;

    const make = (tag, attrs = {}, text) => {
        const node = document.createElementNS(ns, tag);
        for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
        if (text !== undefined) node.textContent = text;
        return node;
    };
    const mode = () => radios.find((r) => r.checked).value;
    const edges = () => data[mode()];

    function edgePath(edge) {
        const a = byId[edge.from];
        const b = byId[edge.to];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const len = Math.hypot(dx, dy) || 1;
        const [ux, uy] = [dx / len, dy / len];
        const start = [a.x + ux * R, a.y + uy * R];
        const end = [b.x - ux * (R + 6), b.y - uy * (R + 6)];
        const bend = 28;  // courbure : deux sens d'une même paire restent lisibles
        const control = [(start[0] + end[0]) / 2 - uy * bend, (start[1] + end[1]) / 2 + ux * bend];
        const label = [(start[0] + 2 * control[0] + end[0]) / 4, (start[1] + 2 * control[1] + end[1]) / 4];
        return { d: `M${start} Q${control} ${end}`, label };
    }

    function render() {
        const current = edges();
        const maxAmount = Math.max(...current.map((e) => e.amount), 1);
        const defs = make("defs");
        const marker = make("marker", {
            id: "arrow", viewBox: "0 0 10 10", refX: 8, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse",
        });
        marker.append(make("path", { d: "M0,0 L10,5 L0,10 z", class: "graph-arrow" }));
        defs.append(marker);
        const children = [defs];

        current.forEach((edge) => {
            const { d, label } = edgePath(edge);
            const linked = selected === null || edge.from === selected || edge.to === selected;
            const group = make("g", { class: `graph-edge${linked ? "" : " graph-dim"}` });
            group.append(
                make("path", { d, "marker-end": "url(#arrow)", "stroke-width": 2 + (6 * edge.amount) / maxAmount }),
                make("text", { x: label[0], y: label[1], class: "graph-amount", "text-anchor": "middle" }, fr(edge.amount)),
            );
            children.push(group);
        });

        data.nodes.forEach((node) => {
            const kind = node.balance > 0 ? "creditor" : node.balance < 0 ? "debtor" : "even";
            const group = make("g", {
                class: `graph-node graph-${kind}${selected === node.id ? " graph-selected" : ""}`,
                tabindex: 0,
                role: "button",
                "data-id": node.id,
                "aria-pressed": selected === node.id ? "true" : "false",
                "aria-label": `${node.name}, solde ${money(node.balance)}`,
            });
            group.append(
                make("circle", { cx: node.x, cy: node.y, r: R }),
                make("text", { x: node.x, y: node.y - 2, "text-anchor": "middle", class: "graph-name" }, node.name.slice(0, 12)),
                make("text", { x: node.x, y: node.y + 14, "text-anchor": "middle", class: "graph-balance" },
                    `${node.balance > 0 ? "+" : ""}${fr(node.balance)}`),
            );
            const choose = () => {
                selected = selected === node.id ? null : node.id;
                render();
                showDetails();
                svg.querySelector(`[data-id="${node.id}"]`)?.focus();
            };
            group.addEventListener("click", choose);
            group.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    choose();
                }
            });
            children.push(group);
        });
        svg.replaceChildren(...children);

        list.replaceChildren(
            ...(current.length
                ? current.map((e) => {
                    const li = document.createElement("li");
                    li.textContent = `${byId[e.from].name} doit ${money(e.amount)} à ${byId[e.to].name}`;
                    return li;
                })
                : [Object.assign(document.createElement("li"), { textContent: "Aucune dette." })])
        );

        const saved = data.before.length - data.after.length;
        summary.textContent = mode() === "before"
            ? `${data.before.length} dette${data.before.length > 1 ? "s" : ""} entre paires de membres, avant simplification.`
            : `${data.after.length} virement${data.after.length > 1 ? "s" : ""} suffi${data.after.length > 1 ? "sent" : "t"} ` +
              `(au plus n − 1 = ${Math.max(n - 1, 0)})` +
              (saved > 0 ? `, soit ${saved} de moins que les dettes brutes.` : ".");
    }

    // Construit avec le DOM (textContent) : les noms venant de la base ne sont jamais interprétés en HTML
    const el = (tag, text, className) => {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    };

    function transferList(items, otherKey, verb, preposition) {
        const ul = el("ul");
        if (!items.length) ul.append(el("li", "rien", "muted"));
        for (const e of items) {
            const li = el("li", `${verb} `);
            li.append(el("strong", money(e.amount)), ` ${preposition} ${byId[e[otherKey]].name}`);
            ul.append(li);
        }
        return ul;
    }

    function showDetails() {
        if (selected === null) {
            details.replaceChildren(el("p", "Cliquez sur un membre pour voir ce qu'il doit et ce qu'on lui doit.", "muted"));
            return;
        }
        const node = byId[selected];
        const balance = node.balance > 0
            ? `Créancier : on lui doit ${money(node.balance)}.`
            : node.balance < 0 ? `Débiteur : doit ${money(-node.balance)}.` : "À l'équilibre.";
        details.replaceChildren(
            el("h3", node.name),
            el("p", balance),
            el("p", "Doit :"),
            transferList(edges().filter((e) => e.from === selected), "to", "verse", "à"),
            el("p", "Reçoit :"),
            transferList(edges().filter((e) => e.to === selected), "from", "reçoit", "de"),
        );
    }

    radios.forEach((radio) => radio.addEventListener("change", () => {
        render();
        showDetails();
    }));
    if (!data.before.length && !data.after.length) {
        summary.textContent = "Aucune dette dans ce groupe pour l'instant.";
    }
    render();
})();
