# bot.py (With Raid/Submission Durations and Markdown Fix)
import os
import logging
import re
import json
import random
import scraper
from typing import Union
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, MessageEntity, BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ConversationHandler, ContextTypes, PicklePersistence
)
from telegram.constants import ParseMode
from telegram import Update, MessageEntity, BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats, ReactionTypeEmoji
import database

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# --- Conversation States ---
(
    AWAITING_HANDLE, AWAITING_AUTH_JSON, AWAITING_DURATIONS
) = range(3)


def _parse_delta(duration_text: str) -> Union[timedelta, None]:
    """Helper function to parse a duration string (e.g., '30m', '12h') into a timedelta object."""
    match = re.match(r'^(\d+)([mhd])$', duration_text)
    if not match:
        return None

    value, unit = int(match.group(1)), match.group(2)
    if unit == 'm':
        return timedelta(minutes=value)
    elif unit == 'h':
        return timedelta(hours=value)
    elif unit == 'd':
        return timedelta(days=value)
    return None

# --- User-Facing Functions ---
# (start, profile_command, start_connect_profile, receive_handle, start_add_auth, receive_auth_file, help_auth_command, cancel are unchanged)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    if update.message.chat.type == 'private':
        welcome_text = (
            "👋 **Welcome to the Comment Verifier Bot!**\n\n"
            "I am ready to help you manage your X verification profile and participate in raids.\n\n"
            "Here are the commands you can use:\n"
            "• `/profile` - View your connected profile and groups.\n"
            "• `/connect` - Connect or update your X.com handle.\n"
            "• `/addauth` - Add your X account's auth data.\n"
            "• `/help` - Learn how to get your auth data."
        )
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    else:
        await update.message.reply_text("Hello! I am the Comment Verifier Bot.")


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's profile information, including groups."""
    user_id = update.message.from_user.id
    user_data = database.get_user_profile(user_id)

    if user_data:
        x_handle, auth_count, completed, total = user_data

        user_groups = database.get_groups_for_user(user_id)
        if user_groups:
            groups_text = "\n".join(f"  - `{group}`" for group in user_groups)
        else:
            groups_text = "  _You haven't added me to any groups yet._"

        profile_text = (
            f"👤 **Your Profile**\n\n"
            f"**X Handle:** `{x_handle}`\n"
            f"**Auth Files:** `{auth_count}`\n"
            f"**Completed Raids:** `{completed} / {total}`\n\n"
            f"👥 **Groups You've Added Me To:**\n{groups_text}"
        )
        await update.message.reply_text(profile_text, parse_mode='Markdown')
    else:
        await update.message.reply_text("No profile found. Use `/connect` to get started.")


async def start_connect_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks for the user's X handle."""
    await update.message.reply_text("Please send me your X.com handle (e.g., @YourHandle).")
    return AWAITING_HANDLE


def _format_time_left(seconds: int) -> str:
    """Formats a duration in seconds into a human-readable string (e.g., 1d 4h)."""
    if seconds < 0:
        return "Ended"

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{int(days)}d")
    if hours > 0:
        parts.append(f"{int(hours)}h")
    if minutes > 0 and days == 0:  # Only show minutes if less than a day
        parts.append(f"{int(minutes)}m")

    if not parts:
        return "Less than a minute"

    return " ".join(parts) + " left"


