/** AI Autograder - main app logic (setup, gather, parse, rubrics, grade, review, export) */

function switchToTab(tabEl) {
    if (!tabEl || !tabEl.dataset.tab) return;
    const tabId = tabEl.dataset.tab;
    document.querySelectorAll(".tab[data-tab]").forEach(x => {
        x.classList.toggle("active", x === tabEl);
        x.setAttribute("aria-selected", x === tabEl ? "true" : "false");
        x.setAttribute("tabindex", x === tabEl ? "0" : "-1");
    });
    document.querySelectorAll(".panel").forEach(p => {
        const isActive = p.id === "panel-" + tabId;
        p.classList.toggle("active", isActive);
        p.setAttribute("aria-hidden", isActive ? "false" : "true");
    });
    if (tabId === "rubrics") { loadRubricsForEdit(); loadRubricEstimate(); }
    if (tabId === "grade") { loadGradeEstimate(); if (!gradeStreamActive) document.getElementById("gradeProgress").innerHTML = ""; }
    tabEl.focus();
}

document.querySelectorAll(".tab[data-tab]").forEach(t => {
    t.setAttribute("aria-selected", t.classList.contains("active") ? "true" : "false");
    t.setAttribute("tabindex", t.classList.contains("active") ? "0" : "-1");
    t.onclick = () => switchToTab(t);
    t.onkeydown = (e) => {
        const tabs = Array.from(document.querySelectorAll(".tab[data-tab]"));
        const idx = tabs.indexOf(t);
        if (e.key === "ArrowRight" || e.key === "ArrowDown") {
            e.preventDefault();
            switchToTab(tabs[(idx + 1) % tabs.length]);
        } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
            e.preventDefault();
            switchToTab(tabs[(idx - 1 + tabs.length) % tabs.length]);
        } else if (e.key === "Home") {
            e.preventDefault();
            switchToTab(tabs[0]);
        } else if (e.key === "End") {
            e.preventDefault();
            switchToTab(tabs[tabs.length - 1]);
        } else if (e.key === " " || e.key === "Enter") {
            e.preventDefault();
            switchToTab(t);
        }
    };
});

document.querySelectorAll(".panel").forEach(p => {
    p.setAttribute("aria-hidden", p.classList.contains("active") ? "false" : "true");
});

// ==================== SETUP ====================
let setupQuestionGroups = [];
let setupConfig = {};

const filterGroupsForUi = window.filterGroupsByGradeOnly || function(groups, gradeOnly) {
    if (!gradeOnly || !Array.isArray(gradeOnly) || gradeOnly.length === 0) return groups;
    const set = new Set(gradeOnly);
    return groups.map(g => g.filter(q => set.has(q))).filter(g => g.length > 0);
};

function _populateSetupFields(cfg) {
    setupConfig = cfg;
    document.getElementById("setupAssignmentName").value = setupConfig.assignment_name || "";
    const w = Math.max(1, parseInt(setupConfig.workers, 10) || 1);
    const slider = document.getElementById("setupWorkers");
    const input = document.getElementById("setupWorkersInput");
    slider.value = Math.min(32, w);
    input.value = w;
    document.getElementById("setupSolutionPath").textContent = setupConfig.solution_notebook ? "✓ " + setupConfig.solution_notebook : "";
    const modelSelect = document.getElementById("setupModel");
    const model = setupConfig.model || DEFAULT_MODEL;
    const hasOpt = Array.from(modelSelect.options).some(o => o.value === model);
    if (hasOpt) modelSelect.value = model;
    else {
        const opt = document.createElement("option");
        opt.value = model;
        opt.textContent = model;
        modelSelect.appendChild(opt);
        modelSelect.value = model;
    }
    const rubricModelSelect = document.getElementById("setupRubricModel");
    const rubricModel = setupConfig.rubric_model || "";
    const hasRubricOpt = Array.from(rubricModelSelect.options).some(o => o.value === rubricModel);
    if (hasRubricOpt) rubricModelSelect.value = rubricModel;
    else if (rubricModel) {
        const opt = document.createElement("option");
        opt.value = rubricModel;
        opt.textContent = rubricModel;
        rubricModelSelect.appendChild(opt);
        rubricModelSelect.value = rubricModel;
    } else {
        rubricModelSelect.value = "";
    }
    document.getElementById("setupRubricReviewCheck").checked = setupConfig.rubric_review !== false;
    document.getElementById("setupIncludeReferenceCheck").checked = setupConfig.include_reference_in_grading === true;
    setupQuestionGroups = (setupConfig.grading || {}).question_groups || [];
    const gradeOnly = (setupConfig.grading || {}).grade_only;
    const gradeOnlyMerge = (setupConfig.grading || {}).grade_only_merge === true;
    document.getElementById("setupGradeOnlyCheck").checked = !!gradeOnly && gradeOnly.length > 0;
    document.getElementById("setupGradeOnlyInput").value = gradeOnly ? gradeOnly.join(", ") : "";
    document.getElementById("setupGradeOnlyInput").classList.toggle("hidden", !gradeOnly || gradeOnly.length === 0);
    document.getElementById("setupGradeOnlyMergeCheck").checked = gradeOnlyMerge;
    document.getElementById("setupGradeOnlyMergeWrap").classList.toggle("hidden", !gradeOnly || gradeOnly.length === 0);
    renderSetupGroups();
    updateOutputDirHint();
}

