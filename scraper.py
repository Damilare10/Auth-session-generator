# scraper.py
import asyncio
import os
import random
from collections import Counter, defaultdict
from playwright.async_api import async_playwright


async def human_wait(min_s=0.5, max_s=1.2):
    """Waits for a random short period to mimic human behavior."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def scrape_single_tweet(context, tweet_url: str) -> set:
    """
    Scrapes a single tweet URL for all unique commenter handles.
    It scrolls, clicks "Show more replies", AND clicks "Show probable spam"
    before extracting handles.
    """
    usernames = set()
    page = await context.new_page()
    try:
        await page.goto(tweet_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector('article[data-testid="tweet"]', timeout=30000)
        await human_wait()

        print("Scrolling to load initial comments...")
        for i in range(5):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await human_wait(1.5, 2.5)

        # --- UPGRADED: CLICK-TO-REVEAL LOGIC ---
        try:
            # PART 1: Click all "Show more replies" buttons in a loop
            print("Looking for 'Show more replies' buttons...")
            while True:
                show_more_button = page.locator(
                    'div[role="button"]:has-text("Show more replies")')
                if await show_more_button.count() == 0:
                    show_more_button = page.locator(
                        'div[role="button"]:has-text("Show")')

                if await show_more_button.count() > 0:
                    print("Found 'Show more' button, clicking to reveal...")
                    await show_more_button.first.click()
                    await human_wait(2.0, 3.0)  # Wait for new comments to load
                else:
                    print("No more 'Show more' buttons found.")
                    break

            # PART 2: Specifically look for and click the "Show probable spam" link
            print("Looking for a 'Show probable spam' link...")
            # Playwright's get_by_text is perfect for this. It finds the element with that exact text.
            spam_link = page.get_by_text("Show probable spam")

            # Check if the element is actually visible on the page before trying to click
            if await spam_link.count() > 0 and await spam_link.is_visible():
                print("Found 'Show probable spam', clicking to reveal...")
                await spam_link.click()
                # Wait for the spam comments to load
                await human_wait(2.0, 3.0)
            else:
                print("No 'Show probable spam' link found.")

        except Exception as e:
            # This is not a critical error, as these buttons won't always exist.
            print(
                f"Could not click a reveal button (this is normal if none exist): {e}")
        # --- END OF UPGRADED LOGIC ---

        print("Finished revealing comments. Now extracting all handles...")
        all_tweets = page.locator('article[data-testid="tweet"]')
        count = await all_tweets.count()
        print(f"Found {count} total tweet/comment articles on the page.")

        if count > 1:
            for i in range(1, count):
                comment_article = all_tweets.nth(i)
                try:
                    user_link_locator = comment_article.locator(
                        'div[data-testid="User-Name"] a[href^="/"][role="link"]')
                    if await user_link_locator.count() > 0:
                        href = await user_link_locator.first.get_attribute("href")
                        if href:
                            handle = f"@{href.lstrip('/')}".lower()
                            usernames.add(handle)
                except Exception:
                    continue

    except Exception as e:
        print(f"An error occurred while scraping {tweet_url}: {e}")
    finally:
        await page.close()

    if usernames:
        print(
            f"✅ Success! Extracted {len(usernames)} unique handles from {tweet_url}.")
    else:
        print(f"⚠️ No handles were extracted from {tweet_url}.")

    return usernames


async def run_scrape_and_check(participant_ids: list, tweet_urls: list, target_usernames: list) -> str:
    """
    Orchestrates scraping and checking with case-insensitive matching.
    """
    all_auth_files = []
    for user_id in participant_ids:
        user_auth_dir = f"user_data/{user_id}/"
        if os.path.exists(user_auth_dir):
            all_auth_files.extend(
                [os.path.join(user_auth_dir, f) for f in os.listdir(
                    user_auth_dir) if f.endswith('.json')]
            )

    if not all_auth_files:
        return "❌ **Error:** No authentication files found for any of the raid participants. Cannot perform verification."

    found_handles_by_url = defaultdict(set)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])

        for i, url in enumerate(tweet_urls):
            auth_file_to_use = random.choice(all_auth_files)
            print(
                f"--- Attempting to use storage state from: {auth_file_to_use} ---")

            context = await browser.new_context(storage_state=auth_file_to_use)
            # The returned set from this function now contains ONLY lowercase handles
            handles_from_tweet = await scrape_single_tweet(context, url)
            found_handles_by_url[url] = handles_from_tweet
            await context.close()

    # --- REVISED CROSS-REFERENCING LOGIC ---
    # We will store counts against the user's ORIGINAL handle for the report.
    user_comment_counts = Counter()
    for target_handle in target_usernames:
        # For the check, convert the target handle to lowercase.
        target_handle_lower = target_handle.lower()

        for found_handles_set in found_handles_by_url.values():
            # Now we compare a lowercase handle to a set of lowercase handles.
            if target_handle_lower in found_handles_set:
                # But we increment the counter for the original, properly capitalized handle.
                user_comment_counts[target_handle] += 1

    # --- The report generation remains the same ---
    total_links_checked = len(tweet_urls)
    report = f"✅ **Verification Report** ✅\n\nChecked **{total_links_checked}** random links. The following raid participants were found:\n\n"

    found_users = sorted([handle for handle, count in user_comment_counts.items(
    ) if count > 0], key=lambda x: user_comment_counts[x], reverse=True)

    if found_users:
        for handle in found_users:
            count = user_comment_counts[handle]
            report += f" • `{handle}` - Commented on **{count} of {total_links_checked}** links.\n"
    else:
        report += "_None of the participants were found in the comments._\n"

    report += "\n"

    not_found_users = [
        handle for handle in target_usernames if user_comment_counts[handle] == 0]

    if not_found_users:
        report += f"❌ **Participants NOT Found:**\n"
        for handle in not_found_users:
            report += f" • `{handle}`\n"

    return report
