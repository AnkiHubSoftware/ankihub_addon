set -e
set -o pipefail

# Every path below is relative to the repository root.
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# --locked, because a plain `uv run` syncs first and will re-lock a lockfile that has fallen behind
# pyproject.toml - after which build.py's own `uv export --locked` sees the lock it just wrote and
# is satisfied. The artifact would then be built from a resolution nobody reviewed.
uv run --locked scripts/build.py

mkdir -p dist
rm -rf dist/release
cp -r ankihub dist/release

cd dist/release

# update version file
../../scripts/calver.sh > VERSION

# remove temporary files. -exec rather than a pipe into xargs, which splits on whitespace: a
# vendored path containing a space would become two unrelated arguments to `rm -rf`. -depth so a
# directory is removed only after find has already walked into it.
find . -depth \( -name __pycache__ -o -regex ".*\.py[cod]" -o -name .DS_Store -o -name .pytest_cache -o -name .mypy_cache \) -exec rm -rf -- {} +

# zip updates an existing archive in place and never drops entries whose files are gone, so a
# leftover artifact would keep shipping packages that are no longer vendored.
rm -f "$PROJECT_ROOT/ankihub.ankiaddon"
zip -r "../../ankihub.ankiaddon" . -x ./tests\*

cd "$PROJECT_ROOT"

# Check the archive contains its required members. Read from the extracted zip rather than from
# ankihub/lib, so a fault in assembling it just above is caught as well as one in what build.py
# vendored. Lives here because every workflow that produces an artifact runs this script - the
# GitHub release, the AnkiWeb and S3 uploads, the PR test builds.
check_dir="$(mktemp -d)"
trap 'rm -rf "$check_dir"' EXIT
unzip -q ankihub.ankiaddon -d "$check_dir/addon"

for member in manifest.json VERSION __init__.py lib/django lib/peewee.py; do
  if [ ! -e "$check_dir/addon/$member" ]; then
    echo "ankihub.ankiaddon is missing $member" >&2
    exit 1
  fi
done

# Import the 3.10-only layer on 3.10. Nothing else does: scripts/check_addon_import.py runs on 3.9,
# where the add-on gates protobuf off. protobuf_ext is imported rather than just checked for, because
# protobuf-py catches a failed import of it, which would leave a missing or broken extension silent.
# Covers Linux only - the vendored extension is built for this host, and protobuf-py falls back to
# pure Python on the platforms it cannot load on.
"$(uv python find 3.10)" -I -S -c "import sys; sys.path.insert(0, '$check_dir/addon/lib'); import protobuf, protobuf_ext"

# Parse the bundle under 3.9. scripts/check_addon_import.py does this and much more, but it needs an
# Anki running on 3.9 and so exists only in the CI lane that has one - while the AnkiWeb and S3
# uploads are dispatchable on their own and would otherwise publish with no 3.9 check at all. This
# narrows that gap rather than closing it: it catches syntax a release started using, not a
# 3.10-only import. The two modules the add-on itself gates off above 3.9 are excluded.
"$(uv python find 3.9)" -I -S -m compileall -q -x '/(protobuf|protobuf_ext)/' "$check_dir/addon/lib"

echo "ankihub.ankiaddon is assembled, parses on Python 3.9, and its Python 3.10 layer loads"
