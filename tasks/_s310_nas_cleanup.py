"""s3-clone eval NAS cleanup: build cache + transfer residue. Keeps the CUA key file."""
from __future__ import annotations

import base64
import sys

sys.path.insert(0, r"C:\Users\99744\AppData\Local\Temp\opencode\skc")
from nas_s3_inspect import connect, run  # noqa: E402
from nas_xfer import put_bytes  # noqa: E402

CLEANUP = r"""#!/bin/bash
set -u
D=/usr/local/bin/docker
echo "=== df before ==="
df -h /volume1 | tail -1

echo "=== bind mounts from /tmp (must be excluded from deletion) ==="
ids=$($D ps -q)
mounted=""
if [ -n "$ids" ]; then
  mounted=$($D inspect --format '{{range .Mounts}}{{.Source}}
{{end}}' $ids | grep '^/tmp' | sort -u)
fi
if [ -n "$mounted" ]; then
  echo "EXCLUDE (mounted into running containers):"
  echo "$mounted"
else
  echo "no_tmp_mounts"
fi

echo "=== rm exited scoring containers ==="
$D rm -f s3-verify s3-cua slack-verify4 2>&1 || true

echo "=== /tmp skc files (files only, skip dirs, skip mounted) ==="
find /tmp -maxdepth 1 -user skc -type f > /tmp/_s310_cleanup_list.txt
total=$(wc -l < /tmp/_s310_cleanup_list.txt)
deleted=0
while IFS= read -r f; do
  if [ -n "$mounted" ] && printf '%s\n' "$mounted" | grep -qxF "$f"; then
    echo "skip (mounted): $f"
    continue
  fi
  rm -f "$f" && deleted=$((deleted+1))
done < /tmp/_s310_cleanup_list.txt
echo "deleted $deleted / $total tmp files"
rm -f /tmp/_s310_cleanup_list.txt

echo "=== s3-eval probe leftovers (KEEP cua_mimo.env + run_cua_mimo.sh) ==="
rm -v /var/services/homes/skc/s3-eval/probe_cua.sh \
      /var/services/homes/skc/s3-eval/probe_import_07.py \
      /var/services/homes/skc/s3-eval/logs/cua_mimo.log 2>&1 || true
chmod 600 /var/services/homes/skc/s3-eval/cua_mimo.env && echo "key file kept, perm tightened to 600"
ls -la /var/services/homes/skc/s3-eval/

echo "=== df after quick pass ==="
df -h /volume1 | tail -1
"""


def main() -> int:
    c = connect()
    put_bytes(c, "/tmp/_s310_cleanup.sh", CLEANUP.encode("utf-8"), mode="0755")
    out, err, rc = run(c, "bash /tmp/_s310_cleanup.sh; rm -f /tmp/_s310_cleanup.sh", timeout=300)
    print(out)
    if err.strip():
        print("ERR", err[:800])
    print("rc", rc)
    c.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
