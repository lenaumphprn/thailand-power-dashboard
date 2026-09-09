#!/bin/sh
set -eu
cd "$(dirname "$0")"
python weekly_refresh.py "$@"
