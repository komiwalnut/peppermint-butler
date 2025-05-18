import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
import json
import os
import re
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import dotenv
from typing import List, Optional
import logging
from logging.handlers import RotatingFileHandler

intents = discord.Intents.default()
intents.dm_messages = True

dotenv.load_dotenv()

logger = logging.getLogger('peppermint_butler')
logger.setLevel(logging.INFO)
handler = RotatingFileHandler('peppermint_butler.log', maxBytes=5 * 1024 * 1024, backupCount=5)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

bot = commands.Bot(command_prefix=None, intents=intents)
scheduler = AsyncIOScheduler()

DATA_FILE = "user_reminders.json"
THUMBNAIL_URL = "https://img.notionusercontent.com/s3/prod-files-secure%2F74b0bc64-8301-4be7-a968-d92c5cbce991%2Fd9776d11-124a-424c-be60-4350f1baab70%2FJoy.png/size/w=1420?exp=1746945395&sig=jnidwmYZuc40hXjoAvdj06VgKaOfq_MtejPUVcFEF9o&id=104c48ae-3fdd-8042-a5f8-fbbd4c558ac5&table=block"

QUESTS = {
    "General Daily Check-In": "https://discord.com/channels/410537146672349205/1361578026256564255/1362069080022061287",
    "Collectors Daily Check-In": "https://discord.com/channels/410537146672349205/1361578026256564255/1362760752435433482",
    "Land Owners Daily Check-In": "https://discord.com/channels/410537146672349205/1361578026256564255/1362761587810893955"
}


def normalize_timezone(tz_string):
    if tz_string in pytz.all_timezones:
        return tz_string

    match = re.match(r'^(GMT|UTC)([+-])(\d{1,2})(?::(\d{2}))?$', tz_string, re.IGNORECASE)
    if match:
        prefix, sign, hours, minutes = match.groups()
        hours = int(hours)
        minutes = int(minutes or 0)

        inverse_sign = "+" if sign == "-" else "-"

        if minutes == 0:
            return f"Etc/GMT{inverse_sign}{hours}"

        total_minutes = hours * 60 + (minutes if sign == "+" else -minutes)
        return pytz.FixedOffset(total_minutes * (-1))

    return tz_string


def load_user_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse {DATA_FILE}, it may be corrupted")
            return {}
        except Exception as err:
            logger.error(f"Error loading {DATA_FILE}: {str(err)}")
            return {}
    return {}


def save_user_data(data):
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as err:
        logger.error(f"Error saving to {DATA_FILE}: {str(err)}")


@bot.event
async def on_ready():
    logger.info(f'Bot is ready! Logged in as {bot.user}')
    setup_scheduler()
    scheduler.start()
    logger.info("Syncing slash commands...")
    await bot.tree.sync()
    logger.info("Slash commands synced!")


def setup_scheduler():
    user_data = load_user_data()
    for user_id, user_info in user_data.items():
        reminder_time = user_info.get('reminder_time')
        timezone_str = user_info.get('timezone')
        timezone_normalized = user_info.get('timezone_normalized', timezone_str)

        try:
            timezone_to_use = timezone_normalized if 'timezone_normalized' in user_info else normalize_timezone(timezone_str)

            tz = pytz.timezone(timezone_to_use) if isinstance(timezone_to_use, str) else timezone_to_use

            hour, minute = map(int, reminder_time.split(':'))

            trigger = CronTrigger(
                hour=hour,
                minute=minute,
                timezone=tz
            )

            scheduler.add_job(
                send_reminder,
                trigger=trigger,
                id=f"remind_{user_id}",
                replace_existing=True,
                args=[user_id]
            )

            logger.info(f"Scheduled reminder for user {user_id} at {reminder_time} {timezone_str}")
        except Exception as err:
            logger.error(f"Error scheduling reminder for user {user_id}: {str(err)}")

    logger.info(f"Scheduled reminders for {len(user_data)} users")


