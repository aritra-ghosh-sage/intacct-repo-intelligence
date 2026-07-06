#!/usr/bin/env bash
set -e

cd ~/projects/ia-app
git pull --ff-only

cd ~/projects/intacct-repo-intelligence
source .venv/bin/activate
python -m parser.scan_repo
python -m parser.extract_symbols
