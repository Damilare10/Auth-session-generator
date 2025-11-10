# database.py (Updated for one link per user rule)
import sqlite3
import time

DATABASE_FILE = "bot_data.db"

# --- CORE INITIALIZATION ---


def initialize_database():
    """Creates/updates the necessary tables for the bot."""
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()

        # User table (unchanged)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                x_handle TEXT UNIQUE,
                auth_file_count INTEGER DEFAULT 0,
                completed_raids INTEGER DEFAULT 0,
                total_raids INTEGER DEFAULT 0
            )
        """)

        # Raids Table (unchanged)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raids (
                raid_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                start_timestamp INTEGER NOT NULL,
                submission_deadline_timestamp INTEGER NOT NULL, -- End of link collection
                engagement_deadline_timestamp INTEGER NOT NULL, -- End of commenting (final deadline)
                is_active BOOLEAN DEFAULT 1
            )
        """)

        # Raid Links Table (unchanged)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raid_links (
                link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                raid_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                UNIQUE(raid_id, url)
            )
        """)

        # --- UPDATED: Raid Participants Table ---
        # Added has_submitted_link to track submissions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raid_participants (
                participation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                raid_id INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                links_commented INTEGER DEFAULT 0,
                has_submitted_link INTEGER DEFAULT 0, -- 0 for no, 1 for yes
                UNIQUE(raid_id, telegram_id)
            )
        """)

        # User Groups Table (unchanged)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_groups (
                user_group_id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                group_name TEXT NOT NULL,
                UNIQUE(telegram_id, group_id)
            )
        """)

    print("Database initialized successfully with all tables.")


# --- USER PROFILE FUNCTIONS ---

def is_user_registered(telegram_id):
    """Checks if a user exists in the users table. Returns True or False."""
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM users WHERE telegram_id = ?", (telegram_id,))
        return cursor.fetchone() is not None

# (connect_user_profile, get_user_profile, update_auth_file_count, etc. are unchanged)


def connect_user_profile(telegram_id, x_handle):
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (telegram_id, x_handle) VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET x_handle=excluded.x_handle
        """, (telegram_id, x_handle))


def get_user_profile(telegram_id):
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT x_handle, auth_file_count, completed_raids, total_raids FROM users WHERE telegram_id = ?", (telegram_id,))
        return cursor.fetchone()


def update_auth_file_count(telegram_id, count):
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET auth_file_count = ? WHERE telegram_id = ?", (count, telegram_id))


def add_user_to_group(telegram_id, group_id, group_name):
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO user_groups (telegram_id, group_id, group_name)
            VALUES (?, ?, ?)
        """, (telegram_id, group_id, group_name))


def get_raid_participants_with_handles(raid_id):
    """
    Retrieves the telegram_id and x_handle of all participants for a specific raid.
    Returns a list of tuples: [(telegram_id, x_handle), ...]
    """
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        # This query joins the users and raid_participants tables to get the required info
        cursor.execute("""
            SELECT u.telegram_id, u.x_handle
            FROM users u
            JOIN raid_participants p ON u.telegram_id = p.telegram_id
            WHERE p.raid_id = ?
        """, (raid_id,))
        return cursor.fetchall()


def get_groups_for_user(telegram_id):
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT group_name FROM user_groups WHERE telegram_id = ?", (
                telegram_id,)
        )
        return [item[0] for item in cursor.fetchall()]

# --- RAID MANAGEMENT FUNCTIONS ---


def add_raid_link_and_mark_submitted(raid_id, telegram_id, url):
    """
    Atomically checks if a user has submitted, and if not, adds their link and marks them as submitted.
    Returns True on success, False if they had already submitted.
    """
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        # First, check if the user has already submitted a link for this raid.
        cursor.execute(
            "SELECT 1 FROM raid_participants WHERE raid_id = ? AND telegram_id = ? AND has_submitted_link = 1",
            (raid_id, telegram_id)
        )
        if cursor.fetchone():
            return False  # User has already submitted, do nothing.

        # If not, proceed to add the link and update their status.
        # Add the link to the general raid pool.
        cursor.execute(
            "INSERT OR IGNORE INTO raid_links (raid_id, url) VALUES (?, ?)", (raid_id, url))

        # Mark the user as having submitted. This will create or update their participant record.
        cursor.execute("""
            INSERT INTO raid_participants (raid_id, telegram_id, has_submitted_link) VALUES (?, ?, 1)
            ON CONFLICT(raid_id, telegram_id) DO UPDATE SET has_submitted_link = 1
        """, (raid_id, telegram_id))

        return True  # Action was successful.


# (create_new_raid, get_active_raid_id, etc. are unchanged)
def create_new_raid(group_id, submission_deadline_timestamp, engagement_deadline_timestamp):
    """Creates a new raid with the two distinct deadlines."""
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        current_time = int(time.time())
        cursor.execute(
            "INSERT INTO raids (group_id, start_timestamp, submission_deadline_timestamp, engagement_deadline_timestamp) VALUES (?, ?, ?, ?)",
            (group_id, current_time, submission_deadline_timestamp,
             engagement_deadline_timestamp)
        )
        return cursor.lastrowid


def get_active_raid_id(group_id):
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT raid_id FROM raids WHERE group_id = ? AND is_active = 1", (group_id,))
        raid_data = cursor.fetchone()
        return raid_data[0] if raid_data else None


def get_active_raid_details(group_id):
    """
    Checks for an active raid and returns its ID and both deadlines.
    Returns: (raid_id, submission_deadline_ts, engagement_deadline_ts) or None.
    """
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT raid_id, submission_deadline_timestamp, engagement_deadline_timestamp FROM raids WHERE group_id = ? AND is_active = 1",
            (group_id,)
        )
        return cursor.fetchone()


def deactivate_raid(raid_id):
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE raids SET is_active = 0 WHERE raid_id = ?", (raid_id,))


def get_links_for_raid(raid_id):
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT url FROM raid_links WHERE raid_id = ?", (raid_id,)
        )
        return [item[0] for item in cursor.fetchall()]
