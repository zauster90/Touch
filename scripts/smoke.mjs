#!/usr/bin/env node
/** Calls every MCP tool once against a running TD. Run: `npm run smoke`. */
import * as api from "../dist/index.js";

const checks = [
  ["td_pane", () => api.paneTool()],
  ["td_selection", () => api.selectionTool()],
  ["td_operators", () => api.operatorsTool({ path: "/" })],
  ["td_errors", () => api.errorsTool()],
  ["td_params (read)", () => api.paramsTool({ path: "/" })],
  ["td_graph", () => api.graphTool({ path: "/", depth: 1 })],
  ["td_execute", () => api.executeTool({ code: "print('smoke ok')" })],
  ["td_layout (preview)", () => api.layoutTool({ path: "/project1", apply: false })],
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
