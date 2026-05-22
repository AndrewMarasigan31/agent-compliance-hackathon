import math
import os
import folium
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
from sklearn.cluster import KMeans
from streamlit_folium import st_folium

CSV_PATH = os.path.join(os.path.dirname(__file__), "store_leads.csv")
TODAY = pd.Timestamp(datetime.now().date())
CLOSED_REASONS = {"Permanently Closed", "Temporarily Closed", "Wala ang may ari"}
EXCLUDED_STATUSES = {"pending", "dispatched"}


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


def _apply_hard_exclusions(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only stores eligible for scoring: correct day window, not hard-excluded."""
    # Remove repeat no-conversion stores
    mask = (df["number_of_visits"].fillna(0) >= 5) & (df["no_delivered_orders"].fillna(0) == 0)
    df = df[~mask].copy()

    # Keep only stores in the eligible day windows
    days_since = (TODAY - df["last_delivered_date"]).dt.days
    eligible = (
        df["last_delivered_date"].isna()                          # never ordered
        | ((days_since >= 60) & (days_since <= 730))              # New/Revival: 60–730 days
        | ((days_since >= 31) & (days_since < 60))                # P30D: 31–60 days
    )
    return df[eligible].reset_index(drop=True)


def _split_pools_from_df(cluster_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a pre-filtered DataFrame into (new_revival, p30d) pools."""
    days_since = (TODAY - cluster_df["last_delivered_date"]).dt.days
    # New/Revival: never ordered OR 60–730 days churned
    new_revival = cluster_df[
        cluster_df["last_delivered_date"].isna() | ((days_since >= 60) & (days_since <= 730))
    ].copy()
    p30d = cluster_df[(days_since >= 31) & (days_since < 60)].copy()
    return new_revival.reset_index(drop=True), p30d.reset_index(drop=True)


def split_pools(df: pd.DataFrame, gcu: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (new_revival_pool, p30d_pool) for the given GCU."""
    return _split_pools_from_df(df[df["gcu"] == gcu].copy())


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
    """For each store, count other stores within radius_km."""
    lats = pool["lat"].values
    lons = pool["long"].values
    counts = []
    for i in range(len(pool)):
        count = sum(
            1 for j in range(len(pool))
            if i != j and _haversine_km(lats[i], lons[i], lats[j], lons[j]) <= radius_km
        )
        counts.append(count)
    return pd.Series(counts, index=pool.index)


def _minmax(series: pd.Series) -> pd.Series:
    mn, mx = series.min(), series.max()
    return (series - mn) / (mx - mn + 1e-9)


def score_new_revival(pool: pd.DataFrame) -> pd.DataFrame:
    df = pool.copy()

    # Never-ordered stores get days_since=0 (below range), boosting (1 - n_days)
    days_since = (TODAY - df["last_delivered_date"]).dt.days.fillna(0)

    df["_never_visited"] = (df["number_of_visits"].isna() | (df["number_of_visits"] == 0)).astype(float)
    df["_cluster_density"] = _cluster_density(df)
    df["_delivery_proximity"] = df["delivery_days"].apply(
        lambda x: max(0.0, (7 - _days_until_next_delivery(x)) / 6)
    )

    n_days = _minmax(days_since)
    n_cluster = _minmax(df["_cluster_density"].astype(float))
    n_delivery = _minmax(df["_delivery_proximity"])

    # Lower days_since = higher score (recently churned = warmer lead)
    df["score"] = (
        0.35 * df["_never_visited"]
        + 0.30 * (1.0 - n_days)
        + 0.20 * n_cluster
        + 0.15 * n_delivery
    )

    return df.sort_values("score", ascending=False).reset_index(drop=True)


def score_p30d(pool: pd.DataFrame) -> pd.DataFrame:
    df = pool.copy()

    days_since = (TODAY - df["last_delivered_date"]).dt.days.fillna(0)
    df["_delivery_proximity"] = df["delivery_days"].apply(
        lambda x: max(0.0, (7 - _days_until_next_delivery(x)) / 6)
    )
    df["_never_visited"] = (df["number_of_visits"].isna() | (df["number_of_visits"] == 0)).astype(float)

    n_orders = _minmax(df["no_delivered_orders"].fillna(0).astype(float))
    n_days = _minmax(days_since)
    n_delivery = _minmax(df["_delivery_proximity"])

    # Lower days_since = more recent = higher score
    df["score"] = (
        0.40 * n_orders
        + 0.35 * n_delivery
        + 0.25 * (1.0 - n_days)
    )

    return df.sort_values("score", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Store selector — 70:30 split with cross-pool backfill
# ---------------------------------------------------------------------------

def select_stores(new_revival: pd.DataFrame, p30d: pd.DataFrame, target: int = 30, nr_ratio: float = 0.70) -> pd.DataFrame:
    """Pick `target` stores using configurable ratio with cross-pool backfill."""
    nr_target = round(target * nr_ratio)
    p30_target = target - nr_target

    nr_pool = new_revival.copy()
    nr_pool["pool"] = "New/Revival"
    p30_pool = p30d.copy()
    p30_pool["pool"] = "P30D"

    nr_pick = nr_pool.head(nr_target)
    p30_pick = p30_pool.head(p30_target)

    if len(nr_pick) < nr_target:
        extra = p30_pool.iloc[p30_target:p30_target + (nr_target - len(nr_pick))]
        p30_pick = pd.concat([p30_pick, extra], ignore_index=True)

    if len(p30_pick) < p30_target:
        extra = nr_pool.iloc[nr_target:nr_target + (p30_target - len(p30_pick))]
        nr_pick = pd.concat([nr_pick, extra], ignore_index=True)

    combined = pd.concat([nr_pick, p30_pick], ignore_index=True)
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
# Streamlit app
# ---------------------------------------------------------------------------

def run_global_pipeline(df: pd.DataFrame, nr_ratio: float = 0.70) -> list[pd.DataFrame]:
    """Return a list of geographically clustered beats across all GCUs.

    K = floor(total eligible stores / 30) clusters via KMeans on lat/long.
    Each cluster has at least 30 stores. Remainder is distributed across beats.
    Each cluster is independently scored and routed (70:30 split preserved).
    No store appears in more than one beat.
    """
    all_stores = _apply_hard_exclusions(df.copy())
    if all_stores.empty:
        return []

    n = len(all_stores)
    K = max(1, math.floor(n / 30))

    if K <= 1:
        all_stores["_cluster"] = 0
        K = 1
    else:
        coords = all_stores[["lat", "long"]].values
        kmeans = KMeans(n_clusters=K, random_state=42, n_init=10)
        all_stores["_cluster"] = kmeans.fit_predict(coords)

        # Merge undersized clusters (< 20 stores) into nearest cluster by centroid
        MIN_CLUSTER_SIZE = 30
        changed = True
        while changed:
            changed = False
            sizes = all_stores["_cluster"].value_counts()
            small = sizes[sizes < MIN_CLUSTER_SIZE].index.tolist()
            if not small:
                break
            centroids = all_stores.groupby("_cluster")[["lat", "long"]].mean()
            for cid in small:
                if cid not in all_stores["_cluster"].values:
                    continue
                c = centroids.loc[cid]
                others = centroids.drop(index=cid)
                if others.empty:
                    break
                dists = others.apply(lambda r: _haversine_km(c["lat"], c["long"], r["lat"], r["long"]), axis=1)
                nearest = dists.idxmin()
                all_stores.loc[all_stores["_cluster"] == cid, "_cluster"] = nearest
                changed = True

    unique_clusters = sorted(all_stores["_cluster"].unique())
    beats = []
    for cluster_id in unique_clusters:
        cluster_stores = all_stores[all_stores["_cluster"] == cluster_id].copy()
        new_revival_raw, p30d_raw = _split_pools_from_df(cluster_stores)

        new_revival = score_new_revival(new_revival_raw) if not new_revival_raw.empty else new_revival_raw
        p30d = score_p30d(p30d_raw) if not p30d_raw.empty else p30d_raw

        target = min(30, len(cluster_stores))
        selected = select_stores(new_revival, p30d, target=target, nr_ratio=nr_ratio)

        if selected.empty:
            continue

        beat = optimize_route(selected, cluster_stores)
        beats.append(beat)

    return beats


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

    nr_pct = st.slider("New/Revival vs P30D ratio", min_value=50, max_value=100, value=70, step=5,
                        format="%d%% New/Revival")
    nr_ratio = nr_pct / 100

    if st.button("Generate All Beats"):
        beats = run_global_pipeline(df, nr_ratio=nr_ratio)
        st.session_state["beats"] = beats
        st.session_state["nr_ratio"] = nr_ratio
        st.session_state["agent_name"] = agent_name.strip() or "Agent"
        st.success(f"Generated {len(beats)} beat(s) — {nr_pct}% New/Revival / {100 - nr_pct}% P30D")

    if "beats" in st.session_state:
        beats = st.session_state["beats"]
        saved_agent = st.session_state.get("agent_name", "Agent")

        st.subheader(f"{saved_agent} — {len(beats)} Beat(s)")

        beat_labels = [f"Beat {i+1}" for i in range(len(beats))]
        col_beat, col_toggle = st.columns([3, 1])
        with col_beat:
            selected_beat_label = st.selectbox("Select Beat", beat_labels)
        with col_toggle:
            show_all = st.toggle("Show all beats on map", value=False)

        beat_idx = beat_labels.index(selected_beat_label)
        daily_list = beats[beat_idx]
        beat_color = BEAT_COLORS[beat_idx % len(BEAT_COLORS)]

        if show_all:
            m = build_all_beats_map(beats, df)
        else:
            m = build_map(daily_list, daily_list, color=beat_color)
        st_folium(m, width="100%", height=500)

        # Store table
        def _days_since_label(last_date):
            if pd.isna(last_date):
                return "Never"
            return int((TODAY - last_date).days)

        cols = ["rank", "store_name", "barangay", "city", "pool",
                "last_delivered_date", "_never_visited", "score"]
        for col in ["username", "gcu"]:
            if col in daily_list.columns:
                cols.append(col)
        table = daily_list[cols].copy()
        table["Days Since Last Order"] = table["last_delivered_date"].apply(_days_since_label)
        table["Never Visited"] = table["_never_visited"].apply(lambda x: "Yes" if x == 1.0 else "No")
        table["score"] = table["score"].round(2)
        table = table.drop(columns=["last_delivered_date", "_never_visited"])
        rename_map = {"rank": "Rank", "store_name": "Store Name", "barangay": "Barangay",
                      "city": "City", "pool": "Pool", "score": "Score", "username": "Username"}
        table = table.rename(columns=rename_map)
        if "gcu" in table.columns:
            table = table.rename(columns={"gcu": "GCU"})
        display_cols = ["Rank", "Store Name"]
        if "Username" in table.columns:
            display_cols.append("Username")
        if "GCU" in table.columns:
            display_cols.append("GCU")
        display_cols += ["Barangay", "City", "Pool", "Never Visited", "Days Since Last Order", "Score"]
        table = table[[c for c in display_cols if c in table.columns]]
        table = table.sort_values("Rank").reset_index(drop=True)

        st.dataframe(table, height=400, use_container_width=True)

        with st.expander("How stores are scored"):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**New/Revival pool (top 21)**")
                st.markdown("""
| Factor | Weight |
|---|---|
| Never visited before | 35% |
| Recency (lower days = higher score) | 30% |
| Nearby store density (2km radius) | 20% |
| Delivery day coming soon | 15% |

Scope: never-ordered OR 60–730 days churned. Stores with 5+ visits and 0 orders excluded.
All inputs normalized 0–1 before weighting.
""")
            with col2:
                st.markdown("**P30D pool (top 9)**")
                st.markdown("""
| Factor | Weight |
|---|---|
| Order frequency | 40% |
| Delivery day coming soon | 35% |
| Recency (lower days = higher score) | 25% |

Stores with 5+ visits and 0 orders excluded.
All inputs normalized 0–1 before weighting.
""")

        # CSV export
        csv_cols = ["rank", "store_name", "barangay", "city", "gcu", "lat", "long",
                    "last_delivered_date", "no_delivered_orders", "delivery_days", "pool"]
        export_df = daily_list[[c for c in csv_cols if c in daily_list.columns]].sort_values("rank")
        csv_bytes = export_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label=f"Download {selected_beat_label} CSV",
            data=csv_bytes,
            file_name=f"{saved_agent.replace(' ', '_')}_{selected_beat_label.replace(' ', '_')}_daily_list.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
