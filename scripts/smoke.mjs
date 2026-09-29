#!/usr/bin/env node
/** Calls every read-only MCP tool once against a running TD. Run: `npm run smoke`. */
import * as api from "../dist/index.js";

const checks = [
  ["td_pane", () => api.paneTool()],
  ["td_selection", () => api.selectionTool()],
  ["td_operators", () => api.operatorsTool({ path: "/" })],
  ["td_errors", () => api.errorsTool()],
  ["td_params (read)", () => api.paramsTool({ path: "/" })],
  ["td_graph", () => api.graphTool({ path: "/", depth: 1 })],
  ["td_perf", () => api.perfTool({ path: "/", depth: 2 })],
  ["td_execute", () => api.executeTool({ code: "print('smoke ok')\n1 + 1" })],
  ["td_project", () => api.projectTool()],
  ["td_find", () => api.findTool({ path: "/", depth: 2, limit: 5 })],
  ["td_info", () => api.infoTool({ path: "/project1" })],
  ["td_types", () => api.typesTool({ family: "TOP", filter: "noise" })],
];

let failed = 0;
for (const [name, fn] of checks) {
  try {
    const out = await fn();
    const txt = out.content?.[0]?.text ?? "<non-text>";
    console.log(`OK  ${name} — ${txt.slice(0, 80).replace(/\n/g, " ")}`);
  } catch (e) {
    console.error(`FAIL ${name} — ${e.message}`);
    failed++;
  }
}
process.exit(failed ? 1 : 0);
