import html
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
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

# เขตเวลาไทย UTC+7
THAILAND_TIMEZONE = timezone(timedelta(hours=7))

# เก็บเฉพาะประกาศที่มีอายุไม่เกิน 7 วัน
MAX_JOB_AGE_DAYS = 7

# GitHub Actions จะส่งค่า true เข้ามาเมื่อเลือกทดสอบอีเมล
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
    """
    คืนค่าวันที่ปัจจุบันตามเวลาไทย UTC+7
    """

    return datetime.now(
        THAILAND_TIMEZONE
    ).date()


def convert_two_digit_year(year_number):
    """
    แปลงปีแบบ 2 หลัก

    ตัวอย่าง:
    69 หมายถึง พ.ศ. 2569 หรือ ค.ศ. 2026
    """

    current_year = get_thailand_today().year
    current_buddhist_year = current_year + 543
    current_buddhist_short = current_buddhist_year % 100

    if year_number <= current_buddhist_short + 10:
        buddhist_year = 2500 + year_number
    else:
        buddhist_year = 2400 + year_number

    return buddhist_year - 543


def parse_thai_posted_date(text):
    """
    แปลงข้อความวันที่ภาษาไทยเป็น datetime.date

    รองรับตัวอย่าง:
    วันนี้
    เมื่อวาน
    2 ชั่วโมงที่แล้ว
    30 นาทีที่แล้ว
    3 วันที่แล้ว
    3 วันที่ผ่านมา
    12 พ.ย. 2569
    12 พฤศจิกายน 2569
    12 พ.ย. 69
    12/11/2569
    12-11-2026

    ถ้าอ่านวันที่ไม่ได้ จะคืนค่า None
    """

    if not text:
        return None

    normalized_text = " ".join(
        str(text).replace("\u00a0", " ").split()
    ).strip()

    today = get_thailand_today()

    if "วันนี้" in normalized_text:
        return today

    if "เมื่อวาน" in normalized_text:
        return today - timedelta(days=1)

    if re.search(
        r"\d+\s*(นาที|ชั่วโมง)(?:ที่แล้ว|ที่ผ่านมา)?",
        normalized_text,
    ):
        return today

    day_ago_match = re.search(
        r"(\d+)\s*วัน(?:ที่แล้ว|ที่ผ่านมา)",
        normalized_text,
    )

    if day_ago_match:
        number_of_days = int(
            day_ago_match.group(1)
        )

        return today - timedelta(
            days=number_of_days
        )

    thai_months = {
        "ม.ค.": 1,
        "ม.ค": 1,
        "มกราคม": 1,
        "ก.พ.": 2,
        "ก.พ": 2,
        "กุมภาพันธ์": 2,
        "มี.ค.": 3,
        "มี.ค": 3,
        "มีนาคม": 3,
        "เม.ย.": 4,
        "เม.ย": 4,
        "เมษายน": 4,
        "พ.ค.": 5,
        "พ.ค": 5,
        "พฤษภาคม": 5,
        "มิ.ย.": 6,
        "มิ.ย": 6,
        "มิถุนายน": 6,
        "ก.ค.": 7,
        "ก.ค": 7,
        "กรกฎาคม": 7,
        "ส.ค.": 8,
        "ส.ค": 8,
        "สิงหาคม": 8,
        "ก.ย.": 9,
        "ก.ย": 9,
        "กันยายน": 9,
        "ต.ค.": 10,
        "ต.ค": 10,
        "ตุลาคม": 10,
        "พ.ย.": 11,
        "พ.ย": 11,
        "พฤศจิกายน": 11,
        "ธ.ค.": 12,
        "ธ.ค": 12,
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
        flags=re.IGNORECASE,
    )

    if thai_date_pattern:
        day_number = int(
            thai_date_pattern.group(1)
        )

        month_text = thai_date_pattern.group(2)
        month_number = thai_months.get(month_text)

        year_text = thai_date_pattern.group(3)

        if year_text:
            year_number = int(year_text)

            if year_number > 2400:
                year_number -= 543

            elif year_number < 100:
                year_number = convert_two_digit_year(
                    year_number
                )

        else:
            year_number = today.year

        try:
            parsed_date = date(
                year_number,
                month_number,
                day_number,
            )

            # ถ้าไม่มีปีและวันที่ดูเหมือนอยู่ในอนาคต
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

    numeric_date_pattern = re.search(
        r"\b(\d{1,2})\d{1,2}\d{2,4}\b",
        normalized_text,
    )

    if numeric_date_pattern:
        day_number = int(
            numeric_date_pattern.group(1)
        )

        month_number = int(
            numeric_date_pattern.group(2)
        )

        year_number = int(
            numeric_date_pattern.group(3)
        )

        if year_number > 2400:
            year_number -= 543

        elif year_number < 100:
            year_number = convert_two_digit_year(
                year_number
            )

        try:
            return date(
                year_number,
                month_number,
                day_number,
            )

        except ValueError:
            return None

    iso_date_pattern = re.search(
        r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b",
        normalized_text,
    )

    if iso_date_pattern:
        year_number = int(
            iso_date_pattern.group(1)
        )

        month_number = int(
            iso_date_pattern.group(2)
        )

        day_number = int(
            iso_date_pattern.group(3)
        )

        try:
            return date(
                year_number,
                month_number,
                day_number,
            )

        except ValueError:
            return None

    return None


