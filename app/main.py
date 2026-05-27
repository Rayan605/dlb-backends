"""
Liste Party API — Dans le bon.
"""
import os
import uuid
import sqlite3
import base64
import io
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timezone

import qrcode
import stripe
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import get_db, init_db
from . import schemas
from .auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_admin
)

# ─── CONFIG ───────────────────────────────────────────────
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", Path(__file__).parent.parent / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_IMG   = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
ALLOWED_VIDEO = {".mp4", ".mov", ".webm", ".avi"}
MAX_IMG_MB    = 8
MAX_VIDEO_MB  = 200

STRIPE_SECRET         = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
FRONTEND_URL          = os.environ.get("FRONTEND_URL", "http://localhost:8000")

def _normalize_url(raw: str) -> str:
    """Garantit que l URL a un scheme http(s)://."""
    url = (raw or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url or "http://localhost:8000"

BACKEND_PUBLIC_URL = _normalize_url(os.environ.get("BACKEND_PUBLIC_URL", "http://localhost:8000"))

if STRIPE_SECRET:
    stripe.api_key = STRIPE_SECRET

# ─── APP ──────────────────────────────────────────────────
app = FastAPI(title="Liste Party API", version="3.0.0")

allowed_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

init_db()

@app.on_event("startup")
def startup():
    init_db()

@app.get("/")
def root():
    return {"status": "ok", "service": "liste-party-api", "version": "3.0.0"}

@app.get("/health")
def health():
    return {"status": "healthy"}


# ─── PERMISSIONS ──────────────────────────────────────────
def require_scanner(user: dict = Depends(get_current_user)) -> dict:
    if not (user.get("is_scanner") or user.get("is_admin")):
        raise HTTPException(status_code=403, detail="Accès réservé aux scanners.")
    return user


# ─── QR HELPERS ───────────────────────────────────────────
def _qr_b64(data: str) -> str:
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

def _qr_payload(res_id, qr_token, holder_name, formula_name, event_id, event_title):
    return f"LP|RES|{res_id}|{qr_token}|{holder_name}|{formula_name}|EVENT:{event_id}|{event_title}"

def _qr_payload_guest(g_id, qr_token, guest_name, host_name, event_id, event_title):
    return f"LP|GUEST|{g_id}|{qr_token}|{guest_name}|HOST:{host_name}|EVENT:{event_id}|{event_title}"


# ─── AUTH ─────────────────────────────────────────────────
@app.post("/auth/register", response_model=schemas.Token, status_code=201)
def register(payload: schemas.UserCreate):
    with get_db() as conn:
        if conn.execute("SELECT id FROM users WHERE email=?", (payload.email.lower(),)).fetchone():
            raise HTTPException(409, "Cet email est déjà utilisé, frère.")
        cur = conn.execute(
            "INSERT INTO users (email,password_hash,first_name,last_name,gender,social) VALUES (?,?,?,?,?,?)",
            (payload.email.lower(), hash_password(payload.password),
             payload.first_name.strip(), payload.last_name.strip(),
             payload.gender, payload.social.strip()),
        )
        user = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return {"access_token": create_access_token(user["id"]), "token_type": "bearer", "user": _user_out(user)}


@app.post("/auth/login", response_model=schemas.Token)
def login(payload: schemas.UserLogin):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (payload.email.lower(),)).fetchone()
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Email ou mot de passe incorrect.")
    return {"access_token": create_access_token(user["id"]), "token_type": "bearer", "user": _user_out(user)}


@app.get("/auth/me", response_model=schemas.UserOut)
def me(user: dict = Depends(get_current_user)):
    return _user_out(user)


def _user_out(u):
    return {
        "id": u["id"], "email": u["email"],
        "first_name": u["first_name"], "last_name": u["last_name"],
        "gender": u["gender"],
        "social": u.get("social") or "",
        "is_admin": bool(u.get("is_admin")),
        "is_scanner": bool(u.get("is_scanner")),
        "created_at": u.get("created_at"),
    }


# ─── ADMIN: gestion scanners & users ──────────────────────
@app.get("/admin/users")
def admin_list_users(admin: dict = Depends(require_admin)):
    with get_db() as conn:
        return conn.execute(
            "SELECT id,email,first_name,last_name,gender,social,is_admin,is_scanner,created_at FROM users ORDER BY created_at DESC"
        ).fetchall()


