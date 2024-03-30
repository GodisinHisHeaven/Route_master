import discord
from discord.ext import commands
import os
from wind import get_wind_direction_at_hour
from repo import download_and_parse_xlsx
import json
import random

# Retrieve the bot token from environment variables
token = os.getenv('DISCORD_BOT_TOKEN')

# Configure intents
intents = discord.Intents.default()  # Use default intents
intents.messages = True  # Ensure the bot can track messages
intents.message_content = True  # Allow access to message content

# Initialize the bot with the command prefix and intents
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.command()
@commands.has_permissions(administrator=True)
async def synccommands(ctx):
    await ctx.send("Syncing commands...")
    await bot.tree.sync()
    await ctx.send("Commands synced!")

# Define a command: when someone types "!ping", the bot will respond with "Pong!"
@bot.hybrid_command()
async def ping(ctx):
    """Respond with 'Pong!' when someone types '!ping'"""
    await ctx.send('Pong!')

@bot.hybrid_command()
async def wind_forecast(ctx, hour: int):
    """Get the wind direction forecast for a specific hour."""
    # Call your function with the specified hour
    direction, time = get_wind_direction_at_hour(hour)
    if direction:
        # Send back the forecast information
        await ctx.send(f"Wind direction at {time} will be {direction}.")
    else:
        # Handle cases where no forecast could be retrieved
        await ctx.send("Could not retrieve forecast data.")
        
@bot.hybrid_command()
async def pickroute(ctx, hour: int):
    """Recommends a cycling route based on the ideal wind direction for a given hour."""
    # Get the wind direction for the given hour
    direction, time = get_wind_direction_at_hour(hour)
    
    # Check if we got a valid direction
    if not direction:
        await ctx.send(f"Could not retrieve wind direction for {hour}:00.")
        return

    # Download and parse the Excel file to get routes as JSON
    json_data = download_and_parse_xlsx()
    # Load the data as a JSON object
    routes = json.loads(json_data)

    # Filter routes by the ideal wind direction and within the 20-50 mile range
    filtered_routes = [route for route in routes if route['Ideal Wind Direction'] == direction and 20 <= route['Distance (mi)'] <= 50]
    
    # If there are matching routes, pick one at random
    if filtered_routes:
        selected_route = random.choice(filtered_routes)
        # Create a response message with the route's details
        response = (
            f"Recommended Route: {selected_route['Route name']}\n"
            f"Distance: {selected_route['Distance (mi)']} miles\n"
            f"Ideal Wind Direction: {selected_route['Ideal Wind Direction']}\n"
            f"Link: {selected_route['Ride with GPS link']}\n"
            f"Notes: {selected_route.get('Notes', 'No additional notes.')}"
        )
        await ctx.send(response)
    else:
        await ctx.send("No suitable routes found for the specified wind direction and distance range.")

# Run the bot with the token
bot.run(token)
