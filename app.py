from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pandas as pd
import streamlit as st


DEFAULT_INVENTORY_PATH = Path("C:/lre_inventory_repository")


def _safe_read_json_records(file_path: Path) -> list[dict[str, Any]]:
    """Read JSON records from either full-JSON or JSON-lines files."""
    try:
        raw_text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return []

    if not raw_text:
        return []

    records: list[dict[str, Any]] = []

    # Try to parse as one JSON payload first (dict or list).
    try:
        payload = json.loads(raw_text)
        if isinstance(payload, dict):
            records = [payload]
        elif isinstance(payload, list):
            records = [item for item in payload if isinstance(item, dict)]
        else:
            records = []
    except json.JSONDecodeError:
        # Fall back to JSON-lines (one JSON object per line).
        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    records.append(parsed)
            except json.JSONDecodeError:
                continue

    return records


def _ensure_resource_id(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    if "resourceId" not in out.columns:
        out["resourceId"] = pd.NA

    if "InstanceId" in out.columns:
        out["resourceId"] = out["resourceId"].fillna(out["InstanceId"])
    if "instanceId" in out.columns:
        out["resourceId"] = out["resourceId"].fillna(out["instanceId"])

    return out


@st.cache_data(show_spinner=False)
def load_data(base_path_str: str) -> dict[str, pd.DataFrame]:
    """
    Digest phase:
    Recursively scan inventory folder and return one DataFrame per category.
    """
    base_path = Path(base_path_str)
    if not base_path.exists() or not base_path.is_dir():
        return {}

    category_frames: dict[str, pd.DataFrame] = {}

    for category_dir in sorted([p for p in base_path.iterdir() if p.is_dir()]):
        decoded_category = unquote(category_dir.name)
        all_records: list[dict[str, Any]] = []

        for json_file in category_dir.rglob("*.json"):
            file_records = _safe_read_json_records(json_file)
            if not file_records:
                continue
            for record in file_records:
                record["_source_file"] = str(json_file)
                all_records.append(record)

        if all_records:
            df = pd.DataFrame.from_records(all_records)
            df = _ensure_resource_id(df)
            category_frames[decoded_category] = df
        else:
            category_frames[decoded_category] = pd.DataFrame()

    return category_frames


def _pick_latest_by_resource(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "resourceId" not in df.columns:
        return df

    out = df.copy()
    if "captureTime" in out.columns:
        out["captureTime"] = pd.to_datetime(out["captureTime"], errors="coerce", utc=True)
        out = out.sort_values(["resourceId", "captureTime"], ascending=[True, False])

    out = out.dropna(subset=["resourceId"])
    out = out.drop_duplicates(subset=["resourceId"], keep="first")
    return out


def _extract_tags(tags_df: pd.DataFrame) -> pd.DataFrame:
    if tags_df.empty:
        return pd.DataFrame(columns=["resourceId", "Product", "FarmName", "CustomerName", "hostname"])

    needed_keys = {"Product", "FarmName", "CustomerName", "hostname"}
    out = tags_df.copy()
    out = out[out["Key"].isin(needed_keys)] if "Key" in out.columns else pd.DataFrame()
    if out.empty or "resourceId" not in out.columns or "Value" not in out.columns:
        return pd.DataFrame(columns=["resourceId", "Product", "FarmName", "CustomerName", "hostname"])

    out = out.dropna(subset=["resourceId", "Key"])
    out = out.sort_values("captureTime", ascending=False) if "captureTime" in out.columns else out
    pivot = (
        out.drop_duplicates(subset=["resourceId", "Key"], keep="first")
        .pivot_table(index="resourceId", columns="Key", values="Value", aggfunc="first")
        .reset_index()
    )
    pivot.columns.name = None
    return pivot


def _extract_registry_versions(reg_df: pd.DataFrame) -> pd.DataFrame:
    if reg_df.empty:
        return pd.DataFrame(columns=["resourceId", "Major", "Minor"])

    out = reg_df.copy()
    if "ValueName" not in out.columns or "Value" not in out.columns or "resourceId" not in out.columns:
        return pd.DataFrame(columns=["resourceId", "Major", "Minor"])

    out = out[out["ValueName"].isin(["Major", "Minor"])].dropna(subset=["resourceId"])
    if out.empty:
        return pd.DataFrame(columns=["resourceId", "Major", "Minor"])

    out = out.sort_values("captureTime", ascending=False) if "captureTime" in out.columns else out
    version_df = (
        out.drop_duplicates(subset=["resourceId", "ValueName"], keep="first")
        .pivot_table(index="resourceId", columns="ValueName", values="Value", aggfunc="first")
        .reset_index()
    )
    version_df.columns.name = None

    # Renamed fields from raw registry keys:
    # ValueName=Major -> Major (product major version)
    # ValueName=Minor -> Minor (patch version)
    for col in ["Major", "Minor"]:
        if col not in version_df.columns:
            version_df[col] = pd.NA

    return version_df[["resourceId", "Major", "Minor"]]


def _extract_applications(app_df: pd.DataFrame) -> pd.DataFrame:
    if app_df.empty or "resourceId" not in app_df.columns:
        return pd.DataFrame(columns=["resourceId", "InstalledProductName", "InstalledProductPublisher"])

    out = app_df.copy()
    for col in ["Publisher", "Name"]:
        if col not in out.columns:
            out[col] = ""

    mask = (
        out["Publisher"].fillna("").str.contains("OpenText|Microfocus", case=False, regex=True)
        | out["Name"].fillna("").str.contains("LoadRunner|Performance Engineering|LRE", case=False, regex=True)
    )
    out = out[mask].dropna(subset=["resourceId"])

    if out.empty:
        return pd.DataFrame(columns=["resourceId", "InstalledProductName", "InstalledProductPublisher"])

    out = out.sort_values("captureTime", ascending=False) if "captureTime" in out.columns else out
    out = out.drop_duplicates(subset=["resourceId"], keep="first")

    cols = ["resourceId"]
    if "Name" in out.columns:
        out = out.rename(columns={"Name": "InstalledProductName"})
        cols.append("InstalledProductName")
    if "Publisher" in out.columns:
        out = out.rename(columns={"Publisher": "InstalledProductPublisher"})
        cols.append("InstalledProductPublisher")

    return out[cols]


def _major_to_numeric(series: pd.Series) -> pd.Series:
    extracted = series.astype("string").str.extract(r"(\d+)", expand=False)
    return pd.to_numeric(extracted, errors="coerce")


def process_data(category_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Transformation phase:
    Join instance + tags + registry + app details using resourceId as primary key.
    """
    instance_df = category_frames.get("AWS:InstanceInformation", pd.DataFrame()).copy()
    tag_df = category_frames.get("AWS:Tag", pd.DataFrame()).copy()
    registry_df = category_frames.get("AWS:WindowsRegistry", pd.DataFrame()).copy()
    app_df = category_frames.get("AWS:Application", pd.DataFrame()).copy()

    instance_df = _ensure_resource_id(instance_df)
    instance_df = _pick_latest_by_resource(instance_df)

    keep_cols = [
        "resourceId",
        "ComputerName",
        "IpAddress",
        "PlatformName",
        "InstanceStatus",
    ]
    for c in keep_cols:
        if c not in instance_df.columns:
            instance_df[c] = pd.NA
    instance_df = instance_df[keep_cols]

    tags = _extract_tags(_ensure_resource_id(tag_df))
    versions = _extract_registry_versions(_ensure_resource_id(registry_df))
    apps = _extract_applications(_ensure_resource_id(app_df))

    combined = instance_df.merge(tags, on="resourceId", how="left")
    combined = combined.merge(versions, on="resourceId", how="left")
    combined = combined.merge(apps, on="resourceId", how="left")

    # Null management
    for col in ["ComputerName", "IpAddress", "Product", "FarmName", "CustomerName", "Major", "Minor"]:
        if col not in combined.columns:
            combined[col] = pd.NA
    combined["ComputerName"] = combined["ComputerName"].fillna(combined.get("hostname", pd.NA))
    combined["Product"] = combined["Product"].fillna("Unknown")
    combined["FarmName"] = combined["FarmName"].fillna("Unknown")
    combined["CustomerName"] = combined["CustomerName"].fillna("Unknown")
    combined["Major"] = combined["Major"].fillna("Unknown")
    combined["Minor"] = combined["Minor"].fillna("Unknown")
    combined["IpAddress"] = combined["IpAddress"].fillna("Unknown")

    # De-duplicate primary key
    combined = combined.dropna(subset=["resourceId"]).drop_duplicates(subset=["resourceId"], keep="first")

    major_num = _major_to_numeric(combined["Major"])
    combined["ComplianceState"] = pd.Series("Unknown", index=combined.index)
    combined.loc[major_num >= 26, "ComplianceState"] = "Green"
    combined.loc[major_num == 25, "ComplianceState"] = "Yellow"
    combined.loc[major_num < 25, "ComplianceState"] = "Red"

    final_cols = [
        "resourceId",
        "ComputerName",
        "IpAddress",
        "Product",
        "FarmName",
        "CustomerName",
        "Major",
        "Minor",
        "PlatformName",
        "InstanceStatus",
        "InstalledProductName",
        "InstalledProductPublisher",
        "ComplianceState",
    ]
    for c in final_cols:
        if c not in combined.columns:
            combined[c] = pd.NA

    combined = combined[final_cols].sort_values(["Product", "FarmName", "ComputerName"], na_position="last")
    return combined


def main_ui() -> None:
    st.set_page_config(page_title="LRE AWS Inventory Dashboard", layout="wide")
    st.title("LRE AWS Inventory Dashboard")
    st.caption("Data source: AWS Systems Manager Inventory export from local repository")

    with st.sidebar:
        st.header("Configuration")
        base_path = st.text_input(
            "Inventory root folder",
            value=str(DEFAULT_INVENTORY_PATH),
            help="Root folder containing AWS%3A* category folders.",
        )
        if st.button("Clear cache / Reload all files"):
            st.cache_data.clear()
            st.rerun()

    with st.spinner("Loading inventory files..."):
        category_frames = load_data(base_path)

    if not category_frames:
        st.error("No data loaded. Verify the folder path and that it contains JSON files.")
        return

    combined = process_data(category_frames)
    if combined.empty:
        st.warning("Data was loaded but no joinable records were found by resourceId.")
        return

    st.subheader("Digest phase output")
    digest_rows = []
    for category, df in category_frames.items():
        digest_rows.append(
            {
                "Category": category,
                "Rows": int(len(df)),
                "Columns": int(len(df.columns)),
            }
        )
    st.dataframe(pd.DataFrame(digest_rows).sort_values("Category"), use_container_width=True, hide_index=True)

    st.sidebar.header("Filters")
    farm_values = sorted([v for v in combined["FarmName"].dropna().astype(str).unique()])
    product_values = sorted([v for v in combined["Product"].dropna().astype(str).unique()])
    major_values = sorted([v for v in combined["Major"].dropna().astype(str).unique()])
    minor_values = sorted([v for v in combined["Minor"].dropna().astype(str).unique()])

    default_product = ["LRE"] if "LRE" in product_values else []
    selected_farms = st.sidebar.multiselect("FarmName", farm_values)
    selected_products = st.sidebar.multiselect("Product", product_values, default=default_product)
    selected_major = st.sidebar.multiselect("Major (Version)", major_values)
    selected_minor = st.sidebar.multiselect("Minor (Patch)", minor_values)

    filtered = combined.copy()
    if selected_farms:
        filtered = filtered[filtered["FarmName"].isin(selected_farms)]
    if selected_products:
        filtered = filtered[filtered["Product"].isin(selected_products)]
    if selected_major:
        filtered = filtered[filtered["Major"].isin(selected_major)]
    if selected_minor:
        filtered = filtered[filtered["Minor"].isin(selected_minor)]

    total = len(filtered)
    green = int((filtered["ComplianceState"] == "Green").sum())
    yellow = int((filtered["ComplianceState"] == "Yellow").sum())
    red = int((filtered["ComplianceState"] == "Red").sum())

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Filtered instances", total)
    m2.metric("Green (>=26)", green)
    m3.metric("Yellow (=25)", yellow)
    m4.metric("Red (<25)", red)

    st.subheader("Combined inventory table")
    st.dataframe(filtered, use_container_width=True, hide_index=True)

    st.subheader("Compliance pie chart")
    compliance_df = (
        filtered["ComplianceState"]
        .value_counts(dropna=False)
        .rename_axis("ComplianceState")
        .reset_index(name="Count")
    )
    color_scale = {
        "domain": ["Green", "Yellow", "Red", "Unknown"],
        "range": ["#2ca02c", "#ffbf00", "#d62728", "#9e9e9e"],
    }
    st.vega_lite_chart(
        compliance_df,
        {
            "mark": {"type": "arc", "innerRadius": 30},
            "encoding": {
                "theta": {"field": "Count", "type": "quantitative"},
                "color": {
                    "field": "ComplianceState",
                    "type": "nominal",
                    "scale": color_scale,
                    "legend": {"title": "Compliance"},
                },
                "tooltip": [
                    {"field": "ComplianceState", "type": "nominal"},
                    {"field": "Count", "type": "quantitative"},
                ],
            },
        },
        use_container_width=True,
    )


if __name__ == "__main__":
    main_ui()
