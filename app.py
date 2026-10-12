from datetime import date, timedelta

from flask import Flask, jsonify, render_template, request

import ai_model
from database import (
    BLOOD_GROUPS,
    SHELF_LIFE_DAYS,
    get_connection,
    init_db,
    is_eligible_donor,
)

app = Flask(__name__)
init_db()

URGENCY_LEVELS = ["Routine", "Urgent", "Emergency"]
GENDERS = ["Male", "Female", "Other"]


def error(message, code=400):
    return jsonify({"error": message}), code


def valid_group(group):
    return group in BLOOD_GROUPS


@app.route("/")
def index():
    return render_template("index.html", groups=BLOOD_GROUPS, urgency_levels=URGENCY_LEVELS)


# ---------------------------------------------------------------- dashboard stats
@app.get("/api/stats")
def stats():
    today = date.today()
    soon = today + timedelta(days=7)
    conn = get_connection()
    donors = conn.execute("SELECT COUNT(*) FROM donors").fetchone()[0]
    units = conn.execute(
        "SELECT COALESCE(SUM(units), 0) FROM inventory WHERE expiry_date >= ?",
        (today.isoformat(),),
    ).fetchone()[0]
    expiring = conn.execute(
        "SELECT COALESCE(SUM(units), 0) FROM inventory WHERE expiry_date >= ? AND expiry_date <= ?",
        (today.isoformat(), soon.isoformat()),
    ).fetchone()[0]
    open_requests = conn.execute(
        "SELECT COUNT(*) FROM blood_requests WHERE status = 'Open'"
    ).fetchone()[0]
    conn.close()
    return jsonify({
        "donors": donors,
        "units_available": units,
        "expiring_7d": expiring,
        "open_requests": open_requests,
    })


# ---------------------------------------------------------------- donors
@app.get("/api/donors")
def list_donors():
    group = request.args.get("group", "").strip()
    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM donors WHERE 1 = 1"
    params = []
    if group:
        sql += " AND blood_group = ?"
        params.append(group)
    if q:
        sql += " AND (name LIKE ? OR city LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY id DESC"

    conn = get_connection()
    donors = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    for d in donors:
        d["eligible"] = is_eligible_donor(d["last_donation"])
    return jsonify(donors)


@app.post("/api/donors")
def add_donor():
    d = request.get_json(silent=True) or {}
    name = str(d.get("name", "")).strip()
    phone = str(d.get("phone", "")).strip()
    city = str(d.get("city", "")).strip()
    email = str(d.get("email", "")).strip() or None
    gender = d.get("gender")
    group = d.get("blood_group")
    last_donation = d.get("last_donation") or None

    try:
        age = int(d.get("age"))
    except (TypeError, ValueError):
        return error("Age must be a number.")

    if not name:
        return error("Name is required.")
    if not 18 <= age <= 65:
        return error("Donor age must be between 18 and 65.")
    if gender not in GENDERS:
        return error("Choose a valid gender.")
    if not valid_group(group):
        return error("Choose a valid blood group.")
    if len(phone) < 10:
        return error("Enter a valid phone number.")
    if not city:
        return error("City is required.")

    if last_donation:
        try:
            date.fromisoformat(last_donation)
        except ValueError:
            return error("Last donation date is invalid.")

    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO donors (name, age, gender, blood_group, phone, email, city, last_donation)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, age, gender, group, phone, email, city, last_donation),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return jsonify({"id": new_id, "message": f"{name} registered as a {group} donor."}), 201


