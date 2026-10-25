"""Configured place matching and factual display, independent of presence.

A geofence identifies a registered place's vicinity and configured environment.
It does not measure whether the phone is inside a building.
"""
import math

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from typing import Literal


class RegisteredPlace(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=40)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: float = Field(default=200, ge=20, le=2000)
    environment: Literal["indoor", "outdoor", "unknown"] = "indoor"

    @field_validator("id", "name")
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("地点名称和标识须为非空单行文本")
        return value


def validate_places(value):
    if not isinstance(value, list):
        raise ValueError("落脚处须为列表")
    places = [RegisteredPlace.model_validate(item).model_dump() for item in value]
    if len({place["id"] for place in places}) != len(places):
        raise ValueError("落脚处标识重复")
    return places


def _distance(lat, lng, other_lat, other_lng):
    phi, other_phi = math.radians(lat), math.radians(other_lat)
    angle = (math.sin(math.radians(other_lat - lat) / 2) ** 2
             + math.cos(phi) * math.cos(other_phi) * math.sin(math.radians(other_lng - lng) / 2) ** 2)
    return 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, angle))))


def match_place(lat, lng, places):
    if lat is None or lng is None:
        return None
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    matches = []
    for item in places if isinstance(places, list) else []:
        try:
            place = RegisteredPlace.model_validate(item).model_dump()
        except (ValidationError, TypeError, ValueError):
            continue
        distance = _distance(lat, lng, place["lat"], place["lng"])
        if distance <= place["radius_m"]:
            matches.append((distance, place["id"], place))
    return min(matches, key=lambda item: (item[0], item[1]))[2] if matches else None


def location_label(category, name=None, environment=None):
    if category == "home":
        return "在家地点范围内"
    if category == "work":
        name = name or "公司"
    label = f"外出·{name}" if name else "外出·地点未识别"
    suffix = {"indoor": "室内落脚处", "outdoor": "户外地点"}.get(environment, "室内外未知")
    return f"{label}（{suffix}）"


def place_observation(name, environment, wifi_connected=None):
    """Concise place facts, with an explicitly inferred indoor interpretation.

    Callers admit only fresh GPS/place and WiFi observations. No SSID binding
    is required; WiFi supplements the registered environment, not presence.
    """
    if not name:
        return "室内外暂无确认依据"
    location = f"当前位于{name}范围内"
    if wifi_connected is True:
        location += "且连接WiFi"
        if environment == "indoor":
            return f"{location}，推测在{name}室内"
    elif wifi_connected is False:
        location += "且未连接WiFi"
    setting = {"indoor": "室内落脚处", "outdoor": "户外地点"}.get(environment, "室内外未知")
    return f"{location}，登记环境：{setting}"


def snapshot_place_observation(snapshot):
    """Shared current-place NL for consumers of the same timestamped snapshot."""
    if snapshot.wifi_is_home is True and not snapshot.is_stale("wifi", 300):
        return ""
    name = getattr(snapshot, "location_place_name", None)
    if not name or snapshot.is_stale("gps_place", 1800):
        return ""
    wifi = snapshot.wifi_connected if not snapshot.is_stale("wifi", 300) else None
    return place_observation(name, snapshot.location_environment, wifi)


def snapshot_location_label(snapshot):
    if snapshot.wifi_is_home is True and not snapshot.is_stale("wifi", 300):
        return "在家（家庭WiFi）"
    if not snapshot.is_stale("gps_place", 1800):
        return location_label(snapshot.location_category, snapshot.location_place_name, snapshot.location_environment)
    if not snapshot.is_stale("gps", 1800) and snapshot.location_category:
        return location_label(snapshot.location_category)
    if snapshot.wifi_is_home is False and not snapshot.is_stale("wifi", 300):
        return "外出（室内外未知）"
    return "位置暂不可确认"