@app.patch("/admin/users/{user_id}/scanner")
def toggle_scanner(user_id: int, is_scanner: bool, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        u = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            raise HTTPException(404, "Utilisateur introuvable.")
        conn.execute("UPDATE users SET is_scanner=? WHERE id=?", (1 if is_scanner else 0, user_id))
    return {"ok": True, "user_id": user_id, "is_scanner": is_scanner}


@app.patch("/admin/users/{user_id}/admin")
def toggle_admin(user_id: int, is_admin: bool, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        conn.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if is_admin else 0, user_id))
    return {"ok": True}


# ─── EVENTS ───────────────────────────────────────────────
@app.get("/events", response_model=List[schemas.EventOut])
def list_events(upcoming_only: bool = True):
    with get_db() as conn:
        if upcoming_only:
            rows = conn.execute("SELECT * FROM events WHERE is_past=0 ORDER BY date ASC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY date DESC").fetchall()
        return [_event_extras(conn, e) for e in rows]


@app.get("/events/past", response_model=List[schemas.EventOut])
def list_past_events():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM events WHERE is_past=1 ORDER BY date DESC").fetchall()
        return [_event_extras(conn, e) for e in rows]


@app.get("/events/{event_id}", response_model=schemas.EventOut)
def get_event(event_id: int):
    with get_db() as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(404, "Soirée introuvable.")
        return _event_extras(conn, ev)


def _event_extras(conn, event):
    imgs = conn.execute(
        "SELECT id,filename,position,is_recap,media_type FROM event_images WHERE event_id=? ORDER BY position ASC",
        (event["id"],),
    ).fetchall()
    taken = (conn.execute(
        "SELECT COALESCE(SUM(quantity),0) as t FROM reservations WHERE event_id=? AND status IN ('paid','pending')",
        (event["id"],),
    ).fetchone()["t"] or 0)
    return {
        **event,
        "is_past": bool(event.get("is_past")),
        "images":       [_img_out(i) for i in imgs if not i["is_recap"]],
        "recap_images": [_img_out(i) for i in imgs if i["is_recap"]],
        "seats_taken": taken,
        "seats_left": max(0, event["max_people"] - taken),
    }

def _img_out(img):
    return {
        "id":         img["id"],
        "filename":   img["filename"],
        "url":        f"{BACKEND_PUBLIC_URL}/uploads/{img['filename']}",
        "position":   img["position"],
        "is_recap":   bool(img.get("is_recap")),
        "media_type": img.get("media_type") or "image",
    }


@app.post("/events", response_model=schemas.EventOut, status_code=201)
async def create_event(
    title:       str = Form(...),
    description: str = Form(""),
    date:        str = Form(...),
    city:        str = Form(...),
    department:  str = Form(...),
    max_people:  int = Form(...),
    images: List[UploadFile] = File(default=[]),
    admin: dict = Depends(require_admin),
):
    try:
        datetime.fromisoformat(date.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Date invalide (ISO 8601).")
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO events (title,description,date,city,department,max_people) VALUES (?,?,?,?,?,?)",
            (title, description, date, city, department, max_people),
        )
        eid = cur.lastrowid
        await _save_imgs(conn, eid, images, False)
        return _event_extras(conn, conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())


@app.post("/events/{event_id}/recap-images", response_model=schemas.EventOut)
async def add_recap_images(
    event_id: int,
    images: List[UploadFile] = File(...),  # accepte images ET vidéos
    admin: dict = Depends(require_admin),
):
    with get_db() as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(404, "Soirée introuvable.")
        conn.execute("UPDATE events SET is_past=1 WHERE id=?", (event_id,))
        await _save_imgs(conn, event_id, images, True)
        return _event_extras(conn, conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone())