async def timezone_autocomplete(_interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    common_offsets = [
                         f"UTC+{i}" for i in range(0, 13)
                     ] + [
                         f"UTC-{i}" for i in range(1, 13)
                     ] + [
                         f"GMT+{i}" for i in range(0, 13)
                     ] + [
                         f"GMT-{i}" for i in range(1, 13)
                     ]

    if not current:
        return [app_commands.Choice(name=tz, value=tz) for tz in (pytz.common_timezones[:15] + common_offsets[:10])[:25]]

    if current.upper().startswith(("UTC", "GMT")):
        matching_offsets = [tz for tz in common_offsets if current.upper() in tz.upper()]
        matching_timezones = [tz for tz in pytz.common_timezones if current.lower() in tz.lower()]

        combined_results = matching_offsets + matching_timezones
        return [app_commands.Choice(name=tz, value=tz) for tz in combined_results[:25]]

    matching_timezones = [tz for tz in pytz.common_timezones if current.lower() in tz.lower()]
    return [app_commands.Choice(name=tz, value=tz) for tz in matching_timezones[:25]]


@bot.tree.command(name="setreminder", description="Set your daily quest reminder time with your timezone")
@app_commands.describe(
    set_time="Time in HH:MM format (24-hour, e.g. 14:00 for 2 PM)",
    timezone="Your timezone (e.g. UTC, US/Pacific, Asia/Manila, UTC+8, GMT-5, etc.)"
)
@app_commands.autocomplete(timezone=timezone_autocomplete)
async def set_reminder(
        interaction: discord.Interaction,
        set_time: str,
        timezone: str
):
    try:
        hour, minute = map(int, set_time.split(':'))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError("Invalid time range")

        try:
            user_timezone = normalize_timezone(timezone)
            tz = pytz.timezone(user_timezone) if isinstance(user_timezone, str) else user_timezone
        except pytz.exceptions.UnknownTimeZoneError:
            raise ValueError(f"Unknown timezone: {timezone}. Please use a valid timezone format like 'US/Pacific', 'UTC+8', or 'GMT-5'.")
        except Exception as err:
            raise ValueError(f"Invalid timezone: {timezone}. Error: {str(err)}")

        user_data = load_user_data()
        user_id = str(interaction.user.id)

        if user_id in user_data:
            user_data[user_id]['reminder_time'] = set_time
            user_data[user_id]['timezone'] = timezone
            user_data[user_id]['timezone_normalized'] = str(user_timezone)
        else:
            user_data[user_id] = {
                'discord_name': interaction.user.name,
                'reminder_time': set_time,
                'timezone': timezone,
                'timezone_normalized': str(user_timezone)
            }

        save_user_data(user_data)

        try:
            hour, minute = map(int, set_time.split(':'))

            trigger = CronTrigger(
                hour=hour,
                minute=minute,
                timezone=tz
            )

            scheduler.add_job(
                send_reminder,
                trigger=trigger,
                id=f"remind_{user_id}",
                replace_existing=True,
                args=[user_id]
            )
        except Exception as err:
            logger.error(f"Error scheduling reminder for user {user_id}: {str(err)}")
            raise ValueError(f"Error scheduling reminder: {str(err)}")

        try:
            await interaction.user.send("✅ Your reminder has been set successfully! You'll receive daily quest reminders in your DMs.")

            reminder_datetime = datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
            reminder_epoch = int(reminder_datetime.timestamp())
            current_epoch = int(datetime.now(tz).timestamp())

            logger.info(f"User {interaction.user.name} ({user_id}) set reminder for {set_time} {timezone}")
            await interaction.response.send_message(
                f"✅ Your daily quest reminder has been set for <t:{reminder_epoch}:t> in your timezone ({timezone})!\n"
                f"Current time in your timezone: <t:{current_epoch}:t>",
                ephemeral=True
            )
        except Exception as err:
            logger.error(f"Error sending confirmation to {interaction.user.name}: {str(err)}")

    except ValueError as err:
        logger.error(f"Error in set_reminder for {interaction.user.name}: {str(err)}")
        await interaction.response.send_message(
            f"❌ {str(err)}\n Please use the format `HH:MM` (24-hour format) and a valid timezone.",
            ephemeral=True
        )
    except Exception as err:
        logger.error(f"Unexpected error in set_reminder for {interaction.user.name}: {str(err)}")
        await interaction.response.send_message(
            f"❌ An unexpected error occurred: {str(err)}. Please try again later.",
            ephemeral=True
        )


@bot.tree.command(name="stopreminder", description="Stop receiving daily quest reminders")
async def stop_reminder(interaction: discord.Interaction):
    user_data = load_user_data()
    user_id = str(interaction.user.id)

    if user_id in user_data:
        del user_data[user_id]
        save_user_data(user_data)

        job_id = f"remind_{user_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        logger.info(f"User {interaction.user.name} ({user_id}) stopped reminders")
        await interaction.response.send_message("✅ Your daily quest reminders have been stopped!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ You don't have any reminders set up!", ephemeral=True)


@bot.tree.command(name="checkreminder", description="Check your current reminder settings")
async def check_reminder(interaction: discord.Interaction):
    user_data = load_user_data()
    user_id = str(interaction.user.id)

    if user_id in user_data:
        reminder_time = user_data[user_id]['reminder_time']
        timezone_display = user_data[user_id].get('timezone')
        user_timezone_normalized = user_data[user_id].get('timezone_normalized', timezone_display)

        try:
            tz = pytz.timezone(user_timezone_normalized) if isinstance(user_timezone_normalized, str) else user_timezone_normalized
        except Exception:
            tz = normalize_timezone(timezone_display)
            if isinstance(tz, str):
                tz = pytz.timezone(tz)

        now = datetime.now(tz)
        logger.info(f"User {interaction.user.name} ({user_id}) checked reminder settings")

        hour, minute = map(int, reminder_time.split(':'))
        reminder_datetime = datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
        reminder_epoch = int(reminder_datetime.timestamp())
        current_epoch = int(now.timestamp())

        message = f"🕒 Your daily quest reminder is set for <t:{reminder_epoch}:t> ({timezone_display})\n" \
                  f"Current time in your timezone: <t:{current_epoch}:t>"

        await interaction.response.send_message(message, ephemeral=True)
    else:
        await interaction.response.send_message(
            "❌ You don't have any reminders set up! Use `/setreminder` to set one.",
            ephemeral=True
        )


async def send_reminder(user_id):
    try:
        user_data = load_user_data()
        if user_id not in user_data:
            logger.warning(f"User {user_id} not found in data file during reminder")
            return

        user = await bot.fetch_user(int(user_id))
        if not user:
            logger.warning(f"Couldn't fetch user with ID {user_id}")
            return

        user_timezone_display = user_data[user_id].get('timezone')
        user_timezone_normalized = user_data[user_id].get('timezone_normalized', user_timezone_display)

        try:
            tz = pytz.timezone(user_timezone_normalized) if isinstance(user_timezone_normalized, str) else user_timezone_normalized
        except Exception:
            tz = normalize_timezone(user_timezone_display)
            if isinstance(tz, str):
                tz = pytz.timezone(tz)

        discord_name = user_data[user_id].get('discord_name', f"User {user_id}")

        embed = discord.Embed(
            title="🍬 Daily Quest Reminders!",
            description="Don't forget to complete your daily quests!",
            color=discord.Color.blue()
        )

        embed.set_thumbnail(url=THUMBNAIL_URL)

        for quest_name, quest_link in QUESTS.items():
            emoji = "🎁" if "General" in quest_name else "🍫" if "Collectors" in quest_name else "🏝️"
            embed.add_field(
                name=f"{emoji} {quest_name}",
                value=f"[Click here to go to quest]({quest_link})",
                inline=False
            )

        current_time = datetime.now(tz).strftime('%Y-%m-%d %H:%M')
        embed.set_footer(text=f"Reminder sent at {current_time} ({user_timezone_display})")

        try:
            await user.send(embed=embed)
            logger.info(f"Sent reminder to {discord_name} ({user_id}) at {current_time} ({user_timezone_display})")
        except discord.Forbidden:
            logger.warning(f"Failed to send reminder to {discord_name} ({user_id}): DMs are blocked")
        except Exception as err:
            logger.error(f"Error sending reminder to {discord_name} ({user_id}): {str(err)}")

    except Exception as err:
        logger.error(f"Error sending reminder to user {user_id}: {str(err)}")


@bot.tree.command(name="listtimezones", description="List common timezones to use with the setreminder command")
async def list_timezones(interaction: discord.Interaction):
    timezone_regions = {
        "North America": [tz for tz in pytz.common_timezones if "US/" in tz or "America/N" in tz or "America/L" in tz or "America/C" in tz or "America/D" in tz][:8],
        "Europe": [tz for tz in pytz.common_timezones if "Europe/" in tz][:8],
        "Asia": [tz for tz in pytz.common_timezones if "Asia/" in tz][:8],
        "Oceania": [tz for tz in pytz.common_timezones if "Australia/" in tz or "Pacific/" in tz][:6],
        "South America": [tz for tz in pytz.common_timezones if "America/S" in tz or "America/B" in tz][:6],
        "Africa": [tz for tz in pytz.common_timezones if "Africa/" in tz][:6],
        "UTC/GMT Offsets": ["UTC", "UTC+8", "UTC-5", "GMT", "GMT+7", "GMT-4"]
    }

    embed = discord.Embed(
        title="🌍 Available Timezones",
        description="Here are some common timezones you can use with the `/setreminder` command:\n\n[Click here for a complete list of all supported timezones](https://gist.github.com/heyalexej/8bf688fd67d7199be4a1682b3eec7568#file-pytz-time-zones-py)",
        color=discord.Color.green()
    )

    for region, timezones in timezone_regions.items():
        timezone_list = "\n".join(timezones)
        embed.add_field(name=region, value=f"```\n{timezone_list}\n```", inline=False)

    embed.set_footer(text="You can search for specific timezones when using the /setreminder command")

    logger.info(f"User {interaction.user.name} ({interaction.user.id}) requested timezone list")
    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    try:
        bot_token = os.getenv("DISCORD_BOT_TOKEN")
        if not bot_token:
            logger.critical("DISCORD_BOT_TOKEN environment variable not found!")
            exit(1)

        logger.info("Starting bot...")
        bot.run(bot_token)
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}")
