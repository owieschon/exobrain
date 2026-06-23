.PHONY: help test lint check eval

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

test: ## Run all verification harnesses (the project's test suite)
	@echo ">> verify_auto_ingest.py"
	python3 tools/verify_auto_ingest.py
	@echo ">> verify_health_check.py"
	python3 tools/verify_health_check.py

eval: ## Score the gate classifier against the labeled dataset
	python3 tools/eval.py

lint: ## Lint the tooling with ruff (pip install -e '.[dev]')
	ruff check tools/

check: lint test ## Lint then test -- the full local gate
