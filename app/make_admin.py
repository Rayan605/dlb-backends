"""
Script utilitaire — promouvoir un user en admin.
Usage : python -m app.make_admin email@exemple.com
"""
import sys
from .database import get_db, init_db


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m app.make_admin <email>")
        sys.exit(1)
    email = sys.argv[1].lower()
    init_db()
    with get_db() as conn:
        u = conn.execute("SELECT id, email FROM users WHERE email = ?", (email,)).fetchone()
        if not u:
            print(f"Aucun utilisateur avec l'email {email}")
            sys.exit(2)
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
        print(f"OK : {email} est maintenant admin.")


if __name__ == "__main__":
    main()
