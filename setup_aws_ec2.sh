#!/usr/bin/env bash
# Forwarding script for BigProject_Back/setup_aws_ec2.sh
set -e
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$BASE_DIR/../setup_aws_ec2.sh" ]; then
  exec "$BASE_DIR/../setup_aws_ec2.sh" "$@"
else
  exec python3 "$BASE_DIR/setup_bootstrap.py" "$@"
fi
