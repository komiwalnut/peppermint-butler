import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import json
import os
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

logger = logging.getLogger('discord_bot')
logger.setLevel(logging.INFO)
handler = RotatingFileHandler('discord_bot.log', maxBytes=5 * 1024 * 1024, backupCount=5)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

bot = commands.Bot(command_prefix=None, intents=intents)
scheduler = AsyncIOScheduler()

DEFAULT_REMINDER_TIME = "14:00"
DEFAULT_TIMEZONE = "UTC"
DATA_FILE = "user_reminders.json"
THUMBNAIL_URL = "https://img.notionusercontent.com/s3/prod-files-secure%2F74b0bc64-8301-4be7-a968-d92c5cbce991%2Fd9776d11-124a-424c-be60-4350f1baab70%2FJoy.png/size/w=1420?exp=1746945395&sig=jnidwmYZuc40hXjoAvdj06VgKaOfq_MtejPUVcFEF9o&id=104c48ae-3fdd-8042-a5f8-fbbd4c558ac5&table=block"

COMMON_TIMEZONES = [
    "UTC", "GMT",
    "US/Pacific", "US/Mountain", "US/Central", "US/Eastern", "US/Alaska", "US/Hawaii",
    "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York", "America/Anchorage",
    "America/Vancouver", "America/Edmonton", "America/Toronto", "America/Montreal",
    "America/Mexico_City", "America/Bogota", "America/Lima", "America/Santiago", "America/Sao_Paulo", "America/Buenos_Aires",
    "Europe/London", "Europe/Dublin", "Europe/Paris", "Europe/Berlin", "Europe/Rome", "Europe/Madrid",
    "Europe/Amsterdam", "Europe/Brussels", "Europe/Vienna", "Europe/Stockholm", "Europe/Oslo",
    "Europe/Warsaw", "Europe/Moscow", "Europe/Istanbul", "Europe/Athens",
    "Africa/Cairo", "Africa/Johannesburg", "Africa/Lagos", "Africa/Nairobi",
    "Asia/Jerusalem", "Asia/Dubai", "Asia/Karachi", "Asia/Kolkata", "Asia/Dhaka",
    "Asia/Bangkok", "Asia/Singapore", "Asia/Manila", "Asia/Jakarta", "Asia/Shanghai",
    "Asia/Hong_Kong", "Asia/Taipei", "Asia/Seoul", "Asia/Tokyo",
    "Australia/Perth", "Australia/Adelaide", "Australia/Brisbane", "Australia/Sydney", "Australia/Melbourne",
    "Pacific/Auckland", "Pacific/Fiji", "Pacific/Honolulu",
    "Atlantic/Reykjavik", "Atlantic/Azores", "Atlantic/Cape_Verde",
    "Indian/Maldives", "Indian/Mauritius", "Indian/Reunion",
    "Brazil/East", "Brazil/West", "Canada/Atlantic", "Canada/Central", "Canada/Eastern", "Canada/Pacific",
    "Chile/Continental", "Cuba", "Egypt", "Iran", "Israel", "Jamaica", "Japan", "Mexico/General",
    "NZ", "Poland", "Portugal", "Singapore", "Turkey"
]

QUESTS = {
    "General Daily Check-In": "https://discord.com/channels/410537146672349205/1361578026256564255/1362069080022061287",
    "Collectors Daily Check-In": "https://discord.com/channels/410537146672349205/1361578026256564255/1362760752435433482",
    "Land Owners Daily Check-In": "https://discord.com/channels/410537146672349205/1361578026256564255/1362761587810893955"
}


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
        reminder_time = user_info.get('reminder_time', DEFAULT_REMINDER_TIME)
        timezone_str = user_info.get('timezone', DEFAULT_TIMEZONE)

        try:
            tz = pytz.timezone(timezone_str)
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
    if not current:
        return [app_commands.Choice(name=tz, value=tz) for tz in COMMON_TIMEZONES[:25]]

    matching_timezones = [tz for tz in pytz.all_timezones if current.lower() in tz.lower()]
    return [app_commands.Choice(name=tz, value=tz) for tz in matching_timezones[:25]]


