"""s3-clone_10: transfer TASK_ROOT -> NAS ~/s3-eval/app with LF fix + MD5 audit.

Reuses skc helpers (paramiko). Text files are MD5-compared AFTER \r\n->\n
normalization on both sides, so the compare is independent of platform EOL.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\99744\AppData\Local\Temp\opencode\skc")
from nas_s3_inspect import connect, run  # noqa: E402
from nas_xfer import put_bytes, put_tar_stdin  # noqa: E402

TASK_ROOT = Path(r"D:\PC_AI\Project\HiveTestProject\s3-clone_10")
NAS_EVAL = "/var/services/homes/skc/s3-eval"
OFFICIAL_TESTS = Path(r"C:\Users\99744\AppData\Local\Temp\opencode\swe-marathon\tasks\s3-clone\tests")

EXCLUDE_DIRS = {
    "__pycache__", ".pytest_cache", ".git", ".hiveweave", ".venv", "venv",
    "node_modules", ".vite", ".cache", ".idea", ".vscode",
    "data", "tests",
}
EXCLUDE_FILE_SUFFIX = {".pyc", ".bak", ".tmp", ".log"}
TEXT_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".json", ".toml",
    ".yaml", ".yml", ".md", ".txt", ".html", ".css", ".sh", ".rs", ".go",
}


def iter_transfer_files(root: Path):
    for dirpath, dirnames, filenames in os_walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix.lower() in EXCLUDE_FILE_SUFFIX or name in (".DS_Store", "Thumbs.db"):
                continue
            yield p


def os_walk(root: Path):
    import os
    return os.walk(root)


def md5_of(path: Path) -> str:
    raw = path.read_bytes()
    # text = known text ext, or extensionless NUL-free file (e.g. .gitignore)
    is_text = path.suffix.lower() in TEXT_EXTS or (
        path.suffix == "" and b"\x00" not in raw
    )
    if is_text:
        raw = raw.replace(b"\r\n", b"\n")
    return hashlib.md5(raw).hexdigest()


def build_tar() -> bytes:
    buf = io.BytesIO()
    n = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in iter_transfer_files(TASK_ROOT):
            arc = p.relative_to(TASK_ROOT).as_posix()
            tf.add(p, arcname=arc, recursive=False)
            n += 1
    print(f"tar: {n} files, {buf.tell()/1e6:.1f} MB")
    return buf.getvalue()


def main() -> int:
    # 1) local manifest (LF-normalized md5 for text, raw md5 otherwise)
    manifest: dict[str, str] = {}
    for p in iter_transfer_files(TASK_ROOT):
        manifest[p.relative_to(TASK_ROOT).as_posix()] = md5_of(p)
    print(f"local manifest: {len(manifest)} files")

    # 2) connect
    c = connect()

    # 3) what did the previous NAS run score? (context only)
    out, _, _ = run(c, f"cat {NAS_EVAL}/logs/verifier/metrics.json 2>/dev/null | head -12")
    print("=== previous NAS metrics (context) ===")
    print(out)

    # 4) verify NAS official tests integrity for correctness-critical files
    local_tests_md5 = {}
    for p in sorted(OFFICIAL_TESTS.iterdir()):
        if p.is_file() and (p.name.startswith("test_") or p.name in
                            ("conftest.py", "anti_cheat.py", "test.sh")):
            # local checkout is CRLF; NAS copies are LF — normalize before md5
            local_tests_md5[p.name] = hashlib.md5(
                p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    ok_tests = True
    for name, want in local_tests_md5.items():
        out, _, rc = run(c, f"md5sum {NAS_EVAL}/task/tests/{name} 2>/dev/null | cut -d' ' -f1")
        got = out.strip()
        if got != want:
            ok_tests = False
            print(f"TESTS MISMATCH: {name} nas={got} local={want}")
    print("official tests integrity:", "OK" if ok_tests else "MISMATCH — must re-upload tests")
    if not ok_tests:
        c.close()
        return 2

    # 5) cua_config.json sanity: must equal the official backup on NAS
    out, _, _ = run(c, f"diff -q {NAS_EVAL}/task/tests/cua_config.json {NAS_EVAL}/task/tests/cua_config.official.json && echo SAME || echo DIFFER")
    print("cua_config vs official backup:", out.strip())
    if "DIFFER" in out:
        out2, err2, rc2 = run(c, f"cp {NAS_EVAL}/task/tests/cua_config.official.json {NAS_EVAL}/task/tests/cua_config.json && echo restored")
        print("restore:", out2.strip(), err2.strip())

    # 6) clean old app, extract new
    out, err, rc = run(c, f"rm -rf {NAS_EVAL}/app && mkdir -p {NAS_EVAL}/app && echo cleaned")
    print("clean:", out.strip(), err.strip())

    tar_bytes = build_tar()
    (Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug") / "_s310_app.tar.gz").parent.mkdir(exist_ok=True)
    (Path(r"D:\PC_AI\Project\HiveWeave\tasks") / "_s310_app.tar.gz").write_bytes(tar_bytes)
    put_tar_stdin(c, f"{NAS_EVAL}/app", tar_bytes)

    # 7) LF fix on NAS (text files only, binary-safe replace)
    LF_FIX = r'''
import pathlib
root = pathlib.Path("/var/services/homes/skc/s3-eval/app")
n = 0
for f in root.rglob("*"):
    if not f.is_file():
        continue
    try:
        b = f.read_bytes()
    except Exception:
        continue
    if b"\r\n" in b:
        f.write_bytes(b.replace(b"\r\n", b"\n"))
        n += 1
print("lf_fixed:", n)
'''
    put_bytes(c, "/tmp/_s310_fix_lf.py", LF_FIX.encode("utf-8"))
    out, err, rc = run(c, "python3 /tmp/_s310_fix_lf.py", timeout=120)
    print("lf fix:", out.strip(), err.strip())

    # 8) remote manifest via md5sum, compare file-by-file
    out, err, rc = run(
        c,
        f"cd {NAS_EVAL}/app && find . -type f | sed 's|^\\./||' | sort | xargs -d '\\n' md5sum",
        timeout=180,
    )
    remote: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            remote[parts[1].strip()] = parts[0].strip()
    missing = sorted(set(manifest) - set(remote))
    extra = sorted(set(remote) - set(manifest))
    diff = sorted(k for k in (set(manifest) & set(remote)) if manifest[k] != remote[k])
    print(f"remote files: {len(remote)}  local: {len(manifest)}")
    for label, lst in (("MISSING_ON_NAS", missing), ("EXTRA_ON_NAS", extra), ("MD5_DIFF", diff)):
        if lst:
            print(f"{label}: {len(lst)}")
            for k in lst[:20]:
                print("   ", k)
    if missing or extra or diff:
        print("TRANSFER AUDIT: FAIL")
        c.close()
        return 3
    print("TRANSFER AUDIT: PASS — every transferred file md5-matches (EOL-normalized)")
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
