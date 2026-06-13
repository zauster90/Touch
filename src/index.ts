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

export async function td(endpoint: string, init: RequestInit = {}): Promise<any> {
  const url = `http://${HOST}:${getPort()}${endpoint}`;
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${loadToken()}`);
  // Note: undici (Node's fetch) forbids setting the `Host` header directly —
  // it is derived from the URL authority. We use `localhost` in the URL so
  // the resulting header is `Host: localhost:<port>`.
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ""}`);
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
export async function paramsTool({ path: p, params }: { path: string; params?: Record<string, unknown> }) {
  const q = `?path=${encodeURIComponent(p)}`;
  if (params !== undefined) {
    return asText(await td(`/params${q}`, {
      method: "PATCH",
      body: JSON.stringify(params),
      headers: { "Content-Type": "application/json" },
    }));
  }
  return asText(await td(`/params${q}`));
}
export async function errorsTool() { return asText(await td("/errors")); }
export async function screenshotTool({ path: p }: { path: string; width?: number; height?: number }) {
  const q = `?path=${encodeURIComponent(p)}`;
  const r = await td(`/screenshot${q}`);
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

// ---------------------------------------------------------------------------
// MCP server wiring
// ---------------------------------------------------------------------------

function buildServer(): McpServer {
  const server = new McpServer({ name: "touch", version: "0.3.0" });

  server.registerTool("td_execute", {
    title: "Execute Python in TouchDesigner",
    description: "Run Python code inside TD. `me` refers to the from_op context operator.",
    inputSchema: { code: z.string(), from_op: z.string().optional() },
  }, executeTool);

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

  server.registerTool("td_operators", {
    title: "List operators at a path",
    description: "List direct children of the operator at `path` (default '/').",
    inputSchema: { path: z.string().optional() },
  }, operatorsTool);

  server.registerTool("td_params", {
    title: "Read or write parameters",
    description: "Read params (omit `params`) or patch them (`{ tx: 5 }` style).",
    inputSchema: { path: z.string(), params: z.record(z.any()).optional() },
  }, paramsTool);

  server.registerTool("td_errors", {
    title: "Get project errors and warnings",
    description: "Walks the project tree and returns any TD errors/warnings.",
    inputSchema: {},
  }, errorsTool);

  server.registerTool("td_screenshot", {
    title: "Screenshot a TOP",
    description: "Returns a PNG of a TOP's current frame for visual inspection.",
    inputSchema: { path: z.string(), width: z.number().optional(), height: z.number().optional() },
  }, screenshotTool);

  server.registerTool("td_graph", {
    title: "Export a subgraph as JSON",
    description: "Structured graph of a subnet: nodes + implied wires via `inputs`.",
    inputSchema: { path: z.string().optional(), depth: z.number().optional() },
  }, graphTool);

  server.registerTool("td_create", {
    title: "Create an operator",
    description: "Create an operator of `type` under `parent`; optional `name`, `pos`, and `inputs` (paths to wire).",
    inputSchema: {
      type: z.string(),
      parent: z.string(),
      name: z.string().optional(),
      pos: z.tuple([z.number(), z.number()]).optional(),
      inputs: z.array(z.string()).optional(),
    },
  }, createTool);

  server.registerTool("td_chop", {
    title: "Read CHOP channel data",
    description: "Sample channels off a CHOP. Optional `chan` name filter and `samples` cap (default 16). See the actual numbers flowing through the network.",
    inputSchema: { path: z.string(), chan: z.string().optional(), samples: z.number().optional() },
  }, chopTool);

  server.registerTool("td_dat", {
    title: "Read DAT table contents",
    description: "Read a DAT's cells (capped by `rows`/`cols`) plus its raw text.",
    inputSchema: { path: z.string(), rows: z.number().optional(), cols: z.number().optional() },
  }, datTool);

  server.registerTool("td_perf", {
    title: "Probe cook performance",
    description: "Slowest-cooking operators under `path`, by last cook time (ms). Use to find what to optimize.",
    inputSchema: { path: z.string().optional(), depth: z.number().optional(), top: z.number().optional() },
  }, perfTool);

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
    inputSchema: { path: z.string() },
  }, deleteTool);

  server.registerTool("td_bind", {
    title: "Bind a parameter reactively",
    description: "Make a parameter reactive. mode 'expression' (default) sets an expr like \"op('audio')['rms']\"; mode 'constant' sets a fixed `val`.",
    inputSchema: {
      path: z.string(),
      param: z.string(),
      expr: z.string().optional(),
      mode: z.enum(["expression", "constant"]).optional(),
      val: z.any().optional(),
    },
  }, bindTool);

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
