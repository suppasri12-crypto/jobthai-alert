import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


JOBTHAI_URL = "https://www.jobthai.com/หางาน/ทำงานที่บ้าน"
STATE_FILE = Path("jobs.json")
MESSAGE_FILE = Path("message.html")

# GitHub Actions จะส่งค่า true เข้ามาเมื่อเลือกโหมดทดสอบอีเมล
FORCE_TEST = os.getenv("FORCE_TEST", "false").lower() == "true"


def normalize_url(url):
    """ลบ query string และ fragment เพื่อป้องกัน URL เดียวกันถูกนับซ้ำ"""
    parsed = urlparse(url)

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            "",
            "",
            "",
        )
    )


def is_job_detail_url(url):
    """ตรวจว่า URL มีลักษณะเป็นหน้ารายละเอียดประกาศงานหรือไม่"""
    path = urlparse(url).path.lower()

    patterns = [
        r"/job/\d+",
        r"/jobs/\d+",
        r"/th/job/\d+",
        r"/งาน/\d+",
    ]

    return any(re.search(pattern, path) for pattern in patterns)


def load_previous_jobs():
    """อ่านข้อมูลประกาศที่เคยตรวจพบ"""
    if not STATE_FILE.exists():
        return {}

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            return data

        # รองรับ jobs.json รูปแบบเก่าที่อาจเป็น list
        if isinstance(data, list):
            converted = {}

            for item in data:
                if isinstance(item, str):
                    converted[item] = {
                        "title": "ประกาศงาน",
                        "url": item,
                    }
                elif isinstance(item, dict) and item.get("url"):
                    converted[item["url"]] = item

            return converted

    except (OSError, json.JSONDecodeError) as error:
        print("WARNING: อ่าน jobs.json ไม่สำเร็จ:", error)

    return {}


