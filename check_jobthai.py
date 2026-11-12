import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


# =========================================================
# การตั้งค่า
# =========================================================

JOBTHAI_URL = "https://www.jobthai.com/หางาน/ทำงานที่บ้าน"

STATE_FILE = Path("jobs.json")
MESSAGE_FILE = Path("message.html")
SUBJECT_FILE = Path("email_subject.txt")

# รับค่าจาก GitHub Actions
# true = สร้างอีเมลทดสอบทันที
# false = ตรวจสอบงานใหม่ตามปกติ
FORCE_TEST = os.getenv("FORCE_TEST", "false").strip().lower() == "true"


# =========================================================
# ฟังก์ชันจัดการ URL
# =========================================================

def normalize_url(url):
    """
    ทำ URL ให้เป็นรูปแบบมาตรฐาน โดยลบ query string และ fragment
    เพื่อไม่ให้ประกาศเดียวกันถูกนับเป็นหลายรายการ
    """

    parsed = urlparse(url)

    normalized_path = parsed.path.rstrip("/")

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            normalized_path,
            "",
            "",
            "",
        )
    )


def is_job_detail_url(url):
    """
    ตรวจสอบว่า URL มีลักษณะเป็นหน้ารายละเอียดงานหรือไม่
    """

    parsed = urlparse(url)

    if "jobthai.com" not in parsed.netloc.lower():
        return False

    path = parsed.path.lower()

    patterns = [
        r"/job/\d+",
        r"/jobs/\d+",
        r"/th/job/\d+",
        r"/งาน/\d+",
        r"/งาน/.+/\d+",
    ]

    return any(
        re.search(pattern, path)
        for pattern in patterns
    )


# =========================================================
# อ่านและบันทึกข้อมูลเดิม
# =========================================================

def load_previous_jobs():
    """
    อ่านข้อมูลรายการงานที่บันทึกไว้ใน jobs.json
    """

    if not STATE_FILE.exists():
        print("jobs.json does not exist.")
        return {}

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            return data

        # รองรับ jobs.json รุ่นเก่าที่เก็บเป็น list
        if isinstance(data, list):
            converted = {}

            for item in data:
                if isinstance(item, str):
                    normalized_url = normalize_url(item)

                    converted[normalized_url] = {
                        "title": "ประกาศงาน",
                        "url": normalized_url,
                    }

                elif isinstance(item, dict):
                    item_url = item.get("url")

                    if item_url:
                        normalized_url = normalize_url(item_url)

                        converted[normalized_url] = {
                            "title": item.get(
                                "title",
                                "ประกาศงาน",
                            ),
                            "url": normalized_url,
                        }

            return converted

        print("WARNING: jobs.json has an unsupported format.")

    except json.JSONDecodeError as error:
        print("WARNING: jobs.json is not valid JSON:", error)

    except OSError as error:
        print("WARNING: Cannot read jobs.json:", error)

    return {}


def save_jobs(jobs):
    """
    บันทึกรายการงานปัจจุบันลง jobs.json
    """

    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            jobs,
            file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

    print("Updated jobs.json")
    print("Saved jobs:", len(jobs))


# =========================================================
# ดาวน์โหลดหน้า JobThai
# =========================================================