@app.delete("/events/{event_id}/recap-images/{image_id}", status_code=204)
def delete_recap_image(event_id: int, image_id: int, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        img = conn.execute(
            "SELECT * FROM event_images WHERE id=? AND event_id=? AND is_recap=1", (image_id, event_id)
        ).fetchone()
        if not img:
            raise HTTPException(404)
        conn.execute("DELETE FROM event_images WHERE id=?", (image_id,))
        try: (UPLOAD_DIR / img["filename"]).unlink(missing_ok=True)
        except Exception: pass
    return None


@app.patch("/events/{event_id}/mark-past", response_model=schemas.EventOut)
def mark_past(event_id: int, is_past: bool = True, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        conn.execute("UPDATE events SET is_past=? WHERE id=?", (1 if is_past else 0, event_id))
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(404)
        return _event_extras(conn, ev)


@app.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: int, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        imgs = conn.execute("SELECT filename FROM event_images WHERE event_id=?", (event_id,)).fetchall()
        conn.execute("DELETE FROM events WHERE id=?", (event_id,))
    for img in imgs:
        try: (UPLOAD_DIR / img["filename"]).unlink(missing_ok=True)
        except Exception: pass
    return None


async def _save_imgs(conn, event_id, images, is_recap):
    """Sauvegarde des fichiers médias (images et vidéos)."""
    start = (conn.execute(
        "SELECT COALESCE(MAX(position),0) as m FROM event_images WHERE event_id=? AND is_recap=?",
        (event_id, 1 if is_recap else 0)
    ).fetchone()["m"] or 0) + 1
    for idx, img in enumerate(images or []):
        if not img.filename: continue
        ext = Path(img.filename).suffix.lower()
        is_video = ext in ALLOWED_VIDEO
        is_image = ext in ALLOWED_IMG
        if not is_video and not is_image: continue
        media_type = "video" if is_video else "image"
        max_mb = MAX_VIDEO_MB if is_video else MAX_IMG_MB
        content_bytes = await img.read()
        if len(content_bytes) > max_mb * 1024 * 1024:
            raise HTTPException(400, f"Fichier trop lourd ({img.filename} — max {max_mb} Mo)")
        fname = f"{uuid.uuid4().hex}{ext}"
        (UPLOAD_DIR / fname).write_bytes(content_bytes)
        conn.execute(
            "INSERT INTO event_images (event_id,filename,position,is_recap,media_type) VALUES (?,?,?,?,?)",
            (event_id, fname, start + idx, 1 if is_recap else 0, media_type),
        )


# ─── FORMULAS ─────────────────────────────────────────────
@app.get("/formulas", response_model=List[schemas.FormulaOut])
def list_formulas():
    with get_db() as conn:
        return conn.execute("SELECT * FROM formulas ORDER BY position ASC, id ASC").fetchall()


@app.post("/formulas", response_model=schemas.FormulaOut, status_code=201)
def create_formula(payload: schemas.FormulaCreate, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        max_pos = conn.execute("SELECT COALESCE(MAX(position), 0) as m FROM formulas").fetchone()["m"]
        cur = conn.execute(
            "INSERT INTO formulas (name, description, price_cents, max_guests, position) VALUES (?,?,?,?,?)",
            (payload.name.strip(), payload.description, payload.price_cents, payload.max_guests, max_pos + 1),
        )
        return conn.execute("SELECT * FROM formulas WHERE id=?", (cur.lastrowid,)).fetchone()


@app.patch("/formulas/{formula_id}", response_model=schemas.FormulaOut)
def update_formula(formula_id: int, payload: schemas.FormulaUpdate, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM formulas WHERE id=?", (formula_id,)).fetchone():
            raise HTTPException(404, "Formule introuvable.")
        ups, params = [], []
        for f in ("name", "description", "price_cents", "max_guests"):
            v = getattr(payload, f)
            if v is not None:
                ups.append(f"{f}=?"); params.append(v)
        if ups:
            params.append(formula_id)
            conn.execute(f"UPDATE formulas SET {', '.join(ups)} WHERE id=?", params)
        return conn.execute("SELECT * FROM formulas WHERE id=?", (formula_id,)).fetchone()


@app.delete("/formulas/{formula_id}", status_code=204)
def delete_formula(formula_id: int, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM formulas WHERE id=?", (formula_id,)).fetchone():
            raise HTTPException(404, "Formule introuvable.")
        used = conn.execute(
            "SELECT COUNT(*) as c FROM reservations WHERE formula_id=? AND status IN ('paid','pending')",
            (formula_id,),
        ).fetchone()["c"]
        if used > 0:
            raise HTTPException(409, f"Impossible de supprimer : {used} réservation(s) active(s) utilisent cette formule.")
        conn.execute("DELETE FROM formulas WHERE id=?", (formula_id,))
    return None


# ─── RESERVATIONS ─────────────────────────────────────────
@app.post("/reservations/checkout", response_model=schemas.CheckoutSessionOut)
def create_checkout(payload: schemas.ReservationCreate, user: dict = Depends(get_current_user)):
    """
    Règles :
    - Si une PAID existe pour cet user+event → 409
    - Si une PENDING existe → elle est supprimée et remplacée (user avait abandonné Stripe)
    - Places comptées en excluant la pending de cet user (pour ne pas se bloquer soi-même)
    """
    with get_db() as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (payload.event_id,)).fetchone()
        if not ev:
            raise HTTPException(404, "Soirée introuvable.")
        if ev.get("is_past"):
            raise HTTPException(400, "Cette soirée est terminée, check les prochaines.")

        formula = conn.execute("SELECT * FROM formulas WHERE id=?", (payload.formula_id,)).fetchone()
        if not formula:
            raise HTTPException(404, "Formule introuvable.")

        # Paid existante → bloquer
        if conn.execute(
            "SELECT id FROM reservations WHERE user_id=? AND event_id=? AND status='paid'",
            (user["id"], payload.event_id),
        ).fetchone():
            raise HTTPException(409, "T'as déjà une réservation confirmée pour cette soirée.")

        # Places dispo : on exclut la pending de cet user (elle sera remplacée)
        taken_others = conn.execute(
            """SELECT COALESCE(SUM(quantity),0) as t FROM reservations
               WHERE event_id=? AND status IN ('paid','pending')
               AND NOT (user_id=? AND status='pending')""",
            (payload.event_id, user["id"]),
        ).fetchone()["t"] or 0
        # quantity = 1 (hôte) + max_guests : on réserve toutes les places dès l achat
        quantity = 1 + (formula.get("max_guests") or 0)
        if taken_others + quantity > ev["max_people"]:
            raise HTTPException(409, "Plus de places disponibles, désolé frère.")

        # Supprimer l'ancienne pending abandonnée + insérer la nouvelle
        conn.execute(
            "DELETE FROM reservations WHERE user_id=? AND event_id=? AND status='pending'",
            (user["id"], payload.event_id),
        )
        cur = conn.execute(
            "INSERT INTO reservations (user_id,event_id,formula_id,quantity,status) VALUES (?,?,?,?,'pending')",
            (user["id"], payload.event_id, payload.formula_id, quantity),
        )
        resa_id = cur.lastrowid

    if not STRIPE_SECRET:
        raise HTTPException(503, "Stripe n'est pas configuré (STRIPE_SECRET_KEY manquant).")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "unit_amount": formula["price_cents"],
                    "product_data": {
                        "name": f"{ev['title']} — {formula['name']}",
                        "description": (formula["description"] or "")[:500],
                    },
                },
                "quantity": 1,
            }],
            customer_email=user["email"],
            success_url=f"{FRONTEND_URL}/success.html?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}/event.html?id={ev['id']}&cancelled=1",
            metadata={
                "reservation_id": str(resa_id),
                "user_id": str(user["id"]),
                "event_id": str(ev["id"]),
                "formula_id": str(formula["id"]),
            },
        )
    except stripe.error.StripeError as e:
        with get_db() as conn:
            conn.execute("DELETE FROM reservations WHERE id=?", (resa_id,))
        raise HTTPException(502, f"Erreur Stripe: {e}")

    with get_db() as conn:
        conn.execute("UPDATE reservations SET stripe_session_id=? WHERE id=?", (session.id, resa_id))

    return {"checkout_url": session.url, "session_id": session.id, "reservation_id": resa_id}

