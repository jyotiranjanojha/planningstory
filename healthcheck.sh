#!/usr/bin/env bash
set -euo pipefail

NODE_PORT="${PORT:-3000}"
curl -fsS --max-time 5 "http://127.0.0.1:${NODE_PORT}/api/ready" >/dev/null
