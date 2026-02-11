import discord
from discord.ext import commands
import os

# Optional: load local .env in dev environments.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from wind import get_wind_direction_at_hour
from repo import download_and_parse_xlsx
import json
import random
import logging
from collections import defaultdict

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Retrieve the bot token from environment variables
# IMPORTANT: do not hard-code secrets in source code.
token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("Missing DISCORD_TOKEN. Set it in environment (or .env).")

# Configure intents
intents = discord.Intents.default()  # Use default intents
intents.messages = True  # Ensure the bot can track messages
intents.message_content = True  # Allow access to message content

# Initialize the bot with the command prefix and intents
bot = commands.Bot(command_prefix="!", intents=intents)

# Load request count data from file
request_counts_file = "request_counts.json"
if os.path.exists(request_counts_file):
    with open(request_counts_file, "r") as f:
        request_counts = json.load(f)
else:
    request_counts = {}

# Ensure the request_counts is a defaultdict
request_counts = defaultdict(int, request_counts)


@bot.event
async def on_ready():
    await bot.tree.sync()
    logger.info("Commands synced with Discord.")
    print("Commands synced with Discord.")


@bot.command()
@commands.has_permissions(administrator=True)
async def synccommands(ctx):
    logger.info("Syncing commands initiated by %s", ctx.author)
    await ctx.send("Syncing commands...")
    await bot.tree.sync()
    await ctx.send("Commands synced!")
    logger.info("Commands successfully synced.")


# Define a command: when someone types "!ping", the bot will respond with "Pong!"
@bot.hybrid_command()
async def ping(ctx):
    """Respond with 'Pong!' when someone types '!ping'"""
    logger.info("Ping command invoked by %s", ctx.author)
    await ctx.send("Pong!")


@bot.hybrid_command()
async def wind_forecast(ctx, hour: int):
    """Get the wind direction forecast for a specific hour."""
    logger.info("wind_forecast command invoked by %s with hour: %d", ctx.author, hour)
    # Call your function with the specified hour
    result = get_wind_direction_at_hour(hour)
    if result:
        direction, time = result
        # Send back the forecast information
        await ctx.send(f"Wind direction at {time} will be {direction}.")
        logger.info("Wind direction at %s will be %s", time, direction)
    else:
        # Handle cases where no forecast could be retrieved
        await ctx.send("Could not retrieve forecast data.")
        logger.warning("Could not retrieve wind direction for hour: %d", hour)


@bot.hybrid_command()
async def pickroute(ctx, hour: int, mile: float):
    """Recommend a cycling route based on the wind direction and closest distance."""
    user_name = str(ctx.author)
    request_counts[user_name] += 1
    logger.info(
        "pickroute command invoked by %s with hour: %d and mile: %.2f",
        ctx.author,
        hour,
        mile,
    )

    # Save the updated request counts to the file
    with open(request_counts_file, "w") as f:
        json.dump(request_counts, f)

    # Get the wind direction for the given hour
    result = get_wind_direction_at_hour(hour)

    # Check if we got a valid direction
    if not result:
        await ctx.send(f"Could not retrieve wind direction for {hour}:00.")
        logger.warning("Could not retrieve wind direction for hour: %d", hour)
        return

    direction, _ = result
    logger.info("Retrieved wind direction: %s for hour: %d", direction, hour)

    # Download and parse the Excel file to get routes as JSON
    json_data = download_and_parse_xlsx()
    if not json_data:
        await ctx.send("Could not retrieve route data.")
        logger.error("Failed to retrieve route data from download_and_parse_xlsx()")
        return

    # Load the data as a JSON object
    try:
        routes = json.loads(json_data)
    except json.JSONDecodeError:
        await ctx.send("Error decoding route data.")
        logger.error("JSON decoding error for route data.")
        return

    # Check if routes data is a list
    if not isinstance(routes, list):
        await ctx.send("Route data is not in the expected format.")
        logger.error("Route data is not in the expected format.")
        return

    # Filter routes by the ideal wind direction
    filtered_routes = [
        route for route in routes if route.get("Ideal Wind Direction") == direction
    ]
    logger.info(
        "Found %d routes matching the wind direction %s",
        len(filtered_routes),
        direction,
    )

    # If there are matching routes, find the one closest to the specified mile distance
    if filtered_routes:
        closest_route = min(
            filtered_routes, key=lambda route: abs(route.get("Distance (mi)", 0) - mile)
        )
        # Ensure 'Notes' is not None before attempting to slice
        notes = closest_route.get("Notes", "No additional notes.")
        if notes is None:
            notes = "No additional notes."
        # Create a response message with the route's details
        response = (
            f"Recommended Route: {closest_route.get('Route name', 'N/A')}\n"
            f"Distance: {closest_route.get('Distance (mi)', 'N/A')} miles\n"
            f"Ideal Wind Direction: {closest_route.get('Ideal Wind Direction', 'N/A')}\n"
            f"Link: {closest_route.get('Ride with GPS link', 'N/A')}\n"
            f"Notes: {notes[:90]}"
        )
        await ctx.send(response)
        logger.info("Recommended route: %s", closest_route.get("Route name", "N/A"))
    else:
        await ctx.send("No suitable routes found for the specified wind direction.")
        logger.warning("No suitable routes found for wind direction %s", direction)


# Run the bot with the token
logger.info("Starting bot...")
bot.run(token)
