/** AI Autograder - main app logic (setup, gather, parse, rubrics, grade, review, export) */

function syncPanelInert(panelEl, isActive) {
    panelEl.setAttribute("aria-hidden", isActive ? "false" : "true");
    if (isActive) panelEl.removeAttribute("inert");
    else panelEl.setAttribute("inert", "");
}

function switchToTab(tabEl) {
    if (!tabEl || !tabEl.dataset.tab) return;
    // Guard unsaved Review edits before switching away
    if (typeof reviewDirty !== "undefined" && reviewDirty) {
        if (!confirm("You have unsaved changes in Review. Discard?")) return;
        reviewDirty = false;
    }
    const tabId = tabEl.dataset.tab;
    document.querySelectorAll(".tab[data-tab]").forEach(x => {
        x.classList.toggle("active", x === tabEl);
        x.setAttribute("aria-selected", x === tabEl ? "true" : "false");
        x.setAttribute("tabindex", x === tabEl ? "0" : "-1");
    });
    document.querySelectorAll(".panel").forEach(p => {
        const isActive = p.id === "panel-" + tabId;
        p.classList.toggle("active", isActive);
        syncPanelInert(p, isActive);
    });
    if (tabId === "rubrics") { loadRubricsForEdit(); loadRubricEstimate(); }
    if (tabId === "grade") { loadGradeEstimate(); if (!gradeStreamActive) document.getElementById("gradeProgress").innerHTML = ""; }
    if (tabId === "review") { autoLoadReview(); }
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
    syncPanelInert(p, p.classList.contains("active"));
});

// ==================== SETUP ====================
const DEFAULT_DOC_TITLE = "AI Autograder";

function syncDocumentTitle(assignmentName) {
    const name = (assignmentName || "").trim();
    document.title = name ? `${DEFAULT_DOC_TITLE} — ${name}` : DEFAULT_DOC_TITLE;
}

/** Last path segment for compact UI; full path shown in title. */
function notebookPathBasename(p) {
    if (!p) return "";
    const norm = String(p).replace(/\\/g, "/");
    const parts = norm.split("/").filter(Boolean);
    return parts[parts.length - 1] || "";
}

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
    const pathEl = document.getElementById("setupSolutionPath");
    const sn = setupConfig.solution_notebook;
    if (sn) {
        const base = notebookPathBasename(sn);
        pathEl.textContent = base ? `✓ …/${base}` : "✓ " + sn;
        pathEl.title = sn;
    } else {
        pathEl.textContent = "";
        pathEl.removeAttribute("title");
    }
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
    const genaiModelSelect = document.getElementById("setupGenaiModel");
    const genaiModel = (setupConfig.genai_detection_model || "").trim() || "gpt-4.1-mini";
    const hasGenaiOpt = Array.from(genaiModelSelect.options).some(o => o.value === genaiModel);
    if (hasGenaiOpt) genaiModelSelect.value = genaiModel;
    else {
        const opt = document.createElement("option");
        opt.value = genaiModel;
        opt.textContent = genaiModel;
        genaiModelSelect.appendChild(opt);
        genaiModelSelect.value = genaiModel;
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
    syncDocumentTitle(setupConfig.assignment_name);
}

const LS_LAST_ASSIGNMENT = "ai_autograder_last_assignment";

function syncAssignmentSelectToName(name) {
    const sel = document.getElementById("setupAssignmentSelect");
    if (!sel) return;
    const v = (name || "").trim();
    if (!v) {
        sel.value = "";
        return;
    }
    if (Array.from(sel.options).some(o => o.value === v)) sel.value = v;
}

async function refreshAssignmentsDropdown() {
    const sel = document.getElementById("setupAssignmentSelect");
    if (!sel) return;
    const keep = sel.value;
    try {
        const r = await fetchWithRetry(API + "/assignments");
        const data = await r.json();
        const names = Array.isArray(data.assignments) ? data.assignments : [];
        sel.innerHTML = "";
        const opt0 = document.createElement("option");
        opt0.value = "";
        opt0.textContent = "— select —";
        sel.appendChild(opt0);
        for (const n of names) {
            const o = document.createElement("option");
            o.value = n;
            o.textContent = n;
            sel.appendChild(o);
        }
        if (keep && names.includes(keep)) sel.value = keep;
    } catch (_) {}
}

async function doLoadAssignment() {
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
        try {
            localStorage.setItem(LS_LAST_ASSIGNMENT, data.assignment_name);
        } catch (_) {}
        await refreshAssignmentsDropdown();
        syncAssignmentSelectToName(data.assignment_name);
    } catch (e) {
        statusEl.innerHTML = `<span class="status-error">Error: ${escHtml(e.message)}</span>`;
    } finally {
        setLoading(btn, false, "Load / Create");
    }
}

document.getElementById("setupLoadBtn").onclick = () => doLoadAssignment();