@app.post("/reservations/free", response_model=schemas.ReservationOut)
def create_free_reservation(payload: schemas.ReservationCreate, user: dict = Depends(get_current_user)):
    """
    Réservation gratuite — uniquement pour les comptes avec gender='F' et la formule à 0€.
    Génère directement le QR code sans passer par Stripe.
    """
    if user.get("gender") != "F":
        raise HTTPException(403, "Cette formule gratuite est réservée aux filles.")

    with get_db() as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (payload.event_id,)).fetchone()
        if not ev:
            raise HTTPException(404, "Soirée introuvable.")
        if ev.get("is_past"):
            raise HTTPException(400, "Cette soirée est terminée.")

        formula = conn.execute("SELECT * FROM formulas WHERE id=?", (payload.formula_id,)).fetchone()
        if not formula:
            raise HTTPException(404, "Formule introuvable.")
        if formula.get("price_cents", 1) != 0:
            raise HTTPException(400, "Cette formule n'est pas gratuite.")

        # Paid existante → bloquer
        if conn.execute(
            "SELECT id FROM reservations WHERE user_id=? AND event_id=? AND status='paid'",
            (user["id"], payload.event_id),
        ).fetchone():
            raise HTTPException(409, "T'as déjà une réservation confirmée pour cette soirée.")

        # Places dispo
        taken_others = conn.execute(
            """SELECT COALESCE(SUM(quantity),0) as t FROM reservations
               WHERE event_id=? AND status IN ('paid','pending')
               AND NOT (user_id=? AND status='pending')""",
            (payload.event_id, user["id"]),
        ).fetchone()["t"] or 0
        quantity = 1 + (formula.get("max_guests") or 0)
        if taken_others + quantity > ev["max_people"]:
            raise HTTPException(409, "Plus de places disponibles, désolé frère.")

        # Supprimer une éventuelle pending, créer directement en 'paid'
        conn.execute(
            "DELETE FROM reservations WHERE user_id=? AND event_id=? AND status='pending'",
            (user["id"], payload.event_id),
        )
        qr_token  = uuid.uuid4().hex
        inv_token = uuid.uuid4().hex if (formula.get("max_guests") or 0) > 0 else None
        cur = conn.execute(
            """INSERT INTO reservations
               (user_id,event_id,formula_id,quantity,status,qr_token,invite_token,amount_paid_cents,paid_at)
               VALUES (?,?,?,?,'paid',?,?,0,CURRENT_TIMESTAMP)""",
            (user["id"], payload.event_id, payload.formula_id, quantity, qr_token, inv_token),
        )
        resa_id = cur.lastrowid

        res = conn.execute(
            """SELECT r.*, e.title as event_title, e.date as event_date,
                      f.name as formula_name, f.max_guests as formula_max_guests,
                      u.first_name as holder_first_name, u.last_name as holder_last_name
               FROM reservations r
               JOIN events e  ON e.id=r.event_id
               JOIN formulas f ON f.id=r.formula_id
               JOIN users u   ON u.id=r.user_id
               WHERE r.id=?""",
            (resa_id,),
        ).fetchone()
        return res


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Event.construct_from(__import__("json").loads(payload), stripe.api_key)
        except Exception:
            raise HTTPException(400, "Payload invalide.")
    else:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except Exception:
            raise HTTPException(400, "Signature invalide.")

    if event["type"] == "checkout.session.completed":
        s = event["data"]["object"]
        rid = int(s["metadata"].get("reservation_id", 0))
        if rid:
            _confirm_resa(rid, s.get("amount_total"), s.get("payment_intent"))

    elif event["type"] in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        s = event["data"]["object"]
        rid = int(s["metadata"].get("reservation_id", 0))
        if rid:
            with get_db() as conn:
                conn.execute(
                    "UPDATE reservations SET status='cancelled' WHERE id=? AND status='pending'", (rid,)
                )
    return {"received": True}


