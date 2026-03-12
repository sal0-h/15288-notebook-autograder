/** Shared utilities and API for AI Autograder UI */
const API = "";
const DEFAULT_MODEL = "gpt-5-mini";

function setLoading(btn, loading, text) {
    if (loading) {
        btn.disabled = true;
        const isDark = btn.classList.contains("btn-secondary");
        btn.innerHTML = `<span class="spinner${isDark ? " spinner-dark" : ""}"></span>${text}`;
    } else {
        btn.disabled = false;
        btn.textContent = text;
    }
}

function escHtml(s) {
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function sanitizeQuestionHtml(s) {
    if (typeof DOMPurify === "undefined") return escHtml(s || "");
    return DOMPurify.sanitize(String(s || ""), {
        ALLOWED_TAGS: ["b", "i", "em", "strong", "font", "span", "br", "p", "ul", "ol", "li", "code", "pre", "sub", "sup"],
        ALLOWED_ATTR: ["color", "style"],
    });
}

function formatEstimate(data) {
    if (!data || data.error) return null;
    const pt = data.prompt_tokens || 0;
    const ct = data.completion_tokens || 0;
    const rawCost = data.cost_usd != null ? data.cost_usd : 0;
    const cost = isNaN(rawCost) ? 0 : rawCost;
    const total = pt + ct;
    return `~$${cost.toFixed(2)}, ~${total.toLocaleString()} tokens`;
}

/**
 * Filter question groups to only include groups that contain at least one
 * question in gradeOnly. Returns all groups unchanged when gradeOnly is falsy.
 * @param {string[][]} groups
 * @param {string[]|null|undefined} gradeOnly
 * @returns {string[][]}
 */
function filterGroupsByGradeOnly(groups, gradeOnly) {
    if (!gradeOnly || !Array.isArray(gradeOnly) || gradeOnly.length === 0) return groups;
    const set = new Set(gradeOnly);
    return groups.map(g => g.filter(q => set.has(q))).filter(g => g.length > 0);
}

window.filterGroupsByGradeOnly = filterGroupsByGradeOnly;

async function loadRubricEstimate() {
    const el = document.getElementById("rubricEstimate");
    if (!el) return;
    try {
        const r = await fetch(API + "/estimate/rubrics");
        const data = await r.json();
        const txt = formatEstimate(data);
        el.textContent = txt ? `(${txt})` : "";
    } catch (_) { el.textContent = ""; }
}

async function loadGradeEstimate() {
    const el = document.getElementById("gradeEstimate");
    if (!el) return;
    try {
        const r = await fetch(API + "/estimate/grade");
        const data = await r.json();
        const txt = formatEstimate(data);
        el.textContent = txt ? `(${txt})` : "";
    } catch (_) { el.textContent = ""; }
}

async function loadRegradeEstimate(studentName) {
    const el = document.getElementById("regradeEstimate");
    if (!el || !studentName) return;
    try {
        const r = await fetch(API + "/estimate/grade/" + encodeURIComponent(studentName));
        const data = await r.json();
        const txt = formatEstimate(data);
        el.textContent = txt ? `(${txt})` : "";
    } catch (_) { el.textContent = ""; }
}
