import requests
import json
import os

URL = "https://www.jobthai.com/หางาน/ทำงานที่บ้าน"

headers = {
    "User-Agent": "Mozilla/5.0"
}

response = requests.get(URL, headers=headers)
html = response.text

jobs = []

for line in html.splitlines():
    if "งาน" in line:
        jobs.append(line.strip())

jobs = list(set(jobs))

old_jobs = []

if os.path.exists("jobs.json"):
    with open("jobs.json", "r", encoding="utf-8") as f:
        old_jobs = json.load(f)

new_jobs = [job for job in jobs if job not in old_jobs]

if new_jobs:

    message = "<h2>พบงานใหม่ใน JobThai</h2><ul>"

    for job in new_jobs[:20]:
        message += f"<li>{job}</li>"

    message += "</ul>"

    with open("message.html", "w", encoding="utf-8") as f:
        f.write(message)

with open("jobs.json", "w", encoding="utf-8") as f:
    json.dump(jobs, f, ensure_ascii=False)
