/** Shared utilities and API for AI Autograder UI */
const API = "";
const DEFAULT_MODEL = "gpt-4.1-mini";

const FETCH_RETRIES = 2;
const FETCH_RETRY_DELAY_MS = 1000;

/**
 * Fetch with retry on network failure or 5xx. Retries up to FETCH_RETRIES times.
 * @param {string} url
 * @param {RequestInit} options
 * @returns {Promise<Response>}
 */
async function fetchWithRetry(url, options = {}) {
    let lastErr;
    for (let attempt = 0; attempt <= FETCH_RETRIES; attempt++) {
        try {
            const r = await fetch(url, options);
            if (r.status >= 500 && attempt < FETCH_RETRIES) {
                await new Promise(resolve => setTimeout(resolve, FETCH_RETRY_DELAY_MS));
                continue;
            }
            return r;
        } catch (e) {
            lastErr = e;
            if (attempt < FETCH_RETRIES) {
                await new Promise(resolve => setTimeout(resolve, FETCH_RETRY_DELAY_MS));
            } else {
                throw e;
            }
        }
    }
    throw lastErr;
}

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

/** FastAPI HTTP error `detail` may be a string or validation array. */
function estimateErrorMessage(data, response) {
    if (!data) return null;
    if (data.error) return data.error;
    if (response && !response.ok) {
        const d = data.detail;
        if (typeof d === "string") return d;
        if (Array.isArray(d))
            return d
                .map((x) =>
                    x && typeof x === "object" && x.msg != null ? String(x.msg) : JSON.stringify(x)
                )
                .join("; ");
        return response.statusText || "Request failed";
    }
    return null;
}

/**
 * @param {object} data Parsed JSON body
 * @param {Response|null} response Optional fetch response (for HTTP errors)
 * @returns {{ kind: "ok" | "error", text: string } | null}
 */
function formatEstimate(data, response) {
    const err = estimateErrorMessage(data, response);
    if (err) return { kind: "error", text: err };
    if (!data) return null;
    const pt = data.prompt_tokens || 0;
    const ct = data.completion_tokens || 0;
    const rawCost = data.cost_usd != null ? data.cost_usd : 0;
    const cost = isNaN(rawCost) ? 0 : rawCost;
    const total = pt + ct;
    let line = `~$${cost.toFixed(2)}, ~${total.toLocaleString()} tokens`;
    if (data.pending_students != null && data.total_parsed != null) {
        const sk = data.skipped_students != null ? `, ${data.skipped_students} already graded` : "";
        line += ` — ${data.pending_students} to grade / ${data.total_parsed} parsed${sk}`;
    }
    if (data.note) line += ` — ${data.note}`;
    return { kind: "ok", text: line };
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
        const r = await fetchWithRetry(API + "/estimate/rubrics");
        const data = await r.json();
        const fmt = formatEstimate(data, r);
        if (!fmt) {
            el.textContent = "";
            el.classList.remove("status-error");
            return;
        }
        el.textContent = `(${fmt.text})`;
        el.classList.toggle("status-error", fmt.kind === "error");
    } catch (_) {
        el.textContent = "";
        el.classList.remove("status-error");
    }
}

async function loadGradeEstimate() {
    const el = document.getElementById("gradeEstimate");
    const hint = document.getElementById("gradeQueueHint");
    if (!el) return;
    try {
        const r = await fetchWithRetry(API + "/estimate/grade");
        const data = await r.json();
        const fmt = formatEstimate(data, r);
        if (!fmt) {
            el.textContent = "";
            el.classList.remove("status-error");
        } else {
            el.textContent = `(${fmt.text})`;
            el.classList.toggle("status-error", fmt.kind === "error");
        }
        if (hint) {
            const err = fmt && fmt.kind === "error";
            if (err) {
                hint.textContent = "";
                hint.classList.add("hidden");
            } else if (data.pending_students != null && data.total_parsed != null) {
                const pend = data.pending_students;
                const tot = data.total_parsed;
                const sk = data.skipped_students != null ? data.skipped_students : Math.max(0, tot - pend);
                hint.textContent =
                    pend === 0
                        ? `No one left to grade (${tot} parsed; ${sk} already graded). Remove graded_results.json to re-grade all, or use grade-only merge for partial regrades.`
                        : `Resume: ${pend} student(s) still need grading (${sk} skipped — already graded). Next in queue after you start.`;
                hint.classList.remove("hidden");
            } else {
                hint.textContent = "";
                hint.classList.add("hidden");
            }
        }
    } catch (_) {
        el.textContent = "";
        el.classList.remove("status-error");
        if (hint) {
            hint.textContent = "";
            hint.classList.add("hidden");
        }
    }
}

async function loadRegradeEstimate(studentName) {
    const el = document.getElementById("regradeEstimate");
    if (!el || !studentName) return;
    try {
        const r = await fetchWithRetry(API + "/estimate/grade/" + encodeURIComponent(studentName));
        const data = await r.json();
        const fmt = formatEstimate(data, r);
        if (!fmt) {
            el.textContent = "";
            el.classList.remove("status-error");
            return;
        }
        el.textContent = `(${fmt.text})`;
        el.classList.toggle("status-error", fmt.kind === "error");
    } catch (_) {
        el.textContent = "";
        el.classList.remove("status-error");
    }
}
