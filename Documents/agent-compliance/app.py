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
BEAT_TARGET = 50  # stores per beat (matches the deployed app's beat size)
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


RECENT_MIN_DAYS = 15
RECENT_MAX_DAYS = 29
RECENT_BEAT_CAP = 0.40  # 15-29d "recent" stores may fill at most this share of a beat


def _apply_hard_exclusions(df: pd.DataFrame, cutoff_year: int | None = None, include_ncmb: bool = False, include_recent: bool = False) -> pd.DataFrame:
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

    # Recent early-slip window: stores that last ordered 15-29 days ago (this version only).
    # Prefer the explicit bucket label; fall back to the date window when it's absent.
    recent_mask = pd.Series(False, index=df.index)
    if include_recent:
        recent_mask = (days_since >= RECENT_MIN_DAYS) & (days_since <= RECENT_MAX_DAYS)
        if "bucket" in df.columns:
            recent_mask = recent_mask | (df["bucket"] == "15-29 Days No Delivery (NKA)")

    eligible = (
        df["last_delivered_date"].isna()                          # never ordered
        | nr_mask                                                 # New/Revival: 60d+ (year-filtered)
        | ((days_since >= 31) & (days_since < 60))                # P30D: 31–60 days
        | ncmb_mask                                               # Non Current Month Buyers (if enabled)
        | recent_mask                                             # Recent 15–29d (this version only)
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


def score_by_date(pool: pd.DataFrame) -> pd.DataFrame:
    """Rank purely by last delivery date, tiered: NCMB > P30D > Churned > never-ordered.

    Within each tier, most recently delivered stores rank first. Falls back to
    day-thresholds when the `bucket` column is absent.
    """
    df = pool.copy()
    days_since = (TODAY - df["last_delivered_date"]).dt.days

    if "bucket" in df.columns:
        tier = pd.Series(0, index=df.index)
        tier[df["bucket"] == "Churned (60+ Days)"] = 1
        tier[df["bucket"] == "P30D No Delivery (NKA)"] = 2
        tier[df["bucket"] == "Non Current Month Buyer"] = 3
        pool_label = pd.Series("New Store (No Order)", index=df.index)
        pool_label[df["bucket"] == "Churned (60+ Days)"] = "Churned"
        pool_label[df["bucket"] == "P30D No Delivery (NKA)"] = "P30D"
        pool_label[df["bucket"] == "Non Current Month Buyer"] = "Non Month Buyer"
        pool_label[df["bucket"] == "Resat Visited"] = "New Store (No Order)"
        pool_label[df["last_delivered_date"].isna()] = "New Store (No Order)"
        # Recent 15-29d stores (explicit bucket, or date window when unlabeled) → opportunistic segment
        recent_win = (
            (df["bucket"] == "15-29 Days No Delivery (NKA)")
            | ((days_since >= RECENT_MIN_DAYS) & (days_since <= RECENT_MAX_DAYS))
        )
        not_warm = ~df["bucket"].isin(["Non Current Month Buyer", "P30D No Delivery (NKA)", "Churned (60+ Days)"])
        pool_label[recent_win & not_warm] = "Recent (15-29d)"
        df["pool"] = pool_label
    else:
        tier = pd.Series(0, index=df.index)                       # never-ordered
        tier[days_since >= 60] = 1                                # churned
        tier[(days_since >= 31) & (days_since < 60)] = 2          # P30D
        df["pool"] = "Churned"
        df.loc[(days_since >= 31) & (days_since < 60), "pool"] = "P30D"
        df.loc[df["last_delivered_date"].isna(), "pool"] = "New Store (No Order)"
        df.loc[(days_since >= RECENT_MIN_DAYS) & (days_since <= RECENT_MAX_DAYS), "pool"] = "Recent (15-29d)"

    # within-tier recency: smaller days_since = higher. never-ordered sorts last.
    recency = 1.0 - _minmax(days_since.fillna(days_since.max() if days_since.notna().any() else 0))
    df["score"] = tier + 0.999 * recency

    return df.sort_values("score", ascending=False).reset_index(drop=True)


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
# Store selector — legacy 70:30 split (kept for reference; unused by V2 pipeline)
# ---------------------------------------------------------------------------

def select_stores(churned: pd.DataFrame, p30d: pd.DataFrame, never_ordered: pd.DataFrame, target: int = 50, churned_ratio: float = 0.70) -> pd.DataFrame:
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

    if len(churned_pick) < churned_target:
        extra = p30d_pool.iloc[p30d_target:p30d_target + (churned_target - len(churned_pick))]
        p30d_pick = pd.concat([p30d_pick, extra], ignore_index=True)

    if len(p30d_pick) < p30d_target:
        extra = churned_pool.iloc[churned_target:churned_target + (p30d_target - len(p30d_pick))]
        churned_pick = pd.concat([churned_pick, extra], ignore_index=True)

    combined = pd.concat([churned_pick, p30d_pick], ignore_index=True)
    combined = combined.drop_duplicates(subset=["store_name", "lat", "long"])

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


def route_distance_km(beat: pd.DataFrame) -> float:
    """Total travel distance along a routed beat (sum of consecutive hops)."""
    if len(beat) < 2:
        return 0.0
    return sum(
        _haversine_km(beat.iloc[i]["lat"], beat.iloc[i]["long"], beat.iloc[i + 1]["lat"], beat.iloc[i + 1]["long"])
        for i in range(len(beat) - 1)
    )


# ---------------------------------------------------------------------------
# Balanced hybrid beats — geographic seeds + parallel round-robin fill
# ---------------------------------------------------------------------------

def _hybrid_beats(ranked: pd.DataFrame, alpha: float = 0.65, cap: int = BEAT_TARGET) -> list[pd.DataFrame]:
    """Geographic seeds + parallel round-robin fill, blending urgency and nearness.

    alpha = weight on lead urgency, (1-alpha) = weight on geographic nearness.
    Beats are anchored to KMeans geographic centroids (so each beat owns a region),
    then filled one store per beat per round — each pick maximizes
    alpha*norm_score + (1-alpha)*(1 - norm_distance_to_its_anchor). Filling all beats
    in parallel keeps urgent leads distributed across regions instead of hoarded in beat 1.
    """
    df = ranked.copy().reset_index(drop=True)
    n = len(df)
    ns = _minmax(df["score"]).to_numpy()
    lats = df["lat"].to_numpy()
    lons = df["long"].to_numpy()
    is_recent = (df["pool"] == "Recent (15-29d)").to_numpy() if "pool" in df.columns else np.zeros(n, dtype=bool)
    recent_cap = int(RECENT_BEAT_CAP * cap)  # max "Recent (15-29d)" stores per beat
    K = max(1, math.ceil(n / cap))

    if K == 1:
        anchors = np.array([[lats.mean(), lons.mean()]])
    else:
        km = KMeans(n_clusters=K, random_state=42, n_init=10).fit(np.column_stack([lats, lons]))
        anchors = km.cluster_centers_

    # Precompute K×n anchor→store distances once (vectorized haversine), then the
    # round-robin fill just indexes into it — O(n·K) instead of O(n²) pure-Python.
    alat = np.radians(anchors[:, 0])[:, None]
    alon = np.radians(anchors[:, 1])[:, None]
    slat = np.radians(lats)[None, :]
    slon = np.radians(lons)[None, :]
    dlat = slat - alat
    dlon = slon - alon
    a = np.sin(dlat / 2) ** 2 + np.cos(alat) * np.cos(slat) * np.sin(dlon / 2) ** 2
    dist = 6371.0 * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))  # shape (K, n)
    dmax = dist.max(axis=1, keepdims=True)
    dmax[dmax == 0] = 1.0
    gain = alpha * ns[None, :] + (1 - alpha) * (1 - dist / dmax)  # shape (K, n)

    unassigned = np.ones(n, dtype=bool)
    beats_idx = [[] for _ in range(K)]
    counts = [0] * K
    recent_counts = [0] * K
    remaining = n
    rnd = 0
    while remaining > 0:
        progressed = False
        # rotate which beat picks first each round to avoid a fixed beat-0 advantage
        for bi in [(rnd + k) % K for k in range(K)]:
            if counts[bi] >= cap or remaining == 0:
                continue
            g = np.where(unassigned, gain[bi], -np.inf)
            # enforce the 15-29d cap: once a beat is full of recent stores, block more
            if recent_counts[bi] >= recent_cap:
                g = np.where(is_recent, -np.inf, g)
            if not np.isfinite(g).any():
                continue  # only recent stores left for a capped beat — leave them
            pick = int(np.argmax(g))
            beats_idx[bi].append(pick)
            unassigned[pick] = False
            counts[bi] += 1
            if is_recent[pick]:
                recent_counts[bi] += 1
            remaining -= 1
            progressed = True
        if not progressed:
            break
        rnd += 1

    beats = []
    for idx in beats_idx:
        if not idx:
            continue
        beat = df.iloc[idx].reset_index(drop=True)
        beats.append(optimize_route(beat, beat))
    return beats


