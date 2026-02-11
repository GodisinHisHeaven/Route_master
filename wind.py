import requests
import os
import time
from datetime import datetime, timedelta

# Cache forecast responses to avoid hammering the API.
_FORECAST_CACHE = {"ts": 0, "data": None}
_FORECAST_TTL_SECONDS = int(os.getenv("OPENWEATHER_CACHE_TTL_SECONDS", "600"))
_REQUEST_TIMEOUT_SECONDS = float(os.getenv("OPENWEATHER_TIMEOUT_SECONDS", "10"))

def _get_forecast_json(url: str):
    now = time.time()
    if _FORECAST_CACHE["data"] is not None and (now - _FORECAST_CACHE["ts"]) < _FORECAST_TTL_SECONDS:
        return _FORECAST_CACHE["data"]

    resp = requests.get(url, timeout=_REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    _FORECAST_CACHE["ts"] = now
    _FORECAST_CACHE["data"] = data
    return data

def get_wind_direction_at_hour(target_hour):
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        print("API key not found. Please set OPENWEATHER_API_KEY environment variable.")
        return None

    lat = float(os.getenv("LAT", "40.102121327005165"))
    lon = float(os.getenv("LON", "-88.22681926647813"))
    url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={api_key}"

    try:
        response = _get_forecast_json(url)
        # Find the closest forecast time to the target_hour, considering the data is in 3-hour intervals
        now = datetime.now()
        target_datetime = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        if target_datetime < now:  # If target hour is in the past today, move to tomorrow
            target_datetime += timedelta(days=1)

        for item in response['list']:
            forecast_time = datetime.fromtimestamp(item['dt'])
            if forecast_time >= target_datetime:
                wind_deg = item['wind']['deg']
                cardinal_directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
                direction = round(wind_deg / 45) % 8
                return cardinal_directions[direction], forecast_time.strftime('%Y-%m-%d %H:%M:%S')
        return None, "Forecast not available for the requested time."
    except requests.RequestException as e:
        print(f"Request failed: {e}")
        return None, None
    except KeyError as e:
        print(f"Unexpected response structure: {e}")
        return None, None


# direction, time = get_wind_direction_at_hour(20)
# if direction:
#     print(f"Wind direction at {time} will be {direction}.")
# else:
#     print("Could not retrieve forecast data.")
