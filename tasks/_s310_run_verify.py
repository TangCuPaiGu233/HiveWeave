"""s3-clone_10: start the official verifier on NAS, or poll until done.

Usage:
  python _s310_run_verify.py start   # replace s3-verify container, launch
  python _s310_run_verify.py poll    # one-shot status (log tail + metrics)
  python _s310_run_verify.py watch   # loop until container exits/metrics ready
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"C:\Users\99744\AppData\Local\Temp\opencode\skc")
from nas_s3_inspect import connect, run  # noqa: E402

NAS_EVAL = "/var/services/homes/skc/s3-eval"
DOCKER = "/usr/local/bin/docker"


def start() -> None:
    c = connect()
    out, err, rc = run(c, f"cat {NAS_EVAL}/run_verify.sh")
    print("=== run_verify.sh ===")
    print(out)
    cmds = [
        f"{DOCKER} rm -f s3-verify 2>/dev/null; echo rm_done",
        f"rm -rf {NAS_EVAL}/logs/verifier {NAS_EVAL}/logs/testsh.log; "
        f"mkdir -p {NAS_EVAL}/logs; echo logs_cleaned",
        f"{DOCKER} run --name s3-verify --network none -d "
        f"-v {NAS_EVAL}/app:/app "
        f"-v {NAS_EVAL}/task/tests:/tests "
        f"-v {NAS_EVAL}/logs:/logs "
        f"-v {NAS_EVAL}/wheels:/wheels "
        f"-v {NAS_EVAL}/run_verify.sh:/run_verify.sh:ro "
        "slack-clone-verifier-node:latest "
        "bash -c 'bash /run_verify.sh > /logs/testsh.log 2>&1; "
        "echo EXIT_CODE=$? >> /logs/testsh.log'",
    ]
    for cmd in cmds:
        out, err, rc = run(c, cmd, timeout=60)
        print("====", cmd[:80])
        print(out.strip())
        if err.strip():
            print("ERR", err.strip()[:300])
    c.close()


def status(c) -> tuple[str, bool]:
    out, _, _ = run(
        c, f"{DOCKER} inspect -f '{{{{.State.Running}}}} {{{{.State.ExitCode}}}}' s3-verify 2>/dev/null"
    )
    running = None
    if out.strip():
        parts = out.split()
        running = parts[0] == "true"
    return out.strip(), (running is True)


def tail(c, n=25) -> str:
    out, _, _ = run(c, f"tail -n {n} {NAS_EVAL}/logs/testsh.log 2>/dev/null")
    return out


def metrics(c) -> str:
    out, _, _ = run(c, f"cat {NAS_EVAL}/logs/verifier/metrics.json 2>/dev/null")
    return out


def poll_once(c) -> None:
    st, running = status(c)
    print("container:", st)
    m = metrics(c)
    if m.strip():
        print("=== metrics.json ===")
        print(m)
    print("=== testsh.log tail ===")
    print(tail(c))
    gates = run(c, f"grep -c '^=== Gate' {NAS_EVAL}/logs/testsh.log 2>/dev/null")[0].strip()
    print(f"gates_started={gates}")


def watch(max_minutes: int = 75) -> None:
    deadline = time.time() + max_minutes * 60
    last_gates = -1
    while time.time() < deadline:
        try:
            c = connect()
        except Exception as e:
            print("connect failed, retry:", e)
            time.sleep(60)
            continue
        st, running = status(c)
        m = metrics(c)
        gates = run(c, f"grep -c '^=== Gate' {NAS_EVAL}/logs/testsh.log 2>/dev/null")[0].strip()
        gates_n = int(gates) if gates.isdigit() else -1
        print(f"[{time.strftime('%H:%M:%S')}] container={st} gates_started={gates}", flush=True)
        if gates_n != last_gates:
            print(tail(c, 12), flush=True)
            last_gates = gates_n
        done = (running is False) or bool(m.strip())
        c.close()
        if done:
            print("=== FINISHED ===")
            c = connect()
            poll_once(c)
            c.close()
            return
        time.sleep(90)
    print("=== TIMEOUT waiting for verifier ===")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "poll"
    if mode == "start":
        start()
    elif mode == "poll":
        c = connect()
        poll_once(c)
        c.close()
    elif mode == "watch":
        watch()
    else:
        raise SystemExit(f"unknown mode {mode}")