document.getElementById("setupResetBtn").onclick = async () => {
    if (!confirm("Reset all config fields to defaults? Your rubrics and graded results are NOT affected.")) return;
    try {
        const r = await fetchWithRetry(API + "/config/default");
        const data = await r.json();
        _populateSetupFields(data);
    } catch (e) {
        alert("Failed to load defaults: " + e.message);
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
    setupConfig.genai_detection_model = document.getElementById("setupGenaiModel").value || "gpt-4.1-mini";
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
    const truncNote = document.getElementById("parsePreviewTruncNote");
    if (truncNote) {
        truncNote.textContent = "";
        truncNote.classList.add("hidden");
    }
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
            const truncated = j.length > 6000;
            document.getElementById("parsePreviewContent").textContent = truncated ? j.slice(0, 6000) + "\n…" : j;
            if (truncNote) {
                if (truncated) {
                    truncNote.textContent = "Preview truncated for display; full parsed JSON is on disk under the parsed output folder.";
                    truncNote.classList.remove("hidden");
                } else {
                    truncNote.textContent = "";
                    truncNote.classList.add("hidden");
                }
            }
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
        const name = (config.assignment_name || "").trim();
        if (!name) {
            syncDocumentTitle("");
        }
        if (name) {
            _populateSetupFields(config);
            document.getElementById("setupConfigFields").classList.remove("hidden");
        }
        let groups = (config.grading || {}).question_groups || [];
        const gradeOnly = (config.grading || {}).grade_only;
        groups = filterGroupsForUi(groups, gradeOnly);
        rubricQuestionGroups = groups;
        renderRubricGroupCheckboxes(groups);
        return groups;
    } catch (_) {
        rubricQuestionGroups = [];
        renderRubricGroupCheckboxes([]);
        syncDocumentTitle("");
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
            <div class="q-block-header" role="button" tabindex="0" aria-expanded="true">
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
    attachRubricCollapseHandlers(form);
}

function attachRubricCollapseHandlers(formRoot) {
    formRoot.querySelectorAll(".rubric-q-block").forEach(block => {
        const header = block.querySelector(".q-block-header");
        const body = block.querySelector(".q-block-body");
        if (!header || !body) return;
        const syncAria = () => {
            header.setAttribute("aria-expanded", body.classList.contains("open") ? "true" : "false");
        };
        header.onclick = (e) => {
            e.preventDefault();
            body.classList.toggle("open");
            syncAria();
        };
        header.onkeydown = (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                body.classList.toggle("open");
                syncAria();
            }
        };
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
    // Show cost estimate in confirmation dialog
    const estEl = document.getElementById("gradeEstimate");
    const estText = estEl ? estEl.textContent.trim() : "";
    const confirmMsg = estText
        ? `Start grading? ${estText}\n\nThis will make API calls that cost real money.`
        : "Start grading? This will make API calls that cost real money.";
    if (!confirm(confirmMsg)) return;

    const list = document.getElementById("gradeProgress");
    const genaiBtn = document.getElementById("genaiDetectBtn");
    const cancelBtn = document.getElementById("gradeCancelBtn");
    const autoGenai = document.getElementById("genaiAutoRun");
    list.innerHTML = "";
    // Track results for summary
    const gradeResults = [];
    let gradeErrors = 0;
    const btn = document.getElementById("gradeBtn");
    setLoading(btn, true, "Grading…");
    genaiBtn.disabled = true;
    cancelBtn.classList.remove("hidden");
    gradeStreamActive = true;
    stopGradePolling();
    try {
        const ev = new EventSource(API + "/grade");
        gradeEv = ev;
        ev.addEventListener("progress", e => {
            const data = JSON.parse(e.data || "{}");
            const student = data.student || "";
            if (data.status === "queue_info") {
                const pending = data.pending ?? 0;
                const div = document.createElement("div");
                if (pending === 0) {
                    div.className = "progress-item error";
                    div.innerHTML = `<span>${escHtml(data.message || "Nothing to grade.")}</span>`;
                } else {
                    div.className = "progress-item done";
                    const next = data.next_student ? escHtml(data.next_student) : "—";
                    const skip = data.skipped != null ? data.skipped : 0;
                    const tp = data.total_parsed != null ? data.total_parsed : "?";
                    div.innerHTML = `<span>Continue from <strong>${next}</strong> — ${pending} pending, ${skip} skipped (${tp} parsed)</span>`;
                }
                list.appendChild(div);
                list.scrollTop = list.scrollHeight;
                return;
            }
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
                gradeResults.push({ score: r.total_score, max: r.total_max, name: r.student_name });
                const existing = list.querySelector(`[data-student="${CSS.escape(r.student_name)}"]`);
                const div = existing || document.createElement("div");
                div.className = "progress-item done";
                div.dataset.student = r.student_name;
                div.innerHTML = `<span>${escHtml(r.student_name)}</span><span style="font-weight:600">${r.total_score}/${r.total_max}</span>`;
                if (!existing) list.appendChild(div);
            } else if (data.status === "error") {
                gradeErrors++;
                const existing = list.querySelector(`[data-student="${CSS.escape(student)}"]`);
                const div = existing || document.createElement("div");
                div.className = "progress-item error";
                div.dataset.student = student;
                div.innerHTML = `<span>${escHtml(student)}</span><span>Error: ${escHtml(data.error || "")}</span>`;
                if (!existing) list.appendChild(div);
            } else if (data.status === "cancelled") {
                const div = document.createElement("div");
                div.className = "progress-item error";
                div.innerHTML = `<span>⏹ Grading stopped by user after ${data.graded_count ?? gradeResults.length} student(s). Already-graded results are saved.</span>`;
                list.appendChild(div);
            }
            list.scrollTop = list.scrollHeight;
        });
        ev.addEventListener("done", () => {
            ev.close();
            gradeEv = null;
            gradeStreamActive = false;
            stopGradePolling();
            setLoading(btn, false, "Start Grading");
            genaiBtn.disabled = false;
            cancelBtn.classList.add("hidden");
            // Show grading summary banner
            if (gradeResults.length > 0) {
                const scores = gradeResults.map(r => r.max > 0 ? (r.score / r.max) * 100 : 0);
                const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
                const min = Math.min(...scores);
                const max = Math.max(...scores);
                const pass = scores.filter(s => s >= 50).length;
                const div = document.createElement("div");
                div.className = "progress-item done grade-summary";
                div.innerHTML = `<strong>Summary:</strong> ${gradeResults.length} graded`
                    + (gradeErrors > 0 ? `, ${gradeErrors} error(s)` : "")
                    + ` · Avg: ${avg.toFixed(1)}% · Min: ${min.toFixed(0)}% · Max: ${max.toFixed(0)}%`
                    + ` · ≥50%: ${pass}/${gradeResults.length}`;
                list.appendChild(div);
                list.scrollTop = list.scrollHeight;
            }
            if (autoGenai && autoGenai.checked) {
                runGenaiDetection("Auto-run after grading");
            }
        });
        ev.onerror = () => {
            ev.close();
            gradeEv = null;
            gradeStreamActive = false;
            stopGradePolling();
            setLoading(btn, false, "Start Grading");
            genaiBtn.disabled = false;
            cancelBtn.classList.add("hidden");
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
        genaiBtn.disabled = false;
        cancelBtn.classList.add("hidden");
    }
};

document.getElementById("gradeCancelBtn").onclick = async () => {
    const cancelBtn = document.getElementById("gradeCancelBtn");
    setLoading(cancelBtn, true, "Stopping…");
    try {
        const r = await fetchWithRetry(API + "/grade/cancel", { method: "POST" });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.detail || "Cancel failed");
        }
    } catch (e) {
        // Non-fatal: grading may have already finished
        cancelBtn.textContent = "Stop after current";
    }
};