def download_page():
    """
    ดาวน์โหลดหน้า Work From Home ของ JobThai
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,"
            "image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    print("Downloading:", JOBTHAI_URL)

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
            "หน้าเว็บมีข้อมูลน้อยผิดปกติ "
            "JobThai อาจป้องกันการเข้าถึงหรือโหลดหน้าไม่สมบูรณ์"
        )

    return response.text


# =========================================================
# ดึงรายการงานออกจาก HTML
# =========================================================

def clean_title(title):
    """
    ทำความสะอาดชื่อตำแหน่งงาน
    """

    if not title:
        return "เปิดดูรายละเอียดประกาศงาน"

    title = " ".join(title.split()).strip()

    if not title:
        return "เปิดดูรายละเอียดประกาศงาน"

    return title[:300]


def extract_jobs(page_source):
    """
    ดึง URL และชื่อตำแหน่งงานจากหน้า JobThai
    """

    soup = BeautifulSoup(page_source, "html.parser")

    jobs = {}

    # วิธีที่ 1: ตรวจจากแท็ก <a href="...">
    for anchor in soup.find_all("a", href=True):
        raw_href = anchor.get("href", "")

        if not isinstance(raw_href, str):
            continue

        raw_href = html.unescape(raw_href).strip()

        if not raw_href:
            continue

        if raw_href.startswith(
            (
                "#",
                "javascript:",
                "mailto:",
                "tel:",
            )
        ):
            continue

        full_url = urljoin(
            JOBTHAI_URL,
            raw_href,
        )

        full_url = normalize_url(full_url)

        if not is_job_detail_url(full_url):
            continue

        title = anchor.get_text(
            " ",
            strip=True,
        )

        if not title:
            title = anchor.get("title", "")

        title = clean_title(title)

        jobs[full_url] = {
            "title": title,
            "url": full_url,
        }

    # วิธีที่ 2: ตรวจ URL ที่อาจซ่อนอยู่ใน JSON หรือ JavaScript
    raw_patterns = [
        r'https?:\\?/\\?/www\.jobthai\.com'
        r'\\?/[^"\'\s<>]*?job\\?/\d+[^"\'\s<>]*',

        r'https?://www\.jobthai\.com'
        r'/[^"\'\s<>]*?/job/\d+[^"\'\s<>]*',

        r'/(?:th/)?job/\d+[^"\'\s<>]*',

        r'/jobs/\d+[^"\'\s<>]*',

        r'/งาน/\d+[^"\'\s<>]*',

        r'/งาน/[^"\'\s<>]+/\d+[^"\'\s<>]*',
    ]

    for pattern in raw_patterns:
        matches = re.findall(
            pattern,
            page_source,
            flags=re.IGNORECASE,
        )

        for raw_url in matches:
            raw_url = raw_url.replace("\\/", "/")
            raw_url = html.unescape(raw_url)

            # ตัดเครื่องหมายที่อาจติดท้าย URL
            raw_url = raw_url.rstrip(
                "\"'\\,;)]}"
            )

            full_url = urljoin(
                JOBTHAI_URL,
                raw_url,
            )

            full_url = normalize_url(full_url)

            if not is_job_detail_url(full_url):
                continue

            if full_url not in jobs:
                jobs[full_url] = {
                    "title": "เปิดดูรายละเอียดประกาศงาน",
                    "url": full_url,
                }

    return jobs


# =========================================================
# สร้างอีเมล
# =========================================================

def create_email(new_jobs, test_mode=False):
    """
    สร้างไฟล์ message.html และ email_subject.txt
    """

    if test_mode:
        subject_text = "ทดสอบระบบ JobThai Alert"
        heading = "ระบบส่งอีเมลทำงานแล้ว"

        intro = (
            "นี่คืออีเมลทดสอบจาก GitHub Actions "
            "ผ่าน Google Apps Script "
            "ไม่ใช่การแจ้งประกาศงานใหม่"
        )

        job_items = ""

    else:
        subject_text = (
            f"พบงาน Work From Home ใหม่ "
            f"{len(new_jobs)} รายการ"
        )

        heading = subject_text

        intro = (
            "พบประกาศงานที่ยังไม่เคยบันทึกอยู่ใน "
            "jobs.json ตามรายการต่อไปนี้"
        )

        item_list = []

        for job in new_jobs[:50]:
            title = job.get(
                "title",
                "ประกาศงาน",
            )

            job_url = job.get(
                "url",
                JOBTHAI_URL,
            )

            safe_title = html.escape(
                title,
                quote=True,
            )

            safe_url = html.escape(
                job_url,
                quote=True,
            )

            item_list.append(
                f"""
                <li style="margin-bottom:16px;">
                    <a
                        href="{safe_url}"
                        style="
                            font-weight:bold;
                            color:#0969da;
                            text-decoration:none;
                        "
                    >
                        {safe_title}
                    </a>

                    <br>

                    <span
                        style="
                            color:#666666;
                            font-size:12px;
                            word-break:break-all;
                        "
                    >
                        {safe_url}
                    </span>
                </li>
                """
            )

        job_items = (
            "<ol style=\"padding-left:24px;\">"
            + "".join(item_list)
            + "</ol>"
        )

        if len(new_jobs) > 50:
            remaining = len(new_jobs) - 50

            job_items += (
                f"<p>ยังมีประกาศเพิ่มเติมอีก "
                f"{remaining} รายการ "
                f"กรุณาเปิดหน้าค้นหาของ JobThai</p>"
            )

    safe_search_url = html.escape(
        JOBTHAI_URL,
        quote=True,
    )

    safe_heading = html.escape(
        heading,
        quote=True,
    )

    safe_intro = html.escape(
        intro,
        quote=True,
    )

    email_content = f"""
    <!doctype html>
    <html lang="th">
      <head>
        <meta charset="utf-8">
        <meta
          name="viewport"
          content="width=device-width, initial-scale=1"
        >
      </head>

      <body
        style="
          background:#f5f5f5;
          margin:0;
          padding:20px;
          font-family:Arial,Tahoma,sans-serif;
          color:#222222;
          line-height:1.6;
        "
      >
        <div
          style="
            max-width:720px;
            margin:0 auto;
            background:#ffffff;
            border:1px solid #dddddd;
            border-radius:8px;
            padding:24px;
          "
        >
          <h2
            style="
              color:#d32f2f;
              margin-top:0;
            "
          >
            {safe_heading}
          </h2>

          <p>{safe_intro}</p>

          {job_items}

          <p style="margin-top:24px;">
            <a
              href="{safe_search_url}"
              style="
                display:inline-block;
                background:#d32f2f;
                color:#ffffff;
                padding:10px 16px;
                border-radius:6px;
                text-decoration:none;
                font-weight:bold;
              "
            >
              เปิดหน้าค้นหางาน JobThai
            </a>
          </p>

          <hr
            style="
              border:0;
              border-top:1px solid #dddddd;
              margin:24px 0 16px;
            "
          >

          <p
            style="
              color:#666666;
              font-size:12px;
              margin-bottom:0;
            "
          >
            ส่งโดยระบบ GitHub Actions และ Google Apps Script
          </p>
        </div>
      </body>
    </html>
    """

    MESSAGE_FILE.write_text(
        email_content,
        encoding="utf-8",
    )

    SUBJECT_FILE.write_text(
        subject_text,
        encoding="utf-8",
    )

    print("Created message.html")
    print("Created email_subject.txt")
    print("Email subject:", subject_text)


# =========================================================
# ลบไฟล์ชั่วคราวจากการทำงานครั้งก่อน
# =========================================================

def remove_old_email_files():
    """
    ลบไฟล์อีเมลเดิมเพื่อป้องกันการส่งข้อความเก่าซ้ำ
    """

    temporary_files = [
        MESSAGE_FILE,
        SUBJECT_FILE,
        Path("payload.json"),
        Path("apps-script-response.json"),
    ]

    for file_path in temporary_files:
        if file_path.exists():
            file_path.unlink()
            print("Removed old file:", file_path)


# =========================================================
# การทำงานหลัก
# =========================================================

def main():
    print("=" * 60)
    print("JobThai Alert")
    print("=" * 60)

    print("JobThai URL:", JOBTHAI_URL)
    print("Force test:", FORCE_TEST)

    remove_old_email_files()

    # โหมดทดสอบส่งอีเมล
    # ไม่ดาวน์โหลด JobThai และไม่แก้ jobs.json
    if FORCE_TEST:
        print("Creating test email...")

        create_email(
            new_jobs=[],
            test_mode=True,
        )

        print("TEST_EMAIL_CREATED=true")
        print("Test mode completed successfully.")
        return

    # โหมดตรวจสอบงานจริง
    try:
        page_source = download_page()

        current_jobs = extract_jobs(
            page_source
        )

    except requests.RequestException as error:
        print("ERROR: Downloading JobThai failed.")
        print("Details:", error)
        sys.exit(1)

    except Exception as error:
        print("ERROR:", error)
        sys.exit(1)

    print("Job links found:", len(current_jobs))

    # ถ้าไม่พบลิงก์เลย ห้ามเขียนทับ jobs.json
    # เพราะอาจเกิดจากหน้าเว็บเปลี่ยนหรือบล็อก GitHub Actions
    if not current_jobs:
        print("ERROR: ไม่พบลิงก์รายละเอียดประกาศงาน")
        print(
            "JobThai อาจเปลี่ยนโครงสร้างหน้าเว็บ "
            "หรือป้องกันการเข้าถึงจาก GitHub Actions"
        )
        print("jobs.json will not be changed.")
        sys.exit(1)

    previous_jobs = load_previous_jobs()

    print("Previously saved:", len(previous_jobs))

    new_job_urls = [
        job_url
        for job_url in current_jobs
        if job_url not in previous_jobs
    ]

    new_jobs = [
        current_jobs[job_url]
        for job_url in new_job_urls
    ]

    print("New jobs found:", len(new_jobs))

    # ถ้ายังไม่มี jobs.json หรือไฟล์เดิมไม่มีข้อมูล
    # ให้สร้างฐานข้อมูลตั้งต้นโดยไม่ส่งอีเมล
    if not previous_jobs:
        print("First run: creating baseline only.")
        print("No email will be sent on the first run.")

    elif new_jobs:
        create_email(
            new_jobs=new_jobs,
            test_mode=False,
        )

        print("NEW_JOBS_DETECTED=true")

        for index, job in enumerate(
            new_jobs[:20],
            start=1,
        ):
            print(
                f"New job {index}:",
                job.get("title", "ประกาศงาน"),
            )
            print(
                "URL:",
                job.get("url", ""),
            )

    else:
        print("No new jobs detected.")
        print("NEW_JOBS_DETECTED=false")
        print("No email will be sent.")

    save_jobs(current_jobs)

    print("JobThai check completed successfully.")


if __name__ == "__main__":
    main()
