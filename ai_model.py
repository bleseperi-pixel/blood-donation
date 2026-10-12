"""
AI engine that follows the pipeline in the diagram:

Historical data -> Data preprocessing -> AI demand forecasting (24h / 7d / 30d)
-> Smart inventory engine (expiry, shortage, excess) -> Optimization engine
-> Recommendations (replenishment, donor requests, redistribution) -> Dashboard
"""
import math
from datetime import date, timedelta

import numpy as np
from sklearn.ensemble import RandomForestRegressor

from database import BLOOD_GROUPS, get_connection, is_eligible_donor

# Recipient group -> donor groups that can safely give to it (red-cell compatibility)
COMPATIBLE_DONORS = {
    "A+": ["A+", "A-", "O+", "O-"],
    "A-": ["A-", "O-"],
    "B+": ["B+", "B-", "O+", "O-"],
    "B-": ["B-", "O-"],
    "AB+": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"],
    "AB-": ["A-", "B-", "AB-", "O-"],
    "O+": ["O+", "O-"],
    "O-": ["O-"],
}

HORIZON_DAYS = 30   # forecast horizon
WINDOW = 14         # how many past days the model looks at
SAFETY_BUFFER = 1.10  # hold 10% above forecast demand
EXCESS_BUFFER = 1.20  # stock above 120% of 30-day demand counts as excess


# ---------------------------------------------------------------- preprocessing
def load_daily_usage(group):
    """Build a continuous daily series for one blood group (missing days become 0)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT usage_date, SUM(units_used) AS units FROM usage_history
           WHERE blood_group = ? GROUP BY usage_date ORDER BY usage_date""",
        (group,),
    ).fetchall()
    conn.close()
    if not rows:
        return [], []

    lookup = {r["usage_date"]: float(r["units"]) for r in rows}
    start = date.fromisoformat(rows[0]["usage_date"])
    end = date.fromisoformat(rows[-1]["usage_date"])

    dates, values = [], []
    day = start
    while day <= end:
        dates.append(day)
        values.append(lookup.get(day.isoformat(), 0.0))
        day += timedelta(days=1)
    return dates, values


def _features(history, day):
    """Feature vector built only from past values, so training and forecasting match."""
    recent = history[-WINDOW:]
    return [
        history[-1],
        history[-7],
        float(np.mean(history[-7:])),
        float(np.mean(recent)),
        float(np.std(recent)),
        day.weekday(),
        day.month,
    ]


# ---------------------------------------------------------------- forecasting
def _train(group):
    dates, values = load_daily_usage(group)
    if len(values) <= WINDOW + 14:
        raise ValueError(f"Not enough history to forecast {group}.")

    X, y = [], []
    for i in range(WINDOW, len(values)):
        X.append(_features(values[:i], dates[i]))
        y.append(values[i])
    X, y = np.array(X), np.array(y)

    # Honest accuracy check: hold out the last 14 days
    split = len(X) - 14
    check = RandomForestRegressor(n_estimators=150, min_samples_leaf=2, random_state=42)
    check.fit(X[:split], y[:split])
    mae = float(np.mean(np.abs(check.predict(X[split:]) - y[split:])))

    model = RandomForestRegressor(n_estimators=150, min_samples_leaf=2, random_state=42)
    model.fit(X, y)
    return model, values, dates[-1], round(mae, 2)


def forecast_group(group):
    """Recursive 30-day forecast; returns 24h, 7-day and 30-day totals."""
    model, values, last_date, mae = _train(group)

    history = list(values)
    daily = []
    for step in range(HORIZON_DAYS):
        day = last_date + timedelta(days=step + 1)
        pred = max(0.0, float(model.predict(np.array([_features(history, day)]))[0]))
        daily.append({"date": day.isoformat(), "units": round(pred, 1)})
        history.append(pred)

    recent_history = [
        {"date": d.isoformat(), "units": v}
        for d, v in zip(
            [last_date - timedelta(days=i) for i in range(29, -1, -1)],
            values[-30:],
        )
    ]

    return {
        "blood_group": group,
        "next_24h": daily[0]["units"],
        "next_7d": round(sum(d["units"] for d in daily[:7]), 1),
        "next_30d": round(sum(d["units"] for d in daily), 1),
        "daily": daily,
        "history": recent_history,
        "model_mae_units_per_day": mae,
        "model": "RandomForest with 14-day lag window",
    }


