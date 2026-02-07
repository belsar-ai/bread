#!/usr/bin/env bash
# Build RPM locally using the same Packit path as CI.
# Usage: scripts/build-rpm.sh [--srpm-only]
#
# Requires: packit, rpm-build
# Produces: rpmbuild/RPMS/noarch/bread-*.noarch.rpm

set -euo pipefail

DEPS=(packit rpm-build)
MISSING=()
for dep in "${DEPS[@]}"; do
    rpm -q "$dep" &>/dev/null || MISSING+=("$dep")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "Missing build dependencies:"
    echo "  sudo dnf install ${MISSING[*]}"
    exit 1
fi

SRPM_ONLY=0
if [[ "${1:-}" == "--srpm-only" ]]; then
    SRPM_ONLY=1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

# Build SRPM via Packit (same path as CI)
PACKIT_OUTPUT=$(packit srpm --preserve-spec 2>&1)
echo "$PACKIT_OUTPUT"
SRPM=$(echo "$PACKIT_OUTPUT" | grep -oP '(?<=SRPM: ).*')

if [[ -z "$SRPM" ]]; then
    echo "Error: packit srpm did not produce an SRPM"
    exit 1
fi

if [[ "$SRPM_ONLY" -eq 1 ]]; then
    echo "$SRPM"
    exit 0
fi

# Build RPM from SRPM
RPMBUILD_DIR="$REPO_DIR/rpmbuild"
mkdir -p "$RPMBUILD_DIR"/{RPMS,SRPMS}

rpmbuild --rebuild \
    --define "_topdir $RPMBUILD_DIR" \
    "$SRPM"

# Clean up Packit artifacts from repo root
rm -f "$REPO_DIR"/bread-*.tar.gz "$SRPM"

echo ""
echo "Build complete:"
ls "$RPMBUILD_DIR/RPMS/noarch/"*.rpm
