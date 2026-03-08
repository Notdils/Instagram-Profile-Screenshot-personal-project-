"""
Instagram Post Screenshot Scraper
----------------------------------
Connects to YOUR already-open Edge browser (where you're logged into Instagram)
and screenshots posts from a target user.

STEP 1 - Kill any existing Edge processes, then launch Edge with remote debugging.
  Run this in CMD:

    taskkill /F /IM msedge.exe /T
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222

  Edge will open and the CMD window will stay "frozen" — that is correct.

STEP 2 - Log into Instagram manually in that Edge window.

STEP 3 - Open a NEW terminal and run this script:
    python instagram_scraper.py --user TARGET_USERNAME --count 12

OUTPUT:
  Screenshots saved to ./instagram_screenshots/
"""

import asyncio
import argparse
from pathlib import Path
from playwright.async_api import async_playwright


SCREENSHOT_DIR = Path("instagram_screenshots")
DEBUG_PORT = 9222


async def scrape_user_posts(page, target_username: str, count: int):
    SCREENSHOT_DIR.mkdir(exist_ok=True)

    print(f"[*] Navigating to @{target_username}'s profile...")
    await page.goto(
        f"https://www.instagram.com/{target_username}/",
        wait_until="domcontentloaded"
    )
    await page.wait_for_timeout(3000)

    if "Page Not Found" in await page.title():
        print(f"[!] Could not find Instagram user: @{target_username}")
        return

    print(f"[*] Collecting up to {count} post links...")
    post_links = set()

    for _ in range(max(1, count // 6 + 2)):
        anchors = await page.locator('a[href*="/p/"]').all()
        for a in anchors:
            href = await a.get_attribute("href")
            if href and "/p/" in href:
                full = f"https://www.instagram.com{href}" if href.startswith("/") else href
                post_links.add(full.split("?")[0])
        if len(post_links) >= count:
            break
        await page.evaluate("window.scrollBy(0, 1200)")
        await page.wait_for_timeout(1500)

    post_links = list(post_links)[:count]
    if not post_links:
        print("[!] No posts found. Make sure you're logged into Instagram in Edge and the account exists.")
        return

    print(f"[✓] Found {len(post_links)} posts. Starting screenshots...")

    for i, link in enumerate(post_links, 1):
        try:
            await page.goto(link, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)

            article = page.locator("article").first
            filename = SCREENSHOT_DIR / f"{target_username}_post_{i:03d}.png"

            if await article.count() > 0:
                await article.screenshot(path=str(filename))
            else:
                await page.screenshot(path=str(filename), full_page=False)

            print(f"  [{i}/{len(post_links)}] Saved: {filename.name}")
            await page.wait_for_timeout(1000)

        except Exception as e:
            print(f"  [{i}/{len(post_links)}] Failed on {link}: {e}")

    print(f"\n[✓] Done! {len(post_links)} screenshots saved to ./{SCREENSHOT_DIR}/")


async def main(target: str, count: int):
    async with async_playwright() as p:
        print(f"[*] Connecting to Edge on port {DEBUG_PORT}...")
        try:
            browser = await p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}")
        except Exception:
            print("\n[!] Could not connect to Edge. Steps to fix:")
            print("    1. Run:  taskkill /F /IM msedge.exe /T")
            print('    2. Run:  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" --remote-debugging-port=9222')
            print("    3. Log into Instagram in the Edge window that opens")
            print("    4. Open a NEW terminal and run this script again\n")
            return

        contexts = browser.contexts
        if not contexts:
            print("[!] No browser contexts found. Open a tab in Edge and try again.")
            return

        pages = contexts[0].pages
        page = pages[0] if pages else await contexts[0].new_page()

        print("[✓] Connected to Edge successfully.")
        await scrape_user_posts(page, target, count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Instagram scraper using your existing Edge session")
    parser.add_argument("--user",  required=True, help="Target Instagram username to scrape")
    parser.add_argument("--count", type=int, default=12, help="Number of posts to screenshot (default: 12)")
    args = parser.parse_args()

    asyncio.run(main(target=args.user, count=args.count))