@app.delete("/api/donors/<int:donor_id>")
def delete_donor(donor_id):
    conn = get_connection()
    cur = conn.execute("DELETE FROM donors WHERE id = ?", (donor_id,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return error("Donor not found.", 404)
    return jsonify({"message": "Donor removed."})


@app.get("/api/compatible/<group>")
def compatible_donors(group):
    """Who can safely donate to a patient of this blood group (the matching feature)."""
    if not valid_group(group):
        return error("Invalid blood group.")
    donor_groups = ai_model.COMPATIBLE_DONORS[group]
    placeholders = ",".join("?" * len(donor_groups))
    conn = get_connection()
    rows = conn.execute(
        f"SELECT name, blood_group, city, phone, last_donation FROM donors WHERE blood_group IN ({placeholders})",
        donor_groups,
    ).fetchall()
    conn.close()
    donors = [dict(r) | {"eligible": is_eligible_donor(r["last_donation"])} for r in rows]
    donors = [d for d in donors if d["eligible"]]
    return jsonify({"recipient_group": group, "can_receive_from": donor_groups, "donors": donors})


# ---------------------------------------------------------------- inventory
@app.get("/api/inventory")
def inventory():
    state, batches = ai_model.inventory_state()
    return jsonify({"groups": state, "batches": batches, "shelf_life_days": SHELF_LIFE_DAYS})


@app.post("/api/donations")
def record_donation():
    """Record units collected from a donation drive or a donor visit."""
    d = request.get_json(silent=True) or {}
    group = d.get("blood_group")
    donor_id = d.get("donor_id")
    try:
        units = int(d.get("units"))
    except (TypeError, ValueError):
        return error("Units must be a number.")

    if not valid_group(group):
        return error("Choose a valid blood group.")
    if not 1 <= units <= 10:
        return error("Units per donation must be between 1 and 10.")

    today = date.today()
    expiry = today + timedelta(days=SHELF_LIFE_DAYS)
    conn = get_connection()
    conn.execute(
        "INSERT INTO inventory (blood_group, units, collected_on, expiry_date) VALUES (?, ?, ?, ?)",
        (group, units, today.isoformat(), expiry.isoformat()),
    )
    if donor_id:
        conn.execute(
            "UPDATE donors SET last_donation = ? WHERE id = ?",
            (today.isoformat(), donor_id),
        )
    conn.commit()
    conn.close()
    return jsonify({
        "message": f"{units} unit(s) of {group} added. Expires {expiry.isoformat()}.",
    }), 201


# ---------------------------------------------------------------- hospital requests
@app.get("/api/requests")
def list_requests():
    conn = get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM blood_requests ORDER BY id DESC LIMIT 50"
    ).fetchall()]
    conn.close()
    return jsonify(rows)


@app.post("/api/requests")
def create_request():
    """Hospital request. Fulfils from stock using FIFO (earliest expiry first) when possible."""
    d = request.get_json(silent=True) or {}
    hospital = str(d.get("hospital", "")).strip()
    patient = str(d.get("patient_name", "")).strip() or None
    group = d.get("blood_group")
    urgency = d.get("urgency", "Routine")
    try:
        units = int(d.get("units"))
    except (TypeError, ValueError):
        return error("Units must be a number.")

    if not hospital:
        return error("Hospital name is required.")
    if not valid_group(group):
        return error("Choose a valid blood group.")
    if urgency not in URGENCY_LEVELS:
        return error("Choose a valid urgency level.")
    if not 1 <= units <= 20:
        return error("Units must be between 1 and 20.")

    today = date.today().isoformat()
    conn = get_connection()
    batches = conn.execute(
        """SELECT id, units FROM inventory
           WHERE blood_group = ? AND units > 0 AND expiry_date >= ?
           ORDER BY expiry_date""",
        (group, today),
    ).fetchall()

    available = sum(b["units"] for b in batches)
    status = "Open"
    if available >= units:
        remaining = units
        for batch in batches:
            take = min(batch["units"], remaining)
            conn.execute("UPDATE inventory SET units = units - ? WHERE id = ?", (take, batch["id"]))
            remaining -= take
            if remaining == 0:
                break
        status = "Fulfilled"

    conn.execute(
        """INSERT INTO blood_requests (hospital, patient_name, blood_group, units, urgency, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (hospital, patient, group, units, urgency, status),
    )
    # Log the demand so the forecaster learns from it
    conn.execute(
        "INSERT INTO usage_history (usage_date, blood_group, units_used) VALUES (?, ?, ?)",
        (today, group, units),
    )
    conn.commit()
    conn.close()

    if status == "Fulfilled":
        message = f"{units} unit(s) of {group} allocated to {hospital}."
    else:
        message = f"Only {available} unit(s) of {group} in stock. Request logged as Open and donor outreach is recommended."
    return jsonify({"status": status, "message": message}), 201


# ---------------------------------------------------------------- AI endpoints
@app.get("/api/forecast/<group>")
def forecast(group):
    if not valid_group(group):
        return error("Invalid blood group.")
    try:
        return jsonify(ai_model.forecast_group(group))
    except ValueError as exc:
        return error(str(exc), 422)


@app.get("/api/recommendations")
def recommendations():
    return jsonify(ai_model.build_recommendations())


if __name__ == "__main__":
    app.run(debug=True, port=5000)
