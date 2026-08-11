"""
free.py — Crée une réservation "offerte" (payée à 0€, statut confirmé)
pour un utilisateur, sans passer par Stripe.

Usage :
    python3 free.py <email>
    python3 free.py <email> <event_id>
    python3 free.py <email> <event_id> <formula_id>

Si event_id est omis, prend la prochaine soirée à venir (la plus proche).
Si formula_id est omis, prend la formule de position la plus basse.

À lancer depuis la racine du projet (là où se trouve le dossier app/),
en SSH/Kudu sur l'App Service, ou en local avec le bon DB_PATH.
"""
import sys
import uuid
from app.database import get_db


def main():
    if len(sys.argv) < 2:
        print("Usage : python3 free.py <email> [event_id] [formula_id]")
        sys.exit(1)

    email = sys.argv[1].strip().lower()
    event_id = int(sys.argv[2]) if len(sys.argv) > 2 else None
    formula_id = int(sys.argv[3]) if len(sys.argv) > 3 else None

    with get_db() as conn:
        user = conn.execute("SELECT id, first_name, last_name, email FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            print(f"❌ Aucun utilisateur trouvé avec l'email {email}")
            print("   → la personne doit d'abord créer un compte dans l'app.")
            sys.exit(2)

        if event_id is None:
            ev = conn.execute("""
                SELECT id, title, date FROM events
                WHERE is_past = 0
                ORDER BY date ASC LIMIT 1
            """).fetchone()
            if not ev:
                print("❌ Aucune soirée à venir trouvée. Précise un event_id.")
                sys.exit(3)
            event_id = ev["id"]
        else:
            ev = conn.execute("SELECT id, title, date FROM events WHERE id=?", (event_id,)).fetchone()
            if not ev:
                print(f"❌ Aucune soirée avec l'id {event_id}")
                sys.exit(3)

        if formula_id is None:
            f = conn.execute("""
                SELECT id, name FROM formulas
                ORDER BY position ASC LIMIT 1
            """).fetchone()
            if not f:
                print("❌ Aucune formule trouvée. Précise un formula_id.")
                sys.exit(4)
            formula_id = f["id"]
        else:
            f = conn.execute("SELECT id, name FROM formulas WHERE id=?", (formula_id,)).fetchone()
            if not f:
                print(f"❌ Aucune formule avec l'id {formula_id}")
                sys.exit(4)

        qr_token = str(uuid.uuid4())
        invite_token = str(uuid.uuid4())

        conn.execute("""
            INSERT INTO reservations
                (user_id, event_id, formula_id, quantity, status,
                 amount_paid_cents, qr_token, invite_token, paid_at)
            VALUES (?, ?, ?, 1, 'paid', 0, ?, ?, CURRENT_TIMESTAMP)
        """, (user["id"], event_id, formula_id, qr_token, invite_token))

        print("✅ Réservation offerte créée :")
        print(f"   Utilisateur : {user['first_name']} {user['last_name']} ({user['email']})")
        print(f"   Soirée      : {ev['title']} ({ev['date']})")
        print(f"   Formule     : {f['name']}")
        print(f"   qr_token    : {qr_token}")


if __name__ == "__main__":
    main()
