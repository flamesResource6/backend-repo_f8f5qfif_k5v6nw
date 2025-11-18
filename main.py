import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import jwt

from database import db, create_document, get_documents
from schemas import User as UserSchema, Restaurant as RestaurantSchema

# ----------------------------------------------------------------------------
# App + Security setup
# ----------------------------------------------------------------------------
app = FastAPI(title="FoodieHungary API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET_KEY = os.getenv("JWT_SECRET", "dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_user_by_email(email: str) -> Optional[dict]:
    users = get_documents("user", {"email": email.lower()})
    return users[0] if users else None


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = get_user_by_email(email)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception:
        raise HTTPException(status_code=401, detail="Could not validate credentials")


# ----------------------------------------------------------------------------
# Basic routes
# ----------------------------------------------------------------------------
@app.get("/")
def read_root():
    return {"name": "FoodieHungary API", "status": "ok"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Connected & Working"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = getattr(db, "name", "unknown")
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:50]}"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    return response


# ----------------------------------------------------------------------------
# Auth routes: register, login, me, change-password, upload avatar
# ----------------------------------------------------------------------------
class RegisterIn(BaseModel):
    name: str
    email: EmailStr
    password: str


@app.post("/auth/register", response_model=Token)
def register(payload: RegisterIn):
    if get_user_by_email(payload.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    user_doc = {
        "name": payload.name,
        "email": payload.email.lower(),
        "password_hash": hash_password(payload.password),
        "avatar_url": None,
    }
    create_document("user", user_doc)
    token = create_access_token({"sub": payload.email.lower()})
    return Token(access_token=token)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@app.post("/auth/login", response_model=Token)
def login(payload: LoginIn):
    user = get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token({"sub": payload.email.lower()})
    return Token(access_token=token)


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str


@app.post("/auth/change-password")
def change_password(payload: PasswordChangeIn, user: dict = Depends(get_current_user)):
    if not verify_password(payload.old_password, user.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="Old password incorrect")
    db.user.update_one({"_id": user["_id"]}, {"$set": {"password_hash": hash_password(payload.new_password)}})
    return {"status": "ok"}


@app.post("/auth/logout")
def logout():
    # Stateless JWT: client deletes token; endpoint exists for symmetry
    return {"status": "ok"}


@app.post("/auth/avatar")
async def upload_avatar(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    # Store a data URL as a quick MVP persistence
    content = await file.read()
    import base64
    data_url = f"data:{file.content_type};base64,{base64.b64encode(content).decode()}"
    db.user.update_one({"_id": user["_id"]}, {"$set": {"avatar_url": data_url}})
    return {"avatar_url": data_url}


# ----------------------------------------------------------------------------
# Restaurants: seed minimal data, search/filter, details
# ----------------------------------------------------------------------------
class RestaurantSeedIn(BaseModel):
    items: List[RestaurantSchema]


@app.post("/restaurants/seed")
def seed_restaurants(payload: RestaurantSeedIn):
    if db is None:
        raise HTTPException(status_code=500, detail="Database unavailable")
    # Upsert by name + city for idempotency
    for r in payload.items:
        db.restaurant.update_one(
            {"name": r.name, "city": r.city},
            {"$set": r.model_dump()},
            upsert=True,
        )
    return {"status": "ok"}


class RestaurantQuery(BaseModel):
    city: Optional[str] = None
    min_rating: Optional[float] = None
    max_price: Optional[int] = None
    cuisine: Optional[str] = None
    q: Optional[str] = None


@app.post("/restaurants/search")
def search_restaurants(filters: RestaurantQuery):
    query = {}
    if filters.city:
        query["city"] = {"$regex": f"^{filters.city}$", "$options": "i"}
    if filters.cuisine:
        query["cuisine"] = {"$regex": filters.cuisine, "$options": "i"}
    if filters.min_rating is not None:
        query["rating"] = {"$gte": float(filters.min_rating)}
    if filters.max_price is not None:
        query["price_level"] = {"$lte": int(filters.max_price)}
    if filters.q:
        query["$or"] = [
            {"name": {"$regex": filters.q, "$options": "i"}},
            {"address": {"$regex": filters.q, "$options": "i"}},
        ]
    items = get_documents("restaurant", query, limit=100)
    # Convert ObjectId to string
    from bson import ObjectId
    def normalize(doc: dict):
        d = dict(doc)
        if isinstance(d.get("_id"), ObjectId):
            d["id"] = str(d.pop("_id"))
        return d
    return {"items": [normalize(x) for x in items]}


@app.get("/restaurants/{restaurant_id}")
def get_restaurant(restaurant_id: str):
    from bson import ObjectId
    try:
        _id = ObjectId(restaurant_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    doc = db.restaurant.find_one({"_id": _id})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    doc["id"] = str(doc.pop("_id"))
    return doc
