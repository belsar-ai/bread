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

To build the RPM from a tagged release, create a source tarball and run `rpmbuild`.

## Version History

- `v0.1.0` - Initial release
