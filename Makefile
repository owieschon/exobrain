.PHONY: help test lint check eval eval-db

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

test: ## Run all verification harnesses (the project's test suite)
	@echo ">> verify_auto_ingest.py"
	python3 tools/verify_auto_ingest.py
	@echo ">> verify_health_check.py"
	python3 tools/verify_health_check.py
	@echo ">> verify_memory_backend.py"
	python3 tools/verify_memory_backend.py
	@echo ">> verify_observability.py"
	python3 tools/verify_observability.py

eval: ## Score the gate classifier against the labeled dataset
	python3 tools/eval.py

eval-db: ## Record two runs to the SQLite metrics store and print the analytical queries
	rm -f eval/results.db
	python3 tools/eval.py --record >/dev/null
	EXOBRAIN_STEM=1 python3 tools/eval.py --record >/dev/null
	python3 tools/eval_db.py

lint: ## Lint the tooling with ruff (pip install -e '.[dev]')
	ruff check tools/

check: lint test ## Lint then test -- the full local gate
