import { afterAll, beforeAll, describe, expect, it } from "vitest";
import http from "node:http";
import { AddressInfo } from "node:net";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

// ---- ephemeral token file ----
let tokenDir: string;
let tokenPath: string;
const TOKEN = "test-token-xyz";

// ---- fake TD HTTP server ----
let server: http.Server;
const calls: Array<{ method: string; url: string; headers: Record<string,string>; body: string }> = [];

beforeAll(async () => {
  tokenDir = fs.mkdtempSync(path.join(os.tmpdir(), "claude-td-"));
  tokenPath = path.join(tokenDir, "token");
  fs.writeFileSync(tokenPath, TOKEN);
  process.env.CLAUDE_TD_TOKEN_PATH = tokenPath;

  server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      calls.push({
        method: req.method!,
        url: req.url!,
        headers: req.headers as Record<string,string>,
        body: Buffer.concat(chunks).toString("utf8"),
      });
      const url = req.url!;
      if (url.startsWith("/pane"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({networkPath:"/",x:0,y:0,zoom:1}));
      if (url.startsWith("/selection"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({operators:[]}));
      if (url.startsWith("/operators"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({path:"/",operators:[]}));
      if (url.startsWith("/errors"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({errors:[],warnings:[]}));
      if (url.startsWith("/execute"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({success:true, stdout:"", stderr:"", from_op:"/"}));
      if (url.startsWith("/params"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({path:"/foo", params:{tx:0}}));
      if (url.startsWith("/graph"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({path:"/", depth:2, nodes:[]}));
      if (url.startsWith("/create"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({path:"/geoA", name:"geoA", type:"geo"}));
      if (url.startsWith("/screenshot"))
        return res.writeHead(200, {"content-type":"image/png"}).end(Buffer.from([0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a]));
      res.writeHead(404).end();
    });
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const port = (server.address() as AddressInfo).port;
  process.env.TDAPI_PORT = String(port);
});

afterAll(async () => {
  await new Promise<void>((r) => server.close(() => r()));
  fs.rmSync(tokenDir, { recursive: true, force: true });
});

describe("td() client", () => {
  it("sends bearer token and host: localhost", async () => {
    const { td } = await import("../src/index.js");
    const res = await td("/pane");
    expect(res.networkPath).toBe("/");
    const call = calls.at(-1)!;
    expect(call.headers.authorization).toBe(`Bearer ${TOKEN}`);
    expect(call.headers.host?.startsWith("localhost")).toBe(true);
  });

  it("screenshot returns base64 PNG content", async () => {
    const { screenshotTool } = await import("../src/index.js");
    const out = await screenshotTool({ path: "/foo" });
    expect(out.content[0].type).toBe("image");
    expect(out.content[0].mimeType).toBe("image/png");
    // Base64 of PNG magic bytes starts with "iVBORw0KGgo"
    expect(out.content[0].data.startsWith("iVBORw0KGgo")).toBe(true);
  });

  it("404 on bad route throws with message", async () => {
    const { td } = await import("../src/index.js");
    await expect(td("/nope")).rejects.toThrow(/404/);
  });

  it("operatorsTool wraps response in MCP text content", async () => {
    const { operatorsTool } = await import("../src/index.js");
    const out = await operatorsTool({ path: "/" });
    expect(out.content[0].type).toBe("text");
    expect(out.content[0].text).toContain('"path": "/"');
  });

  it("paramsTool with params sends PATCH", async () => {
    const { paramsTool } = await import("../src/index.js");
    calls.length = 0;
    await paramsTool({ path: "/foo", params: { tx: 5 } });
    const call = calls.at(-1)!;
    expect(call.method).toBe("PATCH");
    expect(JSON.parse(call.body).tx).toBe(5);
  });

  it("createTool sends POST with JSON body", async () => {
    const { createTool } = await import("../src/index.js");
    calls.length = 0;
    await createTool({ type: "geo", parent: "/", name: "geoA" });
    const call = calls.at(-1)!;
    expect(call.method).toBe("POST");
    expect(JSON.parse(call.body).type).toBe("geo");
  });
});