def get_job_age_days(posted_date):
    """
    คำนวณอายุประกาศเป็นจำนวนวัน
    """

    if posted_date is None:
        return None

    return (
        get_thailand_today() - posted_date
    ).days


def is_within_allowed_age(posted_date):
    """
    อนุญาตประกาศอายุ 0 ถึง 7 วัน

    ประกาศอายุ 8 วันขึ้นไปจะถูกตัดออก
    ถ้าอ่านวันที่ไม่ได้ จะเก็บไว้เพื่อป้องกันพลาดงาน
    """

    if posted_date is None:
        return True

    age_in_days = get_job_age_days(
        posted_date
    )

    # วันที่ในอนาคตอาจมาจากรูปแบบปีที่หน้าเว็บไม่ชัดเจน
    # จึงเก็บไว้ก่อน
    if age_in_days < 0:
        return True

    return age_in_days <= MAX_JOB_AGE_DAYS


# =========================================================
# จัดการ URL
# =========================================================

def normalize_url(url):
    """
    ทำ URL ให้เป็นรูปแบบเดียวกัน

    ลบ query string และ fragment เพื่อป้องกัน
    ประกาศเดียวกันถูกนับเป็นหลายรายการ
    """

    parsed = urlparse(url)

    normalized_path = parsed.path.rstrip("/")

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
    """
    ตรวจว่า URL มีลักษณะเป็นหน้ารายละเอียดงานหรือไม่
    """

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
# อ่านและบันทึก jobs.json
# =========================================================

def load_previous_jobs():
    """
    อ่านรายการงานเดิมจาก jobs.json
    """

    if not STATE_FILE.exists():
        print("jobs.json does not exist.")
        return {}

    try:
        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        if isinstance(data, dict):
            converted = {}

            for saved_url, saved_job in data.items():
                if isinstance(saved_job, dict):
                    job_url = saved_job.get(
                        "url",
                        saved_url,
                    )

                    normalized_url = normalize_url(
                        job_url
                    )

                    converted[normalized_url] = {
                        "title": saved_job.get(
                            "title",
                            "ประกาศงาน",
                        ),
                        "url": normalized_url,
                        "posted_date": saved_job.get(
                            "posted_date"
                        ),
                        "age_days": saved_job.get(
                            "age_days"
                        ),
                    }

                elif isinstance(saved_url, str):
                    normalized_url = normalize_url(
                        saved_url
                    )

                    converted[normalized_url] = {
                        "title": "ประกาศงาน",
                        "url": normalized_url,
                        "posted_date": None,
                        "age_days": None,
                    }

            return converted

        # รองรับ jobs.json รุ่นเก่าที่เป็นรายการ
        if isinstance(data, list):
            converted = {}

            for item in data:
                if isinstance(item, str):
                    normalized_url = normalize_url(
                        item
                    )

                    converted[normalized_url] = {
                        "title": "ประกาศงาน",
                        "url": normalized_url,
                        "posted_date": None,
                        "age_days": None,
                    }

                elif isinstance(item, dict):
                    item_url = item.get("url")

                    if not item_url:
                        continue

                    normalized_url = normalize_url(
                        item_url
                    )

                    converted[normalized_url] = {
                        "title": item.get(
                            "title",
                            "ประกาศงาน",
                        ),
                        "url": normalized_url,
                        "posted_date": item.get(
                            "posted_date"
                        ),
                        "age_days": item.get(
                            "age_days"
                        ),
                    }

            return converted

        print(
            "WARNING: jobs.json has an unsupported format."
        )

    except json.JSONDecodeError as error:
        print(
            "WARNING: jobs.json is not valid JSON:",
            error,
        )

    except OSError as error:
        print(
            "WARNING: Cannot read jobs.json:",
            error,
        )

    return {}


