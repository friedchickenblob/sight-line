.PHONY: help hook

help:
	@echo "make hook   turn on the shared git hooks (commit format, branch names, no pushes to main)"

hook:
	git config core.hooksPath .githooks
	-@chmod +x .githooks/*
	@echo "Git hooks enabled: commit messages and branch names are checked, and pushes to main are blocked."
