---
name: databricks-genie-workbench
description: "Build, deploy, and operate the Databricks Genie Workbench app (FastAPI + React) for creating/scoring/optimizing Genie Spaces. Use when working with the databricks-solutions/databricks-genie-workbench repo."
---

# Databricks Genie Workbench

Use this skill when working with the `databricks-genie-workbench` project:
- Deploying or updating the app in Databricks Apps
- Running installer/deploy scripts
- Configuring `.env.deploy`
- Troubleshooting app deployment/runtime issues

## Local Repo Path

`/Users/will.schweitzer/Documents/Coding/fun-demo/Account Pings/databricks-genie-workbench`

## Primary Commands

Run all commands from the repo root above.

### 1) Prereqs and auth

```bash
databricks auth login --profile <workspace-profile>
```

### 2) Guided install

```bash
./scripts/install.sh
```

### 3) Deploy

```bash
./scripts/deploy.sh
```

### 4) Fast code-only update

```bash
./scripts/deploy.sh --update
```

### 5) Destroy app

```bash
./scripts/deploy.sh --destroy
```

## Manual non-interactive setup

Create `.env.deploy`:

```bash
cat > .env.deploy <<'EOF'
GENIE_WAREHOUSE_ID=<your-sql-warehouse-id>
GENIE_CATALOG=<your-catalog-name>
GENIE_APP_NAME=genie-workbench
GENIE_DEPLOY_PROFILE=<workspace-profile>
GENIE_LLM_MODEL=databricks-claude-sonnet-4-6
GENIE_LAKEBASE_INSTANCE=<lakebase-instance-name>
EOF
```

Then deploy:

```bash
./scripts/deploy.sh
```

## Troubleshooting Commands

```bash
# App logs
databricks apps logs <app-name> --profile <workspace-profile>

# App status
databricks apps get <app-name> --profile <workspace-profile>

# Validate workspace sync output
databricks workspace list /Workspace/Users/<email>/<app-name>/backend --profile <workspace-profile>
```

## Source Reference

- Repo README: `/Users/will.schweitzer/Documents/Coding/fun-demo/Account Pings/databricks-genie-workbench/README.md`
- Deploy script: `/Users/will.schweitzer/Documents/Coding/fun-demo/Account Pings/databricks-genie-workbench/scripts/deploy.sh`
- Installer script: `/Users/will.schweitzer/Documents/Coding/fun-demo/Account Pings/databricks-genie-workbench/scripts/install.sh`

## Usage Notes

- Prefer `./scripts/deploy.sh --update` for iterative code changes.
- Use `install.sh` once per new workspace setup.
- If app appears blank, rerun update deploy and check logs.
