#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# This launcher opens the owner-controlled management menu by default.
# It never executes the archived fscarmen upstream installer.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd -P)"
exec bash "$ROOT/scripts/sb.sh" "$@"
