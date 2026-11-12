import html
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


# =========================================================
# การตั้งค่า
# =========================================================

JOBTHAI_BASE_URL = (
    "https://www.jobthai.com/หางาน/ทำงานที่บ้าน"
)

STATE_FILE = Path("jobs.json")
MESSAGE_FILE = Path("message.html")
SUBJECT_FILE = Path("email_subject.txt")

# เวลาไทย UTC+7
THAILAND_TIMEZONE = timezone(
    timedelta(hours=7)
)

# เก็บเฉพาะประกาศอายุไม่เกิน 7 วัน
MAX_JOB_AGE_DAYS = 7

# จำนวนหน้าสูงสุดที่อนุญาตให้ตรวจ
MAX_PAGES = 100

# หยุดเมื่อพบหน้าที่ไม่มี URL ใหม่ติดต่อกันตามจำนวนนี้
EMPTY_PAGE_LIMIT = 2

# เวลาพักระหว่างหน้า เพื่อลดการส่งคำขอถี่เกินไป
REQUEST_DELAY_SECONDS = 2

# ค่าจาก GitHub Actions
FORCE_TEST = (
    os.getenv("FORCE_TEST", "false")
    .strip()
    .lower()
    == "true"
)


# =========================================================
# วันที่และเวลา
# =========================================================

def get_thailand_today():
    """คืนค่าวันที่ปัจจุบันตามเวลาไทย"""

    return datetime.now(
        THAILAND_TIMEZONE
    ).date()


def convert_two_digit_year(year_number):
    """
    แปลงปี 2 หลักของไทยเป็นปี ค.ศ.

    ตัวอย่าง:
    69 -> พ.ศ. 2569 -> ค.ศ. 2026
    """

    if year_number <= 79:
        buddhist_year = 2500 + year_number
    else:
        buddhist_year = 2400 + year_number

    return buddhist_year - 543


def parse_year(year_text):
    """แปลงปี พ.ศ. หรือ ค.ศ. เป็นปี ค.ศ."""

    if not year_text:
        return None

    year_number = int(year_text)

    if year_number > 2400:
        return year_number - 543

    if year_number < 100:
        return convert_two_digit_year(
            year_number
        )

    return year_number


