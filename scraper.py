import os
import re
import json
import time
import hashlib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")
DB_FILE = "jobs_database.json"

# =====================================================================
# 1. ADD OR EDIT JOB SOURCES HERE
# =====================================================================
# RSS Feeds from Indie & Remote Game Boards
RSS_SOURCES = [
    {"name": "Work With Indies", "url": "https://www.workwithindies.com/careers/rss.xml"},
    {"name": "RemoteFirst QA", "url": "https://remotefirstjobs.com/rss/jobs/qa.rss"},
    {"name": "RemoteFirst Entry-Level", "url": "https://remotefirstjobs.com/rss/jobs/entry-level.rss"},
]

# Direct Studio Career Boards (Greenhouse Public JSON API - avoids fake/middleman posts)
# You can add any studio's Greenhouse board token to this list at any time
GREENHOUSE_STUDIOS = [
    {"name": "Epic Games", "token": "epicgames"},
    {"name": "Bungie", "token": "bungie"},
    {"name": "Riot Games", "token": "riotgames"},
    {"name": "Certain Affinity", "token": "certainaffinity"},
    {"name": "Digital Extremes", "token": "digitalextremes"},
]

# =====================================================================
# 2. TARGET ROLES & HARD BLOCKLIST FILTERS
# =====================================================================
ROLE_CATEGORIES = {
    "Tech & Systems Design": [
        "technical designer", "technical game designer", "systems designer",
        "system designer", "combat designer", "gameplay designer", "gameplay scripter",
        "blueprint", "unreal", "ue5", "godot", "mechanics designer", "ai designer"
    ],
    "Level & Game Design": [
        "level designer", "world designer", "encounter designer", "mission designer",
        "game designer", "junior designer", "associate designer", "quest designer"
    ],
    "QA & Playtesting": [
        "technical qa", "qa analyst", "qa tester", "quality assurance",
        "dev qa", "development qa", "playtester", "game tester", "qa engineer"
    ]
}

# Anything matching these words in the title is immediately thrown out
HARD_BLOCKLIST_TITLES = [
    # Art & Animation
    "animator", "animation", "3d artist", "2d artist", "concept artist",
    "character artist", "environment artist", "vfx artist", "lighting artist",
    "rigging", "rigger", "illustrator", "art director", "tech artist", "technical artist",
    "ui artist", "storyboard", "modeler",
    # Audio
    "audio", "sound designer", "composer", "voice",
    # Unobtainable Executive / Ultra-Senior Roles
    "director", "vice president", "vp ", "principal", "head of", "chief",
    "executive", "staff ", "sr. director", "senior director", "lead ", "senior lead" "senior", "sr.",
    # Corporate / Non-Dev / Spam
    "marketing", "recruiter", "talent acquisition", "accountant", "finance",
    "legal", "counsel", "sales", "public relations", "social media", "community manager",
    "copywriter", "localization", "monetization", "casino", "slot", "blockchain", "web3"
]

# Reject descriptions demanding massive years of experience
SENIOR_DESCRIPTION_PATTERNS = [
    r"\b[6-9]\+?\s*years",
    r"\b1[0-5]\+?\s*years",
    r"minimum of [6-9] years"
]


def classify_and_filter_job(title: str, description: str = "") -> dict:
    """
    Returns classification dict if job is relevant, or None if it should be blocked.
    """
    title_lower = title.lower()
    desc_lower = (description or "").lower()

    # 1. Hard blocklist check on title
    for blocked in HARD_BLOCKLIST_TITLES:
        if blocked in title_lower:
            return None

    # 2. Match against target role categories
    matched_category = None
    for category, keywords in ROLE_CATEGORIES.items():
        if any(kw in title_lower for kw in keywords):
            matched_category = category
            break

    # Also catch generic "Designer" or "QA" titles if description mentions Unreal/Godot/Games
    if not matched_category:
        if ("designer" in title_lower or "qa" in title_lower) and any(
            eng in (title_lower + " " + desc_lower) for eng in ["unreal", "godot", "unity", "game", "level", "combat"]
        ):
            matched_category = "Level & Game Design" if "design" in title_lower else "QA & Playtesting"
        else:
            return None

    # 3. Check description for extreme seniority requirements
    for pattern in SENIOR_DESCRIPTION_PATTERNS:
        if re.search(pattern, desc_lower):
            return None

    # 4. Detect Experience Level
    if any(w in title_lower for w in ["intern", "internship", "trainee", "apprentice"]):
        exp_level = "Internship"
    elif any(w in title_lower for w in ["junior", "jr", "entry", "graduate", "associate", "i ", " 1"]):
        exp_level = "Entry / Associate"
    elif any(w in title_lower for w in ["senior", "sr.", "sr "]):
        exp_level = "Senior"
    else:
        exp_level = "Mid / Standard"

    # 5. Detect Employment Type
    combined = title_lower + " " + desc_lower[:400]
    if "intern" in combined:
        job_type = "Internship"
    elif "part-time" in combined or "part time" in combined:
        job_type = "Part-Time"
    elif "contract" in combined or "freelance" in combined or "temp" in combined:
        job_type = "Contract"
    else:
        job_type = "Full-Time"

    return {
        "category": matched_category,
        "experience_level": exp_level,
        "job_type": job_type
    }


