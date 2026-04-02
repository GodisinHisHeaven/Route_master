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
from strava import summarize_activities, get_athlete_activities_by_token
from strava_oauth import get_auth_url, get_user_token, is_user_connected, start_oauth_server
from training_plan import generate_training_plan
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
    # Start OAuth server for Strava authorization
    oauth_port = int(os.getenv("OAUTH_PORT", "5050"))
    start_oauth_server(port=oauth_port)
    logger.info("OAuth server started on port %d", oauth_port)

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

    # Filter routes by the ideal wind direction.
    # The sheet sometimes contains values like "N/NE", "E or SE", or trailing spaces.
    # We normalize by extracting any cardinal tokens and matching against the forecast direction.
    import re

    def _dir_tokens(val) -> set:
        if val is None:
            return set()
        s = str(val).upper().strip()
        return set(re.findall(r"\b[NSWE]{1,2}\b", s))

    filtered_routes = [route for route in routes if direction in _dir_tokens(route.get("Ideal Wind Direction"))]

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


@bot.hybrid_command()
async def connect_strava(ctx):
    """Link your Strava account to get personalized training plans."""
    user_id = str(ctx.author.id)

    if is_user_connected(user_id):
        await ctx.send("✅ Your Strava account is already connected! Use `/trainme` to get a training plan.", ephemeral=True)
        return

    auth_url = get_auth_url(user_id)
    await ctx.send(
        f"🔗 **Connect your Strava account**\n"
        f"Click the link below to authorize Route Master to read your activities:\n"
        f"{auth_url}\n\n"
        f"After authorizing, come back and use `/trainme` to get your plan.",
        ephemeral=True,
    )


@bot.hybrid_command()
async def disconnect_strava(ctx):
    """Unlink your Strava account from Route Master."""
    user_id = str(ctx.author.id)
    if not is_user_connected(user_id):
        await ctx.send("You don't have a Strava account connected.", ephemeral=True)
        return
    from strava_oauth import _token_store, _save_store
    _token_store.pop(user_id, None)
    _save_store()
    await ctx.send("✅ Strava account disconnected. Use `/connect_strava` to reconnect.", ephemeral=True)


@bot.hybrid_command()
async def trainme(ctx, hours: float = 0.0, *, goals: str = ""):
    """Generate a personalized 7-day training plan based on your Strava data.

    Args:
      hours: Total training hours for next week (e.g. 8). 0 = auto.
      goals: Your training goals (e.g. "preparing for a century")
    """
    user_id = str(ctx.author.id)
    logger.info("trainme command invoked by %s (hours: %s, goals: %s)", ctx.author, hours, goals)

    # Check if user has connected Strava
    if not is_user_connected(user_id):
        auth_url = get_auth_url(user_id)
        await ctx.send(
            f"👋 You haven't connected your Strava account yet!\n"
            f"Click here to authorize: {auth_url}\n\n"
            f"After authorizing, run `/trainme` again.",
            ephemeral=True,
        )
        return

    # Get user's token
    user_data = get_user_token(user_id)
    if not user_data:
        auth_url = get_auth_url(user_id)
        await ctx.send(
            f"⚠️ Your Strava token has expired. Please re-authorize:\n{auth_url}",
            ephemeral=True,
        )
        return

    await ctx.send("🏋️ Fetching your Strava activities and generating a training plan... (this may take ~30s)")

    try:
        # Fetch activities
        activities = get_athlete_activities_by_token(
            access_token=user_data["access_token"],
            days=30,
        )

        if not activities:
            await ctx.send("📭 No activities found in the past 30 days. Get out there and ride! 🚴")
            return

        # Summarize
        summary = summarize_activities(activities)
        athlete_name = user_data.get("athlete_name", str(ctx.author))

        # Generate plan
        raw_plan = generate_training_plan(
            activity_summary=summary,
            user_request=goals,
            athlete_name=athlete_name,
            target_hours=hours if hours > 0 else None,
        )

        # Try to parse as JSON and render embed
        plan_data = None
        try:
            # Strip markdown code fences if present
            cleaned = raw_plan.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
            plan_data = json.loads(cleaned)
        except (json.JSONDecodeError, Exception) as parse_err:
            logger.warning("Failed to parse plan as JSON: %s", parse_err)

        if plan_data and "days" in plan_data:
            # Render as Discord embed
            embed = discord.Embed(
                title=f"📋 Training Plan for {athlete_name}",
                description=(
                    f"_Based on {summary['total_activities']} activities in the past 30 days_\n\n"
                    f"**{plan_data.get('summary', '')}**\n"
                    f"⏱️ Total: ~{plan_data.get('total_hours', '?')}h"
                ),
                color=0xFC4C02,  # Strava orange
            )
            for day_info in plan_data["days"]:
                emoji = day_info.get("emoji", "📋")
                day_name = day_info.get("day", "?")
                activity = day_info.get("activity", "")
                desc = day_info.get("description", "")
                dur = day_info.get("duration_min", "")
                dur_str = f" ({dur} min)" if dur else ""
                embed.add_field(
                    name=f"{emoji} {day_name} — {activity}{dur_str}",
                    value=desc or "\u200b",
                    inline=False,
                )
            embed.set_footer(text="Powered by Strava + Kimi K2.5 • /trainme")
            await ctx.send(embed=embed)
        else:
            # Fallback: send as plain text
            header = f"📋 **Training Plan for {athlete_name}**\n"
            header += f"_Based on {summary['total_activities']} activities in the past 30 days_\n\n"
            full_msg = header + raw_plan
            if len(full_msg) <= 2000:
                await ctx.send(full_msg)
            else:
                await ctx.send(header)
                chunks = [raw_plan[i:i+1900] for i in range(0, len(raw_plan), 1900)]
                for chunk in chunks:
                    await ctx.send(chunk)

        logger.info("Training plan generated for %s (%d activities)", athlete_name, len(activities))

    except Exception as e:
        logger.error("trainme failed for %s: %s", ctx.author, e)
        await ctx.send(f"⚠️ Something went wrong: {e}")


@bot.hybrid_command()
async def mystats(ctx):
    """Show a summary of your recent Strava training data."""
    user_id = str(ctx.author.id)

    if not is_user_connected(user_id):
        await ctx.send("You haven't connected Strava yet. Use `/connect_strava` first.")
        return

    user_data = get_user_token(user_id)
    if not user_data:
        await ctx.send("⚠️ Token expired. Use `/connect_strava` to re-authorize.")
        return

    try:
        activities = get_athlete_activities_by_token(user_data["access_token"], days=30)
        if not activities:
            await ctx.send("📭 No activities in the past 30 days.")
            return

        summary = summarize_activities(activities)
        lines = [f"📊 **{user_data.get('athlete_name', 'Your')} — Last 30 Days**\n"]
        lines.append(f"Total activities: {summary['total_activities']}")

        for sport, data in summary["by_type"].items():
            lines.append(f"• **{sport}**: {data['count']}x, {data['total_miles']} mi, {data['total_minutes']} min")

        lines.append(f"\n**Weekly volume:**")
        for week, data in sorted(summary["weekly_breakdown"].items()):
            lines.append(f"• {week}: {data['miles']} mi ({data['count']} activities)")

        await ctx.send("\n".join(lines))
    except Exception as e:
        await ctx.send(f"⚠️ Error: {e}")


# Run the bot with the token
logger.info("Starting bot...")
bot.run(token)