def _confirm_resa(resa_id: int, amount_total, payment_intent):
    with get_db() as conn:
        res = conn.execute("SELECT * FROM reservations WHERE id=?", (resa_id,)).fetchone()
        if not res or res["status"] == "paid":
            return
        formula  = conn.execute("SELECT * FROM formulas WHERE id=?", (res["formula_id"],)).fetchone()
        qr_tok   = uuid.uuid4().hex
        inv_tok  = uuid.uuid4().hex if formula and formula.get("max_guests", 0) > 0 else None
        conn.execute(
            """UPDATE reservations
               SET status='paid', amount_paid_cents=?, stripe_payment_intent=?,
                   paid_at=CURRENT_TIMESTAMP, qr_token=?, invite_token=?
               WHERE id=?""",
            (amount_total, payment_intent, qr_tok, inv_tok, resa_id),
        )


@app.get("/reservations/me", response_model=List[schemas.ReservationOut])
def list_my_reservations(user: dict = Depends(get_current_user)):
    with get_db() as conn:
        return conn.execute(
            """SELECT r.*, e.title as event_title, e.date as event_date,
                      f.name as formula_name, f.max_guests as formula_max_guests,
                      u.first_name as holder_first_name, u.last_name as holder_last_name
               FROM reservations r
               JOIN events e  ON e.id=r.event_id
               JOIN formulas f ON f.id=r.formula_id
               JOIN users u   ON u.id=r.user_id
               WHERE r.user_id=?
               ORDER BY r.created_at DESC""",
            (user["id"],),
        ).fetchall()