# ---------------------------------------------------------------------------
# Streamlit app
# ---------------------------------------------------------------------------

def run_global_pipeline(df: pd.DataFrame, cutoff_year: int | None = None, alpha: float = 0.65, include_recent: bool = False) -> list[pd.DataFrame]:
    """Rank all eligible stores by last delivery date (NCMB → P30D → Churned, NCMB
    always included), then group them into balanced geographic beats that blend
    lead urgency and travel (`alpha` = urgency weight). When `include_recent` is on,
    stores that last ordered 15-29 days ago are added as an opportunistic segment,
    capped at 40% of each beat."""
    all_stores = _apply_hard_exclusions(df.copy(), cutoff_year=cutoff_year, include_ncmb=True, include_recent=include_recent)
    if all_stores.empty:
        return []
    ranked = score_by_date(all_stores)
    return _hybrid_beats(ranked, alpha=alpha)


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

    urgency_pct = st.slider(
        "Urgency vs travel balance",
        min_value=0, max_value=100, value=65, step=5,
        format="%d%% urgency",
        help="Weight on lead urgency vs geographic nearness. 65% urgency / 35% geography by default. "
             "Higher = chase hot leads harder; lower = tighter regional routes. Beats stay geographically anchored either way.",
    )
    alpha = urgency_pct / 100

    include_recent = st.checkbox(
        "[LP AND PN AGENTS] Include recently-slipping stores (last ordered 15–29 days ago)",
        value=False,
        help="For LP and PN agents. Adds stores that ordered 15–29 days ago as an opportunistic 'if nearby, visit' segment. "
             "Capped at 40% of any route so they never crowd out lapsed customers.",
    )
    if include_recent:
        st.caption("For this option, upload data from this query only: https://data.growsari.com/queries/49599")

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
        "Churned cutoff year (last delivered ≥ Jan 1 of selected year; never-ordered always included)",
        year_options,
        index=default_idx,
    )
    cutoff_year = None if selected_year_str == "All time" else int(selected_year_str)

    st.caption("Stores rank by last delivery date (NCMB → P30D → Churned, always including Non-Current Month Buyers), then split into balanced geographic beats using the urgency/travel weight above.")

    if st.button("Generate All Beats"):
        beats = run_global_pipeline(df, cutoff_year=cutoff_year, alpha=alpha, include_recent=include_recent)
        st.session_state["beats"] = beats
        st.session_state["agent_name"] = agent_name.strip() or "Agent"
        st.session_state["cutoff_year"] = cutoff_year
        st.session_state.pop("_map", None)
        st.session_state.pop("_map_key", None)
        recent_note = " + 15–29d recent stores (≤40%/beat)" if include_recent else ""
        st.success(f"Generated {len(beats)} beat(s) — ranked by last delivery date (NCMB → P30D → Churned), {urgency_pct}% urgency / {100 - urgency_pct}% geography{recent_note}")

    if "beats" in st.session_state:
        beats = st.session_state["beats"]
        saved_agent = st.session_state.get("agent_name", "Agent")

        st.subheader(f"{saved_agent} — {len(beats)} Beat(s)")

        # Travel vs lead-quality tradeoff summary
        summary = pd.DataFrame([
            {
                "Beat": f"Beat {i+1}",
                "Stores": len(b),
                "Travel (km)": round(route_distance_km(b), 1),
                "Avg Score": round(b["score"].mean(), 3) if "score" in b.columns else None,
                "Churned": int((b["pool"] == "Churned").sum()) if "pool" in b.columns else 0,
                "P30D": int((b["pool"] == "P30D").sum()) if "pool" in b.columns else 0,
                "Active Stores (15-29d)": int((b["pool"] == "Recent (15-29d)").sum()) if "pool" in b.columns else 0,
            }
            for i, b in enumerate(beats)
        ])
        if not summary.empty:
            tot_km = summary["Travel (km)"].sum()
            avg_score = (summary["Avg Score"] * summary["Stores"]).sum() / max(summary["Stores"].sum(), 1)
            c1, c2 = st.columns(2)
            c1.metric("Total travel across beats", f"{tot_km:.0f} km")
            c2.metric("Weighted avg lead score", f"{avg_score:.3f}")
            st.dataframe(summary, use_container_width=True, hide_index=True)

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
        table["Last Delivery Date"] = table["last_delivered_date"].dt.strftime("%Y-%m-%d").fillna("Never")
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
        display_cols += ["Barangay", "City", "Pool", "Last Delivery Date", "Days Since Last Order",
                         "# Visits", "Last Visit Date", "Rejection Reason", "Score"]
        table = table[[c for c in display_cols if c in table.columns]]
        table = table.sort_values(["Beat", "Rank"]).reset_index(drop=True)

        st.dataframe(table, height=400, use_container_width=True)

        with st.expander("How stores are ranked"):
            st.markdown("""
Stores are ranked purely by **last delivery date**, in priority tiers:

| Tier | Bucket | Meaning |
|---|---|---|
| 1 (top) | Non-Current Month Buyer | ordered last month, not this month — warmest |
| 2 | P30D | 31–60 days since last delivery |
| 3 | Churned | 60+ days since last delivery |
| bottom | New Store (No Order) | Resat-visited or never-delivered stores — sort last |

Within each tier, the **most recently delivered** store ranks first. Non-Current Month Buyers are always included. Stores with 5+ visits and 0 orders, current-month buyers, closed stores, and stores visited in the last 7 days are excluded.

**Beats** are then built by anchoring to geographic regions and filling each beat in parallel with a blend of lead urgency and nearness (set by the *Urgency vs travel balance* slider), so urgent leads spread across beats instead of piling into day one.
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
