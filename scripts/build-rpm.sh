#!/usr/bin/env bash
# Build RPM from git tree.
# Usage: scripts/build-rpm.sh [--srpm-only]
#
# Requires: rpm-build, python3-devel, python3-uv-build, pyproject-rpm-macros
# Produces: rpmbuild/RPMS/noarch/bread-*.noarch.rpm (and SRPMS/)

set -euo pipefail

DEPS=(rpm-build python3-devel python3-uv-build pyproject-rpm-macros)
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

# Extract name and version from the spec file
NAME=$(rpmspec -q --qf '%{name}\n' "$REPO_DIR/bread.spec" | head -1)
VERSION=$(rpmspec -q --qf '%{version}\n' "$REPO_DIR/bread.spec" | head -1)
TARBALL="${NAME}-${VERSION}.tar.gz"

# Set up rpmbuild tree inside the repo (gitignored)
RPMBUILD_DIR="$REPO_DIR/rpmbuild"
mkdir -p "$RPMBUILD_DIR"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}

# Create source tarball using git archive
# --prefix=name-version/ matches what %autosetup expects
git -C "$REPO_DIR" archive \
    --format=tar.gz \
    --prefix="${NAME}-${VERSION}/" \
    -o "$RPMBUILD_DIR/SOURCES/$TARBALL" \
    HEAD

# Copy spec
cp "$REPO_DIR/bread.spec" "$RPMBUILD_DIR/SPECS/"

# Build SRPM
rpmbuild -bs \
    --define "_topdir $RPMBUILD_DIR" \
    "$RPMBUILD_DIR/SPECS/bread.spec"

if [[ "$SRPM_ONLY" -eq 1 ]]; then
    echo "SRPM built:"
    ls "$RPMBUILD_DIR/SRPMS/"*.src.rpm
    exit 0
fi

# Build RPM from SRPM
rpmbuild --rebuild \
    --define "_topdir $RPMBUILD_DIR" \
    "$RPMBUILD_DIR/SRPMS/"*.src.rpm

echo "Build complete:"
ls "$RPMBUILD_DIR/RPMS/noarch/"*.rpm
ls "$RPMBUILD_DIR/SRPMS/"*.src.rpm
