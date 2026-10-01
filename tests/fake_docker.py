#!/usr/bin/env python3
"""Stand-in for the docker CLI. Records every call and simulates container
behaviour selected by FAKE_MODE. Used to test executor orchestration without a
Docker daemon; it says nothing about real Docker behaviour."""
import json
import os
import pathlib
import sys
import time

d = pathlib.Path(os.environ["FAKE_DOCKER_DIR"])
argv = sys.argv[1:]
with open(d / "calls.jsonl", "a") as f:
    f.write(json.dumps(argv) + "\n")

mode = os.environ.get("FAKE_MODE", "ok")
cmd = argv[0]


def write_state(name, code, oom=False, error=""):
    (d / f"{name}.state").write_text(json.dumps(
        {"Status": "exited", "ExitCode": code, "OOMKilled": oom, "Error": error}))


if cmd == "create":
    if mode == "create_fail":
        print("Unable to find image locally", file=sys.stderr)
        sys.exit(1)
    (d / f"{argv[argv.index('--name') + 1]}.state").write_text("{}")
    print("deadbeef")
elif cmd == "start":
    name = argv[-1]
    (d / "stdin.tar").write_bytes(sys.stdin.buffer.read())
    if mode == "ok":
        time.sleep(float(os.environ.get("FAKE_DELAY", "0")))
        print("hello")
        print("warn", file=sys.stderr)
        write_state(name, 0)
    elif mode == "fail":
        print("bad", file=sys.stderr)
        write_state(name, 3)
    elif mode == "oom":
        write_state(name, 137, oom=True)
        sys.exit(137)
    elif mode == "flood":
        chunk = "x" * 65536
        for _ in range(48):  # ~3 MB
            sys.stdout.write(chunk)
        sys.stdout.flush()
        write_state(name, 0)
    elif mode == "start_error":
        write_state(name, 128, error="OCI runtime create failed")
        sys.exit(125)
    elif mode == "hang":
        time.sleep(60)
elif cmd == "inspect":
    p = d / f"{argv[-1]}.state"
    if mode == "no_inspect" or not p.exists():
        print("Error: No such object", file=sys.stderr)
        sys.exit(1)
    print(p.read_text())
elif cmd == "kill":
    write_state(argv[-1], 137)
elif cmd == "rm":
    (d / f"{argv[-1]}.state").unlink(missing_ok=True)
