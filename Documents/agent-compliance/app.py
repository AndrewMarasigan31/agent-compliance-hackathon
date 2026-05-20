import pandas as pd
import streamlit as st
from datetime import datetime, timedelta

CSV_PATH = "[Growth]_Resat_Store_Leads_3.0_v2_2026_05_20.csv"
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
