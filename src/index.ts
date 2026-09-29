#!/usr/bin/env node
/**
 * Touch — TouchDesigner MCP server.
 *
 * Connects to a Python HTTP server bound to 127.0.0.1 inside TouchDesigner.
 * Every request carries a bearer token sourced from %APPDATA%/claude-td/token
 * (or CLAUDE_TD_TOKEN_PATH for tests).
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { z } from "zod";

// ---------------------------------------------------------------------------
// Config + token
// ---------------------------------------------------------------------------

// Use "localhost" in the URL so undici auto-sets `Host: localhost:<port>`,
// which satisfies the Python server's DNS-rebinding allow-list. The hostname
// resolves to 127.0.0.1 on the loopback interface.
const HOST = "localhost";
function getPort(): number {
  return Number(process.env.TDAPI_PORT ?? 44444);
}

function tokenPath(): string {
  if (process.env.CLAUDE_TD_TOKEN_PATH) return process.env.CLAUDE_TD_TOKEN_PATH;
  if (process.platform === "win32") {
    const base = process.env.APPDATA ?? path.join(os.homedir(), "AppData", "Roaming");
    return path.join(base, "claude-td", "token");
  }
  return path.join(os.homedir(), ".config", "claude-td", "token");
}

function loadToken(): string {
  const p = tokenPath();
  if (!fs.existsSync(p)) {
    throw new Error(
      `Touch token not found at ${p}. Load the TouchAPI.tox into your TD project first.`
    );
  }
  return fs.readFileSync(p, "utf8").trim();
}

// ---------------------------------------------------------------------------
// HTTP helper (exported for tests)
// ---------------------------------------------------------------------------

type BinaryResult = { __binary: true; mimeType: string; buffer: Buffer };

// Actionable hints for the failure modes users actually hit.
const STATUS_HINTS: Record<number, string> = {
  401: "Token mismatch — the TouchAPI token was rotated or regenerated. The token file is re-read on every call, so just retry; if it persists, rebuild TouchAPI.",
  403: "Host header rejected — requests must target localhost/127.0.0.1.",
  404: "Unknown endpoint or operator. If the endpoint is new, rebuild the TouchAPI component from toe/install.py.",
  504: "TouchDesigner's main thread didn't pick up the request in time — check for an open dialog or a very long cook.",
};

export async function td(endpoint: string, init: RequestInit = {}): Promise<any> {
  const url = `http://${HOST}:${getPort()}${endpoint}`;
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${loadToken()}`);
  // Note: undici (Node's fetch) forbids setting the `Host` header directly —
  // it is derived from the URL authority. We use `localhost` in the URL so
  // the resulting header is `Host: localhost:<port>`.
  let res: Response;
  try {
    res = await fetch(url, { ...init, headers });
  } catch (e: any) {
    // Dual-stack `localhost` can surface as an AggregateError (::1 and 127.0.0.1).
    const code = e?.cause?.code ?? e?.cause?.errors?.[0]?.code ?? e?.code;
    if (code === "ECONNREFUSED" || code === "ECONNRESET") {
      throw new Error(
        `Can't reach TouchDesigner at ${HOST}:${getPort()} (${code}). Is TD open with the TouchAPI component loaded and its Status reading READY?`
      );
    }
    throw e;
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    const hint = STATUS_HINTS[res.status];
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ""}${hint ? `\n${hint}` : ""}`);
  }
  const ct = res.headers.get("content-type") ?? "";
  if (ct.startsWith("image/")) {
    return { __binary: true, mimeType: ct, buffer: Buffer.from(await res.arrayBuffer()) } as BinaryResult;
  }
  return res.json();
}

function isBinary(r: unknown): r is BinaryResult {
  return !!r && typeof r === "object" && (r as any).__binary === true;
}

// ---------------------------------------------------------------------------
// Tool handlers (exported for direct test)
// ---------------------------------------------------------------------------

const asText = (obj: unknown) => ({ content: [{ type: "text" as const, text: JSON.stringify(obj, null, 2) }] });

export async function executeTool({ code, from_op }: { code: string; from_op?: string }) {
  const q = from_op ? `?from_op=${encodeURIComponent(from_op)}` : "";
  return asText(await td(`/execute${q}`, {
    method: "POST",
    body: code,
    headers: { "Content-Type": "text/plain" },
  }));
}
export async function paneTool() { return asText(await td("/pane")); }
export async function selectionTool() { return asText(await td("/selection")); }
export async function operatorsTool({ path: p }: { path?: string } = {}) {
  return asText(await td(`/operators?path=${encodeURIComponent(p ?? "/")}`));
}
export async function paramsTool({ path: p, params, names, detail, nondefault }: {
  path: string; params?: Record<string, unknown>; names?: string[]; detail?: boolean; nondefault?: boolean;
}) {
  if (params !== undefined) {
    return asText(await td(`/params?${new URLSearchParams({ path: p })}`, {
      method: "PATCH",
      body: JSON.stringify(params),
      headers: { "Content-Type": "application/json" },
    }));
  }
  const q = new URLSearchParams({ path: p });
  if (names?.length) q.set("names", names.join(","));
  if (detail) q.set("detail", "1");
  if (nondefault) q.set("nondefault", "1");
  return asText(await td(`/params?${q}`));
}
export async function errorsTool({ path: p, depth }: { path?: string; depth?: number } = {}) {
  const q = new URLSearchParams({ path: p ?? "/" });
  if (depth !== undefined) q.set("depth", String(depth));
  return asText(await td(`/errors?${q}`));
}
export async function screenshotTool({ path: p, format }: { path: string; format?: "png" | "jpg" }) {
  const q = new URLSearchParams({ path: p });
  if (format) q.set("format", format);
  const r = await td(`/screenshot?${q}`);
  if (!isBinary(r)) return asText(r);
  return {
    content: [
      { type: "image" as const, data: r.buffer.toString("base64"), mimeType: r.mimeType },
    ],
  };
}
export async function graphTool({ path: p, depth }: { path?: string; depth?: number } = {}) {
  const q = `?path=${encodeURIComponent(p ?? "/")}&depth=${depth ?? 2}`;
  return asText(await td(`/graph${q}`));
}
export async function createTool(args: {
  type: string; parent: string; name?: string; pos?: [number, number]; inputs?: string[];
  params?: Record<string, unknown>;
}) {
  return asText(await td("/create", {
    method: "POST",
    body: JSON.stringify(args),
    headers: { "Content-Type": "application/json" },
  }));
}

const postJson = (endpoint: string, body: unknown) =>
  td(endpoint, { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });

export async function chopTool({ path: p, chan, samples }: { path: string; chan?: string; samples?: number }) {
  const q = new URLSearchParams({ path: p });
  if (chan !== undefined) q.set("chan", chan);
  if (samples !== undefined) q.set("samples", String(samples));
  return asText(await td(`/chop?${q}`));
}
export async function datTool({ path: p, rows, cols }: { path: string; rows?: number; cols?: number }) {
  const q = new URLSearchParams({ path: p });
  if (rows !== undefined) q.set("rows", String(rows));
  if (cols !== undefined) q.set("cols", String(cols));
  return asText(await td(`/dat?${q}`));
}
export async function perfTool({ path: p, depth, top }: { path?: string; depth?: number; top?: number } = {}) {
  const q = new URLSearchParams({ path: p ?? "/" });
  if (depth !== undefined) q.set("depth", String(depth));
  if (top !== undefined) q.set("top", String(top));
  return asText(await td(`/perf?${q}`));
}
export async function connectTool(args: { from: string; to: string; inputIndex?: number; outputIndex?: number }) {
  return asText(await postJson("/connect", args));
}
export async function disconnectTool(args: { to: string; inputIndex?: number }) {
  return asText(await postJson("/disconnect", args));
}
export async function deleteTool(args: { path: string }) {
  return asText(await postJson("/delete", args));
}
export async function bindTool(args: { path: string; param: string; expr?: string; mode?: string; val?: unknown }) {
  return asText(await postJson("/bind", args));
}
export async function layoutTool(args: {
  path: string; apply?: boolean; direction?: "LR" | "TB";
  spacing?: { rank: number; node: number }; rank_gap?: number; node_gap?: number;
  selection_only?: boolean; exclude?: string[];
}) {
  return asText(await postJson("/layout", args));
}

export async function infoTool({ path: p }: { path: string }) {
  return asText(await td(`/info?${new URLSearchParams({ path: p })}`));
}
export async function findTool(args: {
  path?: string; name?: string; type?: string; family?: string; errors?: boolean; depth?: number; limit?: number;
} = {}) {
  const q = new URLSearchParams({ path: args.path ?? "/" });
  if (args.name) q.set("name", args.name);
  if (args.type) q.set("type", args.type);
  if (args.family) q.set("family", args.family);
  if (args.errors) q.set("errors", "1");
  if (args.depth !== undefined) q.set("depth", String(args.depth));
  if (args.limit !== undefined) q.set("limit", String(args.limit));
  return asText(await td(`/find?${q}`));
}
export async function typesTool({ family, filter }: { family?: string; filter?: string } = {}) {
  const q = new URLSearchParams();
  if (family) q.set("family", family);
  if (filter) q.set("filter", filter);
  return asText(await td(`/types?${q}`));
}
export async function datWriteTool(args: { path: string; text?: string; rows?: unknown[][]; append?: boolean }) {
  return asText(await postJson("/dat/write", args));
}
export async function nodeTool(args: {
  path: string; name?: string; pos?: [number, number]; color?: [number, number, number]; comment?: string;
  bypass?: boolean; display?: boolean; render?: boolean; lock?: boolean; viewer?: boolean;
}) {
  return asText(await postJson("/node", args));
}
export async function copyTool(args: { path: string; parent?: string; name?: string; pos?: [number, number] }) {
  return asText(await postJson("/copy", args));
}
export async function customParTool(args: {
  path: string; name: string; style?: string; page?: string; label?: string; size?: number;
  default?: unknown; min?: number; max?: number; clamp?: boolean; menuNames?: string[]; menuLabels?: string[];
}) {
  return asText(await postJson("/custom_par", args));
}
export async function projectTool(args: { play?: boolean; frame?: number; rate?: number; save?: boolean | string } = {}) {
  const mutating = Object.values(args).some((v) => v !== undefined);
  return asText(mutating ? await postJson("/project", args) : await td("/project"));
}

// ---------------------------------------------------------------------------
// MCP server wiring
// ---------------------------------------------------------------------------

const opPath = z.string().describe("Absolute operator path, e.g. '/project1/noise1'");
const xy = z.tuple([z.number(), z.number()]);

function buildServer(): McpServer {
  const server = new McpServer({ name: "touch", version: "0.4.0" });

  // ---- orient ------------------------------------------------------------

  server.registerTool("td_project", {
    title: "Project info & timeline control",
    description: "With no args: project name/folder, TD version, cook rate, timeline (frame, play, rate). "
      + "Pass `play`, `frame`, or `rate` to drive the timeline, or `save` (true = in place, or a .toe path) to save the project.",
    inputSchema: {
      play: z.boolean().optional().describe("true = play, false = pause"),
      frame: z.number().optional().describe("Jump the timeline to this frame"),
      rate: z.number().optional().describe("Timeline FPS"),
      save: z.union([z.boolean(), z.string()]).optional().describe("true = save in place; a string = save as that .toe path"),
    },
  }, projectTool);

  server.registerTool("td_pane", {
    title: "Get network editor pane state",
    description: "Current pane: networkPath, x, y, zoom.",
    inputSchema: {},
  }, paneTool);

  server.registerTool("td_selection", {
    title: "Get selected operators",
    description: "Operators currently selected in the active pane.",
    inputSchema: {},
  }, selectionTool);

  // ---- read topology -----------------------------------------------------

  server.registerTool("td_operators", {
    title: "List operators at a path",
    description: "List direct children of the operator at `path` (default '/').",
    inputSchema: { path: z.string().optional() },
  }, operatorsTool);

  server.registerTool("td_find", {
    title: "Search for operators",
    description: "Recursive search under `path` (default '/'). Filter by `name`/`type` glob (e.g. 'noise*', '*TOP'), "
      + "`family` (TOP, CHOP, SOP, DAT, COMP, MAT, POP), or `errors: true` for only ops with errors/warnings. "
      + "Cheaper than td_graph when you know what you're looking for.",
    inputSchema: {
      path: z.string().optional(),
      name: z.string().optional().describe("fnmatch glob on op name"),
      type: z.string().optional().describe("fnmatch glob on op type, e.g. 'glsl*'"),
      family: z.string().optional(),
      errors: z.boolean().optional(),
      depth: z.number().optional().describe("Recursion depth (default 64)"),
      limit: z.number().optional().describe("Max results (default 100)"),
    },
  }, findTool);

  server.registerTool("td_graph", {
    title: "Export a subgraph as JSON",
    description: "Structured graph of a subnet: nodes + implied wires via `inputs`.",
    inputSchema: { path: z.string().optional(), depth: z.number().optional() },
  }, graphTool);

  server.registerTool("td_info", {
    title: "Inspect one operator",
    description: "Everything about one op in one call: inputs AND outputs (who it feeds), flags (bypass/display/render/lock), "
      + "errors/warnings, cook stats, and family facts — TOP resolution, CHOP channel names/sample rate, SOP point counts, "
      + "DAT size, COMP children + custom pages. Start here when debugging a specific node.",
    inputSchema: { path: opPath },
  }, infoTool);

  server.registerTool("td_types", {
    title: "List creatable operator types",
    description: "Exact type names for td_create (e.g. 'noiseTOP', 'audiofileinCHOP'), optionally by `family` and substring `filter`. "
      + "Use this instead of guessing a type name.",
    inputSchema: {
      family: z.string().optional().describe("TOP, CHOP, SOP, DAT, COMP, MAT, POP"),
      filter: z.string().optional().describe("Case-insensitive substring, e.g. 'noise'"),
    },
  }, typesTool);

  // ---- read data ---------------------------------------------------------

  server.registerTool("td_params", {
    title: "Read or write parameters",
    description: "Read: omit `params`. Narrow with `names` (globs like 'res*'), `nondefault: true` (only changed/expression-driven pars), "
      + "and `detail: true` (mode, expr, default, range, menu options). "
      + "Write: `params: { tx: 5, resolutionw: 1920 }`. A value `{ expr: \"absTime.seconds\" }` sets an expression; "
      + "`true` on a pulse param (reset, reload, ...) fires it. Unknown params are reported under `failed`.",
    inputSchema: {
      path: opPath,
      params: z.record(z.any()).optional().describe("Params to write; omit to read"),
      names: z.array(z.string()).optional(),
      detail: z.boolean().optional(),
      nondefault: z.boolean().optional(),
    },
  }, paramsTool);

  server.registerTool("td_chop", {
    title: "Read CHOP channel data",
    description: "Sample channels off a CHOP. Optional `chan` name filter and `samples` cap (default 16). See the actual numbers flowing through the network.",
    inputSchema: { path: opPath, chan: z.string().optional(), samples: z.number().optional() },
  }, chopTool);

  server.registerTool("td_dat", {
    title: "Read DAT table contents",
    description: "Read a DAT's cells (capped by `rows`/`cols`) plus its raw text.",
    inputSchema: { path: opPath, rows: z.number().optional(), cols: z.number().optional() },
  }, datTool);

  server.registerTool("td_errors", {
    title: "Get project errors and warnings",
    description: "Errors/warnings for every op under `path` (default '/', the whole project).",
    inputSchema: { path: z.string().optional(), depth: z.number().optional() },
  }, errorsTool);

  server.registerTool("td_perf", {
    title: "Probe cook performance",
    description: "Slowest-cooking operators under `path`, by last cook time (ms). Use to find what to optimize.",
    inputSchema: { path: z.string().optional(), depth: z.number().optional(), top: z.number().optional() },
  }, perfTool);

  server.registerTool("td_screenshot", {
    title: "Screenshot a TOP",
    description: "Image of a TOP's current frame for visual inspection. `format: 'jpg'` is much smaller than png — prefer it for quick checks.",
    inputSchema: { path: opPath, format: z.enum(["png", "jpg"]).optional() },
  }, screenshotTool);

  // ---- build & edit ------------------------------------------------------

  server.registerTool("td_create", {
    title: "Create an operator",
    description: "Create an operator of `type` (e.g. 'noiseTOP' — see td_types) under `parent`. Optional `name`, `pos`, "
      + "`inputs` (paths wired to inputs 0..n), and `params` (same format as td_params writes). "
      + "Without `pos`, it's placed just right of its first input. Wiring/param problems come back as `warnings`.",
    inputSchema: {
      type: z.string(),
      parent: z.string(),
      name: z.string().optional(),
      pos: xy.optional(),
      inputs: z.array(z.string()).optional(),
      params: z.record(z.any()).optional(),
    },
  }, createTool);

  server.registerTool("td_copy", {
    title: "Duplicate an operator",
    description: "Copy an op (COMPs copy their whole subnet) into `parent` (default: same parent) as `name`. Wires are not copied.",
    inputSchema: { path: opPath, parent: z.string().optional(), name: z.string().optional(), pos: xy.optional() },
  }, copyTool);

  server.registerTool("td_node", {
    title: "Edit node state",
    description: "Rename (`name`), move (`pos`), recolor (`color` [r,g,b] 0-1), annotate (`comment`), or toggle flags "
      + "(`bypass`, `display`, `render`, `lock`, `viewer`) on an op.",
    inputSchema: {
      path: opPath,
      name: z.string().optional(),
      pos: xy.optional(),
      color: z.tuple([z.number(), z.number(), z.number()]).optional(),
      comment: z.string().optional(),
      bypass: z.boolean().optional(),
      display: z.boolean().optional(),
      render: z.boolean().optional(),
      lock: z.boolean().optional(),
      viewer: z.boolean().optional(),
    },
  }, nodeTool);

  server.registerTool("td_dat_write", {
    title: "Write DAT contents",
    description: "Replace a DAT's `text` (GLSL shaders, Python scripts, JSON) or its table `rows` (list of lists; `append: true` to add). "
      + "After writing a shader, check td_info/td_errors on the GLSL op for compile errors.",
    inputSchema: {
      path: opPath,
      text: z.string().optional(),
      rows: z.array(z.array(z.any())).optional(),
      append: z.boolean().optional(),
    },
  }, datWriteTool);

  server.registerTool("td_connect", {
    title: "Wire two operators",
    description: "Connect `from` op's output into `to` op's input. Indices default to 0.",
    inputSchema: {
      from: z.string(),
      to: z.string(),
      inputIndex: z.number().optional(),
      outputIndex: z.number().optional(),
    },
  }, connectTool);

  server.registerTool("td_disconnect", {
    title: "Remove operator wires",
    description: "Drop wires into `to`. Omit `inputIndex` to clear all inputs.",
    inputSchema: { to: z.string(), inputIndex: z.number().optional() },
  }, disconnectTool);

  server.registerTool("td_delete", {
    title: "Delete an operator",
    description: "Destroy the operator at `path`. Read the graph first — this is irreversible without TD undo.",
    inputSchema: { path: opPath },
  }, deleteTool);

  server.registerTool("td_bind", {
    title: "Bind a parameter reactively",
    description: "Make a parameter reactive. mode 'expression' (default) sets an expr like \"op('audio')['rms']\"; mode 'constant' sets a fixed `val`.",
    inputSchema: {
      path: opPath,
      param: z.string(),
      expr: z.string().optional(),
      mode: z.enum(["expression", "constant"]).optional(),
      val: z.any().optional(),
    },
  }, bindTool);

  server.registerTool("td_custom_par", {
    title: "Add a custom parameter to a COMP",
    description: "Give a COMP a control surface: adds (or replaces) a custom parameter on `page` (default 'Custom'). "
      + "`style`: float, int, toggle, str, menu, strmenu, pulse, rgb, rgba, xy, xyz, file, folder, op, top, chop, python, header... "
      + "`name` is normalized to TD's Capitalized form. Set `min`/`max` (slider range; `clamp: true` to enforce), `default`, `size` (float/int tuples), "
      + "`menuNames`/`menuLabels`. Reference it as op('comp').par.<Name>.",
    inputSchema: {
      path: opPath,
      name: z.string(),
      style: z.string().optional(),
      page: z.string().optional(),
      label: z.string().optional(),
      size: z.number().optional(),
      default: z.any().optional(),
      min: z.number().optional(),
      max: z.number().optional(),
      clamp: z.boolean().optional(),
      menuNames: z.array(z.string()).optional(),
      menuLabels: z.array(z.string()).optional(),
    },
  }, customParTool);

  server.registerTool("td_layout", {
    title: "Auto-arrange a subnet",
    description: "Layered DAG layout (Sugiyama) of the direct children of `path`. Reads wires + tile sizes and untangles crossings. Returns a preview `plan` by default; pass `apply: true` to write nodeX/nodeY. Use `exclude` (names/paths) or `selection_only` to scope it.",
    inputSchema: {
      path: z.string(),
      apply: z.boolean().optional(),
      direction: z.enum(["LR", "TB"]).optional(),
      spacing: z.object({ rank: z.number(), node: z.number() }).optional(),
      rank_gap: z.number().optional(),
      node_gap: z.number().optional(),
      selection_only: z.boolean().optional(),
      exclude: z.array(z.string()).optional(),
    },
  }, layoutTool);

  // ---- escape hatch ------------------------------------------------------

  server.registerTool("td_execute", {
    title: "Execute Python in TouchDesigner",
    description: "Run Python inside TD with the textport's globals (op, parent, absTime, tdu, noiseTOP, ...). "
      + "`me` is the `from_op` operator (default '/'). Returns stdout, stderr, and `result` — the value of the last line "
      + "if it's an expression (OPs come back as their path). Errors include the failing `line` and a trimmed traceback. "
      + "Prefer the structured tools when one fits.",
    inputSchema: {
      code: z.string(),
      from_op: z.string().optional().describe("Operator path bound to `me`"),
    },
  }, executeTool);

  return server;
}

async function main() {
  const server = buildServer();
  await server.connect(new StdioServerTransport());
}

// Only auto-run when executed as a script, not when imported by tests.
// Robust against drive-letter/slash differences on Windows.
const entry = process.argv[1] ? process.argv[1].replace(/\\/g, "/") : "";
const importedAs = import.meta.url.replace(/^file:\/\//, "").replace(/^\/([A-Za-z]:)/, "$1");
if (entry && importedAs.toLowerCase().endsWith(entry.toLowerCase())) {
  main().catch((e) => { console.error(e); process.exit(1); });
}