@app.get("/reservations/confirm")
def confirm_by_session(session_id: str, user: dict = Depends(get_current_user)):
    if not STRIPE_SECRET:
        raise HTTPException(503, "Stripe non configuré.")
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.StripeError as e:
        raise HTTPException(400, str(e))

    resa_id = int(session.metadata.get("reservation_id", 0))
    with get_db() as conn:
        res = conn.execute("SELECT * FROM reservations WHERE id=? AND user_id=?",
                           (resa_id, user["id"])).fetchone()
        if not res:
            raise HTTPException(404, "Réservation introuvable.")

    if session.payment_status == "paid" and res["status"] != "paid":
        _confirm_resa(resa_id, session.amount_total, session.payment_intent)

    with get_db() as conn:
        return conn.execute(
            """SELECT r.*, e.title as event_title, e.date as event_date,
                      f.name as formula_name, f.max_guests as formula_max_guests,
                      u.first_name as holder_first_name, u.last_name as holder_last_name
               FROM reservations r
               JOIN events e  ON e.id=r.event_id
               JOIN formulas f ON f.id=r.formula_id
               JOIN users u   ON u.id=r.user_id
               WHERE r.id=?""",
            (resa_id,),
        ).fetchone()


@app.get("/reservations/{reservation_id}/qr")
def get_reservation_qr(reservation_id: int, user: dict = Depends(get_current_user)):
    with get_db() as conn:
        res = conn.execute(
            "SELECT * FROM reservations WHERE id=? AND user_id=?", (reservation_id, user["id"])
        ).fetchone()
        if not res:
            raise HTTPException(404, "Réservation introuvable.")
        if res["status"] != "paid" or not res["qr_token"]:
            raise HTTPException(400, "Paiement non confirmé.")
        ev      = conn.execute("SELECT * FROM events WHERE id=?",   (res["event_id"],)).fetchone()
        formula = conn.execute("SELECT * FROM formulas WHERE id=?", (res["formula_id"],)).fetchone()

    holder = f"{user['first_name']} {user['last_name']}"
    payload = _qr_payload(reservation_id, res["qr_token"], holder,
                          formula["name"], ev["id"], ev["title"])
    return {
        "qr_base64":   _qr_b64(payload),
        "payload":     payload,
        "reservation_id": reservation_id,
        "holder_name": holder,
        "event_title": ev["title"],
        "event_date":  ev["date"],
        "formula_name": formula["name"],
        "quantity": res["quantity"],
    }


