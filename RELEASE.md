# Release Workflow

This project uses trunk-based development on `main`.

## Branching Strategy

- **`main`**: The trunk (single source of truth)
- **feature branches**: Short-lived branches for new work (e.g., `feat/add-retention-policy`)

## Commit Messages

Follow **Conventional Commits**:
- `feat: ...` for new features
- `fix: ...` for bug fixes
- `refactor: ...` for code restructuring
- `chore: ...` for maintenance tasks
- `docs: ...` for documentation updates

## Cutting a Release

```bash
make release VERSION=X.Y.Z
```

This bumps the version in `pyproject.toml` and `bread.spec`, commits, and creates an annotated git tag. Push manually when ready:

```bash
git push && git push origin vX.Y.Z
```

## CI/CD (GitHub Actions)

When you push a version tag (`v*`), the GitHub Actions workflow automatically:

1. Builds the RPM in a Fedora container
2. Creates a GitHub Release with the RPM and SRPM attached

The workflow is defined in `.github/workflows/release.yml`.

### Full release flow

```bash
make release VERSION=X.Y.Z
git push && git push origin vX.Y.Z
# GitHub Actions takes over from here
```

### Local RPM build (for testing)

```bash
make rpm
```

This runs `scripts/build-rpm.sh`, which creates:
- `rpmbuild/RPMS/noarch/bread-X.Y.Z-1.*.noarch.rpm`
- `rpmbuild/SRPMS/bread-X.Y.Z-1.*.src.rpm`

## COPR Distribution

The bread RPM is distributed via [Fedora COPR](https://copr.fedorainfracloud.org/).

### Setup (one-time, via COPR web UI)

1. Sign in at https://copr.fedorainfracloud.org/ with your FAS account
2. Create a new project (e.g., `bread`)
3. Under **Packages**, add a package:
   - **Source Type**: SCM
   - **Clone URL**: `https://github.com/belsar-ai/bread.git`
   - **Spec file path**: `bread.spec`
   - **SCM Type**: git
   - **Method**: rpkg
4. Under **Settings**, enable the Fedora versions you want to build for
5. Optionally enable auto-rebuild via the webhook URL provided by COPR

### Installing from COPR

```bash
sudo dnf copr enable belsar-ai/bread
sudo dnf install bread
```

## Version History

- `v0.1.0` - Initial release
