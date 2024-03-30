import requests
import os
from datetime import datetime, timedelta

def get_wind_direction_at_hour(target_hour):
    api_key = os.getenv('OPENWEATHER_API_KEY')
    if not api_key:
        print("API key not found. Please set the OPENWEATHER_API_KEY environment variable.")
        return None

    lat = 40.102121327005165
    lon = -88.22681926647813
    url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={api_key}"

    try:
        response = requests.get(url).json()
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
