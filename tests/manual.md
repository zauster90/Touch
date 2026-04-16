# Manual Test Checklist

End-to-end tests that require a running TouchDesigner. Unit tests (`npm run test`, `pytest tests/python_api_test.py`) cover the code in isolation; everything in this file validates the integration.

Work through the sections in order on a clean TD session.

## 1. Prereqs

- [ ] TouchDesigner 2025 or newer installed.
- [ ] Node 20 or newer installed (`node --version`).
- [ ] Python 3.11+ on PATH (matches TD's embedded interpreter, used only for the `pytest` suite).
- [ ] This repo cloned locally. Note the absolute path — you'll need it below.

## 2. Initial setup (one-time)

1. From the repo root:
   ```bash
   npm install
   npm run build
   ```
2. Launch TouchDesigner once and drag `toe/TouchAPI.tox` into `/project1`. The first launch generates the token file at `%APPDATA%\claude-td\token` (Windows) or `~/.config/claude-td/token` (POSIX). Subsequent launches reuse it.
3. Confirm the token file exists:
   - Windows: `type %APPDATA%\claude-td\token`
   - POSIX: `cat ~/.config/claude-td/token`

## 3. Smoke tests (every run)

1. **Start TD.** Open a blank project.
2. **Drop the component.** Drag `toe/TouchAPI.tox` into `/project1`.
3. **Check Status.** Select the `TouchAPI` node. The `Status` custom parameter should read `READY @ 127.0.0.1:44444`. If it reads `stopped`, open TD's textport (`Alt+T`) and fix any Python errors before continuing.
4. **Run the smoke script.** From the repo root in a shell:
   ```bash
   npm run build && npm run smoke
   ```
   Expect 7 `OK` lines — one per tool (`td_pane`, `td_selection`, `td_operators`, `td_errors`, `td_params`, `td_graph`, `td_execute`). `td_screenshot` and `td_create` are exercised via Claude in section 4.
5. **Install the plugin in Claude Code.** In a Claude Code session:
   ```
   /plugin install <absolute-path-to-this-repo>
   ```
6. **Talk to Claude.** Ask: *"Tell me the current TD network path."* Claude should call `td_pane` and respond with a path like `/project1`.

## 4. Per-tool exercises

Phrase each prompt naturally to Claude. The "Tool used" column is what Claude should pick.

| Ask Claude this | Tool used | Expect |
|---|---|---|
| "What network am I looking at?" | `td_pane` | Text response with `networkPath`, `x`, `y`, `zoom`. |
| "What's currently selected?" | `td_selection` | List of selected operator paths (empty if nothing selected). |
| "List the children under `/project1`." | `td_operators` | Array of operators with `name`, `path`, `type`, `opType`. |
| "What are the current params on `/project1/geo1`?" (or any existing op) | `td_params` (read) | Map of parameter names → current values. |
| "Set `tx` to 5 on `/project1/geo1`." | `td_params` (patch) | Confirmation message; verify in TD that `tx` now reads 5. |
| "Run `print(project.folder)` in TD." | `td_execute` | Claude replies with TD's project folder path in stdout. |
| "What errors are in the project?" | `td_errors` | Array (possibly empty) of `{path, severity, message}`. |
| "Screenshot the `out1` render TOP." | `td_screenshot` | Inline PNG image of the current frame. (Create a `renderTOP` named `out1` first if none exists.) |
| "Export the graph under `/project1` to depth 2." | `td_graph` | JSON with `nodes[]` and implicit wires via each node's `inputs`. |
| "Create a `noiseTOP` under `/project1` named `n1`." | `td_create` | New operator appears in the network. Verify visually in TD. |

## 5. Security checks

Run these from a terminal. On Windows, pull the token with `set /p TOKEN=<%APPDATA%\claude-td\token`; on POSIX use `export TOKEN=$(cat ~/.config/claude-td/token)`.

1. **No auth → 401.**
   ```bash
   curl -i http://127.0.0.1:44444/pane
   ```
   Expect `HTTP/1.0 401`.
2. **Wrong token → 401.**
   ```bash
   curl -i -H "Authorization: Bearer wrong" http://127.0.0.1:44444/pane
   ```
   Expect `HTTP/1.0 401`.
3. **Spoofed Host → 403.**
   ```bash
   curl -i -H "Authorization: Bearer $TOKEN" -H "Host: evil.example" http://127.0.0.1:44444/pane
   ```
   Expect `HTTP/1.0 403` (DNS-rebinding guard).
4. **Correct token + allowed host → 200.**
   ```bash
   curl -i -H "Authorization: Bearer $TOKEN" http://127.0.0.1:44444/pane
   ```
   Expect `HTTP/1.0 200` with a JSON body.

## 6. Token rotation test

1. In TD, select the `TouchAPI` node and press the **Rotate token** pulse parameter.
2. Verify the token file mtime updated:
   - Windows: `dir %APPDATA%\claude-td\token`
   - POSIX: `stat ~/.config/claude-td/token`
3. In Claude Code, ask *"Tell me the current TD network path"* — it should fail with a 401 because Claude's MCP server cached the old token at startup.
4. Restart Claude Code. Ask the same question again — it should succeed with the new token.

## 7. Teardown

1. Close the TD project. The HTTP server stops via the `onExit` hook on the `bootstrap` Execute DAT.
2. The token file remains on disk. This is intentional — the next session reuses it, so Claude's MCP server doesn't have to be restarted every time you open TD.
3. To fully reset, delete the token file manually and rotate via the TouchAPI component on next launch.
