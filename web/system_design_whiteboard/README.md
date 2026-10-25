# System design whiteboard

This local demo embeds the official Excalidraw React component and sends explicit shape/arrow changes to the loopback interview service. It does not inspect another browser tab or capture the screen.

## Run the synthetic demo

From the repository root, install the project MCP extra and build the board:

```powershell
python -m pip install -e ".[voice-probe]"
cd web/system_design_whiteboard
npm ci
npm run build
cd ../..
python -m spikes.system_design_whiteboard_probe.server
```

Register that Python command as a separate local MCP server. Call `start_whiteboard_session`; open the returned `board_url`, draw only synthetic content, then read saved changes with `read_whiteboard_changes(session_id, after_cursor)`.

For frontend development, keep the Python server running and use `npm run dev` from this directory. Vite proxies API and self-hosted Excalidraw asset requests to `127.0.0.1:8765`.

The demo uses the local JSONL event store and the existing size/sequence validation. It is not yet attached to a system design interview session or candidate report. Do not enter real personal or confidential information.
