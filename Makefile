.PHONY: lint stubs clean-stubs test publish

lint:
	@uv run --quiet \
	  pre-commit run \
	--config etc/pre-commit.yaml \
	--all

test:
	@PYTHONASYNCIODEBUG=1 \
	uv run --quiet \
	pytest \
		-rs \
		-svvv \
		--cov src \
		--cov-report term-missing

publish:
	@rm -rf dist/ || true
	@uv build
	@uv run uv-publish --repo kuit
	@rm -rf dist/ || true

%:
	@just $@

.DEFAULT_GOAL := default
