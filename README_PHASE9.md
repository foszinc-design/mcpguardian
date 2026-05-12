# MCPGuardian Phase 9 — Windows Event Guard

## Goal

Phase 9 adds a Windows Event Guard layer for PnP/USB/device-change storms. It temporarily pauses WMI/UI-heavy tools while Windows settles after device changes.

## Added module

```text
guardian/windows_event_guard.py
```

## Added internal gateway tools

```text
mcpguardian_windows_event_status
mcpguardian_windows_event_record
```

`record` is intentionally manual/injectable in this build. Native WMI watcher automation is deferred to avoid adding a fragile dependency path. The guard is still useful because it gives the gateway a single debounce state and policy hook.

## Config

```json
{
  "windows_event_guard": {
    "enabled": true,
    "debounce_seconds": 8,
    "pause_backends": ["windows-mcp", "windows_mcp", "native"],
    "pause_tool_patterns": ["powershell", "screenshot", "clipboard", "app", "windows"],
    "state_file": "config/windows_event_guard_state.json"
  }
}
```

## Behavior

When a relevant event is recorded, GatewayRouter blocks matching backend/native tool calls with:

```text
WINDOWS_EVENT_GUARD_PAUSED
```

The block is temporary and expires after `debounce_seconds`.

## Drive snapshot support

The guard can capture and compare drive-letter snapshots. This supports future drive-letter stabilization workflows without changing GatewayRouter again.

## Boundary

Phase 9 does not implement registry tools, UI automation, or automatic Cloudflare/Windows service setup.
