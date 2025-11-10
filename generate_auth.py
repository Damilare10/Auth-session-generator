# generate_auth_for_bot.py
# FINAL VERSION: Solves the "Target Closed" race condition and is .exe compatible.

import asyncio
import os
import sys
from playwright.async_api import async_playwright

OUTPUT_FILENAME = "auth_session.json"

# Forces the .exe to look for the browser in the correct manual installation folder.
CHROME_EXECUTABLE_PATH = os.path.join(
    os.getenv('LOCALAPPDATA'),
    'ms-playwright',
    'chromium-1187',
    'chrome-win',
    'chrome.exe'
)

# Creates a persistent browser profile folder next to the .exe for reliable logins.
PERSISTENT_PROFILE_PATH = os.path.join(os.getcwd(), "auth_profile_data")


async def main():
    if not os.path.exists(CHROME_EXECUTABLE_PATH):
        print("\n" + "!"*60)
        print("FATAL ERROR: Manual Browser Installation Not Found!")
        print("The script could not find chrome.exe at the expected location.")
        print(
            "\nPlease ensure you have manually downloaded and extracted the browser files")
        print("into the correct folder as per the bot's /help instructions.")
        print(f"\nExpected Location: {CHROME_EXECUTABLE_PATH}")
        print("!"*60)
        return

    print("--- Using Manual Override and Persistent Profile ---")

    try:
        async with async_playwright() as p:
            browser_context = await p.chromium.launch_persistent_context(
                user_data_dir=PERSISTENT_PROFILE_PATH,
                headless=False,
                executable_path=CHROME_EXECUTABLE_PATH,
                args=["--disable-blink-features=AutomationControlled"]
            )

            page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()

            print("\n" + "="*60)
            print("ACTION REQUIRED:")
            print(
                "1. The browser window has opened. Please log in to your X.com account.")
            print("2. When you are fully logged in and can see your timeline...")
            print("   !!! DO NOT CLOSE THE BROWSER YOURSELF !!!")
            print("3. Come back to THIS black window and PRESS THE ENTER KEY.")
            print("="*60)

            await page.goto("https://x.com/login", timeout=60000)

            # Wait for user input in the console instead of waiting for the browser to close.
            input("\n>>> Press ENTER here after you have logged in... <<<")

            print("\nLogin complete. Saving your session file...")
            await browser_context.storage_state(path=OUTPUT_FILENAME)

            print("Session saved successfully. Closing browser for you...")
            await browser_context.close()

            print("\n" + "="*60)
            print("✅ ALL DONE!")
            print(
                f"Your session file '{OUTPUT_FILENAME}' has been saved in the same folder as this app.")
            print("You can now go back to the Telegram bot and upload this file.")
            print("="*60)

    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")

if __name__ == "__main__":
    print("--- X.com Auth File Generator for Comment Verifier Bot ---")
    asyncio.run(main())
    print("\nPress Enter to exit.")
    input()
