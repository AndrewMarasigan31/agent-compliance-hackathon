import math
import os
import folium
import numpy as np
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
from sklearn.cluster import KMeans
from streamlit_folium import st_folium

CSV_PATH = os.path.join(os.path.dirname(__file__), "store_leads.csv")
TODAY = pd.Timestamp(datetime.now().date())
CLOSED_REASONS = {
    "Permanently Closed",
    "Temporarily Closed",
    "Hindi mahanap yung tindahan",
    "Masikip ang Daan",
    "Duplicate Account",
    "Walang tindahan",
}
EXCLUDED_STATUSES = {"pending", "dispatched", "packed", "processing", "ready_to_redispatch"}


def _filter_base(df: pd.DataFrame) -> pd.DataFrame:
    df = df[~df["latest_order_status"].isin(EXCLUDED_STATUSES)]
    df = df[~df["bakit hindi umorder si customer?"].isin(CLOSED_REASONS)]
    cutoff = TODAY - timedelta(days=7)
    visited_recently = df["visit_date"].notna() & (df["visit_date"] >= cutoff)
    df = df[~visited_recently]
    return df.reset_index(drop=True)


@st.cache_data
def load_and_filter() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH)
    df["last_delivered_date"] = pd.to_datetime(df["last_delivered_date"], errors="coerce")
    df["visit_date"] = pd.to_datetime(df["visit_date"], errors="coerce")
    return _filter_base(df)


def _apply_hard_exclusions(df: pd.DataFrame, cutoff_year: int | None = None, include_ncmb: bool = False) -> pd.DataFrame:
    """Keep only stores eligible for scoring: correct day window, not hard-excluded."""
    # Remove repeat no-conversion stores
    mask = (df["number_of_visits"].fillna(0) >= 5) & (df["no_delivered_orders"].fillna(0) == 0)
    df = df[~mask].copy()

    # Always exclude Current Month Buyers — they already ordered this month
    if "bucket" in df.columns:
        df = df[df["bucket"] != "Current Month Buyer"].copy()

    # Keep only stores in the eligible day windows
    days_since = (TODAY - df["last_delivered_date"]).dt.days
    nr_cutoff_date = pd.Timestamp(f"{cutoff_year}-01-01") if cutoff_year is not None else None
    nr_mask = (days_since >= 60) & (days_since <= 730) if nr_cutoff_date is None else (days_since >= 60) & (df["last_delivered_date"] >= nr_cutoff_date)

    ncmb_mask = pd.Series(False, index=df.index)
    if include_ncmb and "bucket" in df.columns:
        ncmb_mask = df["bucket"] == "Non Current Month Buyer"

    eligible = (
        df["last_delivered_date"].isna()                          # never ordered
        | nr_mask                                                 # New/Revival: 60d+ (year-filtered)
        | ((days_since >= 31) & (days_since < 60))                # P30D: 31–60 days
        | ncmb_mask                                               # Non Current Month Buyers (if enabled)
    )
    return df[eligible].reset_index(drop=True)


