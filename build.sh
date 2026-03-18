#!/usr/bin/env bash
set -euo pipefail

OUTPUT_NAME="${1:-feifeile}"

poetry run python -m nuitka \
  --onefile \
  --output-dir=dist \
  --output-filename="$OUTPUT_NAME" \
  --include-package=feifeile \
  --assume-yes-for-downloads \
  --remove-output \
  feifeile/cli.py
