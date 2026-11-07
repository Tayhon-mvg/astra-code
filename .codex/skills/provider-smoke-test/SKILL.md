---
name: provider-smoke-test
description: Verify that a newly added free provider can list models and answer one short prompt.
allowed-tools: run_command, read_file
---

# Provider smoke test

1. Run `astra auth test <provider>`.
2. Run `astra models --provider <provider> --refresh` and confirm at least one model is free.
3. Send one tiny prompt through `astra -p "ping" --model <provider>:<model>`.
4. Confirm the answer arrives and the footer says `$0`.
5. If the provider returns 401, stop and report the exact error; do not retry with a different key.
