import discord
from discord.ext import tasks
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timezone, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
NOTIFIER_ROLE_ID = int(os.getenv("NOTIFIER_ROLE_ID"))

EVENT_SCHEDULE = {
    "Easy Dungeon":      "0 * * * *",
    "Medium Dungeon":    "10 * * * *",
    "Leaf Raid":         "15 * * * *",
    "Hard Dungeon":      "20 * * * *",
    "Insane Dungeon":    "30 * * * *",
    "Crazy Dungeon":     "40 * * * *",
    "Nightmare Dungeon": "50 * * * *",
}

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

client = discord.Client(intents=intents)
scheduler = AsyncIOScheduler(timezone=timezone.utc)

persistent_message = None
active_ping_messages = {}

def get_next_run_time(cron_str):
    """Calculates the next scheduled run time from a cron string."""
    trigger = CronTrigger.from_crontab(cron_str, timezone=timezone.utc)
    now = datetime.now(timezone.utc)
    return trigger.get_next_fire_time(None, now)

def get_previous_run_time(cron_str):
    """Calculates the most recent run time from a cron string."""
    trigger = CronTrigger.from_crontab(cron_str, timezone=timezone.utc)
    now = datetime.now(timezone.utc)
    
    for minutes_back in range(1, 121):
        check_time = now - timedelta(minutes=minutes_back)
        next_fire = trigger.get_next_fire_time(None, check_time)
        
        if next_fire and next_fire <= now and (now - next_fire).total_seconds() <= 7200:
            return next_fire
    
    return None

async def send_ping(channel, role_id, event_name):
    """Sends a notification ping and stores it for later deletion."""
    role_to_ping = f"<@&{role_id}>"
    notification_message = f"{role_to_ping} **{event_name}** has started! Join now!"
    
    try:
        sent_message = await channel.send(notification_message)
        print(f"Sent ping for {event_name}.")
        
        active_ping_messages[event_name] = {
            'message': sent_message,
            'delete_time': datetime.now(timezone.utc) + timedelta(minutes=2)
        }
        
    except discord.errors.Forbidden:
        print(f"Error: Bot lacks permissions to send messages in channel {channel.id}.")
    except Exception as e:
        print(f"An error occurred during ping: {e}")

async def cleanup_ping_messages():
    """Clean up expired ping messages."""
    now = datetime.now(timezone.utc)
    messages_to_remove = []
    
    for event_name, msg_data in active_ping_messages.items():
        if now >= msg_data['delete_time']:
            try:
                await msg_data['message'].delete()
                print(f"Deleted ping message for {event_name}.")
                messages_to_remove.append(event_name)
            except discord.errors.NotFound:
                messages_to_remove.append(event_name)
            except Exception as e:
                print(f"Error deleting ping message for {event_name}: {e}")
                messages_to_remove.append(event_name)
    
    for event_name in messages_to_remove:
        del active_ping_messages[event_name]

def format_countdown(seconds):
    """Format seconds into a readable countdown format."""
    if seconds <= 0:
        return "0s"
    
    if seconds >= 3600:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours}h {minutes}m {secs}s"
    elif seconds >= 60:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        return f"{int(seconds)}s"