function setGenaiStatus(message, kind = "info") {
    const el = document.getElementById("genaiStatus");
    if (!el) return;
    if (!message) {
        el.textContent = "";
        el.className = "text-muted-sm mb-2";
        return;
    }
    el.textContent = message;
    el.className = `genai-status mb-2 ${kind}`;
}

async function runGenaiDetection(sourceLabel = "Manual run") {
    const btn = document.getElementById("genaiDetectBtn");
    setLoading(btn, true, "Detecting…");
    setGenaiStatus(`${sourceLabel}: running GenAI detection…`, "info");
    try {
        const r = await fetchWithRetry(API + "/detect-genai", { method: "POST" });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            const detail = err.detail;
            const msg = typeof detail === "string" ? detail : (detail ? JSON.stringify(detail) : r.statusText);
            throw new Error(msg);
        }
        const data = await r.json();
        const warnings = (data.errors && data.errors.length) ? data.errors.length : 0;
        const msg = `GenAI detection complete: ${data.students_processed || 0} students processed, ${data.questions_flagged || 0} flag(s), ${data.students_skipped || 0} skipped${warnings ? `, ${warnings} warning(s)` : ""}.`;
        setGenaiStatus(msg, warnings ? "info" : "success");

        // Keep review list/flags in sync if the user is actively reviewing.
        if (Array.isArray(reviewData) && reviewData.length) {
            try {
                await loadReviewAndCalibration();
                if (document.getElementById("panel-review")?.classList.contains("active")) {
                    renderStudentList();
                    if (currentReviewIdx >= 0 && currentReviewIdx < reviewData.length) {
                        showReviewDetail(currentReviewIdx);
                    }
                }
            } catch (_) {
                // Non-fatal: detection succeeded even if review refresh fails.
            }
        }
    } catch (e) {
        setGenaiStatus(`GenAI detection failed: ${e.message || String(e)}`, "error");
    } finally {
        setLoading(btn, false, "Run GenAI detection");
    }
}

document.getElementById("genaiDetectBtn").onclick = async () => {
    await runGenaiDetection("Manual run");
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

async function autoLoadReview() {
    // Auto-load results when switching to Review tab (skip if grading is active or dirty edits).
    if (reviewDirty || gradeStreamActive) return;
    const btn = document.getElementById("reviewLoadBtn");
    const msgDiv = document.getElementById("reviewCalibrationResults");
    setLoading(btn, true, "Loading…");
    msgDiv.innerHTML = "";
    try {
        await loadReviewAndCalibration();
        parsedCache = {};
        document.getElementById("reviewLayout").classList.remove("hidden");
        renderStudentList();
        const vis = getVisibleStudentOrder();
        if (vis.length && currentReviewIdx < 0) showReviewDetail(vis[0]);
        if (calibrationData.length) {
            msgDiv.innerHTML = `<span class="status-warning review-cal-msg">${calibrationData.length} outlier(s) loaded — run <strong>Calibrate</strong> to refresh.</span>`;
        }
    } catch (e) {
        const err = document.getElementById("reviewError");
        if (err) err.innerHTML = `<span class="status-error">${escHtml(e.message)}</span>`;
    } finally {
        setLoading(btn, false, "Load Results");
    }
}

function getQuestionTypeFromParsed(studentName, qid) {
    const p = parsedCache[studentName];
    if (!p || !p.sections) return null;
    for (const secData of Object.values(p.sections)) {
        if (secData.questions && secData.questions[qid]) {
            return secData.questions[qid].question_type || null;
        }
    }
    return null;
}

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
    const order = getVisibleStudentOrder();
    if (order.length === 0) return;
    if (reviewDirty && !confirm("Discard unsaved changes?")) return;
    reviewDirty = false;
    const pos = order.indexOf(currentReviewIdx);
    if (pos < 0) {
        showReviewDetail(order[0]);
        scrollStudentIntoView();
        return;
    }
    if (pos >= order.length - 1) return;
    showReviewDetail(order[pos + 1]);
    scrollStudentIntoView();
}

