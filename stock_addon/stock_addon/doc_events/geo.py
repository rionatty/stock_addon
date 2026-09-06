# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Turn the coordinates Sales Pro sends into a map on the document.

validate hook (wired in hooks.py) for Customer, Sales Invoice — and so
credit notes, which are the same doctype with is_return set — Sales Order
and Payment Entry.

The app sends a plain "latitude, longitude" string, which is the easy
thing for a phone to send and unreadable for anyone looking at the
document. Frappe's Geolocation field draws a map from GeoJSON, so this
converts one into the other on save.

On a transaction it draws BOTH ends: where the rep was standing, where
the customer is registered, and a line between them. The line is the
point of the exercise — an invoice booked miles from the customer it is
made out to shows up as a long line without anybody reading a number.
The distance is recorded alongside it, because a line answers that
question one document at a time and a number answers it across a
thousand.

The coordinate order is the usual trap: people write "lat, lng" and
GeoJSON stores [longitude, latitude]. Getting it backwards puts a Kampala
sale in the Indian Ocean, plausibly enough that nobody notices — 0.3476,
32.5825 reversed is still a valid point on the map.

Nothing here can block a save. A rep with no signal, an older build of
the app, a customer registered before any of this existed — each just
leaves the map empty rather than refusing the document.
"""

import json
from math import asin, cos, radians, sin, sqrt

import frappe

# Latitude runs -90..90, longitude -180..180. A pair outside that is not
# a location, and is usually the two the wrong way round.
MAX_LATITUDE = 90.0
MAX_LONGITUDE = 180.0

EARTH_RADIUS_KM = 6371.0


def set_location_map(doc, method=None):
    if not doc.meta.get_field("custom_location_map"):
        return                                  # fixtures not migrated yet

    here = _parse(doc.get("custom_location_coordinates"))
    there = _customer_point(doc)

    features = []
    if here:
        features.append(_point_feature(here, "booked"))
    if there:
        features.append(_point_feature(there, "customer"))
    if here and there:
        features.append(_line_feature(here, there))

    doc.custom_location_map = _collection(features) if features else None

    if doc.meta.get_field("custom_location_distance"):
        doc.custom_location_distance = _distance_km(here, there) if (here and there) else 0


def _customer_point(doc):
    """Where the customer this document is for was registered.

    Only for transactions — a Customer has no customer of its own, and
    asking would draw a line from a point to itself.
    """
    if doc.doctype == "Customer":
        return None

    customer = (doc.get("customer") or "").strip()
    if not customer and doc.get("party_type") == "Customer":
        customer = (doc.get("party") or "").strip()      # Payment Entry
    if not customer:
        return None

    if not frappe.get_meta("Customer").get_field("custom_location_coordinates"):
        return None
    return _parse(frappe.db.get_value("Customer", customer, "custom_location_coordinates"))


# ------------------------------------------------------------ geometry
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


def _point_feature(point, role):
    latitude, longitude = point
    return {
        "type": "Feature",
        # frappe's control reads properties.point_type to choose between a
        # circle, a circle marker and a plain marker, so properties must
        # exist. "role" is ours, and it ignores what it does not know.
        "properties": {"role": role},
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
    }


def _line_feature(here, there):
    return {
        "type": "Feature",
        "properties": {"role": "gap"},
        "geometry": {
            "type": "LineString",
            "coordinates": [[here[1], here[0]], [there[1], there[0]]],
        },
    }


def _collection(features):
    return json.dumps({"type": "FeatureCollection", "features": features})


def _distance_km(here, there):
    """Great-circle distance, rounded to metres.

    Haversine rather than flat trigonometry: cheap, and it does not drift
    the way a naive degrees-to-kilometres conversion does away from the
    equator.
    """
    lat1, lon1 = radians(here[0]), radians(here[1])
    lat2, lon2 = radians(there[0]), radians(there[1])
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return round(2 * EARTH_RADIUS_KM * asin(sqrt(a)), 3)
