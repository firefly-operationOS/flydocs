#!/usr/bin/env bash
# Pre-commit hook: reject staged files containing an Anthropic API key.
#
# Anthropic API keys start with ``sk-ant-`` and continue with at least
# 16 url-safe characters. The regex below is conservative enough to
# avoid false positives on placeholder strings like ``sk-ant-foo`` but
# catches every real key emitted by the Anthropic console.

set -euo pipefail

found=0
for path in "$@"; do
  if grep -nE "sk-ant-[a-zA-Z0-9_-]{16,}" -- "$path" >/dev/null 2>&1; then
    echo "ERROR: Anthropic API key detected in $path"
    found=1
  fi
done

if [[ $found -eq 1 ]]; then
  echo
  echo "Strip the key (or replace with a placeholder like ``sk-ant-EXAMPLE``)"
  echo "before committing. If this is a documented example, add the file to"
  echo "the hook's ``exclude:`` list in .pre-commit-config.yaml."
  exit 1
fi