document.getElementById("setupLoadBtn").onclick = async () => {
    const name = document.getElementById("setupAssignmentName").value.trim();
    const statusEl = document.getElementById("setupLoadStatus");
    if (!name) {
        statusEl.innerHTML = `<span class="status-error">Please enter an assignment name.</span>`;
        return;
    }
    const btn = document.getElementById("setupLoadBtn");
    setLoading(btn, true, "Loading…");
    statusEl.innerHTML = `<span style="color:#64748b">Loading…</span>`;
    try {
        const r = await fetchWithRetry(API + "/load-or-create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ assignment_name: name }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || "Server error");
        _populateSetupFields(data.config);
        document.getElementById("setupConfigFields").classList.remove("hidden");
        const icon = data.created ? "✨" : "✓";
        const verb = data.created ? "Created new config" : "Loaded existing config";
        statusEl.innerHTML = `<span class="status-ok">${icon} ${escHtml(verb)} for <strong>${escHtml(data.assignment_name)}</strong></span>`;
    } catch (e) {
        statusEl.innerHTML = `<span class="status-error">Error: ${escHtml(e.message)}</span>`;
    } finally {
        setLoading(btn, false, "Load / Create");
    }
};

function updateOutputDirHint() {
    const name = document.getElementById("setupAssignmentName").value.trim() || "default";
    const safe = name.replace(/[/\\:*?"<>|.]/g, "_").replace(/_+$/, "") || "default";
    document.getElementById("setupOutputDirHint").textContent = "Output: output/" + safe + "/";
}

let dragQchip = null, dragGroupIdx = null, dragGroupCard = null;

function renderSetupGroups() {
    const container = document.getElementById("setupGroupsContainer");
    container.innerHTML = setupQuestionGroups.map((group, gi) => `
        <div class="setup-group-card" data-group-idx="${gi}" data-drag-type="group">
            <div class="setup-group-header">
                <span class="setup-group-handle" draggable="true" data-group-idx="${gi}" title="Drag to reorder groups">⋮⋮</span>
                <span class="q-section-label">Group ${gi + 1}</span>
                <button class="btn btn-secondary" style="padding:4px 10px;margin-left:auto" onclick="removeSetupGroup(${gi})">Remove</button>
            </div>
            <div class="setup-group-questions" data-group-idx="${gi}">
                ${group.map(qid => `<span class="setup-q-chip" draggable="true" data-qid="${escHtml(qid)}" data-group-idx="${gi}">${escHtml(qid)}</span>`).join("")}
                ${group.length === 0 ? '<span style="font-size:0.85rem;color:#94a3b8">Drop questions here</span>' : ""}
            </div>
        </div>
    `).join("");
    attachGroupDragListeners();
}

function attachGroupDragListeners() {
    document.querySelectorAll(".setup-q-chip").forEach(el => {
        el.ondragstart = e => { dragQchip = { qid: el.dataset.qid, groupIdx: parseInt(el.dataset.groupIdx, 10) }; el.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", el.dataset.qid); };
        el.ondragend = () => { el.classList.remove("dragging"); dragQchip = null; document.querySelectorAll(".setup-group-card").forEach(c => c.classList.remove("drag-over")); };
    });
    document.querySelectorAll(".setup-group-handle").forEach(handle => {
        handle.ondragstart = e => { dragGroupCard = parseInt(handle.dataset.groupIdx, 10); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", "group"); handle.closest(".setup-group-card").classList.add("dragging"); };
        handle.ondragend = () => { document.querySelectorAll(".setup-group-card").forEach(c => c.classList.remove("dragging")); dragGroupCard = null; };
    });
    document.querySelectorAll(".setup-group-card").forEach(card => {
        card.ondragover = e => { e.preventDefault(); if (dragQchip !== null || dragGroupCard !== null) card.classList.add("drag-over"); };
        card.ondragleave = () => card.classList.remove("drag-over");
        card.ondrop = e => {
            e.preventDefault();
            card.classList.remove("drag-over");
            if (dragGroupCard !== null) {
                const targetIdx = parseInt(card.dataset.groupIdx, 10);
                if (targetIdx !== dragGroupCard) {
                    const [g] = setupQuestionGroups.splice(dragGroupCard, 1);
                    setupQuestionGroups.splice(targetIdx > dragGroupCard ? targetIdx - 1 : targetIdx, 0, g);
                    renderSetupGroups();
                }
            } else if (dragQchip !== null) {
                moveQuestion(dragQchip.groupIdx, dragQchip.qid, parseInt(card.dataset.groupIdx, 10));
                dragQchip = null;
            }
        };
    });
}

function moveQuestion(fromIdx, qid, toIdx) {
    const fromGroup = setupQuestionGroups[fromIdx];
    const fromIdxQ = fromGroup.indexOf(qid);
    if (fromIdxQ < 0) return;
    fromGroup.splice(fromIdxQ, 1);
    if (fromIdx === toIdx) fromGroup.push(qid);
    else {
        setupQuestionGroups[toIdx].push(qid);
        if (fromGroup.length === 0) setupQuestionGroups.splice(fromIdx, 1);
    }
    renderSetupGroups();
}

window.removeSetupGroup = function(gi) {
    setupQuestionGroups.splice(gi, 1);
    renderSetupGroups();
};

document.getElementById("setupAssignmentName").oninput = updateOutputDirHint;
document.getElementById("setupSolutionUpload").onclick = () => document.getElementById("setupSolutionFile").click();
document.getElementById("setupSolutionFile").onchange = e => {
    document.getElementById("setupParseUploadBtn").disabled = !e.target.files.length;
    document.getElementById("setupSolutionLabel").textContent = e.target.files.length ? e.target.files[0].name : "Upload .ipynb or use existing";
};
document.getElementById("setupWorkers").oninput = e => {
    const v = parseInt(e.target.value, 10);
    document.getElementById("setupWorkersInput").value = v;
};
document.getElementById("setupWorkersInput").oninput = e => {
    let v = parseInt(e.target.value, 10);
    if (!Number.isNaN(v) && v >= 1) {
        const slider = document.getElementById("setupWorkers");
        slider.value = Math.min(32, v);
    }
};
document.getElementById("setupGradeOnlyCheck").onchange = e => {
    const show = e.target.checked;
    document.getElementById("setupGradeOnlyInput").classList.toggle("hidden", !show);
    document.getElementById("setupGradeOnlyMergeWrap").classList.toggle("hidden", !show);
};

document.getElementById("setupParseUploadBtn").onclick = async () => {
    const name = document.getElementById("setupAssignmentName").value.trim();
    const file = document.getElementById("setupSolutionFile").files[0];
    if (!name || !file) {
        document.getElementById("setupResults").innerHTML = `<p class="status-warning">Enter assignment name and select solution notebook.</p>`;
        return;
    }
    const btn = document.getElementById("setupParseUploadBtn");
    setLoading(btn, true, "Parsing…");
    document.getElementById("setupDuplicateWarning").classList.add("hidden");
    document.getElementById("setupResults").innerHTML = "";
    try {
        const fd = new FormData();
        fd.append("assignment_name", name);
        fd.append("solution_file", file);
        const r = await fetchWithRetry(API + "/parse-solution-upload", { method: "POST", body: fd });
        const data = await r.json();
        if (data.detail) throw new Error(data.detail);
        setupConfig.assignment_name = data.assignment_name;
        setupConfig.solution_notebook = data.solution_notebook;
        setupQuestionGroups = data.suggested_groups || [];
        setupConfig.grading = setupConfig.grading || {};
        setupConfig.grading.question_groups = setupQuestionGroups;
        document.getElementById("setupSolutionPath").textContent = "✓ " + data.solution_notebook;
        setupConfig.workers = Math.max(1, parseInt(document.getElementById("setupWorkersInput").value, 10) || 1);
        if (data.duplicate_qids && data.duplicate_qids.length) {
            const w = document.getElementById("setupDuplicateWarning");
            w.textContent = "Duplicate question IDs detected: " + data.duplicate_qids.join(", ") + ". Review the solution notebook.";
            w.classList.remove("hidden");
        }
        document.getElementById("setupGroupsEditor").classList.remove("hidden");
        renderSetupGroups();
        document.getElementById("setupResults").innerHTML = `<p class="status-ok">✓ Found ${data.question_ids?.length || 0} questions in ${Object.keys(data.sections || {}).length} sections.</p>`;
    } catch (e) {
        document.getElementById("setupResults").innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Parse uploaded solution");
        document.getElementById("setupParseUploadBtn").disabled = !document.getElementById("setupSolutionFile").files.length;
    }
};

document.getElementById("setupParseExistingBtn").onclick = async () => {
    const btn = document.getElementById("setupParseExistingBtn");
    setLoading(btn, true, "Parsing…");
    document.getElementById("setupDuplicateWarning").classList.add("hidden");
    document.getElementById("setupResults").innerHTML = "";
    try {
        const r = await fetchWithRetry(API + "/parse-solution", { method: "POST" });
        const data = await r.json();
        if (data.detail) throw new Error(data.detail);
        setupQuestionGroups = data.suggested_groups || [];
        setupConfig.grading = setupConfig.grading || {};
        setupConfig.grading.question_groups = setupQuestionGroups;
        if (data.duplicate_qids && data.duplicate_qids.length) {
            const w = document.getElementById("setupDuplicateWarning");
            w.textContent = "Duplicate question IDs detected: " + data.duplicate_qids.join(", ") + ". Review the solution notebook.";
            w.classList.remove("hidden");
        }
        document.getElementById("setupGroupsEditor").classList.remove("hidden");
        renderSetupGroups();
        document.getElementById("setupResults").innerHTML = `<p class="status-ok">✓ Found ${data.question_ids?.length || 0} questions.</p>`;
    } catch (e) {
        document.getElementById("setupResults").innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Parse existing solution");
    }
};

document.getElementById("setupAddGroupBtn").onclick = () => {
    setupQuestionGroups.push([]);
    renderSetupGroups();
};

document.getElementById("setupSaveBtn").onclick = async () => {
    setupQuestionGroups = setupQuestionGroups.filter(g => g.length);
    setupConfig.assignment_name = document.getElementById("setupAssignmentName").value.trim() || setupConfig.assignment_name || "default";
    setupConfig.model = document.getElementById("setupModel").value;
    setupConfig.rubric_model = document.getElementById("setupRubricModel").value || "";
    setupConfig.rubric_review = document.getElementById("setupRubricReviewCheck").checked;
    setupConfig.include_reference_in_grading = document.getElementById("setupIncludeReferenceCheck").checked;
    setupConfig.workers = Math.max(1, parseInt(document.getElementById("setupWorkersInput").value, 10) || 1);
    setupConfig.grading = setupConfig.grading || {};
    setupConfig.grading.question_groups = setupQuestionGroups;
    const gradeOnlyCheck = document.getElementById("setupGradeOnlyCheck").checked;
    const gradeOnlyInput = document.getElementById("setupGradeOnlyInput").value.trim();
    setupConfig.grading.grade_only = gradeOnlyCheck && gradeOnlyInput ? gradeOnlyInput.split(/[\s,]+/).filter(x => x.trim()) : null;
    setupConfig.grading.grade_only_merge = setupConfig.grading.grade_only ? document.getElementById("setupGradeOnlyMergeCheck").checked : false;
    const btn = document.getElementById("setupSaveBtn");
    setLoading(btn, true, "Saving…");
    try {
        const r = await fetchWithRetry(API + "/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(setupConfig) });
        if (!r.ok) { const err = await r.json(); throw new Error(err.detail || "Save failed"); }
        document.getElementById("setupResults").innerHTML = `<p class="status-ok">✓ Config saved.</p>`;
        loadRubricEstimate();
        loadGradeEstimate();
    } catch (e) {
        document.getElementById("setupResults").innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Save config");
    }
};

// ==================== GATHER ====================
let gatheredDroppedFile = null;
document.getElementById("gatherUpload").onclick = () => document.getElementById("gatherFile").click();
document.getElementById("gatherUpload").ondragover = e => { e.preventDefault(); };
document.getElementById("gatherUpload").ondrop = e => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (!f) return;
    if (f.name.toLowerCase().endsWith(".zip")) {
        gatheredDroppedFile = f;
        document.getElementById("gatherBtn").disabled = false;
        document.getElementById("gatherResults").innerHTML = `<p class="status-ok">Ready: ${escHtml(f.name)}</p>`;
    } else {
        gatheredDroppedFile = null;
        document.getElementById("gatherResults").innerHTML = `<p class="status-warning">Please drop a ZIP file.</p>`;
    }
};
document.getElementById("gatherFile").onchange = e => {
    gatheredDroppedFile = null;
    document.getElementById("gatherBtn").disabled = !e.target.files.length;
};

document.getElementById("gatherBtn").onclick = async () => {
    const file = gatheredDroppedFile || document.getElementById("gatherFile").files[0];
    if (!file) return;
    const btn = document.getElementById("gatherBtn");
    setLoading(btn, true, "Gathering…");
    const fd = new FormData();
    fd.append("zip_file", file);
    try {
        const r = await fetchWithRetry(API + "/gather", { method: "POST", body: fd });
        const data = await r.json();
        const results = data.results || [];
        const ok = results.filter(x => x.status === "ok").length;
        const miss = results.filter(x => x.status === "missing").length;
        const dup = results.filter(x => x.status === "duplicate").length;
        let html = `<p class="status-ok">✓ ${ok} of ${results.length} notebooks gathered</p>`;
        if (miss || dup) html += `<p class="status-warning">${miss} missing, ${dup} duplicate — see table below.</p>`;
        html += "<table><tr><th>Student</th><th>Filename</th><th>Status</th><th>Note</th></tr>";
        results.forEach(x => {
            const cls = x.status === "ok" ? "" : x.status === "duplicate" ? "warning" : "error";
            html += `<tr class="${cls}"><td>${escHtml(x.student_name)}</td><td>${escHtml(x.filename || "—")}</td><td>${x.status}</td><td>${escHtml(x.message || "")}</td></tr>`;
        });
        html += "</table>";
        document.getElementById("gatherResults").innerHTML = html;
    } catch (e) {
        document.getElementById("gatherResults").innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Gather");
        btn.disabled = false;
    }
};

// ==================== PARSE ====================
document.getElementById("parseBtn").onclick = async () => {
    const btn = document.getElementById("parseBtn");
    setLoading(btn, true, "Parsing…");
    document.getElementById("parseResults").innerHTML = "";
    document.getElementById("parsePreview").classList.add("hidden");
    try {
        const r = await fetchWithRetry(API + "/parse", { method: "POST" });
        const data = await r.json();
        if (data.detail) throw new Error(data.detail);
        // Parsed artifacts changed on disk; avoid showing stale cached answers in Review.
        parsedCache = {};
        const report = data.report || [];
        const ok = report.filter(x => x.status === "ok").length;
        const dupes = data.solution_duplicate_qids || [];
        let html = `<p class="${ok === report.length ? "status-ok" : "status-warning"}">Parsed ${report.length} students — ${ok} OK, ${report.length - ok} with warnings.</p>`;
        if (dupes.length) html += `<p class="status-warning">⚠ Duplicate question IDs in solution: ${dupes.join(", ")}</p>`;
        html += "<table><tr><th>Student</th><th>Status</th><th>Matched / Expected</th><th>Missing</th><th>Unexpected</th><th>Duplicate</th></tr>";
        report.forEach(x => {
            const cls = x.status === "ok" ? "" : x.status === "error" ? "error" : "warning";
            const found = x.questions_found || [];
            const missing = x.questions_missing || [];
            const unexpected = x.questions_unexpected || [];
            const duplicate = x.questions_duplicate || [];
            const expectedCount = typeof x.questions_expected_count === "number" ? x.questions_expected_count : (data.solution_questions || []).length;
            const matchedCount = typeof x.questions_matched_count === "number" ? x.questions_matched_count : Math.max(0, found.length - unexpected.length);
            html += `<tr class="${cls}"><td>${escHtml(x.student_name)}</td><td>${x.status}</td><td>${matchedCount}/${expectedCount}</td><td>${escHtml(missing.join(", ") || "—")}</td><td>${escHtml(unexpected.join(", ") || "—")}</td><td>${escHtml(duplicate.join(", ") || "—")}</td></tr>`;
        });
        html += "</table>";
        document.getElementById("parseResults").innerHTML = html;
        if (data.preview) {
            document.getElementById("parsePreview").classList.remove("hidden");
            const j = JSON.stringify(data.preview, null, 2);
            document.getElementById("parsePreviewContent").textContent = j.length > 6000 ? j.slice(0, 6000) + "\n…" : j;
        }
    } catch (e) {
        document.getElementById("parseResults").innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Run Parse");
    }
};

// ==================== RUBRICS ====================
let rubricQuestionGroups = [];
let fullRubricsCache = {};

async function loadRubricGroups() {
    try {
        const r = await fetchWithRetry(API + "/config");
        const config = await r.json();
        let groups = (config.grading || {}).question_groups || [];
        const gradeOnly = (config.grading || {}).grade_only;
        groups = filterGroupsForUi(groups, gradeOnly);
        rubricQuestionGroups = groups;
        renderRubricGroupCheckboxes(groups);
        return groups;
    } catch (_) {
        rubricQuestionGroups = [];
        renderRubricGroupCheckboxes([]);
        return [];
    }
}

async function loadRubricsForEdit() {
    try {
        const [configRes, rubricsRes] = await Promise.all([fetchWithRetry(API + "/config"), fetchWithRetry(API + "/rubrics")]);
        const config = await configRes.json();
        const rubrics = await rubricsRes.json();
        fullRubricsCache = rubrics;
        let groups = (config.grading || {}).question_groups || [];
        const gradeOnly = (config.grading || {}).grade_only;
        groups = filterGroupsForUi(groups, gradeOnly);
        rubricQuestionGroups = groups;
        renderRubricGroupCheckboxes(groups);
        const gradeOnlySet = gradeOnly && gradeOnly.length ? new Set(gradeOnly) : null;
        const toShow = gradeOnlySet ? Object.fromEntries(Object.entries(rubrics).filter(([k]) => gradeOnlySet.has(k))) : rubrics;
        renderRubricForm(toShow);
    } catch (e) {
        document.getElementById("rubricResults").innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    }
}

function renderRubricGroupCheckboxes(groups) {
    const container = document.getElementById("rubricGroupCheckboxes");
    if (!container) return;
    container.innerHTML = "";
    if (!groups || groups.length === 0) {
        container.appendChild(Object.assign(document.createElement("span"), {
            className: "text-muted",
            style: "font-size:0.9rem",
            textContent: "No question groups (configure in Setup).",
        }));
        return;
    }
    groups.forEach((group, idx) => {
        const label = document.createElement("label");
        label.style.cssText = "display:flex;align-items:center;gap:6px;cursor:pointer;font-size:0.9rem";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.className = "rubric-group-cb";
        cb.dataset.groupIdx = String(idx);
        cb.title = "Generate rubric for this group only (merges, does not overwrite others)";
        label.appendChild(cb);
        label.appendChild(document.createTextNode(`Group ${idx}: ${group.join(", ")}`));
        label.onclick = (e) => { if (e.target === cb) return; cb.checked = !cb.checked; };
        container.appendChild(label);
    });
}

function autoResizeRubricTextarea(ta) {
    ta.style.height = "0";
    ta.style.height = Math.max(ta.scrollHeight, 40) + "px";
}

function renderRubricItemRow(desc, ded) {
    return `<tr class="rubric-item-row">
        <td class="rubric-desc-cell"><textarea class="rubric-item-desc" placeholder="Criterion description">${escHtml(desc || "")}</textarea></td>
        <td class="rubric-ded-cell"><input type="number" class="rubric-item-ded" value="${ded != null ? ded : ""}" step="0.5" min="0" placeholder="0" /></td>
        <td class="rubric-remove-cell"><button type="button" class="btn btn-secondary rubric-remove-item" title="Remove">✕</button></td>
    </tr>`;
}

function updateRubricDeductionSum(block) {
    const pts = parseInt(block.dataset.rubricPts || "0", 10);
    let sum = 0;
    block.querySelectorAll(".rubric-item-ded").forEach(inp => { sum += parseFloat(inp.value) || 0; });
    const span = block.querySelector(".rubric-ded-sum");
    if (span) {
        span.textContent = `${sum.toFixed(1)} / ${pts}`;
        span.classList.toggle("status-error", Math.abs(sum - pts) > 0.01);
        span.classList.toggle("status-ok", Math.abs(sum - pts) <= 0.01 && sum > 0);
    }
}

function renderRubricForm(rubrics) {
    const form = document.getElementById("rubricForm");
    const results = document.getElementById("rubricResults");
    if (!rubrics || Object.keys(rubrics).length === 0) {
        form.classList.add("hidden");
        results.innerHTML = "<p class='status-warning'>No rubrics yet. Run Parse, then click Generate Rubrics.</p>";
        document.getElementById("rubricSaveBtn").disabled = true;
        return;
    }
    form.classList.remove("hidden");
    results.innerHTML = "";
    document.getElementById("rubricSaveBtn").disabled = false;
    const allQids = rubricQuestionGroups.length ? rubricQuestionGroups.flat() : Object.keys(rubrics).sort((a, b) => {
        const pa = a.split(".").map(Number);
        const pb = b.split(".").map(Number);
        return (pa[0] - pb[0]) || ((pa[1] || 0) - (pb[1] || 0));
    });
    let html = "";
    const renderBlock = (qid, r) => {
        const pts = r.points || 0;
        const items = r.items || [];
        const rows = items.length ? items.map(i => renderRubricItemRow(i.description, i.deduction)).join("") : renderRubricItemRow("", "");
        return `<div class="q-block rubric-q-block" style="margin-bottom:16px" data-rubric-q="${escHtml(qid)}" data-rubric-pts="${pts}">
            <div class="q-block-header" style="cursor:default">
                <h4>Q${escHtml(qid)} (${pts} pts) — Deductions: <span class="rubric-ded-sum">0 / ${pts}</span></h4>
            </div>
            <div class="q-block-body open">
                <table style="width:100%;border-collapse:collapse;margin-bottom:8px">
                    <thead><tr><th style="text-align:left;padding:6px 8px;font-size:0.85rem">Description</th><th style="width:90px;padding:6px 8px;font-size:0.85rem">Deduction</th><th style="width:40px"></th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
                <button type="button" class="btn btn-secondary rubric-add-item" style="padding:6px 12px;font-size:0.9rem">+ Add item</button>
            </div>
        </div>`;
    };
    for (const group of rubricQuestionGroups) {
        if (!group.length) continue;
        html += "<div class='q-section mb-2'><div class='q-section-label'>Questions " + escHtml(group.join(", ")) + "</div>";
        for (const qid of group) {
            const r = rubrics[qid] || { points: 0, items: [] };
            html += renderBlock(qid, r);
        }
        html += "</div>";
    }
    if (!rubricQuestionGroups.length) {
        for (const qid of allQids) {
            const r = rubrics[qid] || { points: 0, items: [] };
            html += renderBlock(qid, r);
        }
    }
    form.innerHTML = html;
    form.querySelectorAll(".rubric-item-desc").forEach(ta => {
        autoResizeRubricTextarea(ta);
        ta.addEventListener("input", () => autoResizeRubricTextarea(ta));
    });
    form.querySelectorAll(".rubric-q-block").forEach(block => {
        updateRubricDeductionSum(block);
        block.addEventListener("input", () => updateRubricDeductionSum(block));
        block.querySelector(".rubric-add-item").onclick = () => {
            const tbody = block.querySelector("tbody");
            tbody.insertAdjacentHTML("beforeend", renderRubricItemRow("", ""));
            const lastRow = tbody.querySelector(".rubric-item-row:last-child");
            const newTa = lastRow.querySelector(".rubric-item-desc");
            autoResizeRubricTextarea(newTa);
            newTa.addEventListener("input", () => autoResizeRubricTextarea(newTa));
            lastRow.querySelector(".rubric-remove-item").onclick = () => {
                if (block.querySelectorAll(".rubric-item-row").length > 1) lastRow.remove();
                updateRubricDeductionSum(block);
            };
            updateRubricDeductionSum(block);
        };
        block.querySelectorAll(".rubric-remove-item").forEach(btn => {
            btn.onclick = () => {
                const row = btn.closest(".rubric-item-row");
                if (block.querySelectorAll(".rubric-item-row").length > 1) row.remove();
                updateRubricDeductionSum(block);
            };
        });
    });
}

document.getElementById("rubricGenerateBtn").onclick = async () => {
    const btn = document.getElementById("rubricGenerateBtn");
    const progressDiv = document.getElementById("rubricProgress");
    const resultsDiv = document.getElementById("rubricResults");
    const checked = Array.from(document.querySelectorAll(".rubric-group-cb:checked")).map(cb => parseInt(cb.dataset.groupIdx, 10));
    const groupsParam = checked.length ? "?groups=" + checked.join(",") : "";
    setLoading(btn, true, "Generating…");
    resultsDiv.innerHTML = "";
    progressDiv.innerHTML = "";
    progressDiv.classList.remove("hidden");
    progressDiv.appendChild(Object.assign(document.createElement("div"), { className: "progress-item", innerHTML: "<span>Connecting…</span>" }));
    try {
        const ev = new EventSource(API + "/generate-rubrics" + groupsParam);
        let gotResult = false;
        ev.addEventListener("progress", (e) => {
            const data = JSON.parse(e.data || "{}");
            if (data.status === "progress") {
                const last = progressDiv.querySelector(".progress-item:last-child");
                if (last && last.textContent.includes("Connecting")) last.remove();
                const existing = progressDiv.querySelector(`[data-group-idx="${data.current}"]`);
                if (data.done && existing) {
                    existing.classList.remove("working");
                    existing.innerHTML = `<span>Group ${data.current}/${data.total}</span><span>${(data.group || []).join(", ")}</span>`;
                    existing.classList.add("done");
                } else if (!data.done) {
                    if (!existing) {
                        const div = document.createElement("div");
                        div.className = "progress-item working";
                        div.dataset.groupIdx = data.current;
                        div.innerHTML = `<span class="spinner spinner-dark"></span><span>Group ${data.current}/${data.total}</span><span>${(data.group || []).join(", ")}</span>`;
                        progressDiv.appendChild(div);
                    } else {
                        existing.classList.add("working");
                        existing.innerHTML = `<span class="spinner spinner-dark"></span><span>Group ${data.current}/${data.total}</span><span>${(data.group || []).join(", ")}</span>`;
                    }
                }
                progressDiv.scrollTop = progressDiv.scrollHeight;
            } else if (data.status === "done" && data.rubrics) {
                gotResult = true;
                fullRubricsCache = data.rubrics;
                const div = document.createElement("div");
                div.className = "progress-item done";
                div.innerHTML = `<span>✓ Generated ${Object.keys(data.rubrics).length} rubrics</span>`;
                progressDiv.appendChild(div);
                resultsDiv.innerHTML = `<p class="status-ok">✓ Generated rubrics for ${Object.keys(data.rubrics).length} questions.</p>`;
                renderRubricForm(data.rubrics);
                progressDiv.classList.add("hidden");
            } else if (data.status === "error") {
                gotResult = true;
                resultsDiv.innerHTML = `<p class="status-error">Error: ${escHtml(data.error || "")}</p>`;
                progressDiv.classList.add("hidden");
            }
        });
        ev.addEventListener("done", () => { ev.close(); progressDiv.classList.add("hidden"); setLoading(btn, false, "Generate Rubrics"); });
        ev.onerror = async () => {
            ev.close();
            if (!gotResult) {
                progressDiv.classList.add("hidden");
                resultsDiv.innerHTML = "<p class='status-warning'>Stream unavailable. Using POST…</p>";
                try {
                    const body = checked.length ? { group_indices: checked } : {};
                    const r = await fetchWithRetry(API + "/generate-rubrics", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body),
                    });
                    const data = await r.json();
                    if (data.detail) throw new Error(data.detail);
                    fullRubricsCache = data.rubrics || {};
                    resultsDiv.innerHTML = `<p class="status-ok">✓ Generated rubrics for ${Object.keys(fullRubricsCache).length} questions.</p>`;
                    loadRubricsForEdit();
                } catch (err) {
                    resultsDiv.innerHTML = `<p class="status-error">Error: ${escHtml(err.message)}</p>`;
                }
            }
            setLoading(btn, false, "Generate Rubrics");
        };
    } catch (e) {
        setLoading(btn, false, "Generate Rubrics");
        progressDiv.classList.add("hidden");
        resultsDiv.innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    }
};

document.getElementById("rubricSaveBtn").onclick = async () => {
    const form = document.getElementById("rubricForm");
    const formRubrics = {};
    form.querySelectorAll(".rubric-q-block").forEach(block => {
        const qid = block.dataset.rubricQ;
        const pts = parseInt(block.dataset.rubricPts || "0", 10);
        const items = [];
        block.querySelectorAll(".rubric-item-row").forEach(row => {
            const desc = (row.querySelector(".rubric-item-desc")?.value || "").trim();
            const ded = parseFloat(row.querySelector(".rubric-item-ded")?.value) || 0;
            if (desc || ded > 0) items.push({ description: desc || "[unnamed]", deduction: ded });
        });
        formRubrics[qid] = { points: pts, items };
    });
    const rubrics = { ...fullRubricsCache, ...formRubrics };
    const btn = document.getElementById("rubricSaveBtn");
    setLoading(btn, true, "Saving…");
    try {
        const r = await fetchWithRetry(API + "/rubrics", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(rubrics) });
        if (!r.ok) { const err = await r.json(); throw new Error(err.detail || "Save failed"); }
        document.getElementById("rubricResults").innerHTML = `<p class="status-ok">✓ Rubrics saved.</p>`;
    } catch (e) {
        document.getElementById("rubricResults").innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Save Rubrics");
    }
};

document.getElementById("rubricClearBtn").onclick = async () => {
    if (!confirm("Clear all rubrics? This cannot be undone.")) return;
    const btn = document.getElementById("rubricClearBtn");
    setLoading(btn, true, "Clearing…");
    try {
        const r = await fetchWithRetry(API + "/rubrics", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
        if (!r.ok) { const err = await r.json(); throw new Error(err.detail || "Clear failed"); }
        document.getElementById("rubricResults").innerHTML = `<p class="status-ok">✓ All rubrics cleared.</p>`;
        document.getElementById("rubricForm").innerHTML = "";
        document.getElementById("rubricForm").classList.add("hidden");
        document.getElementById("rubricSaveBtn").disabled = true;
    } catch (e) {
        document.getElementById("rubricResults").innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Clear All Rubrics");
    }
};

// ==================== GRADE ====================
let gradeStreamActive = false;
let gradeEv = null;
let gradePollInterval = null;

function stopGradePolling() {
    if (gradePollInterval) { clearInterval(gradePollInterval); gradePollInterval = null; }
}

async function pollGradeProgress() {
    if (!gradeStreamActive) return;
    try {
        const [statusRes, resultsRes] = await Promise.all([
            fetchWithRetry(API + "/grade/status"),
            fetchWithRetry(API + "/results")
        ]);
        const status = await statusRes.json();
        if (!status.in_progress) {
            gradeStreamActive = false;
            gradeEv = null;
            stopGradePolling();
            document.getElementById("gradeBtn").disabled = false;
            document.getElementById("gradeBtn").textContent = "Start Grading";
            return;
        }
        const results = await resultsRes.json();
        const list = document.getElementById("gradeProgress");
        const shown = new Set([...list.querySelectorAll(".progress-item")].map(el => el.dataset.student || "").filter(Boolean));
        for (const r of results) {
            const name = r.student_name;
            if (!name || shown.has(name)) continue;
            shown.add(name);
            const div = document.createElement("div");
            div.className = "progress-item done";
            div.dataset.student = name;
            div.innerHTML = `<span>${escHtml(name)}</span><span style="font-weight:600">${r.total_score}/${r.total_max}</span>`;
            list.appendChild(div);
        }
        list.scrollTop = list.scrollHeight;
    } catch (_) {}
}

document.getElementById("gradeBtn").onclick = async () => {
    const list = document.getElementById("gradeProgress");
    list.innerHTML = "";
    const btn = document.getElementById("gradeBtn");
    setLoading(btn, true, "Grading…");
    gradeStreamActive = true;
    stopGradePolling();
    try {
        const ev = new EventSource(API + "/grade");
        gradeEv = ev;
        ev.addEventListener("progress", e => {
            const data = JSON.parse(e.data || "{}");
            const student = data.student || "";
            if (data.status === "usage") {
                const div = document.createElement("div");
                const u = data.usage || {};
                const cost = data.cost_usd != null ? `$${data.cost_usd}` : "";
                div.className = "progress-item done";
                div.innerHTML = `<span>Token usage: ${(u.prompt_tokens || 0).toLocaleString()} in / ${(u.completion_tokens || 0).toLocaleString()} out</span><span style="font-weight:600">${cost}</span>`;
                list.appendChild(div);
            } else if (data.status === "working") {
                const div = document.createElement("div");
                div.className = "progress-item working";
                div.dataset.student = student;
                div.innerHTML = `<span class="spinner spinner-dark"></span><span>Grading ${escHtml(student)}…</span>`;
                list.appendChild(div);
            } else if (data.status === "done" && data.result) {
                const r = data.result;
                const existing = list.querySelector(`[data-student="${CSS.escape(r.student_name)}"]`);
                const div = existing || document.createElement("div");
                div.className = "progress-item done";
                div.dataset.student = r.student_name;
                div.innerHTML = `<span>${escHtml(r.student_name)}</span><span style="font-weight:600">${r.total_score}/${r.total_max}</span>`;
                if (!existing) list.appendChild(div);
            } else if (data.status === "error") {
                const existing = list.querySelector(`[data-student="${CSS.escape(student)}"]`);
                const div = existing || document.createElement("div");
                div.className = "progress-item error";
                div.dataset.student = student;
                div.innerHTML = `<span>${escHtml(student)}</span><span>Error: ${escHtml(data.error || "")}</span>`;
                if (!existing) list.appendChild(div);
            }
            list.scrollTop = list.scrollHeight;
        });
        ev.addEventListener("done", () => {
            ev.close();
            gradeEv = null;
            gradeStreamActive = false;
            stopGradePolling();
            setLoading(btn, false, "Start Grading");
        });
        ev.onerror = () => {
            ev.close();
            gradeEv = null;
            gradeStreamActive = false;
            stopGradePolling();
            setLoading(btn, false, "Start Grading");
            if (list.querySelectorAll(".progress-item").length === 0) {
                list.innerHTML = "<p class='status-warning'>Connection failed. Grading may already be in progress in another tab.</p>";
            }
        };
    } catch (e) {
        gradeStreamActive = false;
        gradeEv = null;
        stopGradePolling();
        list.innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
        setLoading(btn, false, "Start Grading");
    }
};

document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    if (!gradeStreamActive || !gradeEv) return;
    if (gradeEv.readyState === 2) {
        gradeEv = null;
        pollGradeProgress();
        gradePollInterval = setInterval(pollGradeProgress, 2000);
    }
});

// ==================== REVIEW ====================
let reviewData = [];
let parsedCache = {};
let calibrationData = [];
let currentReviewIdx = -1;
let currentQids = [];
let currentQidIdx = 0;
let reviewDirty = false;

function calibrationLookup() {
    const m = {};
    for (const f of calibrationData) m[f.student_name + "|" + f.qid] = f;
    return m;
}

function renderMarkdown(md) {
    if (!md || !String(md).trim()) return "";
    const html = typeof marked !== "undefined" ? marked.parse(md) : escHtml(md);
    return typeof DOMPurify !== "undefined"
        ? DOMPurify.sanitize(html, { ALLOWED_TAGS: ["b", "i", "em", "strong", "p", "ul", "ol", "li", "code", "pre", "br", "h1", "h2", "h3", "span"], ALLOWED_ATTR: ["class"] })
        : escHtml(html);
}

function getGradedQidsForStudent(s) {
    const qs = s.questions || {};
    const graded = Object.keys(qs).filter(qid => (qs[qid].feedback || "") !== "[skipped - not in grade_only]");
    return graded.sort((a, b) => {
        const pa = a.split(".").map(Number);
        const pb = b.split(".").map(Number);
        return (pa[0] - pb[0]) || ((pa[1] || 0) - (pb[1] || 0));
    });
}

function goNextStudent() {
    if (reviewData.length === 0) return;
    if (reviewDirty && !confirm("Discard unsaved changes?")) return;
    reviewDirty = false;
    currentReviewIdx = Math.min(currentReviewIdx + 1, reviewData.length - 1);
    showReviewDetail(currentReviewIdx);
    renderStudentList();
    scrollStudentIntoView();
}

function goPrevStudent() {
    if (reviewData.length === 0) return;
    if (reviewDirty && !confirm("Discard unsaved changes?")) return;
    reviewDirty = false;
    currentReviewIdx = Math.max(currentReviewIdx - 1, 0);
    showReviewDetail(currentReviewIdx);
    renderStudentList();
    scrollStudentIntoView();
}

function goNextQuestion() {
    if (currentQids.length === 0) return;
    currentQidIdx = Math.min(currentQidIdx + 1, currentQids.length - 1);
    renderQPills();
    renderReviewContent();
}

function goPrevQuestion() {
    if (currentQids.length === 0) return;
    currentQidIdx = Math.max(currentQidIdx - 1, 0);
    renderQPills();
    renderReviewContent();
}

function scrollStudentIntoView() {
    const el = document.querySelector(`.student-item[data-idx="${currentReviewIdx}"]`);
    if (el) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function loadReviewAndCalibration() {
    const [resultsRes, calRes] = await Promise.all([fetchWithRetry(API + "/results"), fetchWithRetry(API + "/calibration")]);
    reviewData = await resultsRes.json();
    if (!Array.isArray(reviewData)) reviewData = [];
    calibrationData = await calRes.json();
    if (!Array.isArray(calibrationData)) calibrationData = [];
}

function isStudentFlagged(s) {
    return getStudentFlagReasons(s).length > 0;
}

function getStudentFlagReasons(s) {
    const reasons = [];
    const qs = s.questions || {};
    const gradedQids = Object.keys(qs).filter(qid => (qs[qid].feedback || "") !== "[skipped - not in grade_only]");
    for (const qid of gradedQids) {
        const q = qs[qid];
        if (q.requires_review) reasons.push({ qid, type: "needs_review" });
        else if ((q.confidence || "").toLowerCase() === "low") reasons.push({ qid, type: "low_confidence" });
    }
    const lookup = calibrationLookup();
    for (const qid of gradedQids) {
        const o = lookup[s.student_name + "|" + qid];
        if (o) reasons.push({ qid, type: "outlier", flag_reason: o.flag_reason });
    }
    return reasons;
}

function formatFlagReasons(reasons) {
    const seen = new Set();
    const parts = [];
    for (const r of reasons) {
        let label = "";
        if (r.type === "needs_review" && !seen.has("needs_review")) { label = "needs review"; seen.add("needs_review"); }
        else if (r.type === "low_confidence" && !seen.has("low_confidence")) { label = "low confidence"; seen.add("low_confidence"); }
        else if (r.type === "outlier" && !seen.has("outlier_" + r.flag_reason)) {
            label = r.flag_reason === "low" ? "outlier (below mean)" : "outlier (above mean)";
            seen.add("outlier_" + (r.flag_reason || ""));
        }
        if (label) parts.push(label);
    }
    return parts.join(", ");
}

function renderStudentList() {
    const list = document.getElementById("studentList");
    if (!list) return;
    const flaggedFirst = document.getElementById("reviewFlaggedFirst")?.checked || false;
    let order = reviewData.map((_, i) => i);
    if (flaggedFirst) {
        order.sort((a, b) => (isStudentFlagged(reviewData[b]) ? 1 : 0) - (isStudentFlagged(reviewData[a]) ? 1 : 0));
    }
    list.innerHTML = order.map(i => {
        const s = reviewData[i];
        const reasons = getStudentFlagReasons(s);
        const flagged = reasons.length > 0;
        const reasonText = formatFlagReasons(reasons);
        const isDirty = i === currentReviewIdx && reviewDirty;
        const dirtyDot = isDirty ? '<span class="review-v2-dirty" title="Unsaved changes">●</span> ' : "";
        return `<div class="student-item ${flagged ? "flagged" : ""} ${i === currentReviewIdx ? "selected" : ""}" data-idx="${i}" title="${escHtml(reasonText) || ""}" tabindex="0" role="button"><span class="sname">${dirtyDot}${flagged ? "⚠ " : ""}${escHtml(s.student_name)}</span><span class="sscore">${s.total_score} / ${s.total_max}</span>${flagged ? `<span class="sscore" style="font-size:0.75rem;color:#d97706;">${escHtml(reasonText)}</span>` : ""}</div>`;
    }).join("");
    list.querySelectorAll(".student-item").forEach(el => {
        const go = () => {
            const idx = parseInt(el.dataset.idx);
            if (idx !== currentReviewIdx && reviewDirty && !confirm("Discard unsaved changes?")) return;
            if (idx !== currentReviewIdx) reviewDirty = false;
            showReviewDetail(idx);
        };
        el.onclick = go;
        el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
    });
}

window.toggleBlock = function(qid) {
    const body = document.getElementById("qbody-" + qid);
    if (body) body.classList.toggle("open");
};

window.recalcTotal = function() {
    const s = currentReviewIdx >= 0 ? reviewData[currentReviewIdx] : null;
    if (!s) return;
    const qs = s.questions || {};
    let total = 0;
    const currentQid = currentQids[currentQidIdx];
    const dedInput = document.querySelector(`#reviewDetail input[data-q-ded="${currentQid}"]`);
    for (const qid of currentQids) {
        if (qid === currentQid && dedInput && qs[qid]) {
            const max = qs[qid].max || 0;
            const ded = parseFloat(dedInput.value) || 0;
            total += Math.max(0, max - ded);
        } else if (qs[qid]) {
            total += qs[qid].score || 0;
        }
    }
    const el = document.getElementById("totalDisplay");
    if (el) el.textContent = Math.round(total * 100) / 100;
};

function renderQuestionContent(qid, q, parsed, studentName) {
    let qMarkdown = "", studentCode = "", studentOutput = "", studentMd = "";
    if (parsed) {
        for (const secData of Object.values(parsed.sections || {})) {
            if (secData.questions && secData.questions[qid]) {
                const pq = secData.questions[qid];
                qMarkdown = pq.question_markdown || "";
                studentCode = pq.answer_code_concat || "";
                studentOutput = pq.answer_text_concat || "";
                studentMd = pq.answer_markdown_concat || "";
                break;
            }
        }
    }
    let html = "";
    if (qMarkdown) html += `<div class="review-v2-section"><div class="review-v2-section-label">Question</div><div class="review-v2-qstatement">${renderMarkdown(qMarkdown)}</div></div>`;
    if (studentCode) {
        html += `<div class="review-v2-section"><div class="review-v2-section-label">Student Code</div><pre class="review-v2-code"><code class="language-python">${escHtml(studentCode)}</code></pre></div>`;
    }
    if (studentOutput) {
        const trunc = 600;
        const isLong = studentOutput.length > trunc;
        const show = isLong ? studentOutput.slice(0, trunc) + "\n…" : studentOutput;
        html += `<div class="review-v2-section"><div class="review-v2-section-label">Output</div><pre class="review-v2-output" id="reviewOutput-${qid}">${escHtml(show)}</pre>`;
        if (isLong) html += `<button type="button" class="btn btn-secondary" style="font-size:0.8rem;padding:4px 8px" data-toggle-output="${qid}">Show all</button>`;
        html += `</div>`;
    }
    if (studentMd) html += `<div class="review-v2-section"><div class="review-v2-section-label">Written Answer</div><div class="review-v2-qstatement">${renderMarkdown(studentMd)}</div></div>`;
    return html;
}

function renderReviewContent() {
    const detail = document.getElementById("reviewDetail");
    const s = currentReviewIdx >= 0 ? reviewData[currentReviewIdx] : null;
    if (!s || currentQids.length === 0) {
        detail.innerHTML = `<p class="review-placeholder">No questions to review for this student.</p>`;
        return;
    }
    const qid = currentQids[currentQidIdx];
    const q = s.questions?.[qid];
    if (!q) {
        detail.innerHTML = `<p class="review-placeholder">Question ${qid} not found.</p>`;
        return;
    }
    const parsed = parsedCache[s.student_name];
    const max = q.max || 0;
    const deduction = Math.max(0, max - (q.score || 0));
    const score = max - deduction;
    const outlier = calibrationLookup()[s.student_name + "|" + qid];
    const outlierBanner = outlier ? `<div class="outlier-banner">Statistical outlier (${outlier.flag_reason === "high" ? "above" : "below"} class mean): score ${outlier.score}/${outlier.max || max}, mean ${outlier.mean} ± ${outlier.std}</div>` : "";
    let html = outlierBanner;
    html += renderQuestionContent(qid, q, parsed, s.student_name);
    html += `<div class="review-v2-ded-widget">Max ${max} pts − <input type="number" data-q-ded="${qid}" data-q-max="${max}" value="${deduction}" min="0" max="${max}" step="0.5"> = <span id="reviewScoreDisplay-${qid}">${score}</span></div>`;
    html += `<textarea class="review-v2-feedback-ta" data-q-fb="${qid}" placeholder="Feedback">${escHtml(q.feedback || "")}</textarea>`;
    detail.innerHTML = html;
    const dedInput = detail.querySelector(`input[data-q-ded="${qid}"]`);
    if (dedInput) {
        dedInput.oninput = () => {
            const d = parseFloat(dedInput.value) || 0;
            const sc = Math.max(0, max - d);
            const span = document.getElementById(`reviewScoreDisplay-${qid}`);
            if (span) span.textContent = sc;
            const s = currentReviewIdx >= 0 ? reviewData[currentReviewIdx] : null;
            if (s?.questions?.[qid]) s.questions[qid].score = sc;
            reviewDirty = true;
            recalcTotal();
            renderStudentList();
        };
    }
    const fbTa = detail.querySelector(`textarea[data-q-fb="${qid}"]`);
    if (fbTa) {
        fbTa.oninput = () => {
            const s = currentReviewIdx >= 0 ? reviewData[currentReviewIdx] : null;
            if (s?.questions?.[qid]) s.questions[qid].feedback = fbTa.value;
            reviewDirty = true;
            renderStudentList();
        };
        if (typeof autoResizeRubricTextarea === "function") {
            autoResizeRubricTextarea(fbTa);
            fbTa.addEventListener("input", () => autoResizeRubricTextarea(fbTa));
        }
    }
    detail.querySelectorAll("pre.review-v2-code code").forEach(el => {
        if (typeof hljs !== "undefined") {
            try {
                if (hljs.highlightElement) {
                    hljs.highlightElement(el);
                } else if (hljs.highlight) {
                    const code = el.textContent;
                    const result = hljs.highlight(code, { language: "python" });
                    el.innerHTML = result.value;
                    el.classList.add("hljs");
                }
            } catch (_) { /* fallback: plain code */ }
        }
    });
    recalcTotal();
    const confBadge = document.getElementById("reviewConfidenceBadge");
    if (confBadge) {
        const conf = (q.confidence || "medium").toLowerCase();
        const confClass = conf === "high" ? "badge-high" : conf === "low" ? "badge-low" : "badge-medium";
        confBadge.className = `badge ${confClass}`;
        confBadge.textContent = `conf: ${conf}`;
    }
    detail.querySelectorAll("[data-toggle-output]").forEach(btn => {
        btn.onclick = () => {
            const id = btn.dataset.toggleOutput;
            const pre = document.getElementById("reviewOutput-" + id);
            if (pre) {
                const s = currentReviewIdx >= 0 ? reviewData[currentReviewIdx] : null;
                if (s) {
                    const parsed = parsedCache[s.student_name];
                    let out = "";
                    if (parsed) {
                        for (const secData of Object.values(parsed.sections || {})) {
                            if (secData.questions && secData.questions[id]) {
                                out = secData.questions[id].answer_text_concat || "";
                                break;
                            }
                        }
                    }
                    pre.textContent = out;
                    pre.classList.add("expanded");
                    btn.remove();
                }
            }
        };
    });
}

function renderQPills() {
    const container = document.getElementById("reviewQPills");
    const stuInfo = document.getElementById("reviewStuInfo");
    const totalDisplay = document.getElementById("totalDisplay");
    const totalMaxDisplay = document.getElementById("totalMaxDisplay");
    if (!container) return;
    if (currentQids.length === 0) {
        container.innerHTML = "";
        if (stuInfo) stuInfo.textContent = "Student 0/0";
        if (totalDisplay) totalDisplay.textContent = "0";
        if (totalMaxDisplay) totalMaxDisplay.textContent = "0";
        return;
    }
    const s = reviewData[currentReviewIdx];
    container.innerHTML = currentQids.map((qid, i) =>
        `<button type="button" class="review-v2-qpill ${i === currentQidIdx ? "active" : ""}" data-qidx="${i}">Q${qid}</button>`
    ).join("");
    container.querySelectorAll(".review-v2-qpill").forEach(btn => {
        btn.onclick = () => {
            currentQidIdx = parseInt(btn.dataset.qidx);
            renderReviewContent();
            renderQPills();
        };
    });
    if (stuInfo) stuInfo.textContent = `Student ${currentReviewIdx + 1}/${reviewData.length}`;
    if (totalDisplay) totalDisplay.textContent = s?.total_score ?? "0";
    if (totalMaxDisplay) totalMaxDisplay.textContent = s?.total_max ?? "0";
}

async function showReviewDetail(idx) {
    currentReviewIdx = idx;
    reviewDirty = false;
    const errEl = document.getElementById("reviewError");
    if (errEl) errEl.innerHTML = "";
    const s = reviewData[idx];
    if (!s) return;
    currentQids = getGradedQidsForStudent(s);
    currentQidIdx = currentQidIdx < currentQids.length ? currentQidIdx : 0;
    const detail = document.getElementById("reviewDetail");
    detail.innerHTML = `<p style="color:#64748b;padding:8px">Loading student answers…</p>`;
    let parsed = parsedCache[s.student_name];
    if (!parsed) {
        try {
            const r = await fetchWithRetry(API + "/parsed/" + encodeURIComponent(s.student_name));
            if (r.ok) { parsed = await r.json(); parsedCache[s.student_name] = parsed; }
        } catch (_) {}
    }
    renderQPills();
    renderReviewContent();
    const confBadge = document.getElementById("reviewConfidenceBadge");
    if (confBadge && currentQids.length > 0) {
        const qid = currentQids[currentQidIdx];
        const q = s.questions?.[qid];
        const conf = (q?.confidence || "medium").toLowerCase();
        const confClass = conf === "high" ? "badge-high" : conf === "low" ? "badge-low" : "badge-medium";
        confBadge.className = `badge ${confClass}`;
        confBadge.textContent = `conf: ${conf}`;
        confBadge.title = "LLM confidence in this grade";
    }
    loadRegradeEstimate(s.student_name);
}

window.saveReview = async function() {
    if (currentReviewIdx < 0) return;
    const s = reviewData[currentReviewIdx];
    const qs = JSON.parse(JSON.stringify(s.questions));
    const dedInput = document.querySelector("#reviewDetail input[data-q-ded]");
    if (dedInput && qs[dedInput.dataset.qDed]) {
        const max = parseFloat(dedInput.dataset.qMax) || 0;
        const ded = parseFloat(dedInput.value) || 0;
        qs[dedInput.dataset.qDed].score = Math.max(0, max - ded);
    }
    document.querySelectorAll("#reviewDetail textarea[data-q-fb]").forEach(ta => {
        if (qs[ta.dataset.qFb]) qs[ta.dataset.qFb].feedback = ta.value;
    });
    let total = 0;
    for (const q of Object.values(qs)) total += q.score;
    total = Math.round(total * 100) / 100;
    const updated = { ...s, questions: qs, total_score: total };
    reviewData[currentReviewIdx] = updated;
    reviewDirty = false;
    const fb = document.getElementById("reviewSaveFeedback");
    try {
        const r = await fetchWithRetry(API + "/results/" + encodeURIComponent(s.student_name), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(updated) });
        if (!r.ok) throw new Error(await r.text());
        const el = document.querySelector(`.student-item[data-idx="${currentReviewIdx}"] .sscore`);
        if (el) el.textContent = `${total} / ${s.total_max}`;
        if (fb) { fb.innerHTML = '<span class="status-ok">✓ Saved</span>'; setTimeout(() => { fb.innerHTML = ""; }, 2000); }
        renderStudentList();
    } catch (e) {
        if (fb) fb.innerHTML = `<span class="status-error">Save failed: ${escHtml(e.message)}</span>`;
    }
};

window.regradeStudent = async function() {
    if (currentReviewIdx < 0) return;
    const s = reviewData[currentReviewIdx];
    if (!confirm("Re-grade this student? This will overwrite current scores with new LLM grades.")) return;
    const btn = document.getElementById("regradeStudentBtn");
    if (!btn) return;
    setLoading(btn, true, "Re-grading…");
    try {
        const r = await fetchWithRetry(API + "/grade/" + encodeURIComponent(s.student_name), { method: "POST" });
        const data = await r.json();
        if (data.detail) throw new Error(data.detail);
        if (data.result) {
            const errEl = document.getElementById("reviewError");
            if (errEl) errEl.innerHTML = "";
            reviewData[currentReviewIdx] = data.result;
            showReviewDetail(currentReviewIdx);
            renderStudentList();
        }
    } catch (e) {
        document.getElementById("reviewError").innerHTML = `<p class="status-error">Re-grade failed: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Re-grade this student");
    }
};

document.getElementById("reviewLoadBtn").onclick = async () => {
    const btn = document.getElementById("reviewLoadBtn");
    const msgDiv = document.getElementById("reviewCalibrationResults");
    setLoading(btn, true, "Loading…");
    msgDiv.innerHTML = "";
    try {
        await loadReviewAndCalibration();
        // Reload parsed notebooks from API every time results are reloaded.
        parsedCache = {};
        document.getElementById("reviewLayout").classList.remove("hidden");
        renderStudentList();
        if (reviewData.length) showReviewDetail(0);
        if (calibrationData.length) msgDiv.innerHTML = `<p class="status-warning">Loaded: ${calibrationData.length} outlier(s) from last calibration. Click "Run Calibration" to re-run.</p>`;
    } catch (e) {
        document.getElementById("reviewDetail").innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Load Results");
        btn.disabled = false;
    }
};

document.getElementById("reviewCalibrateBtn").onclick = async () => {
    const btn = document.getElementById("reviewCalibrateBtn");
    const msgDiv = document.getElementById("reviewCalibrationResults");
    setLoading(btn, true, "Calibrating…");
    msgDiv.innerHTML = "";
    try {
        const r = await fetchWithRetry(API + "/calibrate", { method: "POST" });
        const data = await r.json();
        if (data.detail) throw new Error(data.detail);
        calibrationData = data.flagged || [];
        const n = calibrationData.length;
        msgDiv.innerHTML = n ? `<p class="status-warning">Calibration complete: ${n} outlier(s) flagged. Students with outliers show a ⚠ and yellow banner on affected questions.</p>` : `<p class="status-ok">Calibration complete: no outliers detected (all scores within 2 std of mean).</p>`;
        if (reviewData.length && currentReviewIdx >= 0) showReviewDetail(currentReviewIdx);
        renderStudentList();
    } catch (e) {
        msgDiv.innerHTML = `<p class="status-error">Calibration failed: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Run Calibration");
    }
};

document.getElementById("reviewFlaggedFirst").onchange = () => renderStudentList();

document.getElementById("reviewPrevQ").onclick = goPrevQuestion;
document.getElementById("reviewNextQ").onclick = goNextQuestion;
document.getElementById("reviewPrevStudent").onclick = goPrevStudent;
document.getElementById("reviewNextStudent").onclick = goNextStudent;
document.getElementById("reviewSaveBtn").onclick = () => saveReview();
document.getElementById("reviewSaveBtnBottom").onclick = () => saveReview();
document.getElementById("regradeStudentBtn").onclick = regradeStudent;

document.addEventListener("keydown", (e) => {
    if (!document.getElementById("panel-review")?.classList.contains("active")) return;
    if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
    switch (e.key) {
        case "j": case "J": e.preventDefault(); goNextStudent(); break;
        case "k": case "K": e.preventDefault(); goPrevStudent(); break;
        case "[": e.preventDefault(); goPrevQuestion(); break;
        case "]": e.preventDefault(); goNextQuestion(); break;
        case "s": case "S": e.preventDefault(); saveReview(); break;
        case "r": case "R":
            if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); regradeStudent(); }
            break;
    }
});

// ==================== EXPORT ====================
document.getElementById("exportBtn").onclick = async () => {
    const btn = document.getElementById("exportBtn");
    setLoading(btn, true, "Exporting…");
    document.getElementById("exportResults").innerHTML = "";
    try {
        const r = await fetchWithRetry(API + "/export", { method: "POST" });
        const data = await r.json();
        if (data.detail) throw new Error(data.detail);
        document.getElementById("exportResults").innerHTML = `<p class="status-ok">✓ Exported ${data.students} students.</p><p>Gradescope JSONs: <code>${data.gradescope_dir}</code></p><p>Excel: <code>${data.excel_path}</code></p>`;
        const dl = document.getElementById("excelDownload");
        dl.classList.remove("hidden");
        dl.href = "/export/excel?t=" + Date.now();
    } catch (e) {
        document.getElementById("exportResults").innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Run Export");
    }
};

// Init
loadRubricGroups();
