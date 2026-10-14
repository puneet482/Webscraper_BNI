import asyncio

import pandas as pd
from playwright.async_api import async_playwright

START_URL = ("https://bni-india.in/en-IN/chapterlist?countryIds=3857&regionId=&chapterName="
             "&chapterCity=&chapterArea=&chapterMeetingDay=&chapterMeetingTime=&chapterMeetingType=")
OUTPUT_FILE = "chapter_list.csv"

LINK_SELECTOR = "#chapterListTable a[href*='chapterdetail']"   # chapter names inside the table
NEXT_SELECTOR = ".paginate_button.next:not(.disabled)"     # DataTables "Next" button

COLUMNS = ["Chapter URL", "Chapter", "City", "Area",
           "Meeting Day", "Meeting Time", "Meeting Type"]


async def read_rows(page):
    """Return every chapter row currently shown: its link, name and the other cells."""
    return await page.evaluate("""(sel) => {
        // textContent (not innerText) so cells hidden by the responsive table are still read
        const clean = t => (t || '').replace(/\\s+/g, ' ').trim();
        return [...document.querySelectorAll(sel)].map(a => {
            const tr = a.closest('tr');
            const cells = tr ? [...tr.querySelectorAll('td')].map(td => clean(td.textContent)) : [];
            return {href: a.href, name: clean(a.textContent), cells: cells};
        });
    }""", LINK_SELECTOR)


async def show_max_entries(page):
    """If the table has a 'Show N entries' dropdown, pick the biggest option (fewer pages)."""
    try:
        dropdown = page.locator("select[name$='_length']")
        if await dropdown.count() == 0:
            return
        values = await dropdown.first.locator("option").evaluate_all(
            "els => els.map(e => e.value)")
        best = "-1" if "-1" in values else max(values, key=lambda v: int(v))
        await dropdown.first.select_option(best)
        await page.wait_for_timeout(1500)
        print(f"Set page size to {best}")
    except Exception as e:
        print(f"(could not change page size: {e})")


def to_record(row):
    cells, name = row["cells"], row["name"]
    start = next((i for i, c in enumerate(cells) if c == name), 0)
    rest = (cells[start + 1:] + [""] * 5)[:5]
    return dict(zip(COLUMNS, [row["href"], name] + rest))


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1600, "height": 900},     # wide, so no columns get collapsed
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        page = await context.new_page()

        await page.goto(START_URL, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector(LINK_SELECTOR, state="attached", timeout=30000)
        except Exception:
            await page.screenshot(path="debug_chapterlist.png", full_page=True)
            open("debug_chapterlist.html", "w", encoding="utf-8").write(await page.content())
            print("No chapter links found. Saved debug_chapterlist.png / .html - send me those.")
            await browser.close()
            return

        await show_max_entries(page)

        chapters, page_no, retried = {}, 1, False
        while True:
            rows = await read_rows(page)
            new = 0
            for r in rows:
                if r["href"] not in chapters:
                    chapters[r["href"]] = to_record(r)
                    new += 1
            print(f"Page {page_no}: {len(rows)} rows, {new} new, {len(chapters)} chapters total")

            if new == 0 and not retried:          # table may still be loading: look once more
                retried = True
                await page.wait_for_timeout(1500)
                continue
            retried = False

            nxt = page.locator(NEXT_SELECTOR)
            if new == 0 or await nxt.count() == 0:
                break
            await nxt.first.click()
            page_no += 1
            await page.wait_for_timeout(800)

        await browser.close()

    df = pd.DataFrame(list(chapters.values()), columns=COLUMNS)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"\nSaved {len(df)} chapters to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())