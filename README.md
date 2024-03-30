# Route_master

## How to Use the Bot in Discord

/ping: Type this command, and the bot will respond with "Pong!" It's a simple way to check if the bot is online and responsive.

/windforecast <hour>: Replace <hour> with the hour you're interested in, using 24-hour format (for example, 14 for 2 PM). The bot will give you the wind direction forecast for that hour. Note that wind direction data can vary and is not always consistent; the bot provides the best available forecast. If you enter an hour that has already passed for the current day, the bot will consider it for the next day.

/pickroute <hour>: Use this command followed by an hour in 24-hour format to get a cycling route recommendation based on the ideal wind direction for that hour. Remember, the wind direction data may not be consistent, so the route suggested is based on the best forecast available at the time. Similar to /windforecast, if the specified hour has already passed, the bot will provide a recommendation considering the hour for the next day.