function goPrevStudent() {
    const order = getVisibleStudentOrder();
    if (order.length === 0) return;
    if (reviewDirty && !confirm("Discard unsaved changes?")) return;
    reviewDirty = false;
    const pos = order.indexOf(currentReviewIdx);
    if (pos < 0) {
        showReviewDetail(order[0]);
        scrollStudentIntoView();
        return;
    }
    if (pos <= 0) return;
    showReviewDetail(order[pos - 1]);
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

/** Keep the active question pill visible inside the horizontal `.review-v2-qpills` strip (like student list + J/K). */
function scrollActiveQuestionPillIntoView() {
    const container = document.getElementById("reviewQPills");
    if (!container) return;
    const active = container.querySelector(".review-v2-qpill.active");
    if (active) {
        active.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    }
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
        if (q.suspicious_genai) reasons.push({ qid, type: "genai_suspicion" });
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
        else if (r.type === "genai_suspicion" && !seen.has("genai_suspicion")) {
            label = "GenAI flag";
            seen.add("genai_suspicion");
        }
        if (label) parts.push(label);
    }
    return parts.join(", ");
}

/** Per-question flags for the current student (same rules as getStudentFlagReasons, filtered by qid). */
function getQuestionFlagReasonsForQid(s, qid) {
    return getStudentFlagReasons(s).filter(r => r.qid === qid);
}

/** Short description for pill title / aria-label. */
function formatQuestionFlagsTitle(qid, s) {
    const rs = getQuestionFlagReasonsForQid(s, qid);
    if (rs.length === 0) return "";
    const parts = [];
    const seen = new Set();
    for (const r of rs) {
        if (r.type === "needs_review" && !seen.has("nr")) {
            parts.push("needs review");
            seen.add("nr");
        } else if (r.type === "low_confidence" && !seen.has("lc")) {
            parts.push("low confidence");
            seen.add("lc");
        } else if (r.type === "outlier" && !seen.has("out")) {
            parts.push(r.flag_reason === "low" ? "outlier (below mean)" : "outlier (above mean)");
            seen.add("out");
        } else if (r.type === "genai_suspicion" && !seen.has("ga")) {
            parts.push("GenAI flag");
            seen.add("ga");
        }
    }
    return parts.join(", ");
}

/** CSS class for question pill when this question has flags (distinct colors; multi = combined). */
function getQuestionPillFlagClass(qid, s) {
    const rs = getQuestionFlagReasonsForQid(s, qid);
    if (rs.length === 0) return "";
    const nr = rs.some(r => r.type === "needs_review");
    const out = rs.some(r => r.type === "outlier");
    const lc = rs.some(r => r.type === "low_confidence");
    const ga = rs.some(r => r.type === "genai_suspicion");
    const nTypes = (nr ? 1 : 0) + (out ? 1 : 0) + (lc ? 1 : 0) + (ga ? 1 : 0);
    if (nTypes > 1) return " review-v2-qpill--f-multi";
    if (nr) return " review-v2-qpill--f-nr";
    if (out) return " review-v2-qpill--f-out";
    if (lc) return " review-v2-qpill--f-lc";
    if (ga) return " review-v2-qpill--f-genai";
    return "";
}

function passesReviewFilters(s) {
    const q = (document.getElementById("reviewStudentFilter")?.value || "").trim().toLowerCase();
    if (q && !String(s.student_name || "").toLowerCase().includes(q)) return false;
    const reasons = getStudentFlagReasons(s);
    const types = new Set(reasons.map(r => r.type));
    const needOutlier = document.getElementById("reviewFilterOutlier")?.checked;
    const needLC = document.getElementById("reviewFilterLowConf")?.checked;
    const needNR = document.getElementById("reviewFilterNeedsReview")?.checked;
    const needGenai = document.getElementById("reviewFilterGenai")?.checked;
    if (!needOutlier && !needLC && !needNR && !needGenai) return true;
    return Boolean(
        (needOutlier && types.has("outlier"))
        || (needLC && types.has("low_confidence"))
        || (needNR && types.has("needs_review"))
        || (needGenai && types.has("genai_suspicion"))
    );
}

function getVisibleStudentOrder() {
    const flaggedFirst = document.getElementById("reviewFlaggedFirst")?.checked || false;
    let order = reviewData.map((_, i) => i).filter(i => passesReviewFilters(reviewData[i]));
    if (flaggedFirst) {
        order.sort((a, b) => (isStudentFlagged(reviewData[b]) ? 1 : 0) - (isStudentFlagged(reviewData[a]) ? 1 : 0));
    }
    return order;
}

