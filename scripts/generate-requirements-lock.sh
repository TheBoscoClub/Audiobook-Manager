#!/bin/bash
# Generate library/requirements.lock from a venv on a remote host.
#
# WHY: requirements.txt only carries `>=` floors, so every deploy pulls the newest
# versions those floors allow — production can run versions the test VM never ran.
# The lock is the exact package set the TEST VM ran at release time. upgrade.sh,
# install.sh and the Dockerfile pass it to pip as a constraint (-c), so production
# installs exactly that set. Raising a floor in requirements.txt without
# regenerating the lock is caught by library/tests/test_requirements_lock.py.
#
# USAGE (release time, after /test passed on the test VM):
#   scripts/generate-requirements-lock.sh <test-vm-ip> [--venv PATH]
#       --venv PATH            venv on HOST (default /opt/audiobooks/library/venv)
#       --output FILE          where to write (default library/requirements.lock)
#       --freeze-file FILE     read `pip freeze --all` from FILE instead of ssh
#                              (HOST then only labels the header; test seam)
#       --python-version VER   header value when --freeze-file is used
#
# Excluded from the lock: pip, setuptools, wheel (installer tooling, owned by the
# venv, not by the application) and any editable (`-e`) or direct-reference
# (`name @ file://...`) line — those have no version a constraint could pin.
#
# REFUSES to write (exit 1, existing lock untouched) if any locked version fails
# to satisfy requirements.txt or requirements-docker.txt, or a requirement has no
# lock entry — a lock can never undercut a CVE floor.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQ_FILES=("$PROJECT_DIR/library/requirements.txt" "$PROJECT_DIR/library/requirements-docker.txt")
OUTPUT="$PROJECT_DIR/library/requirements.lock"
VENV="/opt/audiobooks/library/venv"
HOST="" FREEZE_FILE="" PY_VERSION=""

die() {
    echo "ERROR: $*" >&2
    exit 1
}
usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
    --venv) VENV="${2:?--venv needs a path}"; shift 2 ;;
    --output) OUTPUT="${2:?--output needs a path}"; shift 2 ;;
    --freeze-file) FREEZE_FILE="${2:?--freeze-file needs a path}"; shift 2 ;;
    --python-version) PY_VERSION="${2:?--python-version needs a value}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) [[ -z "$HOST" ]] || die "only one HOST allowed"; HOST="$1"; shift ;;
    esac
done
[[ -n "$HOST" ]] || { usage >&2; die "HOST required"; }

work=$(mktemp -d) || die "mktemp failed"
trap 'rm -rf "${work:?}"' EXIT

if [[ -n "$FREEZE_FILE" ]]; then
    [[ -r "$FREEZE_FILE" ]] || die "cannot read $FREEZE_FILE"
    cp "$FREEZE_FILE" "$work/freeze.txt"
    : "${PY_VERSION:=unknown}"
else
    # Plain ssh: key and options come from ~/.ssh/config. Read-only on the host.
    ssh -o BatchMode=yes "$HOST" "'$VENV/bin/pip' freeze --all" >"$work/freeze.txt" ||
        die "ssh $HOST pip freeze failed"
    PY_VERSION=$(ssh -o BatchMode=yes "$HOST" "'$VENV/bin/python' -c 'import platform; print(platform.python_version())'") ||
        die "ssh $HOST python --version failed"
fi
[[ -s "$work/freeze.txt" ]] || die "pip freeze returned nothing"

python3 - "$work/freeze.txt" "$work/lock.body" "${REQ_FILES[@]}" <<'PY' || exit 1
import re
import sys

try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version
except ImportError:
    sys.exit("ERROR: python3 'packaging' module not available")

freeze, out, *req_files = sys.argv[1:]
EXCLUDE = {"pip", "setuptools", "wheel"}
pins: dict[str, str] = {}
for raw in open(freeze, encoding="utf-8"):
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("-e") or " @ " in line:
        print(f"note: skipping non-pinnable line: {line}", file=sys.stderr)
        continue
    m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", line)
    if not m:
        sys.exit(f"ERROR: unrecognised freeze line: {line!r}")
    name = canonicalize_name(m.group(1))
    if name in EXCLUDE:
        continue
    pins[name] = m.group(2)

problems = []
for path in req_files:
    for raw in open(path, encoding="utf-8"):
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        req = Requirement(text)
        name = canonicalize_name(req.name)
        base = path.rsplit("/", 1)[-1]
        if name not in pins:
            problems.append(f"{base}: {req.name} has no entry in the freeze")
        elif not req.specifier.contains(Version(pins[name]), prereleases=True):
            problems.append(
                f"{base}: {req.name}{req.specifier} not satisfied by locked {pins[name]}"
            )
if problems:
    print("REFUSING to write the lock — it would undercut requirements:", file=sys.stderr)
    for p in problems:
        print(f"  {p}", file=sys.stderr)
    sys.exit(1)

with open(out, "w", encoding="utf-8") as fh:
    for name in sorted(pins):
        fh.write(f"{name}=={pins[name]}\n")
print(f"{len(pins)} packages", file=sys.stderr)
PY

tmp_out="$work/requirements.lock"
{
    echo "# Dependency lock — exact package versions, used by pip as a constraint (-c)."
    echo "# Source host:    $HOST (venv $VENV)"
    echo "# Generated:      $(date +%Y-%m-%d)"
    echo "# Python:         $PY_VERSION"
    echo "# Regenerate:     scripts/generate-requirements-lock.sh <test-vm-ip>   (at release time, from the TEST VM)"
    echo "# Excludes:       pip, setuptools, wheel, and editable / direct-reference (-e, @ file://) lines"
    echo "# Guard:          library/tests/test_requirements_lock.py fails if a floor is raised without regenerating"
    cat "$work/lock.body"
} >"$tmp_out"
mkdir -p "$(dirname "$OUTPUT")"
install -m 644 "$tmp_out" "$OUTPUT" || die "could not write $OUTPUT"
echo "Wrote $OUTPUT ($(grep -vc '^#' "$OUTPUT") packages, source $HOST, python $PY_VERSION)"
