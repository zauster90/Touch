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
      if (url.startsWith("/chop"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({path:"/chop1", numChans:1, channels:[{name:"chan1", numSamples:4, samples:[0,1,2,3]}]}));
      if (url.startsWith("/dat"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({path:"/table1", numRows:2, numCols:2, rows:[["a","b"],["c","d"]]}));
      if (url.startsWith("/perf"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({path:"/", slowest:[]}));
      if (url.startsWith("/connect"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({success:true, from:"/a", to:"/b"}));
      if (url.startsWith("/disconnect"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({success:true, to:"/b"}));
      if (url.startsWith("/delete"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({success:true, deleted:{name:"x"}}));
      if (url.startsWith("/bind"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({success:true, mode:"expression"}));
      if (url.startsWith("/layout"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({mode:"preview", target:"/project1", plan:[], broken_edges:[], overlaps:[], excluded:[], stats:{nodes:2}}));
      const echo = (extra: object = {}) =>
        res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({ok:true, ...extra}));
      if (url.startsWith("/info")) return echo({path:"/a", inputs:[], outputs:[]});
      if (url.startsWith("/find")) return echo({count:0, operators:[]});
      if (url.startsWith("/types")) return echo({types:{TOP:["noiseTOP"]}});
      if (url.startsWith("/dat/write")) return echo({success:true});
      if (url.startsWith("/node")) return echo({changed:{}});
      if (url.startsWith("/copy")) return echo({success:true});
      if (url.startsWith("/custom_par")) return echo({success:true});
      if (url.startsWith("/project")) return echo({project:{name:"t.toe"}});
      if (url.startsWith("/unauthorized")) return res.writeHead(401, "Unauthorized").end();
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

  it("chopTool forwards chan + samples as query params", async () => {
    const { chopTool } = await import("../src/index.js");
    calls.length = 0;
    const out = await chopTool({ path: "/chop1", chan: "tx", samples: 8 });
    const call = calls.at(-1)!;
    expect(call.url).toContain("path=%2Fchop1");
    expect(call.url).toContain("chan=tx");
    expect(call.url).toContain("samples=8");
    expect(out.content[0].text).toContain('"chan1"');
  });

  it("datTool reads a table", async () => {
    const { datTool } = await import("../src/index.js");
    const out = await datTool({ path: "/table1" });
    expect(out.content[0].text).toContain('"numRows": 2');
  });

  it("perfTool defaults path to root", async () => {
    const { perfTool } = await import("../src/index.js");
    calls.length = 0;
    await perfTool();
    expect(calls.at(-1)!.url).toContain("path=%2F");
  });

  it("connectTool sends POST with from/to", async () => {
    const { connectTool } = await import("../src/index.js");
    calls.length = 0;
    await connectTool({ from: "/a", to: "/b" });
    const call = calls.at(-1)!;
    expect(call.method).toBe("POST");
    const sent = JSON.parse(call.body);
    expect(sent.from).toBe("/a");
    expect(sent.to).toBe("/b");
  });

  it("deleteTool sends POST with path", async () => {
    const { deleteTool } = await import("../src/index.js");
    calls.length = 0;
    await deleteTool({ path: "/doomed" });
    const call = calls.at(-1)!;
    expect(call.method).toBe("POST");
    expect(JSON.parse(call.body).path).toBe("/doomed");
  });

  it("bindTool sends expr and mode", async () => {
    const { bindTool } = await import("../src/index.js");
    calls.length = 0;
    await bindTool({ path: "/geo1", param: "tx", expr: "op('lfo1')['chan1']" });
    const call = calls.at(-1)!;
    expect(call.method).toBe("POST");
    expect(JSON.parse(call.body).expr).toBe("op('lfo1')['chan1']");
  });

  it("layoutTool POSTs path + options and previews by default", async () => {
    const { layoutTool } = await import("../src/index.js");
    calls.length = 0;
    const out = await layoutTool({ path: "/project1", direction: "TB", exclude: ["TouchAPI"] });
    const call = calls.at(-1)!;
    expect(call.method).toBe("POST");
    const sent = JSON.parse(call.body);
    expect(sent.path).toBe("/project1");
    expect(sent.direction).toBe("TB");
    expect(sent.exclude).toEqual(["TouchAPI"]);
    expect(out.content[0].text).toContain('"mode": "preview"');
  });
  it("screenshotTool forwards format=jpg", async () => {
    const { screenshotTool } = await import("../src/index.js");
    calls.length = 0;
    await screenshotTool({ path: "/out1", format: "jpg" });
    expect(calls.at(-1)!.url).toContain("format=jpg");
  });

  it("paramsTool read forwards names/detail/nondefault filters", async () => {
    const { paramsTool } = await import("../src/index.js");
    calls.length = 0;
    await paramsTool({ path: "/foo", names: ["res*", "tx"], detail: true, nondefault: true });
    const call = calls.at(-1)!;
    expect(call.method).toBe("GET");
    expect(call.url).toContain("names=res*%2Ctx");
    expect(call.url).toContain("detail=1");
    expect(call.url).toContain("nondefault=1");
  });

  it("errorsTool scopes by path", async () => {
    const { errorsTool } = await import("../src/index.js");
    calls.length = 0;
    await errorsTool({ path: "/project1" });
    expect(calls.at(-1)!.url).toContain("path=%2Fproject1");
  });

  it("findTool forwards filters", async () => {
    const { findTool } = await import("../src/index.js");
    calls.length = 0;
    await findTool({ path: "/project1", name: "noise*", family: "TOP", errors: true, limit: 5 });
    const url = calls.at(-1)!.url;
    expect(url).toContain("name=noise*");
    expect(url).toContain("family=TOP");
    expect(url).toContain("errors=1");
    expect(url).toContain("limit=5");
  });

  it("infoTool and typesTool GET", async () => {
    const { infoTool, typesTool } = await import("../src/index.js");
    calls.length = 0;
    await infoTool({ path: "/a" });
    expect(calls.at(-1)!.url).toBe("/info?path=%2Fa");
    const out = await typesTool({ family: "TOP", filter: "noise" });
    expect(calls.at(-1)!.url).toContain("filter=noise");
    expect(out.content[0].text).toContain("noiseTOP");
  });

  it("mutating tools POST their args as JSON", async () => {
    const api = await import("../src/index.js");
    const cases: Array<[string, () => Promise<unknown>, string]> = [
      ["/dat/write", () => api.datWriteTool({ path: "/s", text: "void main(){}" }), "text"],
      ["/node", () => api.nodeTool({ path: "/n", bypass: true }), "bypass"],
      ["/copy", () => api.copyTool({ path: "/n", name: "n2" }), "name"],
      ["/custom_par", () => api.customParTool({ path: "/c", name: "Speed", style: "float" }), "style"],
    ];
    for (const [endpoint, fn, key] of cases) {
      calls.length = 0;
      await fn();
      const call = calls.at(-1)!;
      expect(call.method).toBe("POST");
      expect(call.url).toBe(endpoint);
      expect(JSON.parse(call.body)).toHaveProperty(key);
    }
  });

  it("projectTool GETs with no args and POSTs with timeline args", async () => {
    const { projectTool } = await import("../src/index.js");
    calls.length = 0;
    await projectTool();
    expect(calls.at(-1)!.method).toBe("GET");
    await projectTool({ play: false });
    expect(calls.at(-1)!.method).toBe("POST");
    expect(JSON.parse(calls.at(-1)!.body)).toEqual({ play: false });
  });

  it("401 error carries a token hint", async () => {
    const { td } = await import("../src/index.js");
    await expect(td("/unauthorized")).rejects.toThrow(/Token mismatch/);
  });

  it("connection refused explains how to start TouchAPI", async () => {
    const { td } = await import("../src/index.js");
    const saved = process.env.TDAPI_PORT;
    const probe = http.createServer();
    await new Promise<void>((r) => probe.listen(0, "127.0.0.1", r));
    const deadPort = (probe.address() as AddressInfo).port;
    await new Promise<void>((r) => probe.close(() => r()));
    process.env.TDAPI_PORT = String(deadPort);
    try {
      await expect(td("/pane")).rejects.toThrow(/TouchAPI component/);
    } finally {
      process.env.TDAPI_PORT = saved;
    }
  });
});

describe("dist bundle", () => {
  it("has exactly one shebang line (node rejects a second one)", () => {
    const head = fs.readFileSync(path.join(__dirname, "..", "dist", "index.js"), "utf8").split("\n", 3);
    expect(head[0]).toBe("#!/usr/bin/env node");
    expect(head[1].startsWith("#!")).toBe(false);
  });
});
