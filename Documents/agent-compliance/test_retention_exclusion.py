"""Check retention usernames are excluded from routing, case/encoding-insensitively.
Run: python3 test_retention_exclusion.py
"""
import pandas as pd
from app import _norm_username, _apply_hard_exclusions, RETENTION_USERNAMES, TODAY


def test_norm_username():
    got = _norm_username(pd.Series(["Fa220818u59849", "��ss240214u60600", " io591137u323 "]))
    assert list(got) == ["fa220818u59849", "ss240214u60600", "io591137u323"], list(got)


def test_retention_rows_dropped():
    keep, drop = "notaretentionstore1", sorted(RETENTION_USERNAMES)[0]
    df = pd.DataFrame([
        # churned 200d ago, never visited -> eligible unless retention-listed
        {"username": u, "store_name": u, "bucket": "Churned (60+ Days)",
         "last_delivered_date": TODAY - pd.Timedelta(days=200),
         "number_of_visits": 0, "no_delivered_orders": 1}
        for u in (keep, drop, drop.upper())
    ])
    out = _apply_hard_exclusions(df)
    assert list(out["username"]) == [keep], list(out["username"])


if __name__ == "__main__":
    test_norm_username()
    test_retention_rows_dropped()
    print(f"PASS ({len(RETENTION_USERNAMES)} retention usernames loaded)")