def save_jobs(jobs):
    """
    บันทึกเฉพาะประกาศที่ผ่านเงื่อนไขอายุ
    ลงใน jobs.json
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
# ดาวน์โหลดหน้า JobThai
# =========================================================

def download_page():
    """
    ดาวน์โหลดหน้าค้นหางาน Work From Home
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
        "Accept-Language": (
            "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7"
        ),
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
    print(
        "Downloaded characters:",
        len(response.text),
    )

    response.raise_for_status()

    if len(response.text) < 1000:
        raise RuntimeError(
            "หน้าเว็บมีข้อมูลน้อยผิดปกติ "
            "JobThai อาจป้องกันการเข้าถึง "
            "หรือโหลดหน้าไม่สมบูรณ์"
        )

    return response.text


# =========================================================
# ดึงรายละเอียดงาน
# =========================================================

def clean_title(title):
    """
    ทำความสะอาดชื่อตำแหน่งงาน
    """

    if not title:
        return "เปิดดูรายละเอียดประกาศงาน"

    cleaned_title = " ".join(
        str(title).split()
    ).strip()

    if not cleaned_title:
        return "เปิดดูรายละเอียดประกาศงาน"

    return cleaned_title[:300]


def element_contains_date_text(text):
    """
    ตรวจว่าข้อความมีรูปแบบวันที่หรือเวลาประกาศหรือไม่
    """

    if not text:
        return False

    date_words = [
        "วันนี้",
        "เมื่อวาน",
        "นาทีที่แล้ว",
        "ชั่วโมงที่แล้ว",
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
        word in text
        for word in date_words
    )


def find_job_posted_date(anchor):
    """
    ค้นหาวันที่ประกาศจากลิงก์และกล่องงานรอบลิงก์

    จำกัดการไล่ parent ไม่เกิน 6 ระดับ
    เพื่อป้องกันการนำวันที่จากประกาศอื่นมาใช้
    """

    elements_to_check = []

    current_element = anchor

    for _ in range(6):
        if current_element is None:
            break

        elements_to_check.append(
            current_element
        )

        current_element = (
            current_element.parent
        )

    for element in elements_to_check:
        # ตรวจ attributes ก่อน
        for attribute_name in [
            "datetime",
            "data-date",
            "data-posted-date",
            "data-created-at",
            "title",
            "aria-label",
        ]:
            attribute_value = element.get(
                attribute_name
            )

            if attribute_value:
                parsed_date = parse_thai_posted_date(
                    str(attribute_value)
                )

                if parsed_date is not None:
                    return parsed_date

        element_text = element.get_text(
            " ",
            strip=True,
        )

        if not element_contains_date_text(
            element_text
        ):
            continue

        parsed_date = parse_thai_posted_date(
            element_text
        )

        if parsed_date is not None:
            return parsed_date

    return None


def extract_jobs(page_source):
    """
    ดึงชื่อตำแหน่ง URL และวันที่ประกาศจากหน้า JobThai

    เก็บเฉพาะประกาศอายุไม่เกิน MAX_JOB_AGE_DAYS
    """

    soup = BeautifulSoup(
        page_source,
        "html.parser",
    )

    jobs = {}

    total_job_links = 0
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
                JOBTHAI_URL,
                raw_href,
            )
        )

        if not is_job_detail_url(full_url):
            continue

        total_job_links += 1

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

        age_in_days = get_job_age_days(
            posted_date
        )

        if posted_date is None:
            unknown_date_jobs += 1

            print(
                "Unknown posted date:",
                title,
                "| URL:",
                full_url,
            )

        elif age_in_days < 0:
            future_date_jobs += 1

            print(
                "Future posted date retained:",
                title,
                "| posted:",
                posted_date.isoformat(),
                "| age:",
                age_in_days,
                "| URL:",
                full_url,
            )

        elif not is_within_allowed_age(
            posted_date
        ):
            expired_jobs += 1

            print(
                "Removed old job:",
                title,
                "| posted:",
                posted_date.isoformat(),
                "| age:",
                age_in_days,
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
            "age_days": age_in_days,
        }

    print("=" * 60)
    print("All matching links:", total_job_links)
    print("Duplicate links ignored:", duplicate_links)
    print(
        "Jobs older than",
        MAX_JOB_AGE_DAYS,
        "days removed:",
        expired_jobs,
    )
    print(
        "Jobs with unknown dates retained:",
        unknown_date_jobs,
    )
    print(
        "Jobs with future dates retained:",
        future_date_jobs,
    )
    print(
        "Jobs kept after date filtering:",
        len(jobs),
    )
    print("=" * 60)

    return jobs


# =========================================================
# สร้างอีเมล
# =========================================================