# ─── SCANNER QR ───────────────────────────────────────────
@app.get("/scan/{qr_token}", response_model=schemas.ScanResult)
def scan_qr(qr_token: str, scanner: dict = Depends(require_scanner)):
    """
    Lit un qr_token, valide la réservation et la marque comme scannée.
    Fonctionne pour les réservations normales et invités.
    """
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        # --- Réservation normale ---
        res = conn.execute(
            """SELECT r.*, e.title as event_title, e.date as event_date,
                      f.name as formula_name,
                      u.first_name as holder_first, u.last_name as holder_last
               FROM reservations r
               JOIN events e  ON e.id=r.event_id
               JOIN formulas f ON f.id=r.formula_id
               JOIN users u   ON u.id=r.user_id
               WHERE r.qr_token=?""",
            (qr_token,),
        ).fetchone()

        if res:
            if res["status"] != "paid":
                return schemas.ScanResult(
                    valid=False, type="reservation",
                    message=f"Réservation non payée (statut : {res['status']}).",
                    holder_name=f"{res['holder_first']} {res['holder_last']}",
                    event_title=res["event_title"], event_date=res["event_date"],
                    formula_name=res["formula_name"],
                )
            if res["scanned_at"]:
                return schemas.ScanResult(
                    valid=False, type="reservation",
                    message="QR déjà scanné — tentative de réutilisation.",
                    holder_name=f"{res['holder_first']} {res['holder_last']}",
                    event_title=res["event_title"], event_date=res["event_date"],
                    formula_name=res["formula_name"],
                    already_scanned=True, scanned_at=res["scanned_at"],
                )
            # Valide → marquer
            conn.execute("UPDATE reservations SET scanned_at=? WHERE qr_token=?", (now, qr_token))
            holder_user = conn.execute("SELECT gender FROM users WHERE id=?", (res["user_id"],)).fetchone()
            return schemas.ScanResult(
                valid=True, type="reservation",
                message="✓ Réservation valide. Bienvenue dans le bon.",
                holder_name=f"{res['holder_first']} {res['holder_last']}",
                event_title=res["event_title"], event_date=res["event_date"],
                formula_name=res["formula_name"],
                gender=holder_user["gender"] if holder_user else None,
            )

        # --- Réservation invité ---
        gr = conn.execute(
            """SELECT gr.*,
                      e.title as event_title, e.date as event_date,
                      gu.first_name as guest_first, gu.last_name as guest_last,
                      hu.first_name as host_first,  hu.last_name as host_last
               FROM guest_reservations gr
               JOIN events e   ON e.id=gr.event_id
               JOIN users gu   ON gu.id=gr.guest_user_id
               JOIN reservations r ON r.id=gr.reservation_id
               JOIN users hu   ON hu.id=r.user_id
               WHERE gr.qr_token=?""",
            (qr_token,),
        ).fetchone()

        if gr:
            if gr["status"] != "active":
                return schemas.ScanResult(
                    valid=False, type="guest",
                    message=f"Invitation annulée (statut : {gr['status']}).",
                    holder_name=f"{gr['guest_first']} {gr['guest_last']}",
                    event_title=gr["event_title"], event_date=gr["event_date"],
                    formula_name=f"Invité de {gr['host_first']} {gr['host_last']}",
                )
            if gr["scanned_at"]:
                return schemas.ScanResult(
                    valid=False, type="guest",
                    message="QR invité déjà scanné — tentative de réutilisation.",
                    holder_name=f"{gr['guest_first']} {gr['guest_last']}",
                    event_title=gr["event_title"], event_date=gr["event_date"],
                    formula_name=f"Invité de {gr['host_first']} {gr['host_last']}",
                    already_scanned=True, scanned_at=gr["scanned_at"],
                )
            conn.execute("UPDATE guest_reservations SET scanned_at=? WHERE qr_token=?", (now, qr_token))
            guest_gender = conn.execute("SELECT gender FROM users WHERE id=?", (gr["guest_user_id"],)).fetchone()
            return schemas.ScanResult(
                valid=True, type="guest",
                message=f"✓ Invité valide — hôte : {gr['host_first']} {gr['host_last']}.",
                holder_name=f"{gr['guest_first']} {gr['guest_last']}",
                event_title=gr["event_title"], event_date=gr["event_date"],
                formula_name=f"Invité de {gr['host_first']} {gr['host_last']}",
                gender=guest_gender["gender"] if guest_gender else None,
            )

    # Token inconnu
    return schemas.ScanResult(
        valid=False, type="unknown",
        message="QR code non reconnu dans la base. C'est pas le bon, frère.",
    )


# ─── INVITATIONS ──────────────────────────────────────────
@app.get("/invitations/{invite_token}")
def get_invite_info(invite_token: str):
    with get_db() as conn:
        res = conn.execute(
            """SELECT r.*, e.title as event_title, e.date as event_date, e.city,
                      f.name as formula_name, f.max_guests,
                      u.first_name as host_first, u.last_name as host_last
               FROM reservations r
               JOIN events e  ON e.id=r.event_id
               JOIN formulas f ON f.id=r.formula_id
               JOIN users u   ON u.id=r.user_id
               WHERE r.invite_token=? AND r.status='paid'""",
            (invite_token,),
        ).fetchone()
        if not res:
            raise HTTPException(404, "Lien d'invitation invalide ou expiré.")
        guests = conn.execute(
            "SELECT COUNT(*) as c FROM guest_reservations WHERE reservation_id=? AND status='active'",
            (res["id"],),
        ).fetchone()["c"]
    return {
        "valid": True,
        "host_name":          f"{res['host_first']} {res['host_last']}",
        "event_title":        res["event_title"],
        "event_date":         res["event_date"],
        "event_city":         res["city"],
        "formula_name":       res["formula_name"],
        "max_guests":         res["max_guests"],
        "guests_registered":  guests,
        "spots_left":         max(0, res["max_guests"] - guests),
        "reservation_id":     res["id"],
    }


