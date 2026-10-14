import asyncio
import os
import re

import pandas as pd
from lxml import etree
from playwright.async_api import async_playwright

# ---------------------------------------------------------------- settings
INPUT_FILE = "member_links.csv"      
OUTPUT_FILE = "member_details.csv"
WORKERS = 8          
SAVE_EVERY = 100     
MAX_RETRIES = 3

COLUMNS = [
    "Name", "Company", "Profession", "Speciality",
    "Phone", "Direct", "Mobile", "Chapter", "Company Address",
    "Website", "Facebook", "Instagram", "LinkedIn",
    "My Business", "Ideal Referral", "Top Product",
    "Top Problem Solved", "My Ideal Referral Partner",
    "Favourite BNI Story", "Profile URL",
]

SECTIONS = {
    "Ideal Referral": "ideal referral",
    "Top Product": "top product",
    "Top Problem Solved": "top problem solved",
    "My Ideal Referral Partner": "my ideal referral partner",
    "Favourite BNI Story": "my favourite bni story",
}

BLOCKED = ["bni-india", "bniindia", "bnitos", "youtube.com", "linkedin.com/groups",
           "newsletter", "privacy", "terms", "cookie"]


# ---------------------------------------------------------------- helpers
def read_csv(path):
    """Read a CSV as text (keeps phone numbers intact) and clean the headers."""
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    df.columns = df.columns.str.replace("\ufeff", "", regex=False).str.strip()
    return df


def save(results):
    df = pd.DataFrame(results, columns=COLUMNS).drop_duplicates(subset=["Profile URL"])
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"--- saved {len(df)} profiles ---")


def clean_phone(raw):
    """'tel:+91 95336-90062' -> '9533690062'.
    Removes the 91 country code / leading 0, so numbers are 10 digits and Excel
    shows them in full. Placeholders / too-short values -> ''."""
    raw = str(raw).replace("tel:", "").strip()
    if "e+" in raw.lower():                 
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) < 7 or len(set(digits)) == 1:
        return ""
    return digits


def get_section(tree, title):
    """Find an <h3> whose text equals `title` (any case) and return the text after it."""
    lower, upper = "abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    heads = tree.xpath(f"//h3[translate(normalize-space(.), '{upper}', '{lower}')='{title}']")
    if not heads:
        return ""
    node = heads[0]
    for _ in range(3):                      
        parts = []
        for sib in node.itersiblings():
            if sib.tag == "h3" or sib.xpath(".//h3"):
                break
            parts.append(" ".join(sib.itertext()))
        text = " ".join(" ".join(parts).split())
        if text:
            return text
        node = node.getparent()
        if node is None:
            break
    return ""


# ---------------------------------------------------------------- parsing
def parse_profile(html, row):
    """Turn one profile page's HTML into a dict of fields."""
    tree = etree.HTML(html)
    data = {col: "" for col in COLUMNS}
    data["Chapter"] = row.get("Chapter", "")
    data["Profile URL"] = row["URL"]

    info = "//div[contains(@class,'memberProfileInfo')]"
    data["Name"] = tree.xpath(f"normalize-space({info}//h2)")
    data["Company"] = tree.xpath(f"normalize-space({info}//h2/following-sibling::p[1])")

    
    profession, _, speciality = tree.xpath(f"normalize-space({info}//h6)").partition("|")
    data["Profession"], data["Speciality"] = profession.strip(), speciality.strip()

    
    hrefs = tree.xpath("//ul[contains(@class,'memberContactInfo')]//a[starts-with(@href,'tel:')]/@href")
    numbers = []
    for h in hrefs:
        n = clean_phone(h)
        if n and n not in numbers:
            numbers.append(n)
    for column, number in zip(["Phone", "Direct", "Mobile"], numbers):
        data[column] = number

    address = tree.xpath("//section[contains(@class,'widgetMemberCompanyDetail')]"
                         "//div[@class='textHolder']/h6//text()")
    data["Company Address"] = ", ".join(a.strip() for a in address if a.strip())

    chapter = tree.xpath("normalize-space(//strong[text()='Chapter']/../a)")
    if chapter:
        data["Chapter"] = re.sub(r"^bni\s+", "", chapter, flags=re.I)   

  
    for href in tree.xpath("//a/@href"):
        href = href.strip()
        low = href.lower()
        if low.startswith(("tel:", "mailto:")) or any(b in low for b in BLOCKED):
            continue
        if "facebook.com" in low:
            data["Facebook"] = data["Facebook"] or href
        elif "instagram.com" in low:
            data["Instagram"] = data["Instagram"] or href
        elif "linkedin.com" in low:
            data["LinkedIn"] = data["LinkedIn"] or href
        elif low.startswith("http") and "bni" not in low:
            data["Website"] = data["Website"] or href

    business = tree.xpath("//section[contains(@class,'widgetMemberTxtVideo')]//p//text()")
    data["My Business"] = " ".join(b.strip() for b in business if b.strip())

    for column, title in SECTIONS.items():
        data[column] = get_section(tree, title)

    return data


async def scrape_member(context, row):
    """Open one profile and parse it. Returns a dict, or None if all retries fail."""
    page = await context.new_page()
    try:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await page.goto(row["URL"], wait_until="domcontentloaded", timeout=15000)
                if "/memberdetails" not in page.url:
                    raise RuntimeError(f"redirected to {page.url}")
                await page.wait_for_selector(".memberProfileInfo", timeout=15000)
                return parse_profile(await page.content(), row)
            except Exception as e:
                print(f"  attempt {attempt} failed for {row['URL']}: {e}")
                await asyncio.sleep(2)
        return None
    finally:
        await page.close()


async def worker(context, queue, results, counter):
    """Keeps taking members from the queue until it is empty."""
    while not queue.empty():
        row = queue.get_nowait()
        counter[0] += 1
        print(f"Member {counter[0]}: {row.get('Member') or row['URL']}")

        data = await scrape_member(context, row)
        if data:                                   
            results.append(data)
            if len(results) % SAVE_EVERY == 0:
                save(results)
        await asyncio.sleep(0.3)


async def main():
    links = read_csv(INPUT_FILE)
    print(f"Total profiles: {len(links)}")

    results = read_csv(OUTPUT_FILE).to_dict("records") if os.path.exists(OUTPUT_FILE) else []
    done = {r["Profile URL"] for r in results}
    todo = links[~links["URL"].isin(done)]
    print(f"Already scraped: {len(done)} | Remaining: {len(todo)}")

    queue = asyncio.Queue()
    for _, row in todo.iterrows():
        queue.put_nowait(row.to_dict())

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        counter = [0]
        await asyncio.gather(*(worker(context, queue, results, counter) for _ in range(WORKERS)))
        await browser.close()

    for r in results:
        for col in ("Phone", "Direct", "Mobile"):
            r[col] = clean_phone(r[col])

    save(results)

    try:
        pd.DataFrame(results, columns=COLUMNS).drop_duplicates(subset=["Profile URL"]) \
            .to_excel("member_details.xlsx", index=False)
        print("Also saved member_details.xlsx")
    except ImportError:
        print("Run: pip install openpyxl   (to also get an .xlsx file)")
    print("Finished.")


if __name__ == "__main__":
    asyncio.run(main())