def parse_thai_posted_date(text):
    """
    อ่านวันที่ประกาศจากข้อความไทย

    รองรับ:
    - วันนี้
    - เมื่อวาน
    - 25 นาทีที่แล้ว
    - 3 ชั่วโมงที่แล้ว
    - 5 วันที่แล้ว
    - 5 วันที่ผ่านมา
    - 12 พ.ย. 2569
    - 12 พฤศจิกายน 2569
    - 12/11/2569
    - 12-11-2026
    - 2026-11-12
    """

    if not text:
        return None

    normalized_text = " ".join(
        str(text)
        .replace("\u00a0", " ")
        .split()
    ).strip()

    today = get_thailand_today()

    if "วันนี้" in normalized_text:
        return today

    if "เมื่อวาน" in normalized_text:
        return today - timedelta(days=1)

    recent_time_match = re.search(
        r"\d+\s*(?:นาที|ชั่วโมง)"
        r"(?:ที่แล้ว|ที่ผ่านมา)?",
        normalized_text,
    )

    if recent_time_match:
        return today

    day_ago_match = re.search(
        r"(\d+)\s*วัน"
        r"(?:ที่แล้ว|ที่ผ่านมา)",
        normalized_text,
    )

    if day_ago_match:
        days_ago = int(
            day_ago_match.group(1)
        )

        return today - timedelta(
            days=days_ago
        )

    thai_months = {
        "ม.ค": 1,
        "ม.ค.": 1,
        "มกราคม": 1,
        "ก.พ": 2,
        "ก.พ.": 2,
        "กุมภาพันธ์": 2,
        "มี.ค": 3,
        "มี.ค.": 3,
        "มีนาคม": 3,
        "เม.ย": 4,
        "เม.ย.": 4,
        "เมษายน": 4,
        "พ.ค": 5,
        "พ.ค.": 5,
        "พฤษภาคม": 5,
        "มิ.ย": 6,
        "มิ.ย.": 6,
        "มิถุนายน": 6,
        "ก.ค": 7,
        "ก.ค.": 7,
        "กรกฎาคม": 7,
        "ส.ค": 8,
        "ส.ค.": 8,
        "สิงหาคม": 8,
        "ก.ย": 9,
        "ก.ย.": 9,
        "กันยายน": 9,
        "ต.ค": 10,
        "ต.ค.": 10,
        "ตุลาคม": 10,
        "พ.ย": 11,
        "พ.ย.": 11,
        "พฤศจิกายน": 11,
        "ธ.ค": 12,
        "ธ.ค.": 12,
        "ธันวาคม": 12,
    }

    thai_date_pattern = re.search(
        r"(\d{1,2})\s+"
        r"(ม\.ค\.?|มกราคม|"
        r"ก\.พ\.?|กุมภาพันธ์|"
        r"มี\.ค\.?|มีนาคม|"
        r"เม\.ย\.?|เมษายน|"
        r"พ\.ค\.?|พฤษภาคม|"
        r"มิ\.ย\.?|มิถุนายน|"
        r"ก\.ค\.?|กรกฎาคม|"
        r"ส\.ค\.?|สิงหาคม|"
        r"ก\.ย\.?|กันยายน|"
        r"ต\.ค\.?|ตุลาคม|"
        r"พ\.ย\.?|พฤศจิกายน|"
        r"ธ\.ค\.?|ธันวาคม)"
        r"(?:\s+(\d{2,4}))?",
        normalized_text,
    )

    if thai_date_pattern:
        day_number = int(
            thai_date_pattern.group(1)
        )

        month_text = (
            thai_date_pattern
            .group(2)
            .strip()
        )

        month_number = thai_months.get(
            month_text
        )

        year_text = thai_date_pattern.group(3)

        if year_text:
            year_number = parse_year(
                year_text
            )
        else:
            year_number = today.year

        try:
            parsed_date = date(
                year_number,
                month_number,
                day_number,
            )

            # กรณีไม่แสดงปีและวันที่อยู่ข้างหน้าเกิน 7 วัน
            # ให้ถือว่าเป็นวันที่ของปีก่อน
            if (
                not year_text
                and parsed_date
                > today + timedelta(days=7)
            ):
                parsed_date = date(
                    year_number - 1,
                    month_number,
                    day_number,
                )

            return parsed_date

        except (TypeError, ValueError):
            return None

    # รูปแบบ DD/MM/YYYY หรือ DD-MM-YYYY
    numeric_date_pattern = re.search(
        r"\b(\d{1,2})[/-]"
        r"(\d{1,2})[/-]"
        r"(\d{2,4})\b",
        normalized_text,
    )

    if numeric_date_pattern:
        day_number = int(
            numeric_date_pattern.group(1)
        )

        month_number = int(
            numeric_date_pattern.group(2)
        )

        year_number = parse_year(
            numeric_date_pattern.group(3)
        )

        try:
            return date(
                year_number,
                month_number,
                day_number,
            )

        except ValueError:
            return None

    # รูปแบบ YYYY-MM-DD
    iso_date_pattern = re.search(
        r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b",
        normalized_text,
    )

    if iso_date_pattern:
        try:
            return date(
                int(iso_date_pattern.group(1)),
                int(iso_date_pattern.group(2)),
                int(iso_date_pattern.group(3)),
            )

        except ValueError:
            return None

    return None


def get_job_age_days(posted_date):
    """คำนวณอายุประกาศเป็นจำนวนวัน"""

    if posted_date is None:
        return None

    return (
        get_thailand_today() - posted_date
    ).days


def is_within_allowed_age(posted_date):
    """
    อนุญาตเฉพาะงานอายุ 0-7 วัน

    ถ้าอ่านวันที่ไม่ได้จะเก็บไว้ก่อน
    เพื่อป้องกันการพลาดประกาศใหม่
    """

    if posted_date is None:
        return True

    age_days = get_job_age_days(
        posted_date
    )

    # วันที่ในอนาคตอาจเกิดจากรูปแบบปีไม่ชัดเจน
    if age_days < 0:
        return True

    return age_days <= MAX_JOB_AGE_DAYS


