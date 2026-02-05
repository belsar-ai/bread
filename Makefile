.PHONY: install lint clean release

install:
	uv sync
	uv run pre-commit install

lint:
	uv run pre-commit run --all-files

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf dist/ build/

# Release management
_check_version:
	@if [ -z "$(VERSION)" ]; then echo "Usage: make release VERSION=X.Y.Z"; exit 1; fi

release: _check_version
	@echo "Bumping version to $(VERSION)..."
	sed -i 's/^version = ".*"/version = "$(VERSION)"/' pyproject.toml
	sed -i 's/^Version:        .*/Version:        $(VERSION)/' bread.spec
	git add pyproject.toml bread.spec
	git commit -m "chore: release v$(VERSION)"
	git tag -a v$(VERSION) -m "Release v$(VERSION)"
	@echo "Release v$(VERSION) ready. Push with: git push && git push origin v$(VERSION)"
