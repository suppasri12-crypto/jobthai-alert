import html
import json
import os
import re
import sys
from urllib.parse import urljoin

import requests

URL = "https://www.jobthai.com/หางาน/ทำงานที่บ้าน"
STATE_FILE = "jobs.json"
MESSAGE_FILE = "message.html"

headers = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
}

# ลบข้อความอีเมลเก่าทุกครั้ง เพื่อไม่ให้ส่งซ้ำ
if os.path.exists(MESSAGE_FILE):
    os.remove(MESSAGE_FILE)

response = requests.get(
    URL,
    headers=headers,
    timeout=30,
)

print("HTTP status:", response.status_code)
print("Downloaded characters:", len(response.text))

response.raise_for_status()

page = response.text

# หา URL ที่มีลักษณะเป็นหน้าประกาศงาน
links = re.findall(
    r'href=["\']([^"\']*(?:job|งาน)[^"\']*)["\']',
    page,
    flags=re.IGNORECASE,
)

ignored_parts = (
    "/หางาน/",
    "/search",
    "javascript:",
    "#",
)

current_jobs = {}

for link in links:
    link = html.unescape(link)

    if any(part in link for part in ignored_parts):
        continue

    full_url = urljoin(URL, link)

    # ใช้ URL เป็นรหัสประจำประกาศ
    current_jobs[full_url] = {
        "url": full_url
    }

print("Job links found:", len(current_jobs))

if not current_jobs:
    print("ERROR: ไม่พบลิงก์ประกาศงาน")
    print("หน้า JobThai อาจเปลี่ยนรูปแบบหรือป้องกันการดึงข้อมูล")
    sys.exit(1)

old_jobs = {}

if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            saved = json.load(file)

        if isinstance(saved, dict):
            old_jobs = saved
        elif isinstance(saved, list):
            old_jobs = {str(item): {"url": str(item)} for item in saved}

    except (json.JSONDecodeError, OSError) as error:
        print("Cannot read previous state:", error)

new_urls = [
    job_url
    for job_url in current_jobs
    if job_url not in old_jobs
]

print("Previously saved:", len(old_jobs))
print("New jobs found:", len(new_urls))

# รอบแรกใช้สร้างฐานข้อมูล ไม่ส่งรายการทั้งหมดเป็นอีเมล
if not old_jobs:
    print("First run: creating initial job baseline only")

elif new_urls:
    items = []

    for job_url in new_urls[:50]:
        safe_url = html.escape(job_url, quote=True)
        items.append(
            f'<li><a href="{safe_url}">{safe_url}</a></li>'
        )

    email_body = f"""
    <h2>พบงาน Work From Home ใหม่</h2>
    <p>พบประกาศใหม่ {len(new_urls)} รายการ</p>
    <ul>
      {''.join(items)}
    <  <p>
      <a href="{html.escape(URL, quote=True)}">
        เปิดหน้าค้นหางาน JobThai
      </a>
    </p>
    """

    with open(MESSAGE_FILE, "w", encoding="utf-8") as file:
        file.write(email_body)

    print("Created message.html")

else:
    print("No new jobs. Email will not be sent.")

with open(STATE_FILE, "w", encoding="utf-8") as file:
    json.dump(
        current_jobs,
        file,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

print("Updated jobs.json")
