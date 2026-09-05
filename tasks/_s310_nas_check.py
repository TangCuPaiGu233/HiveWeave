"""s3-clone_10: pre-flight NAS check — reachability + ~/s3-eval environment."""
from __future__ import annotations

import sys

sys.path.insert(0, r"C:\Users\99744\AppData\Local\Temp\opencode\skc")
from nas_s3_inspect import connect, run  # noqa: E402


def main() -> int:
    c = connect()
    cmds = [
        "whoami; hostname",
        "ls ~/s3-eval",
        "ls ~/s3-eval/task/tests | head -30",
        "ls ~/s3-eval/task/tests | wc -l",
        "ls ~/s3-eval/wheels | grep -ciE 'boto3|botocore'",
        "ls ~/s3-eval/run_verify.sh && head -5 ~/s3-eval/run_verify.sh",
        "/usr/local/bin/docker images --format '{{.Repository}}:{{.Tag}}'",
        "/usr/local/bin/docker ps -a --format '{{.Names}} {{.Status}}' | head",
    ]
    ok = True
    for cmd in cmds:
        out, err, rc = run(c, cmd, timeout=30)
        print("====", cmd)
        print(out, end="")
        if err.strip():
            print("ERR", err[:400])
    c.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