@app.post("/invitations/{invite_token}/join", response_model=schemas.GuestReservationOut)
def join_as_guest(invite_token: str, user: dict = Depends(get_current_user)):
    with get_db() as conn:
        res = conn.execute(
            """SELECT r.*, f.max_guests, e.title as event_title, e.date as event_date
               FROM reservations r
               JOIN formulas f ON f.id=r.formula_id
               JOIN events e   ON e.id=r.event_id
               WHERE r.invite_token=? AND r.status='paid'""",
            (invite_token,),
        ).fetchone()
        if not res:
            raise HTTPException(404, "Lien invalide ou expiré.")
        if res["user_id"] == user["id"]:
            raise HTTPException(400, "T'es l'hôte, tu peux pas t'inviter toi-même frère.")
        if conn.execute(
            "SELECT id FROM guest_reservations WHERE reservation_id=? AND guest_user_id=?",
            (res["id"], user["id"]),
        ).fetchone():
            raise HTTPException(409, "T'es déjà inscrit comme invité pour cette résa.")
        guests = conn.execute(
            "SELECT COUNT(*) as c FROM guest_reservations WHERE reservation_id=? AND status='active'",
            (res["id"],),
        ).fetchone()["c"]
        if guests >= res["max_guests"]:
            raise HTTPException(409, "Plus de place pour les invités, c'est complet.")

        host = conn.execute("SELECT * FROM users WHERE id=?", (res["user_id"],)).fetchone()
        qr_tok = uuid.uuid4().hex
        cur = conn.execute(
            "INSERT INTO guest_reservations (reservation_id,guest_user_id,event_id,qr_token) VALUES (?,?,?,?)",
            (res["id"], user["id"], res["event_id"], qr_tok),
        )
        return {
            "id": cur.lastrowid, "reservation_id": res["id"],
            "guest_user_id": user["id"], "event_id": res["event_id"],
            "qr_token": qr_tok, "status": "active", "created_at": None,
            "event_title": res["event_title"], "event_date": res["event_date"],
            "host_first_name": host["first_name"], "host_last_name": host["last_name"],
        }


@app.get("/invitations/guest/{guest_id}/qr")
def get_guest_qr(guest_id: int, user: dict = Depends(get_current_user)):
    with get_db() as conn:
        gr = conn.execute(
            "SELECT * FROM guest_reservations WHERE id=? AND guest_user_id=?", (guest_id, user["id"])
        ).fetchone()
        if not gr:
            raise HTTPException(404)
        host_res = conn.execute("SELECT * FROM reservations WHERE id=?", (gr["reservation_id"],)).fetchone()
        host  = conn.execute("SELECT * FROM users WHERE id=?", (host_res["user_id"],)).fetchone()
        event = conn.execute("SELECT * FROM events WHERE id=?", (gr["event_id"],)).fetchone()
    guest_name = f"{user['first_name']} {user['last_name']}"
    host_name  = f"{host['first_name']} {host['last_name']}"
    payload = _qr_payload_guest(guest_id, gr["qr_token"], guest_name, host_name, event["id"], event["title"])
    return {
        "qr_base64":  _qr_b64(payload),
        "payload":    payload,
        "holder_name": guest_name,
        "host_name":   host_name,
        "event_title": event["title"],
        "event_date":  event["date"],
    }


@app.get("/guests/me", response_model=List[schemas.GuestReservationOut])
def list_my_guest_reservations(user: dict = Depends(get_current_user)):
    with get_db() as conn:
        return conn.execute(
            """SELECT gr.*, e.title as event_title, e.date as event_date,
                      u.first_name as host_first_name, u.last_name as host_last_name
               FROM guest_reservations gr
               JOIN events e       ON e.id=gr.event_id
               JOIN reservations r ON r.id=gr.reservation_id
               JOIN users u        ON u.id=r.user_id
               WHERE gr.guest_user_id=?
               ORDER BY gr.created_at DESC""",
            (user["id"],),
        ).fetchall()


# ─── ADMIN RESERVATIONS ───────────────────────────────────
@app.get("/admin/reservations", response_model=List[schemas.ReservationOut])
def admin_list_reservations(event_id: Optional[int] = None, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        q = """SELECT r.*, e.title as event_title, e.date as event_date,
                      f.name as formula_name, f.max_guests as formula_max_guests,
                      u.first_name as holder_first_name, u.last_name as holder_last_name
               FROM reservations r
               JOIN events e  ON e.id=r.event_id
               JOIN formulas f ON f.id=r.formula_id
               JOIN users u   ON u.id=r.user_id"""
        if event_id:
            return conn.execute(q + " WHERE r.event_id=? ORDER BY r.created_at DESC", (event_id,)).fetchall()
        return conn.execute(q + " ORDER BY r.created_at DESC").fetchall()