def _split_pools_from_df(cluster_df: pd.DataFrame, cutoff_year: int | None = None, include_ncmb: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split a pre-filtered DataFrame into (churned, p30d, never_ordered) pools."""
    days_since = (TODAY - cluster_df["last_delivered_date"]).dt.days
    has_bucket = "bucket" in cluster_df.columns

    never_ordered_mask = cluster_df["last_delivered_date"].isna()

    if has_bucket:
        if cutoff_year is not None:
            cutoff_date = pd.Timestamp(f"{cutoff_year}-01-01")
            churned_mask = (cluster_df["bucket"] == "Churned (60+ Days)") & (cluster_df["last_delivered_date"] >= cutoff_date)
        else:
            churned_mask = cluster_df["bucket"] == "Churned (60+ Days)"

        p30d_mask = cluster_df["bucket"] == "P30D No Delivery (NKA)"
        if include_ncmb:
            p30d_mask = p30d_mask | (cluster_df["bucket"] == "Non Current Month Buyer")
    else:
        if cutoff_year is not None:
            cutoff_date = pd.Timestamp(f"{cutoff_year}-01-01")
            churned_mask = (days_since >= 60) & (cluster_df["last_delivered_date"] >= cutoff_date)
        else:
            churned_mask = days_since >= 60
        p30d_mask = (days_since >= 31) & (days_since < 60)

    churned = cluster_df[churned_mask & ~never_ordered_mask].copy()
    p30d = cluster_df[p30d_mask].copy()
    never_ordered = cluster_df[never_ordered_mask].copy()
    return churned.reset_index(drop=True), p30d.reset_index(drop=True), never_ordered.reset_index(drop=True)


def split_pools(df: pd.DataFrame, gcu: str, cutoff_year: int | None = None, include_ncmb: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (churned, p30d, never_ordered) pools for the given GCU."""
    return _split_pools_from_df(df[df["gcu"] == gcu].copy(), cutoff_year=cutoff_year, include_ncmb=include_ncmb)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

_DAY_NAME_TO_WEEKDAY = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _days_until_next_delivery(delivery_days_str) -> int:
    """Return days from today until the nearest delivery day. 0 if today."""
    if not isinstance(delivery_days_str, str):
        return 7  # no delivery info → furthest possible
    days = [_DAY_NAME_TO_WEEKDAY[d.strip().lower()] for d in delivery_days_str.split(",")
            if d.strip().lower() in _DAY_NAME_TO_WEEKDAY]
    if not days:
        return 7
    today_wd = TODAY.weekday()
    diffs = [(d - today_wd) % 7 for d in days]
    return min(diffs)


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _cluster_density(pool: pd.DataFrame, radius_km: float = 2.0) -> pd.Series:
    """For each store, count other stores within radius_km (vectorized)."""
    lats = np.radians(pool["lat"].values)
    lons = np.radians(pool["long"].values)
    # Broadcast pairwise haversine
    dlat = lats[:, None] - lats[None, :]
    dlon = lons[:, None] - lons[None, :]
    a = np.sin(dlat / 2) ** 2 + np.cos(lats[:, None]) * np.cos(lats[None, :]) * np.sin(dlon / 2) ** 2
    dist = 6371.0 * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    np.fill_diagonal(dist, np.inf)
    counts = (dist <= radius_km).sum(axis=1)
    return pd.Series(counts, index=pool.index)


def _minmax(series: pd.Series) -> pd.Series:
    mn, mx = series.min(), series.max()
    return (series - mn) / (mx - mn + 1e-9)


def score_churned(pool: pd.DataFrame) -> pd.DataFrame:
    df = pool.copy()

    days_since = (TODAY - df["last_delivered_date"]).dt.days.fillna(0)
    df["_cluster_density"] = _cluster_density(df)
    df["_delivery_proximity"] = df["delivery_days"].apply(
        lambda x: max(0.0, (7 - _days_until_next_delivery(x)) / 6)
    )

    n_days = _minmax(days_since)
    n_cluster = _minmax(df["_cluster_density"].astype(float))
    n_delivery = _minmax(df["_delivery_proximity"])

    df["score"] = (
        0.50 * (1.0 - n_days)
        + 0.30 * n_cluster
        + 0.20 * n_delivery
    )

    return df.sort_values("score", ascending=False).reset_index(drop=True)


def score_never_ordered(pool: pd.DataFrame) -> pd.DataFrame:
    df = pool.copy()

    df["_cluster_density"] = _cluster_density(df)
    df["_delivery_proximity"] = df["delivery_days"].apply(
        lambda x: max(0.0, (7 - _days_until_next_delivery(x)) / 6)
    )

    n_cluster = _minmax(df["_cluster_density"].astype(float))
    n_delivery = _minmax(df["_delivery_proximity"])

    df["score"] = (
        0.50 * n_cluster
        + 0.50 * n_delivery
    )

    return df.sort_values("score", ascending=False).reset_index(drop=True)


def score_p30d(pool: pd.DataFrame) -> pd.DataFrame:
    df = pool.copy()

    days_since = (TODAY - df["last_delivered_date"]).dt.days.fillna(0)
    df["_delivery_proximity"] = df["delivery_days"].apply(
        lambda x: max(0.0, (7 - _days_until_next_delivery(x)) / 6)
    )

    # 1 for Non Current Month Buyers — they ordered last month, warmest P30D lead
    df["_ncmb_boost"] = 0.0
    if "bucket" in df.columns:
        df["_ncmb_boost"] = (df["bucket"] == "Non Current Month Buyer").astype(float)

    n_orders = _minmax(df["no_delivered_orders"].fillna(0).astype(float))
    n_days = _minmax(days_since)
    n_delivery = _minmax(df["_delivery_proximity"])

    # ncmb_boost gets 0.30; remaining weights scaled down proportionally (×0.70)
    df["score"] = (
        0.30 * df["_ncmb_boost"]
        + 0.28 * n_orders
        + 0.245 * n_delivery
        + 0.175 * (1.0 - n_days)
    )

    return df.sort_values("score", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Store selector — 70:30 split with cross-pool backfill
# ---------------------------------------------------------------------------

def select_stores(churned: pd.DataFrame, p30d: pd.DataFrame, never_ordered: pd.DataFrame, target: int = 60, churned_ratio: float = 0.70) -> pd.DataFrame:
    """Pick `target` stores: 70% Churned, 30% P30D, Never-Ordered as backfill only."""
    churned_target = round(target * churned_ratio)
    p30d_target = target - churned_target

    churned_pool = churned.copy()
    churned_pool["pool"] = "Churned"
    p30d_pool = p30d.copy()
    p30d_pool["pool"] = "P30D"
    if "bucket" in p30d_pool.columns:
        p30d_pool.loc[p30d_pool["bucket"] == "Non Current Month Buyer", "pool"] = "Non Month Buyer"
    never_ordered_pool = never_ordered.copy()
    never_ordered_pool["pool"] = "Never-Ordered"

    churned_pick = churned_pool.head(churned_target)
    p30d_pick = p30d_pool.head(p30d_target)

    # Cross-fill between Churned and P30D first
    if len(churned_pick) < churned_target:
        extra = p30d_pool.iloc[p30d_target:p30d_target + (churned_target - len(churned_pick))]
        p30d_pick = pd.concat([p30d_pick, extra], ignore_index=True)

    if len(p30d_pick) < p30d_target:
        extra = churned_pool.iloc[churned_target:churned_target + (p30d_target - len(p30d_pick))]
        churned_pick = pd.concat([churned_pick, extra], ignore_index=True)

    combined = pd.concat([churned_pick, p30d_pick], ignore_index=True)
    combined = combined.drop_duplicates(subset=["store_name", "lat", "long"])

    # Never-Ordered backfill capped at 10% of target
    shortfall = target - len(combined)
    max_never_ordered = round(target * 0.12)
    if shortfall > 0 and not never_ordered_pool.empty:
        backfill = never_ordered_pool.head(min(shortfall, max_never_ordered))
        combined = pd.concat([combined, backfill], ignore_index=True)
        combined = combined.drop_duplicates(subset=["store_name", "lat", "long"])

    return combined.head(target).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Route optimizer — nearest-neighbor from GCU centroid
# ---------------------------------------------------------------------------

def optimize_route(selected: pd.DataFrame, all_gcu_stores: pd.DataFrame) -> pd.DataFrame:
    """Order selected stores via nearest-neighbor from GCU centroid. Returns df with 'rank' column."""
    if selected.empty:
        return selected.copy().assign(rank=pd.Series([], dtype=int))

    centroid_lat = all_gcu_stores["lat"].mean()
    centroid_lon = all_gcu_stores["long"].mean()

    remaining = list(selected.index)
    ordered_indices = []
    cur_lat, cur_lon = centroid_lat, centroid_lon

    while remaining:
        nearest = min(
            remaining,
            key=lambda i: _haversine_km(cur_lat, cur_lon, selected.at[i, "lat"], selected.at[i, "long"]),
        )
        ordered_indices.append(nearest)
        cur_lat, cur_lon = selected.at[nearest, "lat"], selected.at[nearest, "long"]
        remaining.remove(nearest)

    result = selected.loc[ordered_indices].copy().reset_index(drop=True)
    result["rank"] = range(1, len(result) + 1)
    return result


# ---------------------------------------------------------------------------
# Beat post-processing
# ---------------------------------------------------------------------------

def _merge_small_beats(beats: list[pd.DataFrame], min_size: int = 45, target: int = 60) -> list[pd.DataFrame]:
    """Merge any beat smaller than min_size into its geographically nearest beat."""
    result = [b.copy() for b in beats]

    changed = True
    while changed:
        changed = False
        small = [i for i, b in enumerate(result) if len(b) < min_size]
        if not small:
            break

        i = min(small, key=lambda x: len(result[x]))
        s_lat = result[i]["lat"].mean()
        s_lon = result[i]["long"].mean()

        best_j = min(
            (j for j in range(len(result)) if j != i),
            key=lambda j: _haversine_km(s_lat, s_lon, result[j]["lat"].mean(), result[j]["long"].mean()),
            default=None,
        )
        if best_j is None:
            break

        merged = pd.concat([result[i], result[best_j]], ignore_index=True)
        merged = merged.drop_duplicates(subset=["store_name", "lat", "long"])
        if "score" in merged.columns:
            merged = merged.sort_values("score", ascending=False).head(target).reset_index(drop=True)
        merged = optimize_route(merged, merged)

        result = [b for k, b in enumerate(result) if k != i and k != best_j]
        result.append(merged)
        changed = True

    return result


# ---------------------------------------------------------------------------
# Streamlit app
# ---------------------------------------------------------------------------

def run_global_pipeline(df: pd.DataFrame, churned_ratio: float = 0.70, cutoff_year: int | None = None, include_ncmb: bool = False) -> list[pd.DataFrame]:
    """Return a list of geographically clustered beats across all GCUs.

    K = floor(total eligible stores / 60) clusters via KMeans on lat/long.
    Each cluster has at least 60 stores. Remainder is distributed across beats.
    Priority: 70% Churned, 30% P30D, Never-Ordered as backfill only.
    No store appears in more than one beat.
    """
    all_stores = _apply_hard_exclusions(df.copy(), cutoff_year=cutoff_year, include_ncmb=include_ncmb)
    if all_stores.empty:
        return []

    n = len(all_stores)
    K = max(1, math.floor(n / 60))

    if K <= 1:
        all_stores["_cluster"] = 0
        K = 1
    else:
        coords = all_stores[["lat", "long"]].values
        kmeans = KMeans(n_clusters=K, random_state=42, n_init=10)
        all_stores["_cluster"] = kmeans.fit_predict(coords)


    unique_clusters = sorted(all_stores["_cluster"].unique())
    beats = []
    for cluster_id in unique_clusters:
        cluster_stores = all_stores[all_stores["_cluster"] == cluster_id].copy()
        churned_raw, p30d_raw, never_ordered_raw = _split_pools_from_df(cluster_stores, cutoff_year=cutoff_year, include_ncmb=include_ncmb)

        churned = score_churned(churned_raw) if not churned_raw.empty else churned_raw
        p30d = score_p30d(p30d_raw) if not p30d_raw.empty else p30d_raw
        never_ordered = score_never_ordered(never_ordered_raw) if not never_ordered_raw.empty else never_ordered_raw

        target = min(60, len(cluster_stores))
        selected = select_stores(churned, p30d, never_ordered, target=target, churned_ratio=churned_ratio)

        if selected.empty:
            continue

        beat = optimize_route(selected, cluster_stores)
        beats.append(beat)

    return _merge_small_beats(beats, min_size=45, target=60)


BEAT_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#aec7e8", "#ffbb78",
]


def build_map(daily_list: pd.DataFrame, all_gcu_stores: pd.DataFrame, color: str = "#1f77b4") -> folium.Map:
    centroid_lat = all_gcu_stores["lat"].mean()
    centroid_lon = all_gcu_stores["long"].mean()

    m = folium.Map(location=[centroid_lat, centroid_lon], zoom_start=13)
    _add_beat_to_map(m, daily_list, color)
    return m


def build_all_beats_map(beats: list[pd.DataFrame], all_gcu_stores: pd.DataFrame) -> folium.Map:
    centroid_lat = all_gcu_stores["lat"].mean()
    centroid_lon = all_gcu_stores["long"].mean()

    m = folium.Map(location=[centroid_lat, centroid_lon], zoom_start=12)
    for i, beat in enumerate(beats):
        color = BEAT_COLORS[i % len(BEAT_COLORS)]
        _add_beat_to_map(m, beat, color, beat_label=f"Beat {i+1}")
    return m


def _add_beat_to_map(m: folium.Map, daily_list: pd.DataFrame, color: str, beat_label: str = "") -> None:
    coords = []
    for _, row in daily_list.iterrows():
        lat, lon = row["lat"], row["long"]
        rank = int(row["rank"])
        coords.append((lat, lon))

        label = f"{beat_label} #{rank}" if beat_label else str(rank)
        folium.Marker(
            location=[lat, lon],
            icon=folium.DivIcon(
                html=f'<div style="background:{color};color:white;border-radius:50%;width:24px;height:24px;'
                     f'display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:10px;">'
                     f'{rank}</div>',
                icon_size=(24, 24),
                icon_anchor=(12, 12),
            ),
            tooltip=f"{label}. {row.get('store_name', '')} — {row.get('barangay', '')}",
            popup=f"<b>{row.get('store_name', '')}</b><br>{row.get('barangay', '')}<br>{beat_label}",
        ).add_to(m)

    if len(coords) > 1:
        folium.PolyLine(coords, color=color, weight=2, opacity=0.7).add_to(m)


def main():
    st.set_page_config(page_title="Agent Compliance — Daily Store List", layout="wide")
    st.title("Agent Compliance — Daily Store List")

    uploaded = st.file_uploader("Upload store leads CSV", type="csv")
    if uploaded is None:
        st.info("Please upload a store leads CSV to get started.")
        return

    agent_name = st.text_input("Agent Name", placeholder="e.g. Juan dela Cruz")

    raw = pd.read_csv(uploaded)
    raw["last_delivered_date"] = pd.to_datetime(raw["last_delivered_date"], errors="coerce")
    raw["visit_date"] = pd.to_datetime(raw["visit_date"], errors="coerce")
    df = _filter_base(raw)
    st.success(f"Loaded {len(df)} stores from uploaded file.")

    # Auto-reset ratio default when NCMB checkbox is toggled
    _prev_ncmb = st.session_state.get("_prev_ncmb_state")
    include_ncmb = st.checkbox(
        "Include Non-Current Month Buyers in P30D pool",
        value=st.session_state.get("include_ncmb", False),
        help="Stores that ordered last month but not this month. When included, beat composition shifts to 30% New/Revival / 70% P30D.",
    )
    if _prev_ncmb != include_ncmb:
        st.session_state["_prev_ncmb_state"] = include_ncmb
        st.session_state["nr_pct"] = 30 if include_ncmb else 70
    st.session_state["include_ncmb"] = include_ncmb

    nr_pct = st.slider(
        "Churned vs P30D ratio",
        min_value=0, max_value=100,
        value=st.session_state.get("nr_pct", 30 if include_ncmb else 70),
        step=5,
        format="%d%% Churned",
        key="nr_pct",
    )
    nr_ratio = nr_pct / 100

    years_in_data = sorted(
        df["last_delivered_date"].dropna().dt.year.unique().tolist(),
        reverse=True,
    )
    year_options = [str(y) for y in years_in_data] + ["All time"]
    default_year = (TODAY - pd.Timedelta(days=730)).year
    default_idx = next(
        (i for i, y in enumerate(year_options) if y == str(default_year)),
        len(year_options) - 1,
    )
    selected_year_str = st.selectbox(
        "New/Revival cutoff year (last delivered ≥ Jan 1 of selected year; never-ordered always included)",
        year_options,
        index=default_idx,
    )
    cutoff_year = None if selected_year_str == "All time" else int(selected_year_str)

    if st.button("Generate All Beats"):
        beats = run_global_pipeline(df, churned_ratio=nr_ratio, cutoff_year=cutoff_year, include_ncmb=include_ncmb)
        st.session_state["beats"] = beats
        st.session_state["nr_ratio"] = nr_ratio
        st.session_state["agent_name"] = agent_name.strip() or "Agent"
        st.session_state["cutoff_year"] = cutoff_year
        st.session_state.pop("_map", None)
        st.session_state.pop("_map_key", None)
        ncmb_note = " (Non-Current Month Buyers included)" if include_ncmb else ""
        st.success(f"Generated {len(beats)} beat(s) — {nr_pct}% Churned / {100 - nr_pct}% P30D{ncmb_note}")

    if "beats" in st.session_state:
        beats = st.session_state["beats"]
        saved_agent = st.session_state.get("agent_name", "Agent")

        st.subheader(f"{saved_agent} — {len(beats)} Beat(s)")

        beat_labels = ["All"] + [f"Beat {i+1}" for i in range(len(beats))]
        col_beat, col_toggle = st.columns([3, 1])
        with col_beat:
            selected_beat_label = st.selectbox("Select Beat", beat_labels)
        with col_toggle:
            show_all_map = st.toggle("Show all beats on map", value=False)

        # Build the active dataframe — either one beat or all combined
        if selected_beat_label == "All":
            frames = []
            for i, b in enumerate(beats):
                b2 = b.copy()
                b2["Beat"] = f"Beat {i+1}"
                frames.append(b2)
            daily_list = pd.concat(frames, ignore_index=True)
            beat_color = "#1f77b4"
        else:
            beat_idx = beat_labels.index(selected_beat_label) - 1
            daily_list = beats[beat_idx].copy()
            daily_list["Beat"] = selected_beat_label
            beat_color = BEAT_COLORS[beat_idx % len(BEAT_COLORS)]

        map_key = (selected_beat_label, show_all_map)
        if st.session_state.get("_map_key") != map_key:
            if show_all_map or selected_beat_label == "All":
                st.session_state["_map"] = build_all_beats_map(beats, df)
            else:
                st.session_state["_map"] = build_map(daily_list, daily_list, color=beat_color)
            st.session_state["_map_key"] = map_key
        st_folium(st.session_state["_map"], width="100%", height=500, returned_objects=[])

        # Store table
        def _days_since_label(last_date):
            if pd.isna(last_date):
                return "Never"
            return int((TODAY - last_date).days)

        cols = ["Beat", "rank", "store_name", "barangay", "city", "pool",
                "last_delivered_date", "score",
                "bakit hindi umorder si customer?", "number_of_visits", "visit_date"]
        for col in ["username", "gcu"]:
            if col in daily_list.columns:
                cols.append(col)
        table = daily_list[[c for c in cols if c in daily_list.columns]].copy()
        table["Days Since Last Order"] = table["last_delivered_date"].apply(_days_since_label)
        table["score"] = table["score"].round(2)
        table["visit_date"] = pd.to_datetime(table["visit_date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("Never")
        table = table.drop(columns=["last_delivered_date"])
        rename_map = {"rank": "Rank", "store_name": "Store Name", "barangay": "Barangay",
                      "city": "City", "pool": "Pool", "score": "Score",
                      "username": "Username", "gcu": "GCU",
                      "bakit hindi umorder si customer?": "Rejection Reason",
                      "number_of_visits": "# Visits", "visit_date": "Last Visit Date"}
        table = table.rename(columns=rename_map)
        display_cols = ["Beat", "Rank", "Store Name"]
        for c in ["Username", "GCU"]:
            if c in table.columns:
                display_cols.append(c)
        display_cols += ["Barangay", "City", "Pool", "Days Since Last Order",
                         "# Visits", "Last Visit Date", "Rejection Reason", "Score"]
        table = table[[c for c in display_cols if c in table.columns]]
        table = table.sort_values(["Beat", "Rank"]).reset_index(drop=True)

        st.dataframe(table, height=400, use_container_width=True)

        with st.expander("How stores are scored"):
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown("**Churned pool (70%)**")
                st.markdown("""
| Factor | Weight |
|---|---|
| Recency (lower days = higher score) | 50% |
| Nearby store density (2km radius) | 30% |
| Delivery day coming soon | 20% |

Scope: 60d+ since last order, has previous order history.
Stores with 5+ visits and 0 orders excluded.
All inputs normalized 0–1 before weighting.
""")
            with col2:
                st.markdown("**P30D pool (30%)**")
                st.markdown("""
| Factor | Weight |
|---|---|
| Non-Current Month Buyer boost | 30% |
| Order frequency | 28% |
| Delivery day coming soon | 24.5% |
| Recency (lower days = higher score) | 17.5% |

Non-Current Month Buyers always rank first when included.
Stores with 5+ visits and 0 orders excluded.
All inputs normalized 0–1 before weighting.
""")
            with col3:
                st.markdown("**Never-Ordered (backfill only)**")
                st.markdown("""
| Factor | Weight |
|---|---|
| Nearby store density (2km radius) | 50% |
| Delivery day coming soon | 50% |

Only appears when Churned + P30D cannot fill the 60-store target.
All inputs normalized 0–1 before weighting.
""")

        # CSV export
        csv_cols = ["Beat", "rank", "store_name", "username", "barangay", "city", "gcu", "lat", "long",
                    "last_delivered_date", "no_delivered_orders", "delivery_days", "pool",
                    "bakit hindi umorder si customer?", "number_of_visits", "visit_date"]
        export_df = daily_list[[c for c in csv_cols if c in daily_list.columns]]
        if selected_beat_label != "All":
            export_df = export_df.sort_values("rank")
        csv_bytes = export_df.to_csv(index=False).encode("utf-8")
        file_label = "All_Beats" if selected_beat_label == "All" else selected_beat_label.replace(" ", "_")
        st.download_button(
            label=f"Download {selected_beat_label} CSV",
            data=csv_bytes,
            file_name=f"{saved_agent.replace(' ', '_')}_{file_label}_daily_list.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
