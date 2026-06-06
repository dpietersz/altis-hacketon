# Databricks

Notebooks and supporting code for the serverless Azure Databricks workspace.

## Layout

- `notebooks/` — source-format (`.py`) Databricks notebooks, importable via the
  Databricks CLI or git folders.
- `jobs/` — job definitions (add when needed).
- `libs/` — shared Python helpers (add when needed).

## Importing a notebook

```bash
databricks workspace import --language PYTHON --format SOURCE \
  notebooks/01_connect_storage.py /Workspace/Users/<you>/altis/01_connect_storage
```

Or connect this repo as a Databricks Git folder and edit in place.
