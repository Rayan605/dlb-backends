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


# -------- EVENTS --------
class EventImage(BaseModel):
    id:       int
    filename: str
    url:      str
    position: int
    is_recap: bool = False


class EventOut(BaseModel):
    id:           int
    title:        str
    description:  Optional[str] = None
    date:         str
    city:         str
    department:   str
    max_people:   int
    is_past:      bool = False
    images:       List[EventImage] = []
    recap_images: List[EventImage] = []
    seats_taken:  int = 0
    seats_left:   int = 0


# -------- FORMULAS --------
class FormulaOut(BaseModel):
    id:          int
    name:        str
    description: Optional[str]
    price_cents: int
    position:    int
    max_guests:  int = 0


class FormulaUpdate(BaseModel):
    name:        Optional[str] = None
    description: Optional[str] = None
    price_cents: Optional[int] = None
    max_guests:  Optional[int] = None


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
