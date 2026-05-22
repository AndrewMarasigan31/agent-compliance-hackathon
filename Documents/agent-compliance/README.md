# Agent Compliance — Daily Store Beat Planner

A Streamlit app that generates daily store visit lists (beats) for field agents, replacing manual store selection with a scored, routed, and geographically clustered daily plan.

---

## What It Does

Field agents currently choose which stores to visit within their territory. This leads to inconsistent productivity and no audit trail. This app solves that by pre-computing exactly which stores each agent should visit each day — ranked by business priority and optimally routed.

### Key Features

- **Upload any store leads CSV** — no hardcoded data, works with any fresh export
- **Automatic beat generation** — clusters all eligible stores geographically using K-means, each beat has a minimum of 30 stores
- **Score-based store ranking** — stores are ranked within each beat by urgency, recency, and route efficiency
- **Optimized routes** — nearest-neighbor routing from the cluster centroid minimizes agent travel
- **Interactive map** — numbered pins with route lines per beat, or all beats at once with distinct colors
- **Configurable New/Revival vs P30D ratio** — slider to adjust how many churned vs recently-lapsed stores appear per beat
- **Agent name labeling** — tag each session with an agent name, reflected in the CSV export filename
- **CSV export per beat** — one-click download per beat to simulate a CASA push

---

## Store Eligibility & Filtering

Before scoring, the following stores are excluded:

| Rule | Reason |
|---|---|
| `latest_order_status` = `pending` or `dispatched` | Active order in flight |
| Closed stores (`Permanently Closed`, `Temporarily Closed`, `Wala ang may ari`) | Not visitable |
| Visited within the last 7 days | Too recent |
| 5+ visits with zero delivered orders | Repeated no-conversion, excluded permanently |

---

## Store Pools

Eligible stores are split into two pools based on `last_delivered_date`:

| Pool | Criteria |
|---|---|
| **New/Revival** | Never ordered (null `last_delivered_date`) OR churned 60–730 days ago |
| **P30D** | Ordered within the last 31–60 days but not recently |

---

## Scoring

All inputs are min-max normalized to [0, 1] within their pool before weighting.

### New/Revival Formula
| Factor | Weight | Notes |
|---|---|---|
| Never visited bonus | 35% | 1 if `number_of_visits == 0`, else 0 |
| Days since last order (flipped) | 30% | Lower days = higher score (recently churned = warmer lead) |
| Geographic cluster density | 20% | Count of stores within 2km radius |
| Delivery proximity | 15% | Score = `max(0, (7 - days_until_next_delivery) / 6)` |

### P30D Formula
| Factor | Weight | Notes |
|---|---|---|
| Order frequency | 40% | `no_delivered_orders` — weekly orderer ranked higher |
| Delivery proximity | 35% | Visit when delivery day is near |
| Days since last order (flipped) | 25% | Recently lapsed = warmer lead |

---

## Beat Generation

1. All eligible stores are clustered geographically using **K-means** (`K = floor(total / 30)`)
2. Clusters smaller than 30 stores are merged into their nearest geographic neighbor
3. Within each cluster, stores are split into New/Revival and P30D pools, scored independently, then selected using the configurable ratio
4. The selected stores are **routed via nearest-neighbor** from the cluster centroid
5. Each beat is ranked 1–30 (or 1–N if the cluster is larger)

---

## Stack

| Component | Technology |
|---|---|
| App framework | Streamlit |
| Data | Pandas |
| Clustering | scikit-learn (KMeans) |
| Maps | Folium + streamlit-folium |
| Routing | Custom nearest-neighbor (haversine distance) |
| Deployment | Streamlit Cloud |

---

## Running Locally

```bash
pip install -r requirements.txt
streamlit run Documents/agent-compliance/app.py
```

---

## Data Format

The app expects a CSV with these columns:

| Column | Used for |
|---|---|
| `lat`, `long` | Geographic clustering + routing |
| `last_delivered_date` | Pool split + urgency scoring |
| `visit_date` | 7-day recency filter |
| `latest_order_status` | Exclude pending/dispatched |
| `bakit hindi umorder si customer?` | Exclude closed stores |
| `number_of_visits` | Never-visited bonus + hard exclusion |
| `no_delivered_orders` | P30D order frequency score |
| `delivery_days` | Delivery proximity score |
| `gcu` | Display in table |
| `username` | Display in table |
| `store_name`, `barangay`, `city` | Display in table and map |

---

## Phase 2 (Out of Scope)

- GPS visit validation in CASA
- Live compliance tracking per agent
- Real-time supervisor dashboard
- SKU-level recommendations (requires order line items)
- CASA live push integration
