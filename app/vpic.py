"""Decodes VINs against NHTSA's free vPIC API (no key required) and turns
the response into suggested values for our own equipment fields. Pure
lookup + mapping -- never touches the database itself, so it's safe to call
repeatedly for a review screen before anything is written.

https://vpic.nhtsa.dot.gov/api/
"""
import requests

SINGLE_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"
BATCH_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVINValuesBatch/"
BATCH_CHUNK_SIZE = 50  # vPIC's own stated limit per batch call

SINGLE_TIMEOUT = 10
BATCH_TIMEOUT = 30

# vPIC's FuelTypePrimary free-text values, lowercased, mapped onto our own
# fuel_type enum (gasoline/diesel/propane/electric/other). Anything not
# listed here comes back with no suggestion at all -- left for a human to
# pick rather than guessed into "other".
FUEL_TYPE_MAP = {
    "gasoline": "gasoline",
    "gasoline/e85": "gasoline",
    "flexible fuel vehicle (ffv)": "gasoline",
    "diesel": "diesel",
    "propane": "propane",
    "liquefied petroleum gas (lpg)": "propane",
    "electric": "electric",
    "compressed natural gas (cng)": "other",
    "hydrogen": "other",
    "hybrid": "other",
}


def is_valid_vin(value):
    """Only a plain length check, per the app's own rule -- not the full VIN
    check-digit algorithm. vPIC itself reports ErrorCode/ErrorText for a
    VIN that doesn't check out, which is surfaced to the user instead."""
    return bool(value) and len(value.strip()) == 17


def _get(url, **kwargs):
    try:
        resp = requests.get(url, timeout=SINGLE_TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp.json(), None
    except requests.RequestException as e:
        return None, str(e)


def _post(url, **kwargs):
    try:
        resp = requests.post(url, timeout=BATCH_TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp.json(), None
    except requests.RequestException as e:
        return None, str(e)


def _format_engine_size(displacement_l):
    if not displacement_l:
        return None
    try:
        return f"{round(float(displacement_l), 1)}L"
    except ValueError:
        return None


# vPIC's own EngineConfiguration values are verbose ("V-Shaped", "W-Shaped")
# -- normalize the common ones to the short prefix people actually write.
# Anything not listed here is used as-is rather than guessed at.
CONFIG_PREFIX_MAP = {
    "v-shaped": "V",
    "w-shaped": "W",
    "inline": "Inline",
    "in-line": "Inline",
    "opposed": "Flat",
    "flat": "Flat",
    "rotary": "Rotary",
}


def _format_engine_configuration(configuration, cylinders):
    """Builds a string like "V8" from vPIC's own EngineConfiguration ("V-
    Shaped", "Inline", "Rotary", "W-Shaped", ...) and EngineCylinders ("8",
    "6", ...) -- never fabricates a configuration vPIC didn't actually
    report. Falls back to whichever of the two is present if only one
    came back (e.g. a rotary engine with no meaningful cylinder count)."""
    configuration = (configuration or "").strip()
    cylinders = (cylinders or "").strip()
    prefix = CONFIG_PREFIX_MAP.get(configuration.lower(), configuration)
    if prefix and cylinders:
        # "V" + "8" -> "V8"; "Inline" + "6" -> "Inline-6".
        sep = "" if len(prefix) <= 2 else "-"
        return f"{prefix}{sep}{cylinders}"
    return prefix or (f"{cylinders}-cylinder" if cylinders else None)


def _extract_fields(result):
    """One vPIC result row -> our suggestion dict. `result` is the flat
    dict vPIC returns per VIN (same shape for single or batch lookups)."""
    error_code = (result.get("ErrorCode") or "").strip()
    error_text = (result.get("ErrorText") or "").strip()

    fuel_raw = (result.get("FuelTypePrimary") or "").strip()
    fuel_suggestion = FUEL_TYPE_MAP.get(fuel_raw.lower())

    year_raw = (result.get("ModelYear") or "").strip()
    suggestions = {
        "year": int(year_raw) if year_raw.isdigit() else None,
        "make": result.get("Make") or None,
        "model": result.get("Model") or None,
        "engine_size": _format_engine_size(result.get("DisplacementL")),
        "engine_configuration": _format_engine_configuration(
            result.get("EngineConfiguration"), result.get("EngineCylinders")
        ),
        "factory_gvw": result.get("GVWR") or None,
        "fuel_type": fuel_suggestion,
    }
    # Drop anything vPIC came back empty on, so the review screen only ever
    # shows a suggestion where there's actually a value to offer.
    suggestions = {k: v for k, v in suggestions.items() if v}

    return {
        "suggestions": suggestions,
        "fuel_type_raw": fuel_raw or None,
        "error_code": error_code,
        "error_text": error_text,
        # vPIC uses "0" for a clean decode; anything else is worth a human
        # glancing at, but the data (if any) is still shown, not discarded.
        "has_error": bool(error_code) and error_code != "0",
    }


def decode_vin(vin):
    """Single VIN -> {ok, network_error, suggestions, fuel_type_raw,
    error_code, error_text, has_error}. Never raises."""
    data, network_error = _get(SINGLE_URL.format(vin=vin))
    if network_error:
        return {"ok": False, "network_error": network_error}

    results = (data or {}).get("Results") or []
    if not results:
        return {"ok": False, "network_error": "vPIC returned no data for this VIN."}

    parsed = _extract_fields(results[0])
    return {"ok": True, "network_error": None, **parsed}


def decode_vins_batch(vins):
    """Many VINs -> {vin: decode_vin()-shaped dict}. Chunks into groups of
    at most 50 (vPIC's own limit) and merges the results back together;
    a network failure on one chunk only marks that chunk's VINs as failed,
    it doesn't lose results already fetched from an earlier chunk."""
    out = {}
    vins = list(dict.fromkeys(vins))  # de-dupe, preserve order
    for i in range(0, len(vins), BATCH_CHUNK_SIZE):
        chunk = vins[i:i + BATCH_CHUNK_SIZE]
        data, network_error = _post(BATCH_URL, data={"format": "json", "data": ";".join(chunk)})

        if network_error:
            for vin in chunk:
                out[vin] = {"ok": False, "network_error": network_error}
            continue

        results = (data or {}).get("Results") or []
        results_by_vin = {(r.get("VIN") or "").strip(): r for r in results}
        for vin in chunk:
            result = results_by_vin.get(vin)
            if result is None:
                out[vin] = {"ok": False, "network_error": "vPIC returned no data for this VIN."}
                continue
            out[vin] = {"ok": True, "network_error": None, **_extract_fields(result)}

    return out
