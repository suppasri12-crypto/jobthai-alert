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

THAILAND_TIMEZONE = timezone(
    timedelta(hours=7)
)

# เก็บประกาศอายุไม่เกิน 7 วัน
MAX_JOB_AGE_DAYS = 7

# ตรวจสูงสุด 100 หน้า
MAX_PAGES = 100

# หยุดเมื่อไม่มี URL ประกาศใหม่ 2 หน้าติดต่อกัน
EMPTY_PAGE_LIMIT = 2

# พักระหว่างหน้า เพื่อลดการเรียกเว็บไซต์ถี่เกินไป
REQUEST_DELAY_SECONDS = 2

# รับค่าจาก GitHub Actions
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
    """คืนวันที่ปัจจุบันตามเวลาไทย UTC+7"""

    return datetime.now(
        THAILAND_TIMEZONE
    ).date()


def convert_two_digit_year(year_number):
    """
    แปลงปีไทยแบบ 2 หลักเป็น ค.ศ.

    ตัวอย่าง:
    69 -> พ.ศ. 2569 -> ค.ศ. 2026
    """

    if year_number <= 79:
        buddhist_year = 2500 + year_number
    else:
        buddhist_year = 2400 + year_number

    return buddhist_year - 543


def parse_year(year_text):
    """แปลงปี พ.ศ. หรือ ค.ศ. เป็น ค.ศ."""

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
    อ่านวันที่ประกาศจากข้อความภาษาไทย

    รองรับตัวอย่าง:
    - วันนี้
    - เมื่อวาน
    - 30 นาทีที่แล้ว
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

    days_ago_match = re.search(
        r"(\d+)\s*วัน"
        r"(?:ที่แล้ว|ที่ผ่านมา)",
        normalized_text,
    )

    if days_ago_match:
        days_ago = int(
            days_ago_match.group(1)
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

    thai_date_match = re.search(
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

    if thai_date_match:
        day_number = int(
            thai_date_match.group(1)
        )

        month_text = (
            thai_date_match.group(2).strip()
        )

        month_number = thai_months.get(
            month_text
        )

        year_text = thai_date_match.group(3)

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

            # กรณีเว็บไม่แสดงปี และวันที่ดูเหมือนเป็นอนาคต
            # ให้ใช้ปีที่แล้ว
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
    numeric_date_match = re.search(
        r"\b(\d{1,2})[/-]"
        r"(\d{1,2})[/-]"
        r"(\d{2,4})\b",
        normalized_text,
    )

    if numeric_date_match:
        try:
            day_number = int(
                numeric_date_match.group(1)
            )

            month_number = int(
                numeric_date_match.group(2)
            )

            year_number = parse_year(
                numeric_date_match.group(3)
            )

            return date(
                year_number,
                month_number,
                day_number,
            )

        except ValueError:
            return None

    # รูปแบบ YYYY-MM-DD
    iso_date_match = re.search(
        r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b",
        normalized_text,
    )

    if iso_date_match:
        try:
            return date(
                int(iso_date_match.group(1)),
                int(iso_date_match.group(2)),
                int(iso_date_match.group(3)),
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
    อนุญาตประกาศอายุ 0 ถึง 7 วัน

    หากไม่สามารถอ่านวันที่ได้ จะเก็บประกาศไว้ก่อน
    เพื่อป้องกันการพลาดประกาศใหม่
    """

    if posted_date is None:
        return True

    age_days = get_job_age_days(
        posted_date
    )

    # วันที่ในอนาคตอาจเกิดจากข้อมูลปีไม่ครบ
    if age_days < 0:
        return True

    return age_days <= MAX_JOB_AGE_DAYS


# =========================================================
# จัดการ URL
# =========================================================

def build_page_url(page_number):
    """
    สร้าง URL ของแต่ละหน้า

    ตัวอย่าง:
    /ทำงานที่บ้าน/1
    /ทำงานที่บ้าน/2
    """

    return (
        f"{JOBTHAI_BASE_URL}/{page_number}"
    )


def normalize_url(url):
    """
    ทำ URL ให้เป็นรูปแบบเดียวกัน

    ลบ query string และ fragment
    เพื่อป้องกันงานเดียวกันถูกนับซ้ำ
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
# ดาวน์โหลดหน้าเว็บ
# =========================================================

def get_request_headers():
    """สร้าง HTTP headers"""

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
    print("Final URL:", response.url)
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
            "WARNING: Page contains too little content."
        )
        return None

    return response.text


# =========================================================
# ดึงข้อมูลประกาศ
# =========================================================

def clean_text(value, maximum_length=300):
    """ทำความสะอาดข้อความ"""

    if not value:
        return ""

    cleaned_value = " ".join(
        str(value).split()
    ).strip()

    return cleaned_value[:maximum_length]


def clean_title(title):
    """ทำความสะอาดชื่อตำแหน่ง"""

    cleaned_title = clean_text(title)

    if not cleaned_title:
        return "เปิดดูรายละเอียดประกาศงาน"

    return cleaned_title


def element_contains_date_text(text):
    """ตรวจคร่าว ๆ ว่าข้อความอาจมีวันที่หรือไม่"""

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
    ค้นหาวันที่ประกาศจากลิงก์
    และกล่องประกาศรอบลิงก์
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


def is_valid_company_name(company_name, job_title):
    """ตรวจว่าข้อความมีลักษณะเป็นชื่อบริษัทหรือไม่"""

    company_name = clean_text(company_name)
    job_title = clean_text(job_title)

    if not company_name:
        return False

    if company_name == job_title:
        return False

    if len(company_name) > 200:
        return False

    rejected_texts = [
        "ดูรายละเอียด",
        "สมัครงาน",
        "งานทั้งหมด",
        "เข้าสู่ระบบ",
        "งานแนะนำ",
        "เงินเดือน",
        "วันที่ประกาศ",
        "วันนี้",
        "เมื่อวาน",
        "ทำงานที่บ้าน",
        "เปิดดูรายละเอียด",
    ]

    return not any(
        rejected_text in company_name
        for rejected_text in rejected_texts
    )


def find_company_name(anchor, job_title):
    """
    ค้นหาชื่อบริษัทจากกล่องประกาศรอบลิงก์

    ถ้าหาไม่พบจะคืนค่า "ไม่พบชื่อบริษัท"
    """

    company_selectors = [
        "[class*='company-name']",
        "[class*='company_name']",
        "[class*='companyName']",
        "[data-testid*='company']",
        "[itemprop='hiringOrganization']",
        "[itemprop='name']",
        "a[href*='/company/']",
        "a[href*='/บริษัท/']",
    ]

    current_element = anchor

    for _ in range(6):
        if current_element is None:
            break

        for selector in company_selectors:
            try:
                company_elements = (
                    current_element.select(selector)
                )
            except Exception:
                company_elements = []

            for company_element in company_elements:
                company_name = clean_text(
                    company_element.get_text(
                        " ",
                        strip=True,
                    )
                )

                if is_valid_company_name(
                    company_name,
                    job_title,
                ):
                    return company_name

        current_element = current_element.parent

    return "ไม่พบชื่อบริษัท"


def extract_job_url_signatures(page_source):
    """
    ดึง URL ประกาศทั้งหมดก่อนกรองวันที่

    ใช้ตรวจว่าหน้าถัดไปมี URL ใหม่หรือไม่
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
    ดึงงานจากหนึ่งหน้า

    ตัดงานอายุเกิน 7 วันออกก่อนรวมผล
    """

    soup = BeautifulSoup(
        page_source,
        "html.parser",
    )

    jobs = {}

    total_links = 0
    duplicate_links = 0
    expired_jobs = 0
    unknown_dates = 0

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

        company_name = find_company_name(
            anchor,
            title,
        )

        posted_date = find_job_posted_date(
            anchor
        )

        age_days = get_job_age_days(
            posted_date
        )

        if posted_date is None:
            unknown_dates += 1

            print(
                "Unknown date retained:",
                title,
                "| Company:",
                company_name,
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
            )

            continue

        jobs[full_url] = {
            "title": title,
            "company": company_name,
            "url": full_url,
            "posted_date": (
                posted_date.isoformat()
                if posted_date is not None
                else None
            ),
            "age_days": age_days,
            "source_page": page_number,
        }

    print("Matching links:", total_links)
    print("Duplicate links:", duplicate_links)
    print("Expired jobs removed:", expired_jobs)
    print("Unknown dates retained:", unknown_dates)
    print("Valid jobs on page:", len(jobs))

    return jobs


def download_all_job_pages():
    """
    ตรวจทุกหน้า /ทำงานที่บ้าน/x

    หยุดเมื่อ:
    - ไม่มี URL ใหม่ 2 หน้าติดต่อกัน
    - พบ HTTP 404
    - ดาวน์โหลดล้มเหลว
    - ตรวจครบ MAX_PAGES
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
                        "Stopping after unavailable pages."
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
                "New URLs on page:",
                len(new_signatures),
            )

            if not page_signatures:
                consecutive_empty_pages += 1

            elif not new_signatures:
                consecutive_empty_pages += 1

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
                "Valid jobs added:",
                added_jobs,
            )

            print(
                "Total valid jobs:",
                len(all_jobs),
            )

            if (
                consecutive_empty_pages
                >= EMPTY_PAGE_LIMIT
            ):
                print(
                    "Stopping after",
                    consecutive_empty_pages,
                    "pages without new URLs.",
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
        "Pages with new URLs:",
        pages_with_new_urls,
    )
    print(
        "Unique URLs before filtering:",
        len(all_signatures),
    )
    print(
        "Jobs retained after filtering:",
        len(all_jobs),
    )
    print("=" * 60)

    return all_jobs


# =========================================================
# จัดการ jobs.json
# =========================================================

def load_previous_jobs():
    """อ่านข้อมูลเดิมจาก jobs.json"""

    if not STATE_FILE.exists():
        print("jobs.json does not exist.")
        return {}

    try:
        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

    except (
        json.JSONDecodeError,
        OSError,
    ) as error:
        print(
            "WARNING: Cannot read jobs.json:",
            error,
        )
        return {}

    converted = {}

    if isinstance(data, dict):
        for saved_url, saved_data in data.items():
            if isinstance(saved_data, dict):
                job_url = saved_data.get(
                    "url",
                    saved_url,
                )

                normalized_url = normalize_url(
                    job_url
                )

                saved_data["url"] = normalized_url

                converted[normalized_url] = (
                    saved_data
                )

            elif isinstance(saved_url, str):
                normalized_url = normalize_url(
                    saved_url
                )

                converted[normalized_url] = {
                    "title": "ประกาศงาน",
                    "company": "ไม่พบชื่อบริษัท",
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
                    "company": "ไม่พบชื่อบริษัท",
                    "url": normalized_url,
                }

            elif isinstance(item, dict):
                item_url = item.get("url")

                if not item_url:
                    continue

                normalized_url = normalize_url(
                    item_url
                )

                item["url"] = normalized_url

                converted[normalized_url] = item

    return converted


def save_jobs(jobs):
    """บันทึกงานที่ผ่านเงื่อนไขลง jobs.json"""

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
# สร้างอีเมล
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
    """
    สร้าง message.html และ email_subject.txt

    รูปแบบ:
    - ตัวอักษรทั้งหมดเป็น Regular
    - ตำแหน่งและบริษัทอยู่บรรทัดเดียวกัน
    - ไม่แสดง URL ยาว
    - คลิกชื่อตำแหน่งเพื่อเปิดประกาศได้
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

            safe_company = html.escape(
                job.get(
                    "company",
                    "ไม่พบชื่อบริษัท",
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

            safe_page = html.escape(
                str(
                    job.get(
                        "source_page",
                        "ไม่ทราบ",
                    )
                ),
                quote=True,
            )

            item_list.append(
                f"""
                <li
                  style="
                    margin-bottom:22px;
                    font-family:Arial,Tahoma,sans-serif;
                    font-size:14px;
                    font-style:normal;
                    font-weight:400;
                    line-height:1.6;
                  "
                >
                  <div
                    style="
                      color:#222222;
                      font-size:16px;
                      font-style:normal;
                      font-weight:400;
                      line-height:1.5;
                    "
                  >
                    <a
                      href="{safe_url}"
                      style="
                        color:#0969da;
                        text-decoration:none;
                        font-size:16px;
                        font-style:normal;
                        font-weight:400;
                      "
                    >
                      {safe_title}
                    </a>

                    <span
                      style="
                        color:#777777;
                        font-style:normal;
                        font-weight:400;
                      "
                    >
                      &nbsp;|&nbsp;
                    </span>

                    <span
                      style="
                        color:#222222;
                        font-size:16px;
                        font-style:normal;
                        font-weight:400;
                      "
                    >
                      บริษัท: {safe_company}
                    </span>
                  </div>

                  <div
                    style="
                      margin-top:4px;
                      color:#333333;
                      font-size:13px;
                      font-style:normal;
                      font-weight:400;
                    "
                  >
                    {safe_date}
                  </div>

                  <div
                    style="
                      color:#666666;
                      font-size:12px;
                      font-style:normal;
                      font-weight:400;
                    "
                  >
                    พบจากหน้าที่: {safe_page}
                  </div>
                </li>
                """
            )

        job_items = (
            '<ol style="'
            'padding-left:24px;'
            'font-style:normal;'
            'font-weight:400;'
            '">'
            + "".join(item_list)
            + "</ol>"
        )

        if len(new_jobs) > 100:
            remaining = (
                len(new_jobs) - 100
            )

            job_items += (
                '<p style="'
                'font-style:normal;'
                'font-weight:400;'
                '">'
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
          font-size:14px;
          font-style:normal;
          font-weight:400;
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
            font-style:normal;
            font-weight:400;
          "
        >
          <h2
            style="
              color:#d32f2f;
              margin-top:0;
              font-size:20px;
              font-style:normal;
              font-weight:400;
            "
          >
            {safe_heading}
          </h2>

          <p
            style="
              font-style:normal;
              font-weight:400;
            "
          >
            {safe_intro}
          </p>

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
                font-style:normal;
                font-weight:400;
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
              font-style:normal;
              font-weight:400;
            "
          >
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

    print("Base URL:", JOBTHAI_BASE_URL)

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

    # โหมดทดสอบอีเมล
    # ไม่ตรวจเว็บไซต์และไม่แก้ jobs.json
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

    # ครั้งแรกสร้างฐานข้อมูลตั้งต้นก่อน
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
                "Company:",
                job.get(
                    "company",
                    "ไม่พบชื่อบริษัท",
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