@bot.tree.command(name="setreminder", description="Set your daily quest reminder time with your timezone")
@app_commands.describe(
    set_time="Time in HH:MM format (24-hour, e.g. 14:00 for 2 PM)",
    timezone="Your timezone (e.g. UTC, US/Pacific, Asia/Manila, etc.)"
)
@app_commands.autocomplete(timezone=timezone_autocomplete)
async def set_reminder(
        interaction: discord.Interaction,
        set_time: str = DEFAULT_REMINDER_TIME,
        timezone: Optional[str] = None
):
    try:
        hour, minute = map(int, set_time.split(':'))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError("Invalid time range")

        user_timezone = timezone if timezone else DEFAULT_TIMEZONE
        try:
            pytz.timezone(user_timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            raise ValueError(f"Unknown timezone: {user_timezone}. Please select from the autocomplete list.")

        user_data = load_user_data()
        user_id = str(interaction.user.id)

        if user_id in user_data:
            user_data[user_id]['reminder_time'] = set_time
            user_data[user_id]['timezone'] = user_timezone
        else:
            user_data[user_id] = {
                'discord_name': interaction.user.name,
                'reminder_time': set_time,
                'timezone': user_timezone
            }

        save_user_data(user_data)
        setup_scheduler()

        now = datetime.now(pytz.timezone(user_timezone))
        time_str = now.strftime('%H:%M')

        logger.info(f"User {interaction.user.name} ({user_id}) set reminder for {set_time} {user_timezone}")
        await interaction.response.send_message(
            f"✅ Your daily quest reminder has been set for {set_time} in your timezone ({user_timezone})!\n"
            f"Current time in your timezone: {time_str}",
            ephemeral=True
        )

    except Exception as err:
        logger.error(f"Error in set_reminder for {interaction.user.name}: {str(err)}")
        await interaction.response.send_message(
            f"❌ Error setting reminder: {str(e)}. Please use the format HH:MM (24-hour format) and a valid timezone.",
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
        timezone = user_data[user_id].get('timezone', DEFAULT_TIMEZONE)

        now = datetime.now(pytz.timezone(timezone))
        current_time = now.strftime('%H:%M')

        logger.info(f"User {interaction.user.name} ({user_id}) checked reminder settings")
        await interaction.response.send_message(
            f"📅 Your daily quest reminder is set for {reminder_time} ({timezone})\n"
            f"Current time in your timezone: {current_time}",
            ephemeral=True
        )
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

        user_timezone = user_data[user_id].get('timezone', DEFAULT_TIMEZONE)
        discord_name = user_data[user_id].get('discord_name', f"User {user_id}")

        embed = discord.Embed(
            title="🎮 Daily Quest Reminders!",
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

        current_time = datetime.now(pytz.timezone(user_timezone)).strftime('%Y-%m-%d %H:%M')
        embed.set_footer(text=f"Reminder sent at {current_time} ({user_timezone})")

        await user.send(embed=embed)
        logger.info(f"Sent reminder to {discord_name} ({user_id}) at {current_time} ({user_timezone})")

    except Exception as err:
        logger.error(f"Error sending reminder to user {user_id}: {str(err)}")


@bot.tree.command(name="listtimezones", description="List common timezones to use with the setreminder command")
async def list_timezones(interaction: discord.Interaction):
    timezone_regions = {
        "North America": ["US/Pacific", "US/Mountain", "US/Central", "US/Eastern", "America/Los_Angeles", "America/New_York"],
        "Europe": ["Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Rome", "Europe/Moscow"],
        "Asia": ["Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai", "Asia/Tokyo", "Asia/Manila", "Asia/Singapore"],
        "Oceania": ["Australia/Perth", "Australia/Sydney", "Pacific/Auckland"],
        "South America": ["America/Sao_Paulo", "America/Buenos_Aires", "America/Santiago"],
        "Africa": ["Africa/Cairo", "Africa/Johannesburg", "Africa/Lagos"],
        "Other": ["UTC", "GMT"]
    }

    embed = discord.Embed(
        title="🌍 Available Timezones",
        description="Here are some common timezones you can use with the `/setreminder` command:",
        color=discord.Color.green()
    )

    for region, timezones in timezone_regions.items():
        timezone_list = "\n".join(timezones)
        embed.add_field(name=region, value=f"```\n{timezone_list}\n```", inline=False)

    embed.set_footer(text="You can also search for specific timezones when using the /setreminder command")

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