@tasks.loop(seconds=10)
async def update_embed():
    """Main loop that updates the countdown embed every 10 seconds."""
    if not persistent_message:
        return

    await cleanup_ping_messages()

    now = datetime.now(timezone.utc)
    
    events_info = []
    for name, schedule in EVENT_SCHEDULE.items():
        next_run = get_next_run_time(schedule)
        prev_run = get_previous_run_time(schedule)
        
        if next_run:
            events_info.append({
                "name": name, 
                "next_time": next_run,
                "prev_time": prev_run
            })

    embed = discord.Embed(
        title="🏰 Dungeon & Raid Schedule",
        description="Live countdowns for hourly dungeons and raids",
        color=discord.Color.dark_purple()
    )

    active_events = []
    upcoming_events = []
    
    for event in events_info:
        if event["prev_time"]:
            time_since_start = (now - event["prev_time"]).total_seconds()
            if 0 <= time_since_start <= 120:
                active_events.append({
                    **event,
                    "time_remaining": 120 - time_since_start
                })
                continue
        
        upcoming_events.append(event)
    
    active_events.sort(key=lambda x: x["time_remaining"])
    
    upcoming_events.sort(key=lambda x: x["next_time"])

    for event in active_events:
        time_remaining = max(0, event["time_remaining"])
        countdown = format_countdown(time_remaining)
        
        embed.add_field(
            name=f"🟢 **ACTIVE** - {event['name']}",
            value=f"⏱️ Join window closes in: **{countdown}**\n🚪 **JOIN NOW!**",
            inline=False
        )

    for i, event in enumerate(upcoming_events):
        time_until = (event["next_time"] - now).total_seconds()
        
        if i == 0 and not active_events:
            status = "🟡 **NEXT UP**"
            countdown = format_countdown(time_until)
            value = f"⏰ Starts in: **{countdown}**"
        else:
            status = "🔴 **SCHEDULED**"
            timestamp = f"<t:{int(event['next_time'].timestamp())}:R>"
            value = f"📅 Starts {timestamp}"

        embed.add_field(
            name=f"{status} - {event['name']}",
            value=value,
            inline=False
        )

    embed.set_footer(text="🤖 Auto-updating every 10 seconds • Join during the green active window!")
    embed.timestamp = now

    try:
        await persistent_message.edit(embed=embed)
    except discord.errors.NotFound:
        print("Embed message was deleted. Attempting to recreate it...")
        update_embed.stop()
        await setup_embed_message()
    except Exception as e:
        print(f"Failed to edit embed: {e}")

async def setup_embed_message():
    """Finds the bot's old message or sends a new one, then starts the update loop."""
    global persistent_message
    try:
        channel = client.get_channel(CHANNEL_ID)
        if not channel:
            print(f"FATAL: Channel with ID {CHANNEL_ID} not found.")
            return

        async for msg in channel.history(limit=50):
            if msg.author.id == client.user.id and msg.embeds:
                if msg.embeds[0].title and "Dungeon & Raid Schedule" in msg.embeds[0].title:
                    persistent_message = msg
                    print(f"Found existing message to edit: {msg.id}")
                    break
        
        if not persistent_message:
            embed = discord.Embed(
                title="🏰 Dungeon & Raid Schedule", 
                description="Initializing countdown system...",
                color=discord.Color.dark_purple()
            )
            persistent_message = await channel.send(embed=embed)
            print(f"Sent new persistent message: {persistent_message.id}")

        if not update_embed.is_running():
            update_embed.start()
            print("Started embed update loop.")

    except discord.errors.Forbidden:
        print(f"FATAL: Bot lacks permissions to read history or send messages in channel {CHANNEL_ID}.")
    except Exception as e:
        print(f"An error occurred during embed setup: {e}")

@client.event
async def on_ready():
    """This function runs once when the bot logs in and is ready."""
    print(f'🤖 Logged in as {client.user.name} (ID: {client.user.id})')
    print('------')

    if not client.get_guild(GUILD_ID):
        print(f"FATAL: Bot is not in the server with ID {GUILD_ID}. Please invite it first.")
        await client.close()
        return

    await setup_embed_message()

    if not scheduler.running:
        channel = client.get_channel(CHANNEL_ID)
        for name, schedule in EVENT_SCHEDULE.items():
            scheduler.add_job(
                send_ping,
                CronTrigger.from_crontab(schedule, timezone=timezone.utc),
                args=[channel, NOTIFIER_ROLE_ID, name],
                id=f"ping_for_{name}",
                replace_existing=True
            )
        
        scheduler.start()
        print("📅 APScheduler started and jobs are scheduled.")
        print(f"🔔 Monitoring {len(EVENT_SCHEDULE)} events for notifications.")

try:
    if not BOT_TOKEN or not GUILD_ID or not CHANNEL_ID or not NOTIFIER_ROLE_ID:
        print("FATAL: Please fill in all the configuration values in the .env file.")
    else:
        client.run(BOT_TOKEN)
except discord.errors.LoginFailure:
    print("FATAL: The bot token is invalid. Please check your token.")
except Exception as e:
    print(f"An error occurred while starting the bot: {e}")