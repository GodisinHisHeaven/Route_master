import requests
import openpyxl
from io import BytesIO
import json
import os
import time

# Allow overriding via env.
url = os.getenv(
    "ROUTES_XLSX_URL",
    "https://docs.google.com/spreadsheets/d/1Pzd0-8KqRsZZgdXCJQJ8Nq1TAmz-TEGusOZKlgG7RDk/export?format=xlsx",
)

# Cache parsed routes to avoid downloading/parsing the sheet on every command.
_ROUTES_CACHE = {"ts": 0, "json": None}
_ROUTES_TTL_SECONDS = int(os.getenv("ROUTES_CACHE_TTL_SECONDS", "1800"))  # 30 min default
_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ROUTES_TIMEOUT_SECONDS", "15"))
_CACHE_PATH = os.getenv("ROUTES_CACHE_PATH", "routes_cache.json")


def _load_disk_cache():
    try:
        if os.path.exists(_CACHE_PATH):
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                return f.read()
    except Exception:
        return None
    return None


def _save_disk_cache(text: str):
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def download_and_parse_xlsx():
    now = time.time()
    if _ROUTES_CACHE["json"] is not None and (now - _ROUTES_CACHE["ts"]) < _ROUTES_TTL_SECONDS:
        return _ROUTES_CACHE["json"]

    filtered_data = []  # List to hold dictionaries of filtered data
    # Download the file
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except Exception:
        # Fallback to disk cache when network fails.
        cached = _load_disk_cache()
        if cached:
            _ROUTES_CACHE["ts"] = now
            _ROUTES_CACHE["json"] = cached
            return cached
        return json.dumps([], ensure_ascii=False)

    if resp.status_code == 200:
        # Open the downloaded .xlsx file
        workbook = openpyxl.load_workbook(
            filename=BytesIO(resp.content), data_only=True
        )
        sheet = workbook.active  # Assumes data is in the first sheet

        # Initialize variables to determine if we are in a data section
        in_data_section = False
        headers = []

        # Iterate over rows
        for row in sheet.iter_rows(values_only=True):
            # Skip empty rows
            if not any(row):
                in_data_section = False
                continue

            # If the first cell in the row is 'Route name', this is a header
            if row[0] == "Route name":
                in_data_section = True
                headers = [cell for cell in row if cell]  # Capture the headers
                continue

            # Skip rows if not in data section or if there are no headers
            if not in_data_section or not headers:
                continue

            # Create a dictionary for each row with the header as the key
            row_data = dict(zip(headers, row))

            # Get the distance value and ensure it is a number
            distance = row_data.get("Distance (mi)")
            if isinstance(distance, (int, float)) and 10 <= distance:
                filtered_data.append(row_data)  # Add the row's data to the list

    # Convert the list of dictionaries to JSON
    json_data = json.dumps(filtered_data, ensure_ascii=False)

    _ROUTES_CACHE["ts"] = now
    _ROUTES_CACHE["json"] = json_data
    _save_disk_cache(json_data)

    return json_data


# # Replace 'url' with your direct download link
# json_result = download_and_parse_xlsx()
# print(json_result)
