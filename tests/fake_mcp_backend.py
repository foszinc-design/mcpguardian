import json
import os
import sys
import time

CALLS = []

TOOLS = [
    {
        "name": "echo",
        "description": "Echo arguments",
        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}},
    },
    {
        "name": "read_file",
        "description": "Read a file path",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
    {
        "name": "sleep",
        "description": "Sleep before responding",
        "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}},
    },
    {
        "name": "crash_once_then_echo",
        "description": "Crash once using FAKE_FAIL_ONCE_FILE then echo on retry",
        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}},
    },
]


def respond(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result if result is not None else {}
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        respond(mid, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        respond(mid, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        CALLS.append({"name": name, "arguments": args})
        if name == "sleep":
            time.sleep(float(args.get("seconds", 1)))
        if name == "crash_once_then_echo":
            flag = os.environ.get("FAKE_FAIL_ONCE_FILE")
            if flag and os.path.exists(flag):
                os.remove(flag)
                sys.stderr.write("crashing once as requested\n")
                sys.stderr.flush()
                os._exit(42)
        respond(mid, {"content": [{"type": "text", "text": json.dumps({"name": name, "arguments": args}, ensure_ascii=False)}]})
    else:
        respond(mid, error={"code": -32601, "message": f"unsupported {method}"})
