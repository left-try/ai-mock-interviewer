# System design whiteboard

This local UI embeds the official Excalidraw React component. It sends normalized shape, arrow, and free-text-note changes to the loopback interview MCP service, scoped to the current interview session. It does not inspect another browser tab or capture the screen.

## Build and run with System Design interview

From the repository root, install the project MCP extra and build the board:

```powershell
python -m pip install -e ".[voice-probe]"
Push-Location web/system_design_whiteboard
npm ci
npm run build
Pop-Location
python tools/voice_probe.py
```

Configure `tools/voice_probe.py` as the project's local `interviewer` MCP server as described in `docs/GETTING_STARTED.md`. After restarting Codex Desktop, start the `system-design-interviewer` skill. It creates one interview session and returns the matching local board link when consent is given.

For frontend development, keep the MCP server running and use `npm run dev` from this directory. Vite proxies API and self-hosted Excalidraw asset requests to `127.0.0.1:8765`.

The app records node and edge changes plus standalone text notes, with event IDs, sequence validation, idempotent retries, payload limits, and cursor-based reads. The MCP service stores answers before reading board deltas and validates report references against the saved session. Do not enter personal or confidential information on the board.
