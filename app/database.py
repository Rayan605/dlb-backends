"""
Database module — Liste Party
"""
import sqlite3
import os
from contextlib import contextmanager
from typing import Generator

# Azure App Service : /home est le seul volume persistant entre redémarrages
DB_PATH = os.environ.get("DB_PATH", "/home/soiree.db")


def dict_factory(cursor, row):
    fields = [column[0] for column in cursor.description]
    return {key: value for key, value in zip(fields, row)}


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        cur = conn.cursor()

        # USERS — champ 'social' remplace instagram + snapchat
        # is_scanner = peut scanner les QR à l'entrée
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            first_name  TEXT NOT NULL,
            last_name   TEXT NOT NULL,
            gender      TEXT NOT NULL CHECK(gender IN ('M', 'F', 'X')),
            social      TEXT NOT NULL DEFAULT '',
            is_admin    INTEGER DEFAULT 0,
            is_scanner  INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # EVENTS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT,
            date        TEXT NOT NULL,
            city        TEXT NOT NULL,
            department  TEXT NOT NULL,
            max_people  INTEGER NOT NULL,
            is_past     INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # EVENT IMAGES — media_type: 'image' | 'video'
        cur.execute("""
        CREATE TABLE IF NOT EXISTS event_images (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    INTEGER NOT NULL,
            filename    TEXT NOT NULL,
            position    INTEGER DEFAULT 0,
            is_recap    INTEGER DEFAULT 0,
            media_type  TEXT NOT NULL DEFAULT 'image',
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
        )
        """)

        # FORMULAS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS formulas (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            description    TEXT,
            price_cents    INTEGER NOT NULL DEFAULT 0,
            position       INTEGER DEFAULT 0,
            max_guests     INTEGER DEFAULT 0,
            is_girls_only  INTEGER DEFAULT 0
        )
        """)

        # RESERVATIONS — quantity vaut toujours 1
        cur.execute("""
        CREATE TABLE IF NOT EXISTS reservations (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id              INTEGER NOT NULL,
            event_id             INTEGER NOT NULL,
            formula_id           INTEGER NOT NULL,
            quantity             INTEGER NOT NULL DEFAULT 1,
            status               TEXT NOT NULL DEFAULT 'pending',
            stripe_session_id    TEXT,
            stripe_payment_intent TEXT,
            amount_paid_cents    INTEGER,
            qr_token             TEXT UNIQUE,
            invite_token         TEXT UNIQUE,
            scanned_at           TEXT,
            created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
            paid_at              TEXT,
            FOREIGN KEY (user_id)    REFERENCES users(id),
            FOREIGN KEY (event_id)   REFERENCES events(id),
            FOREIGN KEY (formula_id) REFERENCES formulas(id)
        )
        """)

        # GUEST RESERVATIONS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS guest_reservations (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id INTEGER NOT NULL,
            guest_user_id  INTEGER NOT NULL,
            event_id       INTEGER NOT NULL,
            qr_token       TEXT UNIQUE NOT NULL,
            status         TEXT NOT NULL DEFAULT 'active',
            scanned_at     TEXT,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (reservation_id) REFERENCES reservations(id),
            FOREIGN KEY (guest_user_id)  REFERENCES users(id),
            FOREIGN KEY (event_id)       REFERENCES events(id)
        )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_resa_event  ON reservations(event_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_resa_user   ON reservations(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_resa_status ON reservations(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_resa_invite ON reservations(invite_token)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_resa_qr     ON reservations(qr_token)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_guest_resa  ON guest_reservations(reservation_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_guest_qr    ON guest_reservations(qr_token)")

        _migrate(cur)

        # Seed formules réelles
        cur.execute("SELECT COUNT(*) as c FROM formulas")
        if cur.fetchone()["c"] == 0:
            formulas = [
                ("Entrée + Boisson",    "Entrée libre + une boisson offerte. T'arrives, t'es servi, c'est parti.",           5000,  1, 0),
                ("Entrée + Bouteille",  "Entrée libre + une bouteille complète. Tu fais le niveau toi-même.",                10000, 2, 1),
                ("Petite Bouteille + B","Une petite bouteille + une boisson. Le bon équilibre pour bien gérer la soirée.",   15000, 3, 2),
                ("Jack Daniel's + B",   "Une Jack Daniel's + une boisson. Pour ceux qui savent ce qu'ils veulent.",          25000, 4, 4),
                ("Champagne + B",       "Champagne + une boisson. C'est le niveau au-dessus, t'es dans le très bon là.",    45000, 5, 9),
            ]
            cur.executemany(
                "INSERT INTO formulas (name, description, price_cents, position, max_guests) VALUES (?,?,?,?,?)",
                formulas
            )


def _migrate(cur):
    """Migration silencieuse pour DB existantes."""
    # users : social remplace instagram/snapchat, ajout is_scanner
    u_cols = {r["name"] for r in cur.execute("PRAGMA table_info(users)").fetchall()}
    if "social" not in u_cols:
        cur.execute("ALTER TABLE users ADD COLUMN social TEXT NOT NULL DEFAULT ''")
    if "is_scanner" not in u_cols:
        cur.execute("ALTER TABLE users ADD COLUMN is_scanner INTEGER DEFAULT 0")
    # Backfill social depuis insta/snap si présents
    if "instagram" in u_cols or "snapchat" in u_cols:
        try:
            cur.execute("""
                UPDATE users SET social = COALESCE(instagram, snapchat, '')
                WHERE social = '' AND (instagram IS NOT NULL OR snapchat IS NOT NULL)
            """)
        except Exception:
            pass

    # reservations
    r_cols = {r["name"] for r in cur.execute("PRAGMA table_info(reservations)").fetchall()}
    for col, dflt in [("qr_token", "TEXT"), ("invite_token", "TEXT"), ("scanned_at", "TEXT")]:
        if col not in r_cols:
            cur.execute(f"ALTER TABLE reservations ADD COLUMN {col} {dflt}")

    # events
    ev_cols = {r["name"] for r in cur.execute("PRAGMA table_info(events)").fetchall()}
    if "is_past" not in ev_cols:
        cur.execute("ALTER TABLE events ADD COLUMN is_past INTEGER DEFAULT 0")

    # event_images
    img_cols = {r["name"] for r in cur.execute("PRAGMA table_info(event_images)").fetchall()}
    if "is_recap" not in img_cols:
        cur.execute("ALTER TABLE event_images ADD COLUMN is_recap INTEGER DEFAULT 0")

    # formulas
    f_cols = {r["name"] for r in cur.execute("PRAGMA table_info(formulas)").fetchall()}
    if "max_guests" not in f_cols:
        cur.execute("ALTER TABLE formulas ADD COLUMN max_guests INTEGER DEFAULT 0")

    # guest_reservations
    g_cols = {r["name"] for r in cur.execute("PRAGMA table_info(guest_reservations)").fetchall()}
    if "scanned_at" not in g_cols:
        cur.execute("ALTER TABLE guest_reservations ADD COLUMN scanned_at TEXT")

    # event_images : ajout media_type
    ei_cols = {r["name"] for r in cur.execute("PRAGMA table_info(event_images)").fetchall()}
    if "media_type" not in ei_cols:
        cur.execute("ALTER TABLE event_images ADD COLUMN media_type TEXT DEFAULT 'image'")

    # formulas : ajout is_girls_only
    f_cols2 = {r["name"] for r in cur.execute("PRAGMA table_info(formulas)").fetchall()}
    if "is_girls_only" not in f_cols2:
        cur.execute("ALTER TABLE formulas ADD COLUMN is_girls_only INTEGER DEFAULT 0")