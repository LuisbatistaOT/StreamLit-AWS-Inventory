# LRE AWS Inventory Dashboard (Streamlit)
> Delivery note: this project was built and iterated using a vibecode workflow in Cursor, with an AI coding agent (`gpt-5.3-codex`).
## Project Objective

This project provides a lightweight dashboard to demonstrate AWS EC2 inventory reporting for LRE environments.

The dashboard reads AWS Systems Manager Inventory exports from a local repository, combines data across multiple inventory categories, and presents:

- a consolidated server inventory view
- dynamic filters for operational analysis
- a compliance status view based on product version policy

Primary goal for the demo:

- quickly identify EC2 instances that are compliant (`>= 26`), warning (`25`), or urgent (`< 25`) based on registry major version.

---

## Data Source

Input root folder (default in app):

- `C:/lre_inventory_repository`

Expected layout:

- category folders such as `AWS%3AInstanceInformation`, `AWS%3ATag`, `AWS%3AWindowsRegistry`, `AWS%3AApplication`
- inside each category: nested partitions (`accountid=.../region=.../resourcetype=ManagedInstanceInventory`)
- JSON files at the leaf level, usually named by EC2 instance id

The app decodes URL-encoded folder names (`AWS%3A...` -> `AWS:...`) and uses each decoded category name as one logical dataset.

---

## Technology Stack

- Python 3.12+
- Streamlit (UI and interactive filtering)
- Pandas (data shaping, joins, pivoting, null handling)
- Pathlib (cross-platform path traversal and recursive file discovery)

---

## How the Code Works

Main file:

- `app.py`

High-level phases:

1. **Digest phase** (`load_data`)
2. **Transformation phase** (`process_data`)
3. **UI phase** (`main_ui`)

### 1) Digest phase: recursive ingestion by category

`load_data(base_path_str)`:

- starts from inventory root (`Path(base_path_str)`)
- iterates each first-level category folder
- for each category, recursively scans `*.json` using `Path.rglob("*.json")`
- reads records with `_safe_read_json_records()`, supporting:
  - single JSON object file
  - JSON array file
  - JSON-lines file (one object per line)
- stores the records from each category in an independent Pandas DataFrame
- returns `dict[str, DataFrame]` keyed by decoded category name (`AWS:...`)

Performance:

- `@st.cache_data` caches the digest output to avoid re-reading all files on every UI interaction.

### 2) Transformation phase: key-based joins and cleanup

`process_data(category_frames)` combines key datasets:

- `AWS:InstanceInformation` -> machine identity and networking
- `AWS:Tag` -> `Product`, `FarmName`, `CustomerName`, `hostname`
- `AWS:WindowsRegistry` -> version fields from registry values:
  - `ValueName=Major` -> `Major`
  - `ValueName=Minor` -> `Minor`
- `AWS:Application` -> installed product context (OpenText / Micro Focus patterns)

Join logic:

- common primary key: `resourceId`
- fallback normalization from `InstanceId` / `instanceId` into `resourceId`
- latest record selection per instance when `captureTime` exists
- deduplication by `resourceId`
- null management for user-facing columns

Compliance logic:

- extract numeric major version from `Major`
- `Green` when major `>= 26`
- `Yellow` when major `== 25`
- `Red` when major `< 25`
- `Unknown` when major is missing or non-numeric

### 3) UI phase: dynamic filters and visual outputs

`main_ui()` builds:

- sidebar configuration for inventory root path
- cache reset button (`Clear cache / Reload all files`)
- digest summary table (rows/columns by category)
- dynamic multiselect filters:
  - `FarmName`
  - `Product` (defaults to `LRE` when present)
  - `Major (Version)`
  - `Minor (Patch)`
- combined interactive table (`st.dataframe`)
- compliance pie chart (Green / Yellow / Red / Unknown)
- top-level KPI metrics (filtered count and compliance counts)

---

## Functional Requirements Mapped

- Recursive scan of local repository by category: **implemented**
- Independent DataFrame per category: **implemented**
- Join required entities by instance key (`resourceId`): **implemented**
- Required output fields:
  - `ComputerName`
  - `IpAddress`
  - `Product`
  - `FarmName`
  - `CustomerName`
  - `resourceId`
  - plus registry/app/context fields for analysis: **implemented**
- Filter defaults to Product = `LRE` when available: **implemented**
- Duplicate cleanup and null handling: **implemented**
- Compliance pie chart by version policy: **implemented**
- Function-based structure:
  - `load_data()`
  - `process_data()`
  - `main_ui()`: **implemented**
- Caching for performance:
  - `@st.cache_data`: **implemented**

---

## Setup and Installation

From project folder:

```powershell
cd C:\Streamlit_LRE_Inventory\StreamLit-AWS-Inventory
```

Recommended approach (virtual environment):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks script activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

---

## Run the Dashboard

```powershell
python -m streamlit run app.py
```

Open browser URL shown by Streamlit (typically `http://localhost:8501`).

---

## Stop / Kill

Normal stop:

- press `Ctrl + C` in the terminal running Streamlit

Force kill (if terminal is stuck):

```powershell
Get-NetTCPConnection -LocalPort 8501 | Select-Object -ExpandProperty OwningProcess
Stop-Process -Id <PID> -Force
```

---

## Interpreter Troubleshooting (important on Windows)

If you see `No module named streamlit`, you likely installed packages with one Python and ran app with another.

Use one interpreter consistently:

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 -m streamlit run app.py
```

---

## Notes for Demo Usage

- Keep inventory root set to `C:/lre_inventory_repository` unless your data location changes.
- After replacing inventory files, use sidebar button **Clear cache / Reload all files**.
- Default Product filter attempts to preselect `LRE` for the focused demo scenario.
