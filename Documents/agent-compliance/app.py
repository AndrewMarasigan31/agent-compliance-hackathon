import math
import os
import folium
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
from streamlit_folium import st_folium

CSV_PATH = os.path.join(os.path.dirname(__file__), "store_leads.csv")
TODAY = pd.Timestamp(datetime.now().date())
CLOSED_REASONS = {"Permanently Closed", "Temporarily Closed", "Wala ang may ari"}
EXCLUDED_STATUSES = {"pending", "dispatched"}


@st.cache_data
def load_and_filter() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH)

    df["last_delivered_date"] = pd.to_datetime(df["last_delivered_date"], errors="coerce")
    df["visit_date"] = pd.to_datetime(df["visit_date"], errors="coerce")

    # Exclude pending/dispatched orders
    df = df[~df["latest_order_status"].isin(EXCLUDED_STATUSES)]

    # Exclude closed stores
    closed_mask = df["bakit hindi umorder si customer?"].isin(CLOSED_REASONS)
    df = df[~closed_mask]

    # Exclude stores visited in the last 7 days
    cutoff = TODAY - timedelta(days=7)
    visited_recently = df["visit_date"].notna() & (df["visit_date"] >= cutoff)
    df = df[~visited_recently]

    return df.reset_index(drop=True)


def split_pools(df: pd.DataFrame, gcu: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (new_revival_pool, p30d_pool) for the given GCU."""
    gcu_df = df[df["gcu"] == gcu].copy()

    days_since = (TODAY - gcu_df["last_delivered_date"]).dt.days

    # New/Revival: never ordered (null) OR 60+ days since last delivery
    new_revival = gcu_df[gcu_df["last_delivered_date"].isna() | (days_since >= 60)].copy()

    # P30D: 31–60 days since last delivery
    p30d = gcu_df[(days_since >= 31) & (days_since < 60)].copy()

    return new_revival.reset_index(drop=True), p30d.reset_index(drop=True)


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

    days_since = (TODAY - df["last_delivered_date"]).dt.days.fillna((TODAY - pd.Timestamp("1900-01-01")).days)

    # churn_tier: 2=never ordered, 1=60+ days, 0=approaching 60
    def churn_tier(row):
        if pd.isna(row["last_delivered_date"]):
            return 2
        d = (TODAY - row["last_delivered_date"]).days
        return 1 if d >= 60 else 0
    df["_churn_tier"] = df.apply(churn_tier, axis=1)

    df["_never_visited"] = (df["number_of_visits"].isna() | (df["number_of_visits"] == 0)).astype(float)
    df["_cluster_density"] = _cluster_density(df)
    df["_delivery_proximity"] = df["delivery_days"].apply(
        lambda x: max(0.0, (7 - _days_until_next_delivery(x)) / 6)
    )
    df["_attempt_penalty"] = df["number_of_visits"].fillna(0)

    n_days = _minmax(days_since)
    n_churn = _minmax(df["_churn_tier"].astype(float))
    n_cluster = _minmax(df["_cluster_density"].astype(float))
    n_delivery = _minmax(df["_delivery_proximity"])
    n_penalty = _minmax(df["_attempt_penalty"])

    df["score"] = (
        0.30 * n_days
        + 0.25 * n_churn
        + 0.20 * df["_never_visited"]
        + 0.15 * n_cluster
        + 0.05 * n_delivery
        - 0.05 * n_penalty
    )

    return df.sort_values("score", ascending=False).reset_index(drop=True)


def score_p30d(pool: pd.DataFrame) -> pd.DataFrame:
    df = pool.copy()

    days_since = (TODAY - df["last_delivered_date"]).dt.days.fillna(0)
    df["_delivery_proximity"] = df["delivery_days"].apply(
        lambda x: max(0.0, (7 - _days_until_next_delivery(x)) / 6)
    )

    n_orders = _minmax(df["no_delivered_orders"].fillna(0).astype(float))
    n_days = _minmax(days_since)
    n_delivery = _minmax(df["_delivery_proximity"])
    # recency_of_drop_off: lower days_since = more recent = higher score
    n_recency = 1.0 - n_days

    df["score"] = (
        0.30 * n_orders
        + 0.30 * n_days
        + 0.25 * n_delivery
        + 0.15 * n_recency
    )

    return df.sort_values("score", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Store selector — 70:30 split with cross-pool backfill
# ---------------------------------------------------------------------------

def select_stores(new_revival: pd.DataFrame, p30d: pd.DataFrame, target: int = 30) -> pd.DataFrame:
    """Pick exactly `target` stores using 70:30 split with cross-pool backfill."""
    nr_target = round(target * 0.70)  # 21
    p30_target = target - nr_target   # 9

    nr_pool = new_revival.copy()
    nr_pool["pool"] = "New/Revival"
    p30_pool = p30d.copy()
    p30_pool["pool"] = "P30D"

    nr_pick = nr_pool.head(nr_target)
    p30_pick = p30_pool.head(p30_target)

    nr_shortfall = nr_target - len(nr_pick)
    p30_shortfall = p30_target - len(p30_pick)

    if nr_shortfall > 0:
        extra = p30_pool.iloc[p30_target:p30_target + nr_shortfall]
        p30_pick = pd.concat([p30_pick, extra], ignore_index=True)

    if p30_shortfall > 0:
        extra = nr_pool.iloc[nr_target:nr_target + p30_shortfall]
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

def run_pipeline(df: pd.DataFrame, gcu: str) -> pd.DataFrame:
    new_revival_raw, p30d_raw = split_pools(df, gcu)
    new_revival = score_new_revival(new_revival_raw) if not new_revival_raw.empty else new_revival_raw
    p30d = score_p30d(p30d_raw) if not p30d_raw.empty else p30d_raw
    selected = select_stores(new_revival, p30d)
    all_gcu_stores = df[df["gcu"] == gcu]
    return optimize_route(selected, all_gcu_stores)


def build_map(daily_list: pd.DataFrame, all_gcu_stores: pd.DataFrame) -> folium.Map:
    centroid_lat = all_gcu_stores["lat"].mean()
    centroid_lon = all_gcu_stores["long"].mean()

    m = folium.Map(location=[centroid_lat, centroid_lon], zoom_start=13)

    coords = []
    for _, row in daily_list.iterrows():
        lat, lon = row["lat"], row["long"]
        rank = int(row["rank"])
        coords.append((lat, lon))

        folium.Marker(
            location=[lat, lon],
            icon=folium.DivIcon(
                html=f'<div style="background:#1f77b4;color:white;border-radius:50%;width:24px;height:24px;'
                     f'display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:11px;">'
                     f'{rank}</div>',
                icon_size=(24, 24),
                icon_anchor=(12, 12),
            ),
            tooltip=f"{rank}. {row.get('store_name', '')} — {row.get('barangay', '')}",
            popup=f"<b>{row.get('store_name', '')}</b><br>{row.get('barangay', '')}",
        ).add_to(m)

    if len(coords) > 1:
        folium.PolyLine(coords, color="blue", weight=2, opacity=0.7).add_to(m)

    return m


def main():
    st.set_page_config(page_title="Agent Compliance — Daily Store List", layout="wide")
    st.title("Agent Compliance — Daily Store List")

    df = load_and_filter()

    gcus = sorted(df["gcu"].dropna().unique().tolist())
    selected_gcu = st.selectbox("Select GCU", gcus)

    if st.button("Generate List"):
        daily_list = run_pipeline(df, selected_gcu)
        st.session_state["daily_list"] = daily_list
        st.session_state["selected_gcu"] = selected_gcu
        st.success(f"Generated {len(daily_list)} stores for {selected_gcu}")

    if "daily_list" in st.session_state:
        daily_list = st.session_state["daily_list"]
        gcu = st.session_state["selected_gcu"]
        all_gcu_stores = df[df["gcu"] == gcu]

        m = build_map(daily_list, all_gcu_stores)
        st_folium(m, width="100%", height=500)

        # Store table
        def _days_since_label(last_date):
            if pd.isna(last_date):
                return "Never"
            return int((TODAY - last_date).days)

        table = daily_list[["rank", "store_name", "barangay", "city", "pool", "last_delivered_date"]].copy()
        table["Days Since Last Order"] = table["last_delivered_date"].apply(_days_since_label)
        table = table.drop(columns=["last_delivered_date"])
        table.columns = ["Rank", "Store Name", "Barangay", "City", "Pool", "Days Since Last Order"]
        table = table.sort_values("Rank").reset_index(drop=True)

        st.dataframe(table, height=400, use_container_width=True)

        # CSV export
        csv_cols = ["rank", "store_name", "barangay", "city", "gcu", "lat", "long",
                    "last_delivered_date", "no_delivered_orders", "delivery_days", "pool"]
        export_df = daily_list[[c for c in csv_cols if c in daily_list.columns]].sort_values("rank")
        csv_bytes = export_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download CSV",
            data=csv_bytes,
            file_name=f"{gcu}_daily_list.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
