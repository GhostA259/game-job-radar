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
# 1. JOB SOURCES
# =====================================================================
RSS_SOURCES = [
    {"name": "Work With Indies", "url": "https://www.workwithindies.com/careers/rss.xml"},
    {"name": "RemoteFirst QA", "url": "https://remotefirstjobs.com/rss/jobs/qa.rss"},
    {"name": "RemoteFirst Entry-Level", "url": "https://remotefirstjobs.com/rss/jobs/entry-level.rss"},
]

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

HARD_BLOCKLIST_TITLES = [
    # Art & Animation
    "animator", "animation", "3d artist", "2d artist", "concept artist",
    "character artist", "environment artist", "vfx artist", "lighting artist",
    "rigging", "rigger", "illustrator", "art director", "tech artist", "technical artist",
    "ui artist", "storyboard", "modeler",
    # Audio
    "audio", "sound designer", "composer", "voice",
    # Senior / Unobtainable Executive Roles
    "senior", "sr.", "sr ", "director", "vice president", "vp ", "principal",
    "head of", "chief", "executive", "staff ", "lead ",
    # Corporate / Non-Dev / Spam
    "marketing", "recruiter", "talent acquisition", "accountant", "finance",
    "legal", "counsel", "sales", "public relations", "social media", "community manager",
    "copywriter", "localization", "monetization", "casino", "slot", "blockchain", "web3"
]

SENIOR_DESCRIPTION_PATTERNS = [
    r"\b[6-9]\+?\s*years",
    r"\b1[0-5]\+?\s*years",
    r"minimum of [6-9] years"
]


def detect_engine(title: str, description: str, company: str = "") -> str:
    """
    Scans title and full description for game engines and Blueprint scripting.
    """
    combined = f"{title} {description} {company}".lower()

    has_unreal = any(w in combined for w in ["unreal", "ue5", "ue4", "epic games"])
    has_blueprints = any(w in combined for w in ["blueprint", "blueprints", "visual scripting", "umg", "niagara", "gameplay ability system"])
    has_godot = any(w in combined for w in ["godot", "gdscript"])
    has_unity = any(w in combined for w in ["unity", "unity3d"])

    if has_unreal and has_blueprints:
        return "Unreal + Blueprints"
    elif has_blueprints:
        return "Unreal + Blueprints"
    elif has_unreal:
        return "Unreal Engine"
    elif has_godot:
        return "Godot"
    elif has_unity:
        return "Unity"
    return "Proprietary / Any"


def classify_and_filter_job(title: str, description: str = "", company: str = "") -> dict:
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

    if not matched_category:
        if ("designer" in title_lower or "qa" in title_lower) and any(
            eng in (title_lower + " " + desc_lower) for eng in ["unreal", "blueprint", "godot", "unity", "game", "level", "combat"]
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
    else:
        exp_level = "Mid / Standard"

    # 5. Detect Employment Type
    combined_short = title_lower + " " + desc_lower[:400]
    if "intern" in combined_short:
        job_type = "Internship"
    elif "part-time" in combined_short or "part time" in combined_short:
        job_type = "Part-Time"
    elif "contract" in combined_short or "freelance" in combined_short or "temp" in combined_short:
        job_type = "Contract"
    else:
        job_type = "Full-Time"

    # 6. Detect Game Engine
    engine = detect_engine(title, description, company)

    return {
        "category": matched_category,
        "experience_level": exp_level,
        "job_type": job_type,
        "engine": engine
    }


def make_id(url: str, title: str) -> str:
    raw = f"{url.strip()}_{title.strip()}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:12]


def scrape_rss_feeds() -> list:
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (GameJobRadar/2.1)"}
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

                company = source["name"]
                if " at " in title:
                    company = title.split(" at ")[-1].strip()
                elif ":" in title:
                    company = title.split(":", 1)[0].strip()

                meta = classify_and_filter_job(title, desc, company)
                if not meta:
                    continue

                results.append({
                    "id": make_id(link, title),
                    "title": title,
                    "company": company,
                    "location": "Remote / Check Listing",
                    "category": meta["category"],
                    "experience_level": meta["experience_level"],
                    "job_type": meta["job_type"],
                    "engine": meta["engine"],
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

                meta = classify_and_filter_job(title, desc, studio["name"])
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
                    "engine": meta["engine"],
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

    # Sort new jobs so Unreal + Blueprints and Entry/Tech Design roles alert first!
    def priority_score(j):
        score = 0
        if j.get("engine") == "Unreal + Blueprints":
            score += 3
        elif j.get("engine") == "Unreal Engine":
            score += 2
        if j.get("category") == "Tech & Systems Design":
            score += 2
        if j.get("experience_level") in ["Entry / Associate", "Internship"]:
            score += 1
        return score

    sorted_new = sorted(new_jobs, key=priority_score, reverse=True)

    for job in sorted_new[:10]:
        is_top_match = (
            "Unreal" in job.get("engine", "")
            or job["category"] == "Tech & Systems Design"
            or job["experience_level"] in ["Entry / Associate", "Internship"]
        )
        color = 0x10B981 if is_top_match else 0x3B82F6

        fields = [
            {"name": "🏢 Studio", "value": job["company"], "inline": True},
            {"name": "⚙️ Engine / Toolset", "value": job.get("engine", "Proprietary / Any"), "inline": True},
            {"name": "🎯 Category", "value": job["category"], "inline": True},
            {"name": "📊 Level", "value": job["experience_level"], "inline": True},
            {"name": "💼 Type", "value": job["job_type"], "inline": True},
            {"name": "📍 Location", "value": job["location"], "inline": True},
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
                "footer": {"text": f"Source: {job['source']} • Updated: {job['date_updated']}"}
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
                    # Retroactively scrub any old Senior roles from the first run
                    if not any(b in item.get("title", "").lower() for b in HARD_BLOCKLIST_TITLES):
                        if "engine" not in item:
                            item["engine"] = detect_engine(item.get("title", ""), "", item.get("company", ""))
                        existing_jobs[item["id"]] = item
        except Exception:
            existing_jobs = {}

    scraped = scrape_greenhouse_studios() + scrape_rss_feeds()
    new_alerts = []

    for job in scraped:
        if job["id"] not in existing_jobs:
            existing_jobs[job["id"]] = job
            new_alerts.append(job)
        else:
            # Update engine tag if it was missing on an existing job
            existing_jobs[job["id"]]["engine"] = job["engine"]

    all_jobs_list = sorted(
        existing_jobs.values(),
        key=lambda x: x.get("date_updated", ""),
        reverse=True
    )[:300]

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