def download_page():
    """ดาวน์โหลดหน้า JobThai"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }

    response = requests.get(
        JOBTHAI_URL,
        headers=headers,
        timeout=45,
    )

    print("HTTP status:", response.status_code)
    print("Downloaded characters:", len(response.text))

    response.raise_for_status()

    if len(response.text) < 1000:
        raise RuntimeError(
            "หน้าเว็บมีข้อมูลน้อยผิดปกติ อาจถูกป้องกันหรือโหลดไม่สมบูรณ์"
        )

    return response.text


def extract_jobs(page_source):
    """ดึงชื่อและ URL ของประกาศงานจาก HTML"""
    soup = BeautifulSoup(page_source, "html.parser")
    jobs = {}

    # วิธีที่ 1: ตรวจจากลิงก์ HTML
    for anchor in soup.find_all("a", href=True):
        href = html.unescape(anchor.get("href", "")).strip()

        if not href:
            continue

        full_url = normalize_url(urljoin(JOBTHAI_URL, href))

        if not is_job_detail_url(full_url):
            continue

        title = " ".join(anchor.get_text(" ", strip=True).split())

        if not title:
            title = anchor.get("title", "").strip()

        if not title:
            title = "เปิดดูรายละเอียดประกาศงาน"

        jobs[full_url] = {
            "title": title[:300],
            "url": full_url,
        }

    # วิธีที่ 2: ตรวจ URL ที่ซ่อนอยู่ใน JSON หรือ JavaScript
    raw_patterns = [
        r'https?:\\?/\\?/www\.jobthai\.com\\?/[^\s"<>]+?/job\\?/\d+[^\s"<>]*',
        r'["\'](\/(?:th\/)?job\/\d+[^"\']*)["\']',
        r'["\'](\/งาน\/\d+[^"\']*)["\']',
    ]

    for pattern in raw_patterns:
        for match in re.findall(pattern, page_source, flags=re.IGNORECASE):
            raw_url = match.replace("\\/", "/")
            full_url = normalize_url(urljoin(JOBTHAI_URL, raw_url))

            if not is_job_detail_url(full_url):
                continue

            if full_url not in jobs:
                jobs[full_url] = {
                    "title": "เปิดดูรายละเอียดประกาศงาน",
                    "url": full_url,
                }

    return jobs


def create_email(new_jobs, test_mode=False):
    """สร้างเนื้อหาอีเมล"""
    if test_mode:
        subject_text = "ทดสอบระบบ JobThai Alert"
        heading = "ระบบส่งอีเมลทำงานแล้ว"
        intro = (
  คืออีเมลทดสอบจาก GitHub Actions ผ่าน Google Apps Script "
            "ไม่ใช่การแจ้งประกาศงานใหม่"
        )
        job_items = ""

    else:
        subject_text = f"พบงาน Work From Home ใหม่ {len(new_jobs)} รายการ"
        heading = subject_text
        intro = "พบประกาศที่ยังไม่เคยอยู่ใน jobs.json ดังต่อไปนี้"

        item_list = []

        for job in new_jobs[:50]:
            safe_title = html.escape(
                job.get("title", "ประกาศงาน"),
                quote=True,
            )
            safe_url = html.escape(
                job["url"],
                quote=True,
            )

            item_list.append(
                f"""
                <li style="margin-bottom:12px;">
                    <a href="{safe_url}"
                       style="font-weight:bold;color:#0969da;">
                        {safe_title}
                    </a>
                    <br>
                    <span style="font-size:12px;color:#666;">
                        {safe_url}
                    </span>
                </li>
                """
            )

        job_items = f"<ol>{''.join(item_list)}</ol>"

    safe_search_url = html.escape(JOBTHAI_URL, quote=True)

    email_content = f"""
    <!doctype html>
    <html lang="th">
      <body style="
          font-family:Arial,Tahoma,sans-serif;
          line-height:1.6;
          color:#222;
          max-width:720px;
          margin:0 auto;
          padding:20px;
      ">
        <h2 style="color:#d32f2f;">
          {html.escape(heading)}
        </h2>

        <p>{html.escape(intro)}</p>

        {job_items}

        <p style="margin-top:24px;">
          <a href="{safe_search_url}"
             style="
                 display:inline-block;
                 background:#d32f2f;
                 color:#fff;
                 padding:10px 16px;
                 border-radius:6px;
                 text-decoration:none;
             ">
            เปิดหน้าค้นหางาน JobThai
          </a>
        </p>

        <hr style="border:0;border-top:1px solid #ddd;margin-top:24px;">

        <p style="font-size:12px;color:#666;">
          ส่งโดยระบบ GitHub Actions และ Google Apps Script
        </p>
      </body>
    </html>
    """

    MESSAGE_FILE.write_text(email_content, encoding="utf-8")

    # Workflow จะอ่านบรรทัดนี้ไปใช้เป็นหัวข้ออีเมล
    Path("email_subject.txt").write_text(
        subject_text,
        encoding="utf-8",
    )

    print("Created message.html")
    print("Email subject:", subject_text)


def save_jobs(jobs):
    """บันทึกประกาศล่าสุด"""
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            jobs,
            file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

    print("Updated jobs.json")


def main():
    # ลบข้อความเก่า ป้องกันการส่งอีเมลเดิมซ้ำ
    for temporary_file in [MESSAGE_FILE, Path("email_subject.txt")]:
        if temporary_file.exists():
            temporary_file.unlink()

    print("JobThai URL:", JOBTHAI_URL)
    print("Force test:", FORCE_TEST)

    # ถ้าเป็นการทดสอบ ไม่จำเป็นต้องรอให้พบงานใหม่
    if FORCE_TEST:
        create_email([], test_mode=True)
        print("TEST_EMAIL_CREATED=true")
        return

    try:
        page_source = download_page()
        current_jobs = extract_jobs(page_source)

    except Exception as error:
        print("ERROR:", error)
        sys.exit(1)

    print("Job links found:", len(current_jobs))

    if not current_jobs:
        print("ERROR: ไม่พบลิงก์รายละเอียดประกาศงาน")
        print("หน้า JobThai อาจเปลี่ยนโครงสร้างหรือป้องกัน GitHub Actions")
        sys.exit(1)

    previous_jobs = load_previous_jobs()

    print("Previously saved:", len(previous_jobs))

    new_job_urls = [
        url for url in current_jobs
        if url not in previous_jobs
    ]

    new_jobs = [
        current_jobs[url]
        for url in new_job_urls
    ]

    print("New jobs found:", len(new_jobs))

    # ถ้ายังไม่มีข้อมูลตั้งต้น ให้บันทึกอย่างเดียว
    # เพื่อป้องกันการส่งงานทั้งหมดหลายร้อยรายการในครั้งแรก
    if not previous_jobs:
        print("First run: creating baseline only")
        print("No email will be sent on the first run.")

    elif new_jobs:
        create_email(new_jobs, test_mode=False)
        print("NEW_JOBS_DETECTED=true")

    else:
        print("No new jobs detected.")
        print("NEW_JOBS_DETECTED=false")

    save_jobs(current_jobs)


if __name__ == "__main__":
    main()
