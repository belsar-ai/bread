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

## COPR Distribution (via Packit)

COPR builds are automated by [Packit](https://packit.dev/). Configuration lives in `.packit.yaml`.

### How it works

- **Pull requests**: Packit builds the RPM in a temporary COPR project and reports the result as a GitHub status check.
- **GitHub releases**: Packit builds a release RPM into `belsar/bread`.

### Setup (one-time)

1. Install the [Packit GitHub App](https://github.com/marketplace/packit-as-a-service) on the repository
2. Ensure your [FAS account](https://accounts.fedoraproject.org/) has your GitHub username populated
3. Grant Packit build permissions on the COPR project:
   ```bash
   copr-cli edit-permissions --builder packit belsar/bread
   ```
4. In the COPR project settings, add `github.com/belsar-ai/bread` to **Packit allowed forge projects**

### Installing from COPR

```bash
sudo dnf copr enable belsar/bread
sudo dnf install bread
```

## Version History

- `v0.1.0` - Initial release
