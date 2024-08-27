import json
import jsonschema
import re
import requests
import time
from urllib.parse import urljoin
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from openai import OpenAI
import os
import csv
import argparse


def setup_cli():
    parser = argparse.ArgumentParser(description="CRA Job Crawler")
    parser.add_argument("--csv", default="cra_job_listings.csv",
                        help="Path to CSV file for output and duplicate checking")
    parser.add_argument("--api_key", help="OpenAI API key")
    parser.add_argument("--model", default="gpt-3.5-turbo",
                        choices=["gpt-3.5-turbo", "gpt-4", "gpt-4o"], help="OpenAI model to use")
    parser.add_argument("--chromedriver", required=True,
                        help="Path to chromedriver")
    parser.add_argument("--additional_links", type=int,
                        default=0, help="Number of additional links to process")
    parser.add_argument("--max_attempts", type=int, default=3,
                        help="Maximum number of attempts for parsing job details")
    return parser.parse_args()


def clean_text(text):
    """Clean text by removing excess whitespace and newlines."""
    # Replace multiple whitespace characters (including newlines) with a single space
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def setup_driver(chromedriver_path):
    """Set up and return a Selenium WebDriver."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    service = Service(chromedriver_path)
    return webdriver.Chrome(service=service, options=chrome_options)


def fetch_cra_jobs(driver):
    """Fetch job listings from CRA website using Selenium."""
    url = "https://cra.org/ads/"
    driver.get(url)

    # Wait for the job listings to load
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CLASS_NAME, "job_listings"))
    )

    # Scroll to load all job listings
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script(
            "window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

    page_source = driver.page_source
    soup = BeautifulSoup(page_source, 'html.parser')
    return soup.find_all('li', class_='job_listing')


def extract_job_details(driver, job, num_additional_links):
    """Extract basic details from a job listing and fetch full description."""
    title = job.find('h3').text.strip()
    link = job.find('a')['href']
    company = job.find('div', class_='location').find('strong').text.strip()
    location = job.find('div', class_='location').text.replace(
        company, "").strip()
    job_type = job.find('li', class_='job-type').text.strip()

    # Fetch the detailed job page
    driver.get(link)
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CLASS_NAME, "job_description"))
    )

    soup = BeautifulSoup(driver.page_source, 'html.parser')

    # Extract full description
    job_description_div = soup.find('div', class_='job_description')
    full_description = job_description_div.text.strip()

    # Find and follow links in the job description
    additional_content = []
    additional_links = []
    links = job_description_div.find_all('a', href=True)
    for i, a in enumerate(links):
        href = a['href']
        if not href.startswith('http'):
            href = urljoin(link, href)
        additional_links.append(href)

        if i < num_additional_links:  # Limit to first n links to avoid overloading
            try:
                response = requests.get(href, timeout=10)
                if response.status_code == 200:
                    link_soup = BeautifulSoup(response.text, 'html.parser')
                    # Extract text from the body, removing scripts and styles
                    for script in link_soup(["script", "style"]):
                        script.decompose()
                    link_text = clean_text(link_soup.get_text())
                    # Truncate to first 1000 characters to avoid overwhelming ChatGPT
                    additional_content.append(
                        f"Additional content from {href}:\n{link_text[:1000]}...")
            except Exception as e:
                print(f"Error fetching content from {href}: {e}")

        # Combine original description with additional content
    full_content = full_description + "\n\n" + "\n\n".join(additional_content)

    # Extract posted date and expiration date
    meta = soup.find('ul', class_='meta')
    posted_date = meta.find('li', class_='date-posted').text.strip()
    expiration_date = meta.find_all(
        'li', class_='date-posted')[1].text.replace("Expires on:", "").strip()

    time.sleep(1)  # Be nice to the server

    return title, link, company, location, job_type, full_content, posted_date, expiration_date, additional_links


def query_openai(prompt, model):
    """Query OpenAI API for information extraction."""
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a helpful assistant that extracts specific information from job listings."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content


def parse_job_details(title, details, max_attempts, model):
    """Parse job details using OpenAI API with structured format and JSON output."""
    # flake8: noqa: E501
    prompt = f"""
    Analyze the following job listing title and details. Extract the requested information following the format and instructions carefully, then return the result as a JSON object.

    Job title: {title}
    Job listing details:
    {details}

    Follow these instructions for each field:

    1. University or company name:
    Extract the full name

    2. Department that is hiring:
    Extract the full department name

    3. Position they are hiring:
    Choose one or more appropriate options, separated by commas: Postdoc, Assistant Professor, Associate Professor, Full Professor, Lecturer

    4. Submission deadline:
    Format as YYYY-MM-DD. If not specified, write "Not specified"

    5. Hiring areas:
    List the main areas, prioritizing and choosing from: Security, Software Engineering, Programming Languages, AI, Machine Learning, Data Science, Theory, Systems, Networks, Human-Computer Interaction, Graphics, Robotics. If general or not specified, write "All areas"

    6. Number of recommendation letters required:
    Provide the number only. If not specified, write "Not specified"

    7. Number of positions:
    Provide the number only. If not specified, write "Not specified"

    8. Additional important comments:
    Summarize any other crucial information

    Return a JSON object with the following structure:
    {{
        "university_name": "Answer for item 1",
        "department": "Answer for item 2",
        "position": "Answer for item 3",
        "submission_deadline": "Answer for item 4",
        "hiring_areas": ["Area 1", "Area 2", ...],
        "recommendation_letters": "Answer for item 6",
        "positions_available": "Answer for item 7",
        "additional_comments": "Answer for item 8"
    }}

    Ensure all fields are present in the JSON, even if the information is not available (use null or appropriate default values in such cases).
    """

    schema = {
        "type": "object",
        "properties": {
            "university_name": {"type": "string"},
            "department": {"type": "string"},
            "position": {"type": "string"},
            "submission_deadline": {"type": "string"},
            "hiring_areas": {"type": "array", "items": {"type": "string"}},
            "recommendation_letters": {"type": ["string", "integer"]},
            "positions_available": {"type": ["string", "integer"]},
            "additional_comments": {"type": "string"}
        },
        "required": ["university_name", "department", "position", "submission_deadline", "hiring_areas", "recommendation_letters", "positions_available", "additional_comments"]
    }

    for attempt in range(max_attempts):
        try:
            response = query_openai(prompt, model)
            parsed_json = json.loads(response)
            jsonschema.validate(instance=parsed_json, schema=schema)
            return parsed_json
        except (json.JSONDecodeError, jsonschema.exceptions.ValidationError) as e:
            if attempt == max_attempts - 1:
                print(
                    f"All attempts ({max_attempts}) failed. Returning default values.")
                return {
                    "university_name": "Not specified",
                    "department": "Not specified",
                    "position": "Not specified",
                    "submission_deadline": "Not specified",
                    "hiring_areas": ["Not specified"],
                    "recommendation_letters": "Not specified",
                    "positions_available": 1,
                    "additional_comments": "Failed to parse job details."
                }


def load_existing_jobs(csv_path):
    existing_jobs = {}
    if os.path.exists(csv_path):
        with open(csv_path, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row.get('Additional Comments') != "Failed to parse job details.":
                    # Store the entire valid row, indexed by its title (or another unique field)
                    existing_jobs[row.get('CRA ID', '')] = row
    return existing_jobs


def main():
    args = setup_cli()

    if args.api_key:
        os.environ["OPENAI_API_KEY"] = args.api_key

    existing_jobs = load_existing_jobs(args.csv)

    driver = setup_driver(args.chromedriver)
    try:
        jobs = fetch_cra_jobs(driver)
        if len(jobs) == 0:
            print("Crawling failed. No job listings found. Please try again later.")
            raise Exception("No job listings found")

        for job in jobs:
            crawl_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            title, link, company, location, job_type, full_content, posted_date, expiration_date, additional_links = extract_job_details(
                driver, job, args.additional_links)
            title = f"{company} ({location}): {title}"

            if title in existing_jobs:
                print(f"Skipping duplicate job: {title}")
                continue

            parsed_details = parse_job_details(
                title, full_content, args.max_attempts, args.model)

            job_info = {
                "Crawl Time": crawl_time,
                "Company/University": parsed_details["university_name"],
                "Department": parsed_details["department"],
                "Position": parsed_details["position"],
                "Job Type": job_type,
                "Location": location,
                "Number of Positions": parsed_details["positions_available"],
                "Hiring Areas": ", ".join(parsed_details["hiring_areas"]),
                "Submission Deadline": parsed_details["submission_deadline"],
                "Number of Recommendation Letters": parsed_details["recommendation_letters"],
                "Posted Date": posted_date,
                "Expiration Date": expiration_date,
                "CRA Link": link,
                "Additional Comments": parsed_details["additional_comments"],
                "Additional Links": "\n".join(additional_links),
                "CRA ID": title
            }

            existing_jobs[title] = job_info
            print(f"Scraped job: {title}")

        # Ensure all jobs have the same keys
        all_keys = set().union(*(d.keys() for d in existing_jobs.values()))
        for job in existing_jobs.values():
            for key in all_keys:
                if key not in job:
                    job[key] = "N/A"

        # Define the order of columns
        ordered_fieldnames = [
            "Company/University",
            "Department",
            "Position",
            "Hiring Areas",
            "Location",
            "Number of Positions",
            "Submission Deadline",
            "Number of Recommendation Letters",
            "Expiration Date",
            "CRA Link",
            "Crawl Time",
            "Posted Date",
            "Job Type",
            "Additional Links",
            "Additional Comments",
            "CRA ID"
        ]

        # Ensure all keys are included, even if not in our predefined order
        for key in all_keys:
            if key not in ordered_fieldnames:
                ordered_fieldnames.append(key)

       # Write results to CSV
        with open(args.csv, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=ordered_fieldnames)
            writer.writeheader()
            for job_info in existing_jobs.values():
                writer.writerow(job_info)

        print(
            f"Scraped {len(existing_jobs)} job listings. Results saved to cra_job_listings.csv")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