function renderStudentList() {
    const list = document.getElementById("studentList");
    if (!list) return;
    const order = getVisibleStudentOrder();
    if (currentReviewIdx >= 0 && order.length > 0 && !order.includes(currentReviewIdx)) {
        showReviewDetail(order[0]);
        return;
    }
    if (currentReviewIdx >= 0 && order.length === 0) {
        currentReviewIdx = -1;
    }
    list.innerHTML = order.map(i => {
        const s = reviewData[i];
        const reasons = getStudentFlagReasons(s);
        const flagged = reasons.length > 0;
        const reasonText = formatFlagReasons(reasons);
        const isDirty = i === currentReviewIdx && reviewDirty;
        const dirtyDot = isDirty ? '<span class="review-v2-dirty" title="Unsaved changes">●</span> ' : "";
        const titleTip = reasonText ? `${escHtml(s.student_name)} — ${escHtml(reasonText)}` : escHtml(s.student_name);
        return `<div class="student-item ${flagged ? "flagged" : ""} ${i === currentReviewIdx ? "selected" : ""}" data-idx="${i}" title="${titleTip}" tabindex="0" role="button"><span class="sname">${dirtyDot}${flagged ? "⚠ " : ""}${escHtml(s.student_name)}</span><span class="sscore">${s.total_score} / ${s.total_max}</span>${flagged ? `<span class="sscore" style="font-size:0.75rem;color:#d97706;">${escHtml(reasonText)}</span>` : ""}</div>`;
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
    const dedInput = document.querySelector(`#reviewDedRow input[data-q-ded="${currentQid}"]`);
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

/** Jupyter may store image payload base64 as a string or list of fragments. */
function normalizeNotebookBase64(raw) {
    if (raw == null || raw === "") return "";
    if (Array.isArray(raw)) return raw.join("");
    return String(raw);
}

/** HTML for plot images from parse_notebook `answer_cells[].images` (mime + base64). */
function buildReviewAnswerImagesHtml(answerCells) {
    if (!Array.isArray(answerCells) || answerCells.length === 0) return "";
    const parts = [];
    let n = 0;
    for (const cell of answerCells) {
        const imgs = cell && cell.images;
        if (!Array.isArray(imgs)) continue;
        for (const img of imgs) {
            if (!img) continue;
            const mime = img.mime === "image/jpeg" ? "image/jpeg" : "image/png";
            const b64 = normalizeNotebookBase64(img.base64);
            if (!b64) continue;
            n += 1;
            const src = `data:${mime};base64,${b64}`;
            parts.push(
                `<figure class="review-v2-output-fig"><img class="review-v2-output-img" src="${src}" alt="Notebook output figure ${n}" loading="lazy"></figure>`
            );
        }
    }
    if (!parts.length) return "";
    return `<div class="review-v2-output-images">${parts.join("")}</div>`;
}

function renderQuestionContent(qid, q, parsed, studentName) {
    let qMarkdown = "", studentCode = "", studentOutput = "", studentMd = "";
    let answerCells = [];
    if (parsed) {
        for (const secData of Object.values(parsed.sections || {})) {
            if (secData.questions && secData.questions[qid]) {
                const pq = secData.questions[qid];
                qMarkdown = pq.question_markdown || "";
                studentCode = pq.answer_code_concat || "";
                studentOutput = pq.answer_text_concat || "";
                studentMd = pq.answer_markdown_concat || "";
                answerCells = pq.answer_cells || [];
                break;
            }
        }
    }
    const outputImagesHtml = buildReviewAnswerImagesHtml(answerCells);
    let html = "";
    if (qMarkdown) html += `<div class="review-v2-section"><div class="review-v2-section-label">Question</div><div class="review-v2-qstatement">${renderMarkdown(qMarkdown)}</div></div>`;
    if (studentCode) {
        html += `<div class="review-v2-section"><div class="review-v2-section-label">Student Code</div><pre class="review-v2-code"><code class="language-python">${escHtml(studentCode)}</code></pre></div>`;
    }
    if (studentOutput || outputImagesHtml) {
        html += `<div class="review-v2-section"><div class="review-v2-section-label">Output</div>`;
        if (outputImagesHtml) html += outputImagesHtml;
        if (studentOutput) {
            const trunc = 600;
            const isLong = studentOutput.length > trunc;
            const show = isLong ? studentOutput.slice(0, trunc) + "\n…" : studentOutput;
            html += `<pre class="review-v2-output" id="reviewOutput-${qid}">${escHtml(show)}</pre>`;
            if (isLong) html += `<button type="button" class="btn btn-secondary" style="font-size:0.8rem;padding:4px 8px" data-toggle-output="${qid}">Show all</button>`;
        }
        html += `</div>`;
    }
    if (studentMd) html += `<div class="review-v2-section"><div class="review-v2-section-label">Written Answer</div><div class="review-v2-qstatement">${renderMarkdown(studentMd)}</div></div>`;
    return html;
}

function renderReviewContent() {
    const work = document.getElementById("reviewWorkPane");
    const gradePh = document.getElementById("reviewGradePlaceholder");
    const gradeBody = document.getElementById("reviewGradeBody");
    const dedRow = document.getElementById("reviewDedRow");
    const scoreLive = document.getElementById("reviewScoreLive");
    const maxLive = document.getElementById("reviewMaxLive");
    const fbTa = document.getElementById("reviewFeedbackTa");
    const s = currentReviewIdx >= 0 ? reviewData[currentReviewIdx] : null;
    if (!s || currentQids.length === 0) {
        if (work) work.innerHTML = `<p class="review-placeholder">No questions to review for this student.</p>`;
        if (gradePh) gradePh.classList.remove("hidden");
        if (gradeBody) gradeBody.classList.add("hidden");
        if (dedRow) dedRow.innerHTML = "";
        if (fbTa) { fbTa.value = ""; delete fbTa.dataset.qFb; }
        return;
    }
    const qid = currentQids[currentQidIdx];
    const q = s.questions?.[qid];
    if (!q) {
        if (work) work.innerHTML = `<p class="review-placeholder">Question ${escHtml(qid)} not found.</p>`;
        if (gradePh) gradePh.classList.remove("hidden");
        if (gradeBody) gradeBody.classList.add("hidden");
        if (dedRow) dedRow.innerHTML = "";
        if (fbTa) { fbTa.value = ""; delete fbTa.dataset.qFb; }
        return;
    }
    if (gradePh) gradePh.classList.add("hidden");
    if (gradeBody) gradeBody.classList.remove("hidden");
    const parsed = parsedCache[s.student_name];
    const max = q.max || 0;
    const deduction = Math.max(0, max - (q.score || 0));
    const score = max - deduction;
    const outlier = calibrationLookup()[s.student_name + "|" + qid];
    const outlierBanner = outlier
        ? `<div class="outlier-banner">Outlier (${outlier.flag_reason === "high" ? "above" : "below"} mean): ${outlier.score}/${outlier.max || max}, μ ${outlier.mean} ± ${outlier.std}</div>`
        : "";
    const genaiNote = (q.suspicious_genai_note || "").trim();
    const genaiBanner = q.suspicious_genai
        ? `<div class="genai-banner">Possible GenAI-style answer (triage only; not a conduct verdict).${genaiNote ? ` <span class="genai-banner-note">${escHtml(genaiNote)}</span>` : ""}</div>`
        : "";
    if (work) work.innerHTML = outlierBanner + genaiBanner + renderQuestionContent(qid, q, parsed, s.student_name);
    if (maxLive) maxLive.textContent = String(max);
    if (scoreLive) scoreLive.textContent = String(score);
    if (dedRow) {
        dedRow.innerHTML = `Max <strong>${max}</strong> − <input type="number" data-q-ded="${qid}" data-q-max="${max}" value="${deduction}" min="0" max="${max}" step="0.5" aria-label="Deduction points"> = <strong id="reviewScoreDisplay-${qid}">${score}</strong>`;
    }
    if (fbTa) {
        fbTa.value = q.feedback || "";
        fbTa.dataset.qFb = qid;
        fbTa.oninput = () => {
            const st = currentReviewIdx >= 0 ? reviewData[currentReviewIdx] : null;
            if (st?.questions?.[qid]) st.questions[qid].feedback = fbTa.value;
            reviewDirty = true;
            renderStudentList();
        };
    }
    const dedInput = dedRow?.querySelector(`input[data-q-ded="${qid}"]`);
    if (dedInput) {
        dedInput.oninput = () => {
            const d = parseFloat(dedInput.value) || 0;
            const sc = Math.max(0, max - d);
            const span = document.getElementById(`reviewScoreDisplay-${qid}`);
            if (span) span.textContent = sc;
            if (scoreLive) scoreLive.textContent = String(sc);
            const st = currentReviewIdx >= 0 ? reviewData[currentReviewIdx] : null;
            if (st?.questions?.[qid]) st.questions[qid].score = sc;
            reviewDirty = true;
            recalcTotal();
            renderStudentList();
        };
    }
    work?.querySelectorAll("pre.review-v2-code code").forEach(el => {
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
    work?.querySelectorAll("[data-toggle-output]").forEach(btn => {
        btn.onclick = () => {
            const id = btn.dataset.toggleOutput;
            const pre = document.getElementById("reviewOutput-" + id);
            if (pre) {
                const st = currentReviewIdx >= 0 ? reviewData[currentReviewIdx] : null;
                if (st) {
                    const p = parsedCache[st.student_name];
                    let out = "";
                    if (p) {
                        for (const secData of Object.values(p.sections || {})) {
                            if (secData.questions && secData.questions[id]) {
                                out = secData.questions[id].answer_text_concat || "";
                                break;
                            }
                        }
                    }
                    pre.textContent = out;
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
        if (stuInfo) stuInfo.textContent = "0 / 0";
        const stuNameEl = document.getElementById("reviewStuName");
        if (stuNameEl) {
            stuNameEl.textContent = "";
            stuNameEl.title = "";
        }
        if (totalDisplay) totalDisplay.textContent = "0";
        if (totalMaxDisplay) totalMaxDisplay.textContent = "0";
        return;
    }
    const s = reviewData[currentReviewIdx];
    container.innerHTML = currentQids.map((qid, i) => {
        const flagCls = getQuestionPillFlagClass(qid, s);
        const flagTitle = formatQuestionFlagsTitle(qid, s);
        const qType = getQuestionTypeFromParsed(s.student_name, qid);
        const typeLabel = qType && qType !== "mixed" ? `[${qType}]` : "";
        const titleParts = [qid, typeLabel, flagTitle].filter(Boolean).join(" · ");
        const labelExtra = flagTitle ? ` — ${flagTitle}` : "";
        const aria = `Question ${qid}${typeLabel ? ` (${qType})` : ""}${labelExtra}`;
        const titleAttr = ` title="${escHtml(titleParts)}"`;
        const pillLabel = typeLabel ? `${qid} <span class="qpill-type">${escHtml(typeLabel)}</span>` : escHtml(qid);
        return `<button type="button" class="review-v2-qpill${i === currentQidIdx ? " active" : ""}${flagCls}" data-qidx="${i}" aria-label="${escHtml(aria)}" aria-pressed="${i === currentQidIdx ? "true" : "false"}"${titleAttr}>${pillLabel}</button>`;
    }).join("");
    container.querySelectorAll(".review-v2-qpill").forEach(btn => {
        btn.onclick = () => {
            currentQidIdx = parseInt(btn.dataset.qidx);
            renderReviewContent();
            renderQPills();
        };
    });
    const vis = getVisibleStudentOrder();
    const vpos = vis.indexOf(currentReviewIdx);
    if (stuInfo) stuInfo.textContent = vis.length ? `${vpos >= 0 ? vpos + 1 : 1} / ${vis.length}` : "0 / 0";
    const stuNameEl = document.getElementById("reviewStuName");
    if (stuNameEl) {
        const nm = s?.student_name || "";
        stuNameEl.textContent = nm;
        stuNameEl.title = nm;
    }
    if (totalDisplay) totalDisplay.textContent = s?.total_score ?? "0";
    if (totalMaxDisplay) totalMaxDisplay.textContent = s?.total_max ?? "0";
    scrollActiveQuestionPillIntoView();
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
    const work = document.getElementById("reviewWorkPane");
    const gradePh = document.getElementById("reviewGradePlaceholder");
    const gradeBody = document.getElementById("reviewGradeBody");
    if (work) work.innerHTML = `<p class="review-placeholder">Loading student work…</p>`;
    if (gradePh) gradePh.classList.remove("hidden");
    if (gradeBody) gradeBody.classList.add("hidden");
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
    renderStudentList();
    scrollStudentIntoView();
    loadRegradeEstimate(s.student_name);
}

window.saveReview = async function() {
    if (currentReviewIdx < 0) return;
    const s = reviewData[currentReviewIdx];
    const qs = JSON.parse(JSON.stringify(s.questions));
    const dedInput = document.querySelector("#reviewDedRow input[data-q-ded]");
    if (dedInput && qs[dedInput.dataset.qDed]) {
        const max = parseFloat(dedInput.dataset.qMax) || 0;
        const ded = parseFloat(dedInput.value) || 0;
        qs[dedInput.dataset.qDed].score = Math.max(0, max - ded);
    }
    const fbTa = document.getElementById("reviewFeedbackTa");
    const qfb = fbTa?.dataset?.qFb;
    if (fbTa && qfb && qs[qfb]) qs[qfb].feedback = fbTa.value;
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
    const oldScore = s.total_score;
    const oldMax = s.total_max;
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
            // Show before/after score diff
            const newScore = data.result.total_score;
            const newMax = data.result.total_max;
            const diff = newScore - oldScore;
            const diffStr = diff >= 0 ? `+${diff.toFixed(1)}` : diff.toFixed(1);
            const saveEl = document.getElementById("reviewSaveFeedback");
            if (saveEl) {
                saveEl.textContent = `Re-graded: ${oldScore}/${oldMax} → ${newScore}/${newMax} (${diffStr})`;
                saveEl.className = "review-save-feedback " + (diff >= 0 ? "status-ok" : "status-warning");
                setTimeout(() => { saveEl.textContent = ""; saveEl.className = "review-save-feedback"; }, 8000);
            }
        }
    } catch (e) {
        document.getElementById("reviewError").innerHTML = `<p class="status-error">Re-grade failed: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Re-grade student");
    }
};

document.getElementById("reviewBatchRegradeBtn").onclick = async function() {
    if (!reviewData || !reviewData.length) return;
    // Find students with needs_review or low confidence on any question
    const flagged = reviewData.filter(s => {
        if (!s.questions) return false;
        return Object.values(s.questions).some(q =>
            q.requires_review || q.confidence === "low"
        );
    });
    if (!flagged.length) {
        alert("No flagged students found (needs_review or low confidence).");
        return;
    }
    if (!confirm(`Re-grade ${flagged.length} flagged student(s)? This will overwrite their current scores.`)) return;
    const btn = document.getElementById("reviewBatchRegradeBtn");
    setLoading(btn, true, `Re-grading 0/${flagged.length}…`);
    const saveEl = document.getElementById("reviewSaveFeedback");
    let done = 0, errors = 0;
    for (const s of flagged) {
        try {
            const r = await fetchWithRetry(API + "/grade/" + encodeURIComponent(s.student_name), { method: "POST" });
            const data = await r.json();
            if (data.detail) throw new Error(data.detail);
            if (data.result) {
                const idx = reviewData.findIndex(x => x.student_name === s.student_name);
                if (idx >= 0) reviewData[idx] = data.result;
            }
            done++;
        } catch (e) {
            errors++;
        }
        btn.textContent = `Re-grading ${done + errors}/${flagged.length}…`;
    }
    setLoading(btn, false, "Re-grade flagged");
    renderStudentList();
    if (currentReviewIdx >= 0) showReviewDetail(currentReviewIdx);
    if (saveEl) {
        saveEl.textContent = `Batch re-grade: ${done} succeeded, ${errors} failed (of ${flagged.length})`;
        saveEl.className = "review-save-feedback " + (errors ? "status-warning" : "status-ok");
        setTimeout(() => { saveEl.textContent = ""; saveEl.className = "review-save-feedback"; }, 10000);
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
        const vis = getVisibleStudentOrder();
        if (vis.length) showReviewDetail(vis[0]);
        if (calibrationData.length) {
            msgDiv.innerHTML = `<span class="status-warning review-cal-msg">${calibrationData.length} outlier(s) loaded — run <strong>Calibrate</strong> to refresh.</span>`;
        }
    } catch (e) {
        const err = document.getElementById("reviewError");
        if (err) err.innerHTML = `<span class="status-error">${escHtml(e.message)}</span>`;
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
        msgDiv.innerHTML = n
            ? `<span class="status-warning review-cal-msg">${n} outlier(s) flagged — ⚠ on students; yellow banner on questions.</span>`
            : `<span class="status-ok review-cal-msg">No outliers (within 2σ).</span>`;
        if (reviewData.length && currentReviewIdx >= 0) showReviewDetail(currentReviewIdx);
        renderStudentList();
    } catch (e) {
        msgDiv.innerHTML = `<span class="status-error review-cal-msg">${escHtml(e.message)}</span>`;
    } finally {
        setLoading(btn, false, "Run Calibration");
    }
};

document.getElementById("reviewFlaggedFirst").onchange = () => renderStudentList();

["reviewStudentFilter", "reviewFilterOutlier", "reviewFilterLowConf", "reviewFilterNeedsReview", "reviewFilterGenai"].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener(id === "reviewStudentFilter" ? "input" : "change", () => renderStudentList());
});

document.getElementById("reviewPrevQ").onclick = goPrevQuestion;
document.getElementById("reviewNextQ").onclick = goNextQuestion;
document.getElementById("reviewPrevStudent").onclick = goPrevStudent;
document.getElementById("reviewNextStudent").onclick = goNextStudent;
document.getElementById("reviewSaveBtn").onclick = () => saveReview();
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
function exportApiPath(suffix) {
    return (typeof API !== "undefined" ? API : "") + suffix;
}

function initExportPanelDownloadHrefs() {
    const linter = document.getElementById("linterZipDownload");
    const gs = document.getElementById("autograderZipDownload");
    if (linter) linter.href = exportApiPath("/export/linter-zip");
    if (gs) gs.href = exportApiPath("/export/autograder-zip");
}

document.getElementById("exportBtn").onclick = async () => {
    const btn = document.getElementById("exportBtn");
    setLoading(btn, true, "Exporting…");
    document.getElementById("exportResults").innerHTML = "";
    try {
        const r = await fetchWithRetry(API + "/export", { method: "POST" });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
            throw new Error(estimateErrorMessage(data, r) || r.statusText || "Export failed");
        }
        const n = data.students | 0;
        const parts = [];
        parts.push(
            `<p class="status-ok">✓ Exported ${n} student${n === 1 ? "" : "s"}.</p>`
        );
        if (n > 0) {
            parts.push(
                `<p>Gradescope JSONs: <code>${escHtml(String(data.gradescope_dir || ""))}</code></p>`
            );
            parts.push(
                `<p>Excel: <code>${escHtml(String(data.excel_path || ""))}</code></p>`
            );
            if (data.autograder_zip) {
                parts.push(
                    `<p>Gradescope autograder ZIP (refreshed with this export): <code>${escHtml(String(data.autograder_zip))}</code></p>`
                );
            }
        } else {
            parts.push(
                `<p class="text-muted">No rows in <code>graded_results.json</code> — Gradescope JSONs, Excel, and the autograder ZIP were not regenerated.</p>`
            );
        }
        document.getElementById("exportResults").innerHTML = parts.join("");
        const dl = document.getElementById("excelDownload");
        if (n > 0 && data.excel_path) {
            dl.classList.remove("hidden");
            dl.href = exportApiPath("/export/excel?t=" + Date.now());
        } else {
            dl.classList.add("hidden");
        }
    } catch (e) {
        document.getElementById("exportResults").innerHTML = `<p class="status-error">Error: ${escHtml(e.message)}</p>`;
    } finally {
        setLoading(btn, false, "Run Export");
    }
};

// Init
loadRubricGroups();
initExportPanelDownloadHrefs();

async function initAssignmentSwitcher() {
    await refreshAssignmentsDropdown();
    let last = null;
    try {
        last = localStorage.getItem(LS_LAST_ASSIGNMENT);
    } catch (_) {}
    const input = document.getElementById("setupAssignmentName");
    const sel = document.getElementById("setupAssignmentSelect");
    if (sel) {
        sel.addEventListener("change", () => {
            const v = sel.value;
            if (!v) return;
            input.value = v;
            updateOutputDirHint();
            doLoadAssignment();
        });
    }
    if (last && input && sel) {
        const names = Array.from(sel.options).map(o => o.value).filter(Boolean);
        if (names.includes(last)) {
            input.value = last;
            sel.value = last;
            updateOutputDirHint();
            await doLoadAssignment();
        }
    }
}
initAssignmentSwitcher();
