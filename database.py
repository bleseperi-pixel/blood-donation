"""SQLite database setup, seed data and shared constants."""
import os
import random
import sqlite3
from datetime import date, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "blood_donation.db")

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
SHELF_LIFE_DAYS = 42          # whole blood is usable for about 42 days
DONATION_GAP_DAYS = 90        # minimum gap between donations for donors

SCHEMA = """
CREATE TABLE IF NOT EXISTS donors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    age           INTEGER NOT NULL,
    gender        TEXT NOT NULL,
    blood_group   TEXT NOT NULL,
    phone         TEXT NOT NULL,
    email         TEXT,
    city          TEXT NOT NULL,
    last_donation TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    blood_group  TEXT NOT NULL,
    units        INTEGER NOT NULL,
    collected_on TEXT NOT NULL,
    expiry_date  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usage_date  TEXT NOT NULL,
    blood_group TEXT NOT NULL,
    units_used  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS blood_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    hospital     TEXT NOT NULL,
    patient_name TEXT,
    blood_group  TEXT NOT NULL,
    units        INTEGER NOT NULL,
    urgency      TEXT NOT NULL,
    status       TEXT NOT NULL,
    requested_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def is_eligible_donor(last_donation):
    """A donor can give again once 90 days have passed since the last donation."""
    if not last_donation:
        return True
    return date.fromisoformat(last_donation) <= date.today() - timedelta(days=DONATION_GAP_DAYS)


def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA)
    count = conn.execute("SELECT COUNT(*) FROM usage_history").fetchone()[0]
    if count == 0:
        _seed(conn)
    conn.commit()
    conn.close()


def _seed(conn):
    """Demo data so the AI forecast and dashboard have something to show on first run."""
    random.seed(7)
    today = date.today()

    # 180 days of synthetic daily demand with weekend uplift and a slow upward trend
    base_demand = {"A+": 9, "A-": 2, "B+": 7, "B-": 2, "AB+": 3, "AB-": 1, "O+": 14, "O-": 4}
    usage_rows = []
    for offset in range(180, 0, -1):
        day = today - timedelta(days=offset)
        for group, base in base_demand.items():
            weekend = 1.25 if day.weekday() >= 5 else 1.0
            trend = 1 + (180 - offset) * 0.002
            units = max(0, round(random.gauss(base * weekend * trend, base * 0.25)))
            usage_rows.append((day.isoformat(), group, units))
    conn.executemany(
        "INSERT INTO usage_history (usage_date, blood_group, units_used) VALUES (?, ?, ?)",
        usage_rows,
    )

    # Current stock: a few batches per group, some close to expiry
    inventory_rows = []
    for group in BLOOD_GROUPS:
        for _ in range(random.randint(3, 5)):
            collected = today - timedelta(days=random.randint(0, 40))
            units = max(2, round(base_demand[group] * random.uniform(2.0, 4.0)))
            inventory_rows.append(
                (group, units, collected.isoformat(),
                 (collected + timedelta(days=SHELF_LIFE_DAYS)).isoformat())
            )
    conn.executemany(
        "INSERT INTO inventory (blood_group, units, collected_on, expiry_date) VALUES (?, ?, ?, ?)",
        inventory_rows,
    )

    donors = [
        ("Arun Kumar", 24, "Male", "O+", "9876500001", "arun@example.com", "Nagercoil", None),
        ("Meena Devi", 31, "Female", "A+", "9876500002", "meena@example.com", "Kanyakumari", "2026-05-10"),
        ("Karthik R", 28, "Male", "B+", "9876500003", "karthik@example.com", "Nagercoil", None),
        ("Priya S", 26, "Female", "O-", "9876500004", "priya@example.com", "Thuckalay", "2026-07-02"),
        ("Rahul M", 35, "Male", "AB+", "9876500005", "rahul@example.com", "Chennai", None),
        ("Divya L", 22, "Female", "A-", "9876500006", "divya@example.com", "Nagercoil", None),
        ("Sathish P", 40, "Male", "B-", "9876500007", "sathish@example.com", "Marthandam", "2026-03-18"),
        ("Anitha K", 29, "Female", "O+", "9876500008", "anitha@example.com", "Kanyakumari", None),
        ("Vignesh T", 27, "Male", "AB-", "9876500009", "vignesh@example.com", "Chennai", None),
        ("Lakshmi N", 33, "Female", "O+", "9876500010", "lakshmi@example.com", "Nagercoil", "2026-08-30"),
    ]
    conn.executemany(
        """INSERT INTO donors (name, age, gender, blood_group, phone, email, city, last_donation)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        donors,
    )