def format_posted_date(job):
    """
    จัดรูปแบบวันที่สำหรับแสดงในอีเมล
    """

    posted_date_text = job.get(
        "posted_date"
    )

    age_in_days = job.get(
        "age_days"
    )

    if not posted_date_text:
        return "วันที่ประกาศ: ไม่พบข้อมูลวันที่"

    try:
        posted_date = date.fromisoformat(
            posted_date_text
        )

        thai_date_text = posted_date.strftime(
            "%d/%m/%Y"
        )

    except ValueError:
        thai_date_text = posted_date_text

    if age_in_days == 0:
        age_text = "วันนี้"

    elif age_in_days == 1:
        age_text = "เมื่อวาน"

    elif (
        isinstance(age_in_days, int)
        and age_in_days > 1
    ):
        age_text = (
            f"{age_in_days} วันที่แล้ว"
        )

    else:
        age_text = "ไม่ทราบอายุประกาศ"

    return (
        f"วันที่ประกาศ: {thai_date_text} "
        f"({age_text})"
    )


def create_email(new_jobs, test_mode=False):
    """
    สร้าง message.html และ email_subject.txt
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
            "พบประกาศใหม่ที่มีอายุไม่เกิน "
            f"{MAX_JOB_AGE_DAYS} วัน "
            "และยังไม่เคยบันทึกใน jobs.json"
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

            posted_date_display = (
                format_posted_date(job)
            )

            safe_title = html.escape(
                title,
                quote=True,
            )

            safe_url = html.escape(
                job_url,
                quote=True,
            )

            safe_date = html.escape(
                posted_date_display,
                quote=True,
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

                    <span
                        style="
                            color:#333333;
                            font-size:13px;
                        "
                    >
                        {safe_date}
                    </span>

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
            '<ol style="padding-left:24px;">'
            + "".join(item_list)
            + "</ol>"
        )

        if len(new_jobs) > 50:
            remaining_jobs = (
                len(new_jobs) - 50
            )

            job_items += (
                "<p>"
                "ยังมีประกาศเพิ่มเติมอีก "
                f"{remaining_jobs} รายการ "
                "กรุณาเปิดหน้าค้นหาของ JobThai"
                "</p>"
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
            ระบบเก็บเฉพาะประกาศอายุไม่เกิน
            {MAX_JOB_AGE_DAYS} วัน ตามเวลาไทย UTC+7
          </p>

          <p
            style="
              color:#666666;
              font-size:12px;
              margin-top:4px;
              margin-bottom:0;
            "
          >
            ส่งโดย GitHub Actions และ Google Apps Script
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
# ลบไฟล์ชั่วคราว
# =========================================================

def remove_old_email_files():
    """
    ลบไฟล์อีเมลจากการรันครั้งก่อน
    เพื่อป้องกันการส่งอีเมลเดิมซ้ำ
    """

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
# การทำงานหลัก
# =========================================================

def main():
    print("=" * 60)
    print("JobThai Alert")
    print("=" * 60)

    print("JobThai URL:", JOBTHAI_URL)
    print(
        "Thailand date:",
        get_thailand_today().isoformat(),
    )
    print(
        "Maximum job age:",
        MAX_JOB_AGE_DAYS,
        "days",
    )
    print("Force test:", FORCE_TEST)

    remove_old_email_files()

    # โหมดทดสอบอีเมล
    # ไม่ดาวน์โหลด JobThai และไม่แก้ jobs.json
    if FORCE_TEST:
        print("Creating test email...")

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
        page_source = download_page()

        current_jobs = extract_jobs(
            page_source
        )

    except requests.RequestException as error:
        print(
            "ERROR: Downloading JobThai failed."
        )
        print("Details:", error)
        sys.exit(1)

    except Exception as error:
        print("ERROR:", error)
        sys.exit(1)

    print(
        "Current valid jobs:",
        len(current_jobs),
    )

    # ไม่เขียนทับ jobs.json ถ้าดึงรายการไม่ได้เลย
    if not current_jobs:
        print(
            "ERROR: ไม่พบประกาศงานที่ผ่านเงื่อนไข"
        )
        print(
            "JobThai อาจเปลี่ยนโครงสร้างหน้าเว็บ "
            "หรือป้องกัน GitHub Actions"
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
                "Posted date:",
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

    # jobs.json จะมีเฉพาะงานปัจจุบันที่อายุไม่เกิน 7 วัน
    # และงานที่ไม่สามารถอ่านวันที่ได้
    save_jobs(current_jobs)

    print(
        "JobThai check completed successfully."
    )


if __name__ == "__main__":
    main()
