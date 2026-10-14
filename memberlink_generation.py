import asyncio
import os
from urllib.parse import urljoin, urlparse, parse_qs

import pandas as pd
from playwright.async_api import async_playwright

OUTPUT_FILE = "member_links.csv"

CHAPTER_URLS = [
    "PASTE FULL CHAPTER URL HERE",
    "https://bni-india.in/en-IN/chapterdetail?chapterId=R8NtNFIn2t%2FuQ862UAXnVg%3D%3D&name=BNI+Aastha",
]


def chapter_name_from_url(url):
    qs = parse_qs(urlparse(url).query)
    name = qs.get("name", [""])[0].replace("+", " ").strip()
    if name.lower().startswith("bni "):
        name = name[4:].strip()
    return name


async def collect_chapter(page, chapter_url):
    await page.goto(chapter_url, wait_until="domcontentloaded", timeout=30000)
    # state="attached": the table exists in the page even if the Members tab isn't shown
    await page.wait_for_selector("#chapterListTable a.linkone", state="attached", timeout=20000)
    await page.wait_for_timeout(1000)

    chapter = chapter_name_from_url(chapter_url)
    rows, seen = [], set()

    while True:
        links = await page.query_selector_all("#chapterListTable tbody tr a.linkone")
        for a in links:
            href = await a.get_attribute("href")
            name = (await a.inner_text()).strip()
            if not href or "memberdetails" not in href:
                continue
            full = urljoin(page.url, href)
            if full in seen:
                continue
            seen.add(full)
            rows.append({"URL": full, "Chapter": chapter, "Member": name})

        # If the table is paginated, click "Next" until it is disabled
        nxt = await page.query_selector("#chapterListTable_next:not(.disabled)")
        if not nxt:
            break
        await nxt.click()
        await page.wait_for_timeout(500)

    print(f"{chapter or chapter_url}: {len(rows)} members")
    return rows


async def main():
    all_rows = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        )
        page = await context.new_page()

        for url in CHAPTER_URLS:
            if url.startswith("PASTE"):
                continue
            try:
                all_rows += await collect_chapter(page, url)
            except Exception as e:
                print(f"FAILED {url}: {e}")

        await browser.close()

    if not all_rows:
        print("No links collected.")
        return

    new_df = pd.DataFrame(all_rows)

    if os.path.exists(OUTPUT_FILE):
        old_df = pd.read_csv(OUTPUT_FILE, dtype=str, encoding="utf-8-sig")
        old_df.columns = old_df.columns.str.replace("\ufeff", "", regex=False).str.strip()
        new_df = pd.concat([old_df, new_df], ignore_index=True)

    new_df.drop_duplicates(subset=["URL"], inplace=True)
    new_df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"\nSaved {len(new_df)} links to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())