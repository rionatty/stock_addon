# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Turn the coordinates Sales Pro sends into a map on the document.

validate hook (wired in hooks.py) for Sales Invoice — and so credit
notes, which are the same doctype with is_return set — Sales Order and
Payment Entry.

The app sends a plain "latitude, longitude" string, which is the easy
thing for it to send and unreadable for anyone looking at the document.
Frappe's Geolocation field draws a map from GeoJSON, so this converts one
into the other on save.

The order matters and is the usual trap: people write "lat, lng" and
GeoJSON stores [longitude, latitude]. Getting it backwards puts a
Kampala sale in the Indian Ocean, and plausibly enough that nobody
notices — 0.3476, 32.5825 reversed is still a valid point on the map.

Nothing here can block a save. A rep with no signal, an older build of
the app, a coordinate typed in by hand — all just leave the map empty
rather than refusing the invoice.
"""

import json

import frappe

# Latitude runs -90..90, longitude -180..180. A pair outside that is not
# a location, and is usually the two the wrong way round.
MAX_LATITUDE = 90.0
MAX_LONGITUDE = 180.0


def set_location_map(doc, method=None):
    if not doc.meta.get_field("custom_location_map"):
        return                                  # fixtures not migrated yet

    point = _parse(doc.get("custom_location_coordinates"))
    doc.custom_location_map = _feature_collection(point) if point else None


def _parse(raw):
    """(latitude, longitude) from what the app sent, or None.

    Accepts the separators a phone might produce and ignores anything
    else — the value is typed by a device, not validated by one.
    """
    text = (raw or "").strip()
    if not text:
        return None

    for separator in (",", ";", " "):
        if separator in text:
            parts = [p.strip() for p in text.split(separator) if p.strip()]
            break
    else:
        return None

    if len(parts) != 2:
        return None

    try:
        latitude, longitude = float(parts[0]), float(parts[1])
    except ValueError:
        return None

    if abs(latitude) > MAX_LATITUDE or abs(longitude) > MAX_LONGITUDE:
        return None
    if latitude == 0 and longitude == 0:
        return None                             # null island: a phone with no fix

    return latitude, longitude


def _feature_collection(point):
    """GeoJSON a Geolocation field will render as a single marker.

    properties is present and empty on purpose: frappe's control reads
    properties.point_type to decide between a circle, a circle marker and
    a plain marker, so an absent properties key would raise on render
    while an empty one gives the marker.
    """
    latitude, longitude = point
    return json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Point",
                "coordinates": [longitude, latitude],   # GeoJSON is lng, lat
            },
        }],
    })
