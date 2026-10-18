"""Pure interpretation of fresh, host-supplied place observations; no registry or network."""

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
