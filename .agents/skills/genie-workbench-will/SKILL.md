---
name: genie-workbench-will
description: Build, update, and query Databricks Genie Spaces in Will's workbench workflows.
---

# Genie Workbench (Will)

Use this skill when working on Genie workbench tasks: creating Genie spaces, updating sample questions, and running conversation queries.

## When to use
- User asks to create or update a Genie Space
- User asks to run natural-language queries against Genie
- User asks to tune sample questions or table coverage for Genie

## Default workflow
1. Confirm active Databricks profile/host is correct for the target workspace.
2. Identify required Unity Catalog tables.
3. Create or update Genie Space using MCP tool `mcp__databricks__create_or_update_genie`.
4. Validate with `mcp__databricks__ask_genie` using 2-3 representative prompts.
5. Return space ID, sample prompts, and any follow-up actions.

## Notes
- Prefer profile-based auth separation (`DEFAULT` vs `other-ws`) to avoid cross-workspace mistakes.
- Keep sample questions focused on business outcomes and table coverage.
