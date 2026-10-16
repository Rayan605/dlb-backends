from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List


class UserCreate(BaseModel):
    email:      EmailStr
    password:   str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=80)
    last_name:  str = Field(min_length=1, max_length=80)
    gender:     str = Field(pattern="^(M|F|X)$")
    social:     str = Field(min_length=1, max_length=120,
                             description="Ton insta ou snap (@pseudo)")


class UserLogin(BaseModel):
    email:    EmailStr
    password: str


class UserOut(BaseModel):
    id:         int
    email:      EmailStr
    first_name: str
    last_name:  str
    gender:     str
    social:     str = ""
    is_admin:   bool = False
    is_scanner: bool = False
    created_at: Optional[str] = None


class Token(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user:         UserOut


# -------- ADMIN UTILS --------
class TestEmailIn(BaseModel):
    email: EmailStr


# -------- NOTIFICATIONS --------
class NotificationOut(BaseModel):
    id:         int
    title:      str
    body:       str
    event_id:   Optional[int] = None
    created_at: Optional[str] = None


class NotificationCreate(BaseModel):
    title:    str = Field(min_length=1, max_length=120)
    body:     str = Field(min_length=1, max_length=500)
    event_id: Optional[int] = None


class DeviceRegisterIn(BaseModel):
    device_token: str = Field(min_length=1, max_length=400)
    platform:     str = Field(default="apns", pattern="^(apns|fcm)$")


# -------- EVENTS --------
class EventImage(BaseModel):
    id:         int
    filename:   str
    url:        str
    position:   int
    is_recap:   bool = False
    media_type: str  = "image"  # "image" | "video"


class EventOut(BaseModel):
    id:           int
    title:        str
    description:  Optional[str] = None
    date:         str
    city:         str
    department:   str
    max_people:   int
    is_past:             bool = False
    reservations_open:   int  = 1
    images:              List[EventImage] = []
    recap_images: List[EventImage] = []
    seats_taken:  int = 0
    seats_left:   int = 0


# -------- FORMULAS --------
class FormulaOut(BaseModel):
    id:            int
    name:          str
    description:   Optional[str]
    price_cents:   int
    position:      int
    max_guests:    int = 0
    is_girls_only: int = 0
    # soirées auxquelles la formule est attribuée (vide = toutes)
    event_ids:     List[int] = []


class FormulaEventsIn(BaseModel):
    """Remplace la liste des soirées attribuées à une formule.
    Liste vide = formule visible sur toutes les soirées."""
    event_ids: List[int] = []


class FormulaCreate(BaseModel):
    name:          str = Field(min_length=1, max_length=120)
    description:   Optional[str] = Field(default=None, max_length=500)
    price_cents:   int = Field(ge=0)
    max_guests:    int = Field(default=0, ge=0)
    is_girls_only: int = Field(default=0, ge=0, le=1)


class FormulaUpdate(BaseModel):
    name:               Optional[str] = Field(default=None, min_length=1, max_length=120)
    description:        Optional[str] = Field(default=None, max_length=500)
    price_cents:        Optional[int] = Field(default=None, ge=0)
    max_guests:         Optional[int] = Field(default=None, ge=0)
    is_girls_only:      Optional[int] = Field(default=None, ge=0, le=1)
    max_reservations:   Optional[int] = Field(default=None, ge=0)


# -------- RESERVATIONS --------
class ReservationCreate(BaseModel):
    event_id:   int
    formula_id: int
    # quantity forcé à 1 — plus de choix
    quantity: int = Field(default=1, ge=1, le=1)


class ReservationOut(BaseModel):
    id:                 int
    user_id:            int
    event_id:           int
    formula_id:         int
    quantity:           int
    status:             str
    amount_paid_cents:  Optional[int] = None
    created_at:         Optional[str] = None
    paid_at:            Optional[str] = None
    scanned_at:         Optional[str] = None
    qr_token:           Optional[str] = None
    invite_token:       Optional[str] = None
    event_title:        Optional[str] = None
    event_date:         Optional[str] = None
    event_address:      Optional[str] = None  # révélé seulement si paid
    formula_name:       Optional[str] = None
    formula_max_guests: Optional[int] = None
    # données user pour le scan
    holder_first_name:  Optional[str] = None
    holder_last_name:   Optional[str] = None


class GuestReservationOut(BaseModel):
    id:             int
    reservation_id: int
    guest_user_id:  int
    event_id:       int
    qr_token:       str
    status:         str
    scanned_at:     Optional[str] = None
    created_at:     Optional[str] = None
    event_title:    Optional[str] = None
    event_date:     Optional[str] = None
    event_address: Optional[str] = None  # invitation acceptée uniquement
    host_first_name: Optional[str] = None
    host_last_name:  Optional[str] = None


class CheckoutSessionOut(BaseModel):
    checkout_url:   str
    session_id:     str
    reservation_id: int


# -------- SCANNER --------
class ScanResult(BaseModel):
    valid:        bool
    type:         str   # "reservation" | "guest"
    message:      str
    holder_name:  Optional[str] = None
    event_title:  Optional[str] = None
    event_date:   Optional[str] = None
    formula_name: Optional[str] = None
    already_scanned: bool = False
    scanned_at:   Optional[str] = None
    gender:       Optional[str] = None   # 'M' | 'F' | 'X' — pour le toast rose