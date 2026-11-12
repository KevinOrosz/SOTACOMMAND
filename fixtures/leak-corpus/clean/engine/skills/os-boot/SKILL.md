---
name: os-boot
description: Boot the SOTACOMMAND at session start. Use when a session opens in a project with os.config.json.
license: Apache-2.0
metadata:
  version: "0.1.0"
  status: experimental
---

Read `os.config.json`, restore the sitrep from memory, and report at most five
lines: state restored, board headline, and whether to wait or act.