# =========================================================
# URL
# =========================================================

def build_page_url(page_number):
    """
    สร้าง URL /ทำงานที่บ้าน/x

    ตัวอย่าง:
    หน้า 1 = /ทำงานที่บ้าน/1
    หน้า 2 = /ทำงานที่บ้าน/2
    """

    return (
        f"{JOBTHAI_BASE_URL}/{page_number}"
    )


def normalize_url(url):
    """
    ลบ query string และ fragment

    ใช้ URL ที่ผ่านการปรับแล้วเป็นรหัสประกาศ
    """

    parsed = urlparse(url)

    normalized_path = (
        parsed.path.rstrip("/")
    )

    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            "",
            "",
            "",
        )
    )


def is_job_detail_url(url):
    """ตรวจว่า URL เป็นหน้ารายละเอียดงานหรือไม่"""

    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()

    if domain not in {
        "jobthai.com",
        "www.jobthai.com",
    }:
        return False

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
# HTTP
# =========================================================

def get_request_headers():
    """คืนค่า HTTP headers"""

    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,"
            "image/webp,*/*;q=0.8"
        ),
        "Accept-Language": (
            "th-TH,th;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def download_page(page_number, session):
    """ดาวน์โหลดหน้าค้นหาตามเลขหน้า"""

    page_url = build_page_url(
        page_number
    )

    print("-" * 60)
    print("Downloading page:", page_number)
    print("URL:", page_url)

    response = session.get(
        page_url,
        headers=get_request_headers(),
        timeout=45,
        allow_redirects=True,
    )

    print("HTTP status:", response.status_code)
    print(
        "Final URL:",
        response.url,
    )
    print(
        "Downloaded characters:",
        len(response.text),
    )

    if response.status_code == 404:
        print(
            "Page does not exist:",
            page_number,
        )

        return None

    response.raise_for_status()

    if len(response.text) < 1000:
        print(
            "WARNING: Page contains unusually "
            "little content:",
            page_number,
        )

        return None

    return response.text


# =========================================================
# ดึงข้อมูลงาน
# =========================================================

def clean_title(title):
    """ทำความสะอาดชื่อประกาศ"""

    if not title:
        return "เปิดดูรายละเอียดประกาศงาน"

    cleaned_title = " ".join(
        str(title).split()
    ).strip()

    if not cleaned_title:
        return "เปิดดูรายละเอียดประกาศงาน"

    return cleaned_title[:300]


def element_contains_date_text(text):
    """ตรวจคร่าว ๆ ว่าข้อความมีวันที่หรือไม่"""

    if not text:
        return False

    date_markers = [
        "วันนี้",
        "เมื่อวาน",
        "นาที",
        "ชั่วโมง",
        "วันที่แล้ว",
        "วันที่ผ่านมา",
        "ม.ค",
        "ก.พ",
        "มี.ค",
        "เม.ย",
        "พ.ค",
        "มิ.ย",
        "ก.ค",
        "ส.ค",
        "ก.ย",
        "ต.ค",
        "พ.ย",
        "ธ.ค",
        "มกราคม",
        "กุมภาพันธ์",
        "มีนาคม",
        "เมษายน",
        "พฤษภาคม",
        "มิถุนายน",
        "กรกฎาคม",
        "สิงหาคม",
        "กันยายน",
        "ตุลาคม",
        "พฤศจิกายน",
        "ธันวาคม",
    ]

    return any(
        marker in text
        for marker in date_markers
    )


def find_job_posted_date(anchor):
    """
    ค้นหาวันที่จากลิงก์และกล่องประกาศรอบลิงก์

    จำกัดการตรวจ parent ไว้ 6 ระดับ
    """

    current_element = anchor

    for _ in range(6):
        if current_element is None:
            break

        for attribute_name in [
            "datetime",
            "data-date",
            "data-posted-date",
            "data-created-at",
            "title",
            "aria-label",
        ]:
            attribute_value = current_element.get(
                attribute_name
            )

            if attribute_value:
                parsed_date = parse_thai_posted_date(
                    attribute_value
                )

                if parsed_date is not None:
                    return parsed_date

        element_text = current_element.get_text(
            " ",
            strip=True,
        )

        if element_contains_date_text(
            element_text
        ):
            parsed_date = parse_thai_posted_date(
                element_text
            )

            if parsed_date is not None:
                return parsed_date

        current_element = current_element.parent

    return None


def extract_job_url_signatures(page_source):
    """
    ดึง URL ประกาศทั้งหมดก่อนกรองวันที่

    ใช้ตรวจว่าหน้าถัดไปเป็นหน้าซ้ำหรือไม่
    """

    soup = BeautifulSoup(
        page_source,
        "html.parser",
    )

    signatures = set()

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        raw_href = anchor.get(
            "href",
            "",
        )

        if not isinstance(raw_href, str):
            continue

        raw_href = html.unescape(
            raw_href
        ).strip()

        if not raw_href:
            continue

        if raw_href.lower().startswith(
            (
                "#",
                "javascript:",
                "mailto:",
                "tel:",
            )
        ):
            continue

        full_url = normalize_url(
            urljoin(
                JOBTHAI_BASE_URL,
                raw_href,
            )
        )

        if is_job_detail_url(full_url):
            signatures.add(full_url)

    return signatures


def extract_jobs(page_source, page_number):
    """
    ดึงงานจากหนึ่งหน้า และตัดงานเกิน 7 วันออก
    """

    soup = BeautifulSoup(
        page_source,
        "html.parser",
    )

    jobs = {}

    total_links = 0
    duplicate_links = 0
    expired_jobs = 0
    unknown_date_jobs = 0
    future_date_jobs = 0

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        raw_href = anchor.get(
            "href",
            "",
        )

        if not isinstance(raw_href, str):
            continue

        raw_href = html.unescape(
            raw_href
        ).strip()

        if not raw_href:
            continue

        if raw_href.lower().startswith(
            (
                "#",
                "javascript:",
                "mailto:",
                "tel:",
            )
        ):
            continue

        full_url = normalize_url(
            urljoin(
                JOBTHAI_BASE_URL,
                raw_href,
            )
        )

        if not is_job_detail_url(full_url):
            continue

        total_links += 1

        if full_url in jobs:
            duplicate_links += 1
            continue

        title = anchor.get_text(
            " ",
            strip=True,
        )

        if not title:
            title = anchor.get(
                "title",
                "",
            )

        title = clean_title(title)

        posted_date = find_job_posted_date(
            anchor
        )

        age_days = get_job_age_days(
            posted_date
        )

        if posted_date is None:
            unknown_date_jobs += 1

            print(
                "Unknown date retained:",
                title,
                "| URL:",
                full_url,
            )

        elif age_days < 0:
            future_date_jobs += 1

            print(
                "Future date retained:",
                title,
                "| Posted:",
                posted_date.isoformat(),
                "| URL:",
                full_url,
            )

        elif not is_within_allowed_age(
            posted_date
        ):
            expired_jobs += 1

            print(
                "Old job removed:",
                title,
                "| Posted:",
                posted_date.isoformat(),
                "| Age:",
                age_days,
                "days",
                "| URL:",
                full_url,
            )

            continue

        jobs[full_url] = {
            "title": title,
            "url": full_url,
            "posted_date": (
                posted_date.isoformat()
                if posted_date is not None
                else None
            ),
            "age_days": age_days,
            "source_page": page_number,
        }

    print(
        "Matching links on page:",
        total_links,
    )
    print(
        "Duplicate links on page:",
        duplicate_links,
    )
    print(
        "Old jobs removed on page:",
        expired_jobs,
    )
    print(
        "Unknown dates retained:",
        unknown_date_jobs,
    )
    print(
        "Future dates retained:",
        future_date_jobs,
    )
    print(
        "Valid jobs kept on page:",
        len(jobs),
    )

    return jobs


def download_all_job_pages():
    """
    ตรวจ /ทำงานที่บ้าน/1, /2, /3 ... ทุกหน้า

    หยุดเมื่อ:
    - ไม่มี URL ใหม่ติดต่อกัน 2 หน้า
    - หน้าเว็บตอบ 404
    - ดาวน์โหลดไม่ได้
    - ตรวจครบ 100 หน้า
    """

    all_jobs = {}
    all_signatures = set()

    pages_checked = 0
    pages_with_new_urls = 0
    consecutive_empty_pages = 0

    with requests.Session() as session:
        for page_number in range(
            1,
            MAX_PAGES + 1,
        ):
            try:
                page_source = download_page(
                    page_number,
                    session,
                )

            except requests.RequestException as error:
                print(
                    "ERROR downloading page",
                    page_number,
                    ":",
                    error,
                )

                break

            if page_source is None:
                consecutive_empty_pages += 1

                print(
                    "Unavailable pages in a row:",
                    consecutive_empty_pages,
                )

                if (
                    consecutive_empty_pages
                    >= EMPTY_PAGE_LIMIT
                ):
                    print(
                        "Stopping after consecutive "
                        "unavailable pages."
                    )
                    break

                continue

            pages_checked += 1

            page_signatures = (
                extract_job_url_signatures(
                    page_source
                )
            )

            new_signatures = (
                page_signatures
                - all_signatures
            )

            print(
                "Job URLs before filtering:",
                len(page_signatures),
            )
            print(
                "New URLs on this page:",
                len(new_signatures),
            )

            if not page_signatures:
                consecutive_empty_pages += 1

                print(
                    "No job URLs found on page:",
                    page_number,
                )

            elif not new_signatures:
                consecutive_empty_pages += 1

                print(
                    "Only duplicate URLs found on page:",
                    page_number,
                )

            else:
                consecutive_empty_pages = 0
                pages_with_new_urls += 1

            all_signatures.update(
                page_signatures
            )

            page_jobs = extract_jobs(
                page_source,
                page_number,
            )

            added_jobs = 0

            for job_url, job_data in page_jobs.items():
                if job_url not in all_jobs:
                    all_jobs[job_url] = job_data
                    added_jobs += 1

            print(
                "Valid jobs added from page:",
                added_jobs,
            )
            print(
                "Total valid jobs collected:",
                len(all_jobs),
            )

            if (
                consecutive_empty_pages
                >= EMPTY_PAGE_LIMIT
            ):
                print(
                    "Stopping after",
                    consecutive_empty_pages,
                    "pages without new job URLs.",
                )
                break

            if page_number < MAX_PAGES:
                print(
                    "Waiting",
                    REQUEST_DELAY_SECONDS,
                    "seconds...",
                )

                time.sleep(
                    REQUEST_DELAY_SECONDS
                )

    print("=" * 60)
    print("Pagination summary")
    print("=" * 60)
    print("Pages checked:", pages_checked)
    print(
        "Pages containing new URLs:",
        pages_with_new_urls,
    )
    print(
        "Unique URLs before date filtering:",
        len(all_signatures),
    )
    print(
        "Jobs retained after 7-day filtering:",
        len(all_jobs),
    )
    print("=" * 60)

    return all_jobs


# =========================================================
# jobs.json
# =========================================================

def load_previous_jobs():
    """อ่านงานที่บันทึกไว้ใน jobs.json"""

    if not STATE_FILE.exists():
        print("jobs.json does not exist.")
        return {}

    try:
        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

    except json.JSONDecodeError as error:
        print(
            "WARNING: jobs.json is invalid:",
            error,
        )
        return {}

    except OSError as error:
        print(
            "WARNING: Cannot read jobs.json:",
            error,
        )
        return {}

    converted = {}

    if isinstance(data, dict):
        iterable = data.items()

        for saved_url, saved_data in iterable:
            if isinstance(saved_data, dict):
                job_url = saved_data.get(
                    "url",
                    saved_url,
                )

                normalized_url = normalize_url(
                    job_url
                )

                converted[normalized_url] = (
                    saved_data
                )

                converted[normalized_url][
                    "url"
                ] = normalized_url

            elif isinstance(saved_url, str):
                normalized_url = normalize_url(
                    saved_url
                )

                converted[normalized_url] = {
                    "title": "ประกาศงาน",
                    "url": normalized_url,
                }

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                normalized_url = normalize_url(
                    item
                )

                converted[normalized_url] = {
                    "title": "ประกาศงาน",
                    "url": normalized_url,
                }

            elif isinstance(item, dict):
                item_url = item.get("url")

                if not item_url:
                    continue

                normalized_url = normalize_url(
                    item_url
                )

                converted[normalized_url] = item
                converted[normalized_url][
                    "url"
                ] = normalized_url

    return converted


def save_jobs(jobs):
    """
    บันทึกเฉพาะงานที่ผ่านเงื่อนไขลง jobs.json
    """

    with STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
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
# อีเมล
# =========================================================

def format_posted_date(job):
    """จัดรูปแบบวันที่สำหรับอีเมล"""

    posted_date_text = job.get(
        "posted_date"
    )

    age_days = job.get(
        "age_days"
    )

    if not posted_date_text:
        return "วันที่ประกาศ: ไม่พบข้อมูล"

    try:
        posted_date = date.fromisoformat(
            posted_date_text
        )

        displayed_date = posted_date.strftime(
            "%d/%m/%Y"
        )

    except ValueError:
        displayed_date = posted_date_text

    if age_days == 0:
        age_text = "วันนี้"

    elif age_days == 1:
        age_text = "เมื่อวาน"

    elif (
        isinstance(age_days, int)
        and age_days > 1
    ):
        age_text = f"{age_days} วันที่แล้ว"

    else:
        age_text = "ไม่ทราบอายุประกาศ"

    return (
        f"วันที่ประกาศ: {displayed_date} "
        f"({age_text})"
    )


def create_email(new_jobs, test_mode=False):
    """สร้าง message.html และ email_subject.txt"""

    if test_mode:
        subject_text = "ทดสอบระบบ JobThai Alert"
        heading = "ระบบส่งอีเมลทำงานแล้ว"

        intro = (
            "นี่คืออีเมลทดสอบจาก GitHub Actions "
            "ผ่าน Google Apps Script "
            "ไม่ใช่การแจ้งประกาศใหม่"
        )

        job_items = ""

    else:
        subject_text = (
            "พบงาน Work From Home ใหม่ "
            f"{len(new_jobs)} รายการ"
        )

        heading = subject_text

        intro = (
            "พบประกาศใหม่จากทุกหน้าของ JobThai "
            f"ที่มีอายุไม่เกิน {MAX_JOB_AGE_DAYS} วัน"
        )

        item_list = []

        for job in new_jobs[:100]:
            safe_title = html.escape(
                job.get(
                    "title",
                    "ประกาศงาน",
                ),
                quote=True,
            )

            safe_url = html.escape(
                job.get(
                    "url",
                    JOBTHAI_BASE_URL,
                ),
                quote=True,
            )

            safe_date = html.escape(
                format_posted_date(job),
                quote=True,
            )

            source_page = job.get(
                "source_page",
                "ไม่ทราบ",
            )

            item_list.append(
                f"""
                <li style="margin-bottom:18px;">
                    <a
                      href="{safe_url}"
                      style="
                        font-weight:bold;
                        color:#0969da;
                        text-decoration:none;
                        font-size:16px;
                      "
                    >
                      {safe_title}
                    </a>

                    <br>

                    <span style="font-size:13px;color:#333333;">
                      {safe_date}
                    </span>

                    <br>

                    <span style="font-size:12px;color:#666666;">
                      พบจากหน้าที่: {source_page}
                    </span>

                    <br>

                    <span
                      style="
                        font-size:12px;
                        color:#666666;
                        word-break:break-all;
                      "
                    >
                      {safe_url}
                    </span>
                </li>
                """
            )

        job_items = (
            '<ol style="padding-left:24px;">'
            + "".join(item_list)
            + "</ol>"
        )

        if len(new_jobs) > 100:
            remaining = (
                len(new_jobs) - 100
            )

            job_items += (
                "<p>"
                f"ยังมีอีก {remaining} รายการ "
                "กรุณาเปิดหน้าค้นหางาน JobThai"
                "</p>"
            )

    safe_heading = html.escape(
        heading,
        quote=True,
    )

    safe_intro = html.escape(
        intro,
        quote=True,
    )

    safe_search_url = html.escape(
        JOBTHAI_BASE_URL,
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
          margin:0;
          padding:20px;
          background:#f5f5f5;
          color:#222222;
          font-family:Arial,Tahoma,sans-serif;
          line-height:1.6;
        "
      >
        <div
          style="
            max-width:760px;
            margin:0 auto;
            padding:24px;
            background:#ffffff;
            border:1px solid #dddddd;
            border-radius:8px;
          "
        >
          <h2 style="color:#d32f2f;margin-top:0;">
            {safe_heading}
          </h2>

          <p>{safe_intro}</p>

          {job_items}

          <p style="margin-top:24px;">
            <a
              href="{safe_search_url}"
              style="
                display:inline-block;
                padding:10px 16px;
                color:#ffffff;
                background:#d32f2f;
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

          <p style="font-size:12px;color:#666666;">
            ตรวจทุกหน้า /ทำงานที่บ้าน/x
            และเก็บเฉพาะประกาศอายุไม่เกิน
            {MAX_JOB_AGE_DAYS} วัน ตามเวลาไทย
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


def remove_old_email_files():
    """ลบไฟล์ชั่วคราวจากการทำงานครั้งก่อน"""

    temporary_files = [
        MESSAGE_FILE,
        SUBJECT_FILE,
        Path("payload.json"),
        Path("apps-script-response.txt"),
        Path("apps-script-response.json"),
    ]

    for file_path in temporary_files:
        if file_path.exists():
            file_path.unlink()

            print(
                "Removed old file:",
                file_path,
            )


# =========================================================
# Main
# =========================================================

def main():
    print("=" * 60)
    print("JobThai Alert")
    print("=" * 60)

    print(
        "Base URL:",
        JOBTHAI_BASE_URL,
    )
    print(
        "Thailand date:",
        get_thailand_today().isoformat(),
    )
    print(
        "Maximum job age:",
        MAX_JOB_AGE_DAYS,
        "days",
    )
    print(
        "Maximum pages:",
        MAX_PAGES,
    )
    print(
        "Force test:",
        FORCE_TEST,
    )

    remove_old_email_files()

    # ทดสอบเฉพาะระบบส่งอีเมล
    if FORCE_TEST:
        create_email(
            new_jobs=[],
            test_mode=True,
        )

        print("TEST_EMAIL_CREATED=true")
        print(
            "Test mode completed successfully."
        )

        return

    try:
        current_jobs = (
            download_all_job_pages()
        )

    except Exception as error:
        print(
            "ERROR while checking JobThai pages:"
        )
        print("Details:", error)
        sys.exit(1)

    if not current_jobs:
        print(
            "ERROR: No valid jobs were collected."
        )
        print(
            "jobs.json will not be changed."
        )
        sys.exit(1)

    previous_jobs = load_previous_jobs()

    print(
        "Previously saved jobs:",
        len(previous_jobs),
    )

    new_job_urls = [
        job_url
        for job_url in current_jobs
        if job_url not in previous_jobs
    ]

    new_jobs = [
        current_jobs[job_url]
        for job_url in new_job_urls
    ]

    print(
        "New jobs found:",
        len(new_jobs),
    )

    if not previous_jobs:
        print(
            "First run: creating baseline only."
        )
        print(
            "No email will be sent on the first run."
        )

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
                job.get(
                    "title",
                    "ประกาศงาน",
                ),
            )
            print(
                "Posted:",
                job.get(
                    "posted_date",
                    "unknown",
                ),
            )
            print(
                "Age:",
                job.get(
                    "age_days",
                    "unknown",
                ),
            )
            print(
                "Source page:",
                job.get(
                    "source_page",
                    "unknown",
                ),
            )
            print(
                "URL:",
                job.get(
                    "url",
                    "",
                ),
            )

    else:
        print("No new jobs detected.")
        print("NEW_JOBS_DETECTED=false")
        print("No email will be sent.")

    save_jobs(current_jobs)

    print(
        "JobThai check completed successfully."
    )


if __name__ == "__main__":
    main()
