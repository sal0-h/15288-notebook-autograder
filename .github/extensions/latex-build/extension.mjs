import { execFile } from "node:child_process";
import { join } from "node:path";
import { joinSession } from "@github/copilot-sdk/extension";

const PAPER_DIR = join(process.cwd(), "research", "paper");
const TEX_FILE = "main.tex";

function runShell(cmd, cwd) {
  return new Promise((resolve) => {
    execFile("bash", ["-c", cmd], { cwd, timeout: 30000 }, (err, stdout, stderr) => {
      if (err) resolve({ ok: false, output: stderr || err.message });
      else resolve({ ok: true, output: stdout });
    });
  });
}

const session = await joinSession({
  tools: [
    {
      name: "latex_build",
      description:
        "Compile the SIGCSE paper (pdflatex + bibtex). Returns: page count, errors, overfull boxes, TODO markers, and word count. Use this instead of running pdflatex manually.",
      parameters: {
        type: "object",
        properties: {},
      },
      handler: async () => {
        const results = [];

        // Full compile cycle
        const compile = await runShell(
          `pdflatex -interaction=nonstopmode ${TEX_FILE} && ` +
            `bibtex main && ` +
            `pdflatex -interaction=nonstopmode ${TEX_FILE} && ` +
            `pdflatex -interaction=nonstopmode ${TEX_FILE}`,
          PAPER_DIR,
        );

        if (!compile.ok && !compile.output.includes("Output written")) {
          return `❌ Compilation failed:\n${compile.output.slice(-2000)}`;
        }

        // Page count
        const pageMatch = compile.output.match(/Output written on main\.pdf \((\d+) pages/);
        const pages = pageMatch ? parseInt(pageMatch[1]) : "?";
        const pageStatus = pages <= 7 ? "✅" : "🔴 OVER LIMIT";
        results.push(`📄 Pages: ${pages} ${pageStatus} (limit: 6 body + 1 ref)`);

        // Errors
        const errors = (compile.output.match(/^!.*/gm) || []);
        if (errors.length > 0) {
          results.push(`❌ Errors (${errors.length}):`);
          errors.slice(0, 5).forEach((e) => results.push(`   ${e}`));
        } else {
          results.push("✅ Zero errors");
        }

        // Overfull boxes (deduplicated across passes)
        const overfullAll = (compile.output.match(/Overfull \\[hv]box.*/g) || []);
        const overfull = [...new Set(overfullAll)];
        if (overfull.length > 0) {
          results.push(`⚠️  Overfull boxes (${overfull.length}):`);
          overfull.forEach((o) => {
            const pts = o.match(/([\d.]+)pt too wide/);
            const severity = pts && parseFloat(pts[1]) > 10 ? "🔴" : "🟡";
            results.push(`   ${severity} ${o.slice(0, 80)}`);
          });
        } else {
          results.push("✅ No overfull boxes");
        }

        // Underfull boxes
        const underfull = (compile.output.match(/Underfull \\[hv]box.*/g) || []);
        if (underfull.length > 0) {
          results.push(`ℹ️  Underfull boxes: ${underfull.length}`);
        }

        // Undefined references (only from final pass)
        const finalPass = compile.output.split("Output written").slice(-2)[0] || "";
        const undefRefs = [...new Set(finalPass.match(/Warning.*undefined/gi) || [])];
        if (undefRefs.length > 0) {
          results.push(`⚠️  Undefined references (${undefRefs.length}):`);
          undefRefs.slice(0, 5).forEach((r) => results.push(`   ${r.trim()}`));
        }

        // TODO markers
        const todoResult = await runShell(
          `grep -n '\\[TODO' ${TEX_FILE} | head -20`,
          PAPER_DIR,
        );
        const todos = (todoResult.output || "").trim().split("\n").filter(Boolean);
        if (todos.length > 0) {
          results.push(`📝 TODOs (${todos.length}):`);
          todos.forEach((t) => {
            const short = t.length > 78 ? t.slice(0, 75) + "..." : t;
            results.push(`   ${short}`);
          });
        } else {
          results.push("✅ No TODO markers");
        }

        // Abstract word count
        const wcResult = await runShell(
          `sed -n '/\\\\begin{abstract}/,/\\\\end{abstract}/p' ${TEX_FILE} | ` +
            `sed 's/\\\\[a-zA-Z]*{[^}]*}//g; s/\\\\[a-zA-Z]*//g; s/[{}~$]//g' | ` +
            `wc -w`,
          PAPER_DIR,
        );
        const wc = parseInt((wcResult.output || "").trim());
        if (!isNaN(wc)) {
          const wcStatus = wc <= 250 ? "✅" : "🔴 OVER LIMIT";
          results.push(`📊 Abstract: ~${wc} words ${wcStatus} (limit: 250)`);
        }

        // BibTeX warnings
        const bibResult = await runShell("cat main.blg", PAPER_DIR);
        const bibWarnings = (bibResult.output || "").match(/Warning--.*/g) || [];
        if (bibWarnings.length > 0) {
          results.push(`📚 BibTeX warnings (${bibWarnings.length}):`);
          bibWarnings.slice(0, 5).forEach((w) => results.push(`   ${w}`));
        }

        return results.join("\n");
      },
    },
  ],
});
