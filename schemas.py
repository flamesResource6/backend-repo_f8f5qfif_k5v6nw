"""
Database Schemas for FoodieHungary MVP

Each Pydantic model represents a collection in MongoDB.
Model name lowercased is used as the collection name.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr


class User(BaseModel):
    """
    Users collection schema
    Collection name: "user"
    """
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Email address")
    password_hash: str = Field(..., description="BCrypt password hash")
    avatar_url: Optional[str] = Field(None, description="Data URL or external URL for profile image")


class Restaurant(BaseModel):
    """
    Restaurants collection schema
    Collection name: "restaurant"
    """
    name: str
    city: str
    address: str
    cuisine: str = Field(..., description="Cuisine type, e.g., Hungarian, Italian")
    price_level: int = Field(..., ge=1, le=4, description="1=cheap, 4=expensive")
    rating: float = Field(..., ge=0, le=5)
    lat: Optional[float] = Field(None, description="Latitude for map display")
    lng: Optional[float] = Field(None, description="Longitude for map display")
    phone: Optional[str] = None
    website: Optional[str] = None