def make_id(url: str, title: str) -> str:
    raw = f"{url.strip()}_{title.strip()}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:12]


def scrape_rss_feeds() -> list:
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (GameJobRadar/2.0)"}
    for source in RSS_SOURCES:
        try:
            res = requests.get(source["url"], headers=headers, timeout=15)
            if res.status_code != 200:
                continue
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = item.findtext("description") or ""
                pub_date = item.findtext("pubDate") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

                meta = classify_and_filter_job(title, desc)
                if not meta:
                    continue

                # Extract company if formatted as "Company: Role" or "Role at Company"
                company = source["name"]
                if " at " in title:
                    parts = title.split(" at ")
                    company = parts[-1].strip()
                elif ":" in title:
                    parts = title.split(":", 1)
                    company = parts[0].strip()

                results.append({
                    "id": make_id(link, title),
                    "title": title,
                    "company": company,
                    "location": "Remote / Check Listing",
                    "category": meta["category"],
                    "experience_level": meta["experience_level"],
                    "job_type": meta["job_type"],
                    "url": link,
                    "date_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    "raw_date": pub_date,
                    "source": source["name"]
                })
        except Exception as e:
            print(f"[RSS Error - {source['name']}]: {e}")
    return results


def scrape_greenhouse_studios() -> list:
    results = []
    for studio in GREENHOUSE_STUDIOS:
        url = f"https://boards-api.greenhouse.io/v1/boards/{studio['token']}/jobs?content=true"
        try:
            res = requests.get(url, timeout=15)
            if res.status_code != 200:
                continue
            data = res.json()
            for job in data.get("jobs", []):
                title = job.get("title", "")
                desc = job.get("content", "")
                link = job.get("absolute_url", "")
                loc = (job.get("location") or {}).get("name", "Unspecified")
                updated = (job.get("updated_at") or "")[:10]

                meta = classify_and_filter_job(title, desc)
                if not meta:
                    continue

                results.append({
                    "id": make_id(link, title),
                    "title": title,
                    "company": studio["name"],
                    "location": loc,
                    "category": meta["category"],
                    "experience_level": meta["experience_level"],
                    "job_type": meta["job_type"],
                    "url": link,
                    "date_updated": updated or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "raw_date": updated,
                    "source": f"{studio['name']} (Direct ATS)"
                })
        except Exception as e:
            print(f"[Greenhouse Error - {studio['name']}]: {e}")
    return results


def send_discord_notification(new_jobs: list):
    if not DISCORD_WEBHOOK_URL or not new_jobs:
        return

    # Prioritize Entry/Associate and Tech/Systems Design roles first
    for job in new_jobs[:10]:
        is_entry_or_tech = (
            job["experience_level"] in ["Entry / Associate", "Internship"]
            or job["category"] == "Tech & Systems Design"
        )
        color = 0x10B981 if is_entry_or_tech else 0x3B82F6

        fields = [
            {"name": "🏢 Studio / Source", "value": job["company"], "inline": True},
            {"name": "🎯 Category", "value": job["category"], "inline": True},
            {"name": "📊 Level", "value": job["experience_level"], "inline": True},
            {"name": "💼 Type", "value": job["job_type"], "inline": True},
            {"name": "📍 Location", "value": job["location"], "inline": True},
            {"name": "🗓️ Updated", "value": job["date_updated"], "inline": True},
        ]

        description_links = f"👉 **[Click Here to View & Apply]({job['url']})**"
        if DASHBOARD_URL:
            description_links += f"\n📱 **[Open Mobile Job Dashboard]({DASHBOARD_URL})**"

        payload = {
            "username": "Game Job Radar",
            "embeds": [{
                "title": f"🎮 {job['title']}",
                "url": job["url"],
                "description": description_links,
                "color": color,
                "fields": fields,
                "footer": {"text": f"Source: {job['source']} • Filtered for Design & QA"}
            }]
        }
        try:
            requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            time.sleep(1.2)
        except Exception as e:
            print(f"Discord error: {e}")


def main():
    existing_jobs = {}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data.get("jobs", []):
                    existing_jobs[item["id"]] = item
        except Exception:
            existing_jobs = {}

    scraped = scrape_greenhouse_studios() + scrape_rss_feeds()
    new_alerts = []

    for job in scraped:
        if job["id"] not in existing_jobs:
            existing_jobs[job["id"]] = job
            new_alerts.append(job)

    # Sort all jobs newest first
    all_jobs_list = sorted(
        existing_jobs.values(),
        key=lambda x: x.get("date_updated", ""),
        reverse=True
    )[:300]  # Keep latest 300 relevant jobs

    output = {
        "last_scraped": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_jobs": len(all_jobs_list),
        "jobs": all_jobs_list
    }

    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Scrape complete! {len(new_alerts)} brand new jobs found. Total saved: {len(all_jobs_list)}.")
    send_discord_notification(new_alerts)


if __name__ == "__main__":
    main()