async def receive_handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    handle = update.message.text.strip()
    if not handle.startswith('@'):
        handle = f"@{handle}"
    user_id = update.message.from_user.id
    database.connect_user_profile(user_id, handle)
    await update.message.reply_text(f"✅ Your profile is now connected to `{handle}`!", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


async def start_add_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompts for the auth JSON file, reminding the user of the filename."""
    await update.message.reply_text(
        "Please upload the `auth_session.json` file now."
        "\n\n(If you need the full instructions on how to create this file, use the /help command.)"
    )
    return AWAITING_AUTH_JSON  # We can reuse the same state name


async def receive_auth_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives a JSON file, validates it as a Playwright state file, and saves it."""
    user_id = update.message.from_user.id

    if not update.message.document or not update.message.document.file_name.lower().endswith('.json'):
        await update.message.reply_text("❌ That's not a valid `.json` file. Please send the file as a document.")
        return AWAITING_AUTH_JSON

    doc_file = await update.message.document.get_file()
    file_content_bytes = await doc_file.download_as_bytearray()
    json_text = file_content_bytes.decode('utf-8')

    try:
        data = json.loads(json_text)
        # A valid Playwright state file is a dictionary with 'cookies' and 'origins' keys.
        is_valid_state_file = (
            isinstance(data, dict) and
            'cookies' in data and
            'origins' in data and
            isinstance(data['cookies'], list)
        )

        if not is_valid_state_file:
            await update.message.reply_text(
                "❌ **Invalid File Content.**\n"
                "This doesn't look like a valid `auth_session.json` file created by the generator app. Please use /help for instructions and try again.",
                parse_mode='Markdown'
            )
            return AWAITING_AUTH_JSON

        user_dir = f"user_data/{user_id}"
        os.makedirs(user_dir, exist_ok=True)
        file_count = len([f for f in os.listdir(
            user_dir) if f.endswith('.json')])
        file_path = os.path.join(user_dir, f"auth_{file_count + 1}.json")

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(json_text)

        database.update_auth_file_count(user_id, file_count + 1)
        await update.message.reply_text(f"✅ Auth file received and saved! You now have **{file_count + 1}** auth file(s).", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    except json.JSONDecodeError:
        await update.message.reply_text("❌ The file content is not valid JSON. Please generate the file again.")
        return AWAITING_AUTH_JSON


# In bot.py, replace this entire function
# In bot.py, replace the /help command's function

async def private_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Provides a detailed, user-friendly guide for the entire auth process."""
    text = (
        "🔐 **How to Securely Connect Your X.com Account**\n\n"
        "To verify your comments, the bot needs a secure session file. This process is much safer than giving out your password and only needs to be done once.\n\n"
        "You will use our simple desktop app to generate this file.\n\n"
        "--- \n"
        "**Step 1: Download the Generator App**\n"
        "Click the link below to download the app for your computer.\n"
        "➡️ https://github.com/Damilare10/Auth-session-generator/releases/download/Auth_File_Generator_v1.0.0/generate_auth.zip\n\n"
        "--- \n"
        "**Step 2: Manual Browser Setup (First-Time Users Only)**\n"
        "The generator app needs a browser to work. If you are running it for the first time, you may need to do a one-time manual setup. It's simple!\n"
        "1. Download this file: `https://playwright.azureedge.net/builds/chromium/1187/chromium-win64.zip`\n"
        "2. Create a specific set of folders in your user directory: `C:\\Users\\YOUR_USERNAME\\AppData\\Local\\ms-playwright\\chromium-1187\\chrome-win\\`\n"
        "3. Unzip the downloaded file and copy all of its contents into that final `chrome-win` folder.\n\n"
        "_(This step is necessary because antivirus software can block automatic installers.)_\n\n"
        "--- \n"
        "**Step 3: Run the App & Log In**\n"
        "1. Double-click the `generate_auth_for_bot.exe` file you downloaded.\n"
        "2. A black console window and a new browser window will open.\n"
        "3. In the **browser window**, log in to your X.com account as you normally would. All login methods (including Google Sign-In) will work.\n\n"
        "--- \n"
        "**Step 4: Save Your Session**\n"
        "1. Once you are fully logged in and can see your timeline, **DO NOT** close the browser.\n"
        "2. Come back to the **black console window**.\n"
        "3. **Press the ENTER key**.\n"
        "4. The script will save your session and close the browser for you.\n\n"
        "--- \n"
        "**Step 5: Upload to the Bot**\n"
        "A new file named `auth_session.json` will appear in the same folder where you ran the app. Come back here, use the `/addauth` command, and send me that file."
    )
    await update.message.reply_text(
        text,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )


async def group_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explains the bot's purpose in a group chat."""
    text = (
        "**Comment Verifier Bot - Group Help**\n\n"
        "My purpose in this group is to manage verification raids.\n\n"
        "1. An **admin** uses `/start_raid` to begin.\n"
        "2. **Members** post X.com (Twitter) links in the chat.\n"
        "3. I will **collect** all valid links automatically.\n"
        "4. Anyone can use `/ongoing_raid` to see a list of the collected links.\n\n"
        "To participate in the verification process, you must first send me a private message to set up your profile."
    )
    await update.message.reply_text(text, parse_mode='Markdown')


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


# --- Group & Raid Functions ---

async def on_group_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When the bot joins a group, it saves who added it and sends a welcome message."""
    adder_user = update.message.from_user
    chat = update.message.chat
    if context.bot.id in [member.id for member in update.message.new_chat_members]:
        database.add_user_to_group(adder_user.id, chat.id, chat.title)
        await update.message.reply_text(
            f"Hello! I'm the Comment Verifier Bot, added by {adder_user.mention_markdown()}.\n\n"
            "An admin can start a new raid by using the `/start_raid` command.\n\n"
            "All members who wish to participate should start a private chat with me to connect their X profile.",
            parse_mode='Markdown'
        )


async def ongoing_raid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Displays the status and links for the current raid, reflecting the
    two-phase (Submission -> Engagement) system.
    """
    if not update.message.chat.type in ['group', 'supergroup']:
        await update.message.reply_text("This command can only be used in a group.")
        return

    chat_id = update.message.chat_id
    raid_details = database.get_active_raid_details(chat_id)

    if not raid_details:
        await update.message.reply_text("There is no raid currently active in this group.")
        return

    # Unpack the correct deadlines from the database
    raid_id, submission_deadline_ts, engagement_deadline_ts = raid_details
    now_ts = int(datetime.now().timestamp())

    # Determine the current status and time remaining until the *next* deadline
    if now_ts < submission_deadline_ts:
        status = "Phase 1: Link Submission"
        time_left_str = _format_time_left(submission_deadline_ts - now_ts)
    elif now_ts < engagement_deadline_ts:
        status = "Phase 2: Engagement (Go Comment!)"
        time_left_str = _format_time_left(engagement_deadline_ts - now_ts)
    else:
        # This case might occur if the verification job is slightly delayed
        status = "Completed (Verification Pending)"
        time_left_str = "Ended"

    # Get the list of links
    raid_links = database.get_links_for_raid(raid_id)

    if not raid_links:
        links_section = "No links have been submitted yet. Post X.com links in the chat to add them."
    else:
        formatted_links = "\n".join(
            f"{i+1}. {link}" for i, link in enumerate(raid_links))
        links_section = f"**Collected Links ({len(raid_links)} total):**\n{formatted_links}"

    # Assemble the final message
    message_text = (
        f"🚀 **Raid #{raid_id} Status**\n\n"
        f"**Current Phase:** `{status}`\n"
        f"**Time Until Next Phase:** `{time_left_str}`\n\n"
        f"{links_section}"
    )

    await update.message.reply_text(
        message_text,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )


async def start_raid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the raid creation process, asking for two durations."""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if not update.message.chat.type in ['group', 'supergroup']:
        await update.message.reply_text("This command can only be used in a group.")
        return ConversationHandler.END

    admins = await context.bot.get_chat_administrators(chat_id)
    if user_id not in [admin.user.id for admin in admins]:
        await update.message.reply_text("Only group admins can start a raid.")
        return ConversationHandler.END

    if database.get_active_raid_id(chat_id):
        await update.message.reply_text("A raid is already active in this group. Please wait for it to finish.")
        return ConversationHandler.END

    # --- UPDATED PROMPT ---
    await update.message.reply_text(
        "🚀 **Starting a new raid!**\n\n"
        "Please specify the **link submission duration** and the **engagement (commenting) duration**, separated by a space.\n\n"
        "**Format:** `<submission_duration> <engagement_duration>`\n"
        "**Example:** `30m 2h` (30 minutes for everyone to submit links, followed by 2 hours for everyone to comment on them).",
        parse_mode='Markdown'
    )
    return AWAITING_DURATIONS


# In bot.py, replace the old end_raid_command with this
async def end_raid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually ends the current raid and triggers verification. (Admin-only)"""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if not update.message.chat.type in ['group', 'supergroup']:
        await update.message.reply_text("This command can only be used in a group.")
        return

    admins = await context.bot.get_chat_administrators(chat_id)
    if user_id not in [admin.user.id for admin in admins]:
        await update.message.reply_text("Only group admins can end a raid.")
        return

    raid_id = database.get_active_raid_id(chat_id)
    if not raid_id:
        await update.message.reply_text("There is no raid currently active to end.")
        return

    # Remove the job from the queue if it exists, to prevent it from running twice
    jobs = context.job_queue.get_jobs_by_name(f"raid_end_{raid_id}")
    for job in jobs:
        job.schedule_removal()

    # Call the core logic
    await _run_raid_verification(chat_id, raid_id, context)


async def _run_raid_verification(chat_id: int, raid_id: int, context: ContextTypes.DEFAULT_TYPE):
    """The core logic for ending a raid and running the scraper."""
    # 1. Gather data for the scraper
    all_links = database.get_links_for_raid(raid_id)
    participants = database.get_raid_participants_with_handles(raid_id)

    if not all_links or not participants:
        await context.bot.send_message(chat_id, f"Raid #{raid_id} is ending, but no links or participants were found. The raid will now be archived.")
        database.deactivate_raid(raid_id)
        return

    # 2. Prepare data for the scraper function
    links_to_check = random.sample(all_links, k=min(5, len(all_links)))
    participant_ids = [p[0] for p in participants]
    target_usernames = [p[1] for p in participants]

    await context.bot.send_message(
        chat_id,
        f"⏳ **Raid #{raid_id} submission time has ended!**\n\n"
        f"🔬 Analyzing **{len(links_to_check)}** random links against **{len(target_usernames)}** participants. This may take a few minutes...",
        parse_mode='Markdown'
    )

    # 3. Run the scraper
    try:
        report = await scraper.run_scrape_and_check(participant_ids, links_to_check, target_usernames)
        await context.bot.send_message(chat_id, report, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Scraper failed for raid {raid_id}: {e}")
        await context.bot.send_message(chat_id, "Sorry, an unexpected error occurred during the verification process.")
    finally:
        # 4. Deactivate the raid
        database.deactivate_raid(raid_id)
        await context.bot.send_message(chat_id, f"Raid #{raid_id} is now complete and has been archived.")


async def auto_end_raid_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback function for the JobQueue to automatically end a raid."""
    job = context.job
    chat_id = job.data["chat_id"]
    raid_id = job.data["raid_id"]

    await _run_raid_verification(chat_id, raid_id, context)


async def receive_durations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receives submission and engagement durations, creates the raid,
    and schedules the final verification.
    """
    duration_text = update.message.text.lower().strip()
    parts = duration_text.split()

    if len(parts) != 2:
        await update.message.reply_text(
            "❌ **Invalid Format**\nPlease provide *two* durations separated by a space. Example: `2h 30m`",
            parse_mode='Markdown'
        )
        return AWAITING_DURATIONS

    # Correctly assign variables based on the new workflow
    submission_delta = _parse_delta(parts[0])
    engagement_delta = _parse_delta(parts[1])

    if not submission_delta or not engagement_delta:
        await update.message.reply_text(
            "❌ **Invalid Format**\nPlease use 'm', 'h', or 'd' for durations. Example: `2h 30m`",
            parse_mode='Markdown'
        )
        return AWAITING_DURATIONS

    # Calculate the two distinct deadlines
    now = datetime.now()
    submission_deadline = now + submission_delta
    engagement_deadline = submission_deadline + \
        engagement_delta  # This is the final deadline

    # Convert to timestamps for the database
    submission_deadline_ts = int(submission_deadline.timestamp())
    engagement_deadline_ts = int(engagement_deadline.timestamp())

    chat_id = update.message.chat_id
    raid_id = database.create_new_raid(
        chat_id, submission_deadline_ts, engagement_deadline_ts
    )

    # Schedule the final verification job to run when the ENGAGEMENT period is over
    total_delay = (engagement_deadline - now).total_seconds()
    context.job_queue.run_once(
        auto_end_raid_callback,
        when=total_delay,
        data={'chat_id': chat_id, 'raid_id': raid_id},
        name=f"raid_end_{raid_id}"
    )

    # Send the correct announcement message for the two-phase raid
    await update.message.reply_text(
        f"✅ **Raid #{raid_id} has BEGUN! Phase 1: Link Submission**\n\n"
        f"Members, please post all target X.com links below.\n\n"
        f"🕒 **Link Submission Ends:** in **{submission_delta}**\n"
        f"🏁 **Engagement (Commenting) Ends:** in **{submission_delta + engagement_delta}**\n\n"
        f"_The bot will stop accepting new links after the first period. Verification will start automatically at the end._",
        parse_mode='Markdown'
    )
    return ConversationHandler.END
# (post_init, link_collector, error_handler are unchanged)


async def post_init(application: Application):
    """Sets the bot's command menus."""
    private_commands = [
        BotCommand("start", "↩️ Main Menu & Welcome"),
        BotCommand("profile", "👤 View Your Profile"),
        BotCommand("connect", "🔗 Connect X Handle"),
        BotCommand("addauth", "➕ Add Auth File"),
        BotCommand("help", "❓ How to get an Auth File"),
    ]
    await application.bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())

    group_commands = [
        BotCommand("start_raid", "🚀 Starts a new verification raid (Admin only)"),
        BotCommand("ongoing_raid", "📊 Shows links for the current raid"),
        BotCommand(
            "end_raid", "🏁 Ends the raid and runs verification (Admin only)"),
        BotCommand("help", "❓ Explains what this bot does"),
    ]
    await application.bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())

    print("Custom command menus have been set.")


async def link_collector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Collects one X.com link per registered user, but only during the raid's
    active submission period.
    """
    if not update.message or not update.message.text or not update.message.from_user:
        return

    # RULE 1: Check if the user is registered with the bot
    user_id = update.message.from_user.id
    if not database.is_user_registered(user_id):
        return  # Silently ignore if the user hasn't signed up in the bot's DM.

    # Check for an active raid in this group
    chat_id = update.message.chat_id
    raid_details = database.get_active_raid_details(chat_id)
    if not raid_details:
        return  # No active raid, do nothing.

    raid_id, submission_deadline_ts, _ = raid_details
    now_ts = int(datetime.now().timestamp())

    # --- CRITICAL NEW LOGIC ---
    # RULE 2: Check if the submission period is still active.
    if now_ts > submission_deadline_ts:
        return  # Silently ignore links because the submission phase is over.

    # Find the FIRST valid X.com/Twitter link in the message
    urls = [
        update.message.text[entity.offset: entity.offset + entity.length]
        for entity in update.message.entities
        if entity.type == MessageEntity.URL
    ]

    first_valid_url = None
    for url in urls:
        if ("x.com" in url and "/status/" in url) or ("twitter.com" in url and "/status/" in url):
            first_valid_url = url.split("?")[0]
            break  # Stop after finding the first valid link

    # If no valid link was found in the message, do nothing.
    if not first_valid_url:
        return

    # RULE 3: Attempt to submit the link (database handles the one-per-user logic)
    # This function returns True if successful, False if they already submitted.
    was_successful = database.add_raid_link_and_mark_submitted(
        raid_id, user_id, first_valid_url)

    # Only react if the link was successfully added.
    if was_successful:
        try:
            await context.bot.set_message_reaction(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                reaction=[ReactionTypeEmoji(emoji="👍")]
            )
        except Exception as e:
            logging.warning(f"Failed to set message reaction: {e}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logging.error("Exception while handling an update:",
                  exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Sorry, an unexpected error occurred.")


# --- Main Bot Setup ---
# In bot.py, replace your entire main() function with this one.

def main() -> None:
    """The main entry point for the bot."""
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not found in .env file.")
        return

    # --- REVISED INITIALIZATION ---

    # 1. Create the JobQueue instance first
    job_queue = JobQueue()

    # 2. Set up persistence for the JobQueue
    persistence = PicklePersistence(filepath="raid_bot_persistence.pkl")

    database.initialize_database()

    # 3. Build the application, passing the JobQueue and persistence objects
    application = (
        Application.builder()
        .token(TOKEN)
        .persistence(persistence)
        .job_queue(job_queue)  # <-- Explicitly add the job queue here
        .post_init(post_init)
        .build()
    )

    # --- END OF REVISED INITIALIZATION ---

    application.add_error_handler(error_handler)

    # --- Full Conversation Handler Definitions ---
    connect_conv = ConversationHandler(
        entry_points=[CommandHandler(
            "connect", start_connect_profile, filters=filters.ChatType.PRIVATE)],
        states={
            AWAITING_HANDLE: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, receive_handle)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    add_auth_conv = ConversationHandler(
        entry_points=[CommandHandler(
            "addauth", start_add_auth, filters=filters.ChatType.PRIVATE)],
        states={
            AWAITING_AUTH_JSON: [MessageHandler(
                filters.Document.ALL, receive_auth_file)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    raid_conv = ConversationHandler(
        entry_points=[CommandHandler("start_raid", start_raid_command)],
        states={
            AWAITING_DURATIONS: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, receive_durations)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # --- Add All Handlers ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler(
        "help", private_help_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler(
        "help", group_help_command, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler(
        "profile", profile_command, filters=filters.ChatType.PRIVATE))

    application.add_handler(connect_conv)
    application.add_handler(add_auth_conv)
    application.add_handler(raid_conv)

    application.add_handler(CommandHandler(
        "ongoing_raid", ongoing_raid_command, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler(
        "end_raid", end_raid_command, filters=filters.ChatType.GROUPS))

    application.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, on_group_join))
    application.add_handler(MessageHandler(filters.Entity(
        MessageEntity.URL) & filters.ChatType.GROUPS, link_collector))

    print("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
