"""
Instagram Post Screenshot Scraper + Gemini Analysis
-----------------------------------------------------
Connects to YOUR already-open Edge browser, screenshots posts,
then sends the first image of each post to Gemini for analysis.

SETUP:
    pip install playwright google-generativeai
    playwright install chromium

STEP 1 - Kill Edge and relaunch with remote debugging:
    taskkill /F /IM msedge.exe /T
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222

STEP 2 - Log into Instagram in that Edge window.

STEP 3 - Open a NEW terminal and run:
    python instagram_scraper.py --user TARGET_USERNAME --count 12 --gemini-key YOUR_API_KEY

OUTPUT:
  instagram_screenshots/   — all post images
  instagram_analysis/      — gemini_analysis_USERNAME.txt with extracted info
"""

import asyncio
import argparse

from pathlib import Path
from playwright.async_api import async_playwright

SCREENSHOT_DIR = Path("instagram_screenshots")
ANALYSIS_DIR   = Path("instagram_analysis")
DEBUG_PORT     = 9222
GEMINI_BATCH   = 10  # Gemini's max images per prompt


# ── Gemini helpers ────────────────────────────────────────────────────────────


def analyze_with_gemini(api_key: str, image_paths: list[Path], post_numbers: list[int]) -> str:
    """Send up to 10 images to Gemini and return the analysis text."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})

    parts = [
        types.Part.from_text(text=
            "You are analyzing Instagram post images. "
            "For each image below, extract ALL information visible — "
            "including any text, titles, dates, names, links, event details, "
            "announcements, or design elements. Be thorough and structured.\n\n"
        )
    ]

    for img_path, post_num in zip(image_paths, post_numbers):
        parts.append(types.Part.from_text(text=f"--- POST {post_num} (file: {img_path.name}) ---\n"))
        parts.append(types.Part.from_bytes(
            data=open(img_path, "rb").read(),
            mime_type="image/png"
        ))
        parts.append(types.Part.from_text(text="\n"))

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=parts
    )
    return response.text


# ── Scraping helpers ──────────────────────────────────────────────────────────

async def screenshot_carousel(page, post_index: int, target_username: str):
    """Screenshots every slide in a post/carousel. Returns list of saved Paths."""
    saved_paths = []
    await page.wait_for_timeout(2000)

    img_index = 1
    while True:
        article = page.locator("article").first
        filename = SCREENSHOT_DIR / f"{target_username}_post_{post_index:03d}_img_{img_index:02d}.png"

        if await article.count() > 0:
            await article.screenshot(path=str(filename))
        else:
            await page.screenshot(path=str(filename), full_page=False)

        saved_paths.append(filename)

        next_btn = page.locator('button[aria-label="Next"]').first
        if await next_btn.count() == 0 or not await next_btn.is_visible():
            break

        await next_btn.click()
        await page.wait_for_timeout(800)
        img_index += 1

        if img_index > 20:
            break

    return saved_paths


async def scrape_user_posts(page, target_username: str, count: int, gemini_key: str | None):
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    if gemini_key:
        ANALYSIS_DIR.mkdir(exist_ok=True)

    print(f"[*] Navigating to @{target_username}'s profile...")
    await page.goto(
        f"https://www.instagram.com/{target_username}/",
        wait_until="domcontentloaded"
    )
    await page.wait_for_timeout(3000)

    if "Page Not Found" in await page.title():
        print(f"[!] Could not find Instagram user: @{target_username}")
        return

    print(f"[*] Collecting up to {count} post links (in order)...")
    post_links = []
    seen = set()

    for _ in range(max(1, count // 6 + 2)):
        anchors = await page.locator('a[href*="/p/"]').all()
        for a in anchors:
            href = await a.get_attribute("href")
            if href and "/p/" in href:
                full = f"https://www.instagram.com{href}" if href.startswith("/") else href
                clean = full.split("?")[0]
                if clean not in seen:
                    seen.add(clean)
                    post_links.append(clean)
        if len(post_links) >= count:
            break
        await page.evaluate("window.scrollBy(0, 1200)")
        await page.wait_for_timeout(1500)

    post_links = post_links[:count]
    if not post_links:
        print("[!] No posts found.")
        return

    print(f"[✓] Found {len(post_links)} posts. Starting screenshots...")

    # post_index → first image path (for Gemini)
    first_images: dict[int, Path] = {}
    total_images = 0

    for i, link in enumerate(post_links, 1):
        try:
            await page.goto(link, wait_until="domcontentloaded")
            saved = await screenshot_carousel(page, i, target_username)
            total_images += len(saved)
            first_images[i] = saved[0]   # only first image per post goes to Gemini
            print(f"  [{i}/{len(post_links)}] {len(saved)} image(s) — first: {saved[0].name}")
            await page.wait_for_timeout(800)
        except Exception as e:
            print(f"  [{i}/{len(post_links)}] Failed: {e}")

    print(f"\n[✓] Screenshots done! {total_images} total images saved to ./{SCREENSHOT_DIR}/")

    # ── Gemini analysis ───────────────────────────────────────────────────────
    if not gemini_key:
        print("\n[!] No Gemini API key provided — skipping analysis.")
        print("    Re-run with --gemini-key YOUR_KEY to enable analysis.")
        return

    print(f"\n[*] Sending first images to Gemini (batch size: {GEMINI_BATCH})...")

    sorted_posts  = sorted(first_images.keys())
    all_responses = []

    # Process in batches of 10
    for batch_start in range(0, len(sorted_posts), GEMINI_BATCH):
        batch_posts  = sorted_posts[batch_start: batch_start + GEMINI_BATCH]
        batch_images = [first_images[p] for p in batch_posts]
        batch_label  = f"posts {batch_posts[0]}–{batch_posts[-1]}"

        print(f"  [*] Analyzing {batch_label}...")
        try:
            response_text = analyze_with_gemini(api_key=gemini_key,
                                                image_paths=batch_images,
                                                post_numbers=batch_posts)
            all_responses.append(f"{'='*60}\nBATCH: {batch_label}\n{'='*60}\n{response_text}\n")
            print(f"  [✓] Batch {batch_label} analyzed.")
        except Exception as e:
            all_responses.append(f"{'='*60}\nBATCH: {batch_label} — ERROR: {e}\n{'='*60}\n")
            print(f"  [!] Batch {batch_label} failed: {e}")

    # Save to txt
    output_file = ANALYSIS_DIR / f"gemini_analysis_{target_username}.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"Gemini Analysis — @{target_username}\n")
        f.write(f"Total posts analyzed: {len(first_images)}\n")
        f.write(f"Images per post: first image only\n\n")
        f.write("\n".join(all_responses))

    print(f"\n[✓] Analysis saved to ./{output_file}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main(target: str, count: int, gemini_key: str | None):
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
            print("[!] No browser contexts found.")
            return

        pages = contexts[0].pages
        page  = pages[0] if pages else await contexts[0].new_page()

        print("[✓] Connected to Edge successfully.")
        await scrape_user_posts(page, target, count, gemini_key)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Instagram scraper + Gemini image analysis")
    parser.add_argument("--user",       required=True,  help="Target Instagram username")
    parser.add_argument("--count",      type=int, default=12, help="Number of posts (default: 12)")
    parser.add_argument("--gemini-key", default=None,   help="Gemini API key for image analysis")
    args = parser.parse_args()

    asyncio.run(main(
        target=args.user,
        count=args.count,
        gemini_key=args.gemini_key,
    ))