# ---------------------------------------------------------------- inventory engine
def inventory_state():
    today = date.today()
    soon = today + timedelta(days=7)

    conn = get_connection()
    batches = [
        dict(r) for r in conn.execute(
            """SELECT id, blood_group, units, collected_on, expiry_date FROM inventory
               WHERE units > 0 AND expiry_date >= ? ORDER BY expiry_date""",
            (today.isoformat(),),
        ).fetchall()
    ]
    eligible_rows = conn.execute(
        "SELECT blood_group, last_donation FROM donors"
    ).fetchall()
    conn.close()

    eligible = {g: 0 for g in BLOOD_GROUPS}
    for r in eligible_rows:
        if is_eligible_donor(r["last_donation"]):
            eligible[r["blood_group"]] += 1

    state = {g: {"total": 0, "expiring_7d": 0, "eligible_donors": eligible[g], "batches": 0}
             for g in BLOOD_GROUPS}
    for b in batches:
        s = state[b["blood_group"]]
        s["total"] += b["units"]
        s["batches"] += 1
        if date.fromisoformat(b["expiry_date"]) <= soon:
            s["expiring_7d"] += b["units"]
    return state, batches


# ---------------------------------------------------------------- optimization engine
def build_recommendations():
    state, _ = inventory_state()
    groups = []

    for g in BLOOD_GROUPS:
        fc = forecast_group(g)
        s = state[g]
        total = s["total"]
        expiring = s["expiring_7d"]
        non_expiring = total - expiring
        demand_7d = fc["next_7d"]
        demand_30d = fc["next_30d"]

        # Demand the 7-day stock cannot cover
        shortage_units = max(0, math.ceil(demand_7d - total))

        # Expiring units that forecast demand will not consume (FIFO: non-expiring used first)
        demand_left_for_expiring = max(0, demand_7d - non_expiring)
        expiry_risk_units = max(0, expiring - math.ceil(demand_left_for_expiring))

        excess_units = max(0, total - math.ceil(demand_30d * EXCESS_BUFFER))
        order_units = max(0, math.ceil(demand_30d * SAFETY_BUFFER) - total)

        if shortage_units > 0:
            status = "Shortage"
        elif excess_units > 0:
            status = "Excess"
        else:
            status = "Balanced"

        groups.append({
            "blood_group": g,
            "status": status,
            "stock_units": total,
            "expiring_7d_units": expiring,
            "forecast_24h": fc["next_24h"],
            "forecast_7d": fc["next_7d"],
            "forecast_30d": fc["next_30d"],
            "shortage_units": shortage_units,
            "expiry_risk_units": expiry_risk_units,
            "excess_units": excess_units,
            "order_units": order_units,
            "eligible_donors": s["eligible_donors"],
            "model_mae": fc["model_mae_units_per_day"],
        })

    by_group = {g["blood_group"]: g for g in groups}

    replenishment = [
        {"blood_group": g["blood_group"], "units": g["order_units"],
         "reason": f"Forecast 30-day demand is {g['forecast_30d']} units; stock is {g['stock_units']}."}
        for g in groups if g["order_units"] > 0
    ]

    donor_requests = []
    for g in groups:
        needed = max(g["shortage_units"], g["order_units"])
        if needed <= 0:
            continue
        donor_groups = COMPATIBLE_DONORS[g["blood_group"]]
        pool = sum(by_group[d]["eligible_donors"] for d in donor_groups)
        donor_requests.append({
            "blood_group": g["blood_group"],
            "units": needed,
            "donor_groups": donor_groups,
            "eligible_donor_pool": pool,
            "urgent": g["shortage_units"] > 0,
        })

    # Redistribution: move surplus from a compatible group to a short group
    remaining_excess = {g["blood_group"]: g["excess_units"] for g in groups}
    redistribution = []
    for g in groups:
        need = g["shortage_units"]
        if need <= 0:
            continue
        for donor_group in COMPATIBLE_DONORS[g["blood_group"]]:
            if need <= 0:
                break
            if donor_group == g["blood_group"] or remaining_excess[donor_group] <= 0:
                continue
            move = min(need, remaining_excess[donor_group])
            redistribution.append({
                "from_group": donor_group,
                "to_group": g["blood_group"],
                "units": move,
            })
            remaining_excess[donor_group] -= move
            need -= move

    expiry_alerts = [
        {"blood_group": g["blood_group"], "units": g["expiry_risk_units"]}
        for g in groups if g["expiry_risk_units"] > 0
    ]

    return {
        "generated_on": date.today().isoformat(),
        "groups": groups,
        "replenishment": replenishment,
        "donor_requests": donor_requests,
        "redistribution": redistribution,
        "expiry_alerts": expiry_alerts,
    }
