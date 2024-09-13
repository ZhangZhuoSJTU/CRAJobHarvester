import json
import jsonschema
import re
import time
import logging
import os
import csv
import argparse
from logging.handlers import RotatingFileHandler
from urllib.parse import urljoin
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup
from openai import OpenAI
from colorama import init, Fore, Back, Style

# Initialize colorama
init(autoreset=True)


class ColoredFormatter(logging.Formatter):
    """Custom formatter to add colors and component names to log messages"""

    COLORS = {
        'DEBUG': Fore.CYAN,
        'INFO': Fore.GREEN,
        'WARNING': Fore.YELLOW,
        'ERROR': Fore.RED,
        'CRITICAL': Fore.RED + Back.WHITE + Style.BRIGHT,
    }

    def format(self, record):
        log_message = super().format(record)
        component = record.name
        line_num = record.lineno
        colored_component = f"{Fore.MAGENTA}{component}{Style.RESET_ALL}"
        colored_line_num = f"{Fore.BLUE}:{line_num}{Style.RESET_ALL}"
        return f"{self.COLORS.get(record.levelname, '')}{log_message} [{colored_component}{colored_line_num}]{Style.RESET_ALL}"


def setup_logging(log_file='cra_job_crawler.log', log_level=logging.DEBUG):
    """Set up logging to file and console."""
    # Set up the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # File formatter (without colors)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s:%(lineno)d - %(levelname)s - %(message)s')

    # Console formatter (with colors, component names, and line numbers)
    console_formatter = ColoredFormatter(
        '%(asctime)s - %(levelname)s - %(message)s')

    # File handler (with rotation)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)

    # Add handlers to logger
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Suppress logs from dependencies
    for module in ['selenium', 'urllib3', 'requests', 'openai', 'bs4', 'httpx', 'httpcore']:
        logging.getLogger(module).setLevel(logging.WARNING)

    # Create and return a logger for this script
    logger = logging.getLogger('cra_job_crawler')
    return logger


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
                        default=3, help="Number of additional links to process")
    parser.add_argument("--max_attempts", type=int, default=3,
                        help="Maximum number of attempts for parsing job details")
    parser.add_argument("--log_level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="Set the logging level")
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


def fetch_page(driver, logger):
    """Fetch a page using Selenium with nested loops for scrolling and loading more listings."""
    while True:  # Outer loop for scrolling
        last_height = driver.execute_script("return document.body.scrollHeight")
        
        while True:  # Inner loop for clicking "Load more listings"
            # Scroll down to bottom
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)  # Wait for the page to load

            try:
                load_more_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, "load_more_jobs"))
                )
                driver.execute_script("arguments[0].click();", load_more_button)
                logger.info("Clicked 'Load more listings' button")
                time.sleep(2)  # Wait for new listings to load
            except (TimeoutException, NoSuchElementException):
                logger.info("No more 'Load more listings' button found. Moving to next scroll.")
                break  # Break the inner loop to move to the next scroll

        # Check if the page height has changed
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            logger.info("Reached the bottom of the page, no more content to load.")
            break  # Break the outer loop
        
        logger.info("Page height changed, continuing to scroll.")

    page_source = driver.page_source
    return BeautifulSoup(page_source, 'html.parser')


def fetch_cra_jobs(driver, logger):
    """Fetch job listings from CRA website using Selenium."""
    url = "https://cra.org/ads/"
    driver.get(url)

    # Wait for the job listings to load
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CLASS_NAME, "job_listings"))
    )
    soup = fetch_page(driver, logger)
    return soup.find_all('li', class_='job_listing')


def extract_job_details(driver, existing_jobs, job, num_additional_links, logger):
    """Extract basic details from a job listing and fetch full description."""
    title = job.find('h3').text.strip()
    link = job.find('a')['href']
    company = job.find('div', class_='location').find('strong').text.strip()
    location = job.find('div', class_='location').text.replace(company, "").strip()
    job_type = job.find('li', class_='job-type').text.strip()

    if link in existing_jobs:
        logger.info(f"Skipping duplicate job: {link}")
        return None

    # Fetch the detailed job page
    logger.info(f"Fetching page: {link}")
    driver.get(link)
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CLASS_NAME, "job_description"))
    )
    soup = fetch_page(driver, logger)

    # Extract full description
    job_description_div = soup.find('div', class_='job_description')
    full_description = job_description_div.text.strip()

    # Find and follow links in the job description
    additional_content = []
    additional_links = []
    links = job_description_div.find_all('a', href=True)
    for i, a in enumerate(links):
        href = a['href']
        logger.debug(f"Processing additional link {i+1}: {href}")
        if href.startswith('mailto:'):
            logger.info(f"Skipping mailto link: {href}")
            continue
        if not href.startswith('http'):
            href = urljoin(link, href)
        additional_links.append(href)

        # Limit to first n links to avoid overloading
        if len(additional_links) < num_additional_links:
            try:
                logger.info(f"Fetching additional content from: {href}")
                driver.get(href)
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                link_soup = fetch_page(driver, logger)
                link_text = clean_text(link_soup.get_text())
                # Truncate to first 10000 characters to avoid overwhelming ChatGPT
                additional_content.append(
                    f"    Additional content from {href}:\n    {link_text[:10000]}...")
                logger.debug(f"Successfully extracted content from {href}")
            except Exception as e:
                logger.error(f"Error fetching content from {href}: {e}")

    # Combine original description with additional content
    full_content = full_description + "\n\n" + "\n\n".join(additional_content)

    # Extract posted date and expiration date
    meta = soup.find('ul', class_='meta')
    posted_date = meta.find('li', class_='date-posted').text.strip()
    expiration_date = meta.find_all('li', class_='date-posted')[1].text.replace("Expires on:", "").strip()

    logger.info(f"Successfully extracted details for job: {title} ({company})")
    time.sleep(1)  # Be nice to the server

    return {
        "title": title,
        "link": link,
        "company": company,
        "location": location,
        "job_type": job_type,
        "full_content": full_content,
        "posted_date": posted_date,
        "expiration_date": expiration_date,
        "additional_links": additional_links
    }


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


def parse_job_details(job_info, max_attempts, model, logger):
    """Parse job details using OpenAI API with structured format and JSON output."""
    title = job_info['title']
    details = job_info['full_content']
    prompt = f"""
    Analyze the following job listing details. Extract the requested information following the format and instructions carefully, then return the result as a JSON object.

    Follow these instructions for each field:

    1. Institution: Extract the full name of the university or institution
    2. Department: Extract the full department name
    3. Location: Provide the city and state/country
    4. Positions: Number of positions available (if not specified, use 1)
    5. Field: List the main areas of hiring, prioritizing and selecting from the following options: Security, Software Engineering, Programming Languages, AI, Machine Learning, Data Science, Theory, Systems, Networks, Human-Computer Interaction, Graphics, Robotics. For areas not covered by these options, use "Others". If the areas are general or not specified, write "All Areas"
    6. Preference: List any preferred specializations or focus areas within the main fields
    7. Min. Rec. Letters: Minimum number of recommendation letters required (if not specified, use 3)
    8. Max. Rec. Letters: Maximum number of recommendation letters allowed (if not specified, use -)
    9. Review Starts: Date when application review begins (format as YYYY-MM-DD, if not specified, use "Opened")
    10. Deadline: Application deadline (format as YYYY-MM-DD, if not specified, use "Open until filled")
    11. Additional Material: List any required application materials besides cover letter, CV, research statements, teaching statements, diversity statements, and recommendation letters
    12. Notes: Any other important information not covered by the above fields

    Return a JSON object with the following structure:
    {{
        "institution": "Answer for item 1",
        "department": "Answer for item 2",
        "location": "Answer for item 3",
        "positions": "Answer for item 4",
        "field": "Answer for item 5",
        "preference": "Answer for item 6",
        "min_rec_letters": "Answer for item 7",
        "max_rec_letters": "Answer for item 8",
        "review_starts": "Answer for item 9",
        "deadline": "Answer for item 10",
        "additional_material": "Answer for item 11",
        "notes": "Answer for item 12"
    }}

    Ensure all fields are present in the JSON, even if the information is not available (use null or appropriate default values in such cases).

    ---

    Job title: {title}

    Job listing details:
    {details}
    """

    schema = {
        "type": "object",
        "properties": {
            "institution": {"type": "string"},
            "department": {"type": "string"},
            "location": {"type": "string"},
            "positions": {"type": ["string", "integer"]},
            "field": {"type": "string"},
            "preference": {"type": "string"},
            "min_rec_letters": {"type": ["string", "integer"]},
            "max_rec_letters": {"type": ["string", "integer"]},
            "review_starts": {"type": "string"},
            "deadline": {"type": "string"},
            "additional_material": {"type": "string"},
            "notes": {"type": "string"}
        },
        "required": ["institution", "department", "location", "positions", "field", "preference", "min_rec_letters", "max_rec_letters", "review_starts", "deadline", "additional_material", "notes"]
    }

    for attempt in range(max_attempts):
        try:
            logger.debug(f"Attempting to parse job details (attempt {attempt+1}/{max_attempts})")
            response = query_openai(prompt, model)
            logger.debug(f"Response from {model}: {response}")
            parsed_json = json.loads(response)
            jsonschema.validate(instance=parsed_json, schema=schema)
            return parsed_json
        except (json.JSONDecodeError, jsonschema.exceptions.ValidationError) as e:
            if attempt == max_attempts - 1:
                logger.error(f"All attempts ({max_attempts}) failed on {job_info['title']}. Returning default values.")
                return {
                    "institution": "Not specified",
                    "department": "Not specified",
                    "location": "Not specified",
                    "positions": 1,
                    "field": "Not specified",
                    "preference": "Not specified",
                    "min_rec_letters": 3,
                    "max_rec_letters": "-",
                    "review_starts": "Opened",
                    "deadline": "Open until filled",
                    "additional_material": "Not specified",
                    "notes": "Failed to parse job details."
                }



def load_existing_jobs(csv_path):
    existing_jobs = {}
    if os.path.exists(csv_path):
        with open(csv_path, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                existing_jobs[row.get('Link', '')] = row
    return existing_jobs

def write_job_to_csv(job_data, csv_path, fieldnames, logger):
    """Write a single job to the CSV file."""
    file_exists = os.path.isfile(csv_path)
    
    with open(csv_path, 'a', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()  # Write header if file doesn't exist
        
        writer.writerow(job_data)
    
    logger.info(f"Updated CSV with job: {job_data['Institution']} - {job_data['Department']}")

def main():
    args = setup_cli()
    logger = setup_logging(log_level=getattr(logging, args.log_level))
    logger.info("Starting CRA Job Crawler")

    if args.api_key:
        os.environ["OPENAI_API_KEY"] = args.api_key

    existing_jobs = load_existing_jobs(args.csv)
    logger.info(f"Loaded {len(existing_jobs)} existing jobs from {args.csv}")

    fieldnames = [
        "Institution", "Department", "Location", "Positions", "Field", "Preference",
        "Min. Rec. Letters", "Max. Rec. Letters", "Review Starts", "Deadline",
        "CS Rankings", "In List", "Link", "Additional Material", "Notes"
    ]

    driver = setup_driver(args.chromedriver)
    try:
        jobs = fetch_cra_jobs(driver, logger)
        if len(jobs) == 0:
            logger.error("Crawling failed. No job listings found. Please try again later.")
            raise Exception("No job listings found")

        logger.info(f"Found {len(jobs)} job listings on CRA website")

        new_jobs_count = 0
        for job in jobs:
            job_info = extract_job_details(driver, existing_jobs, job, args.additional_links, logger)
            if job_info is None:
                continue

            parsed_details = parse_job_details(job_info, args.max_attempts, args.model, logger)

            job_data = {
                "Institution": parsed_details["institution"],
                "Department": parsed_details["department"],
                "Location": parsed_details["location"],
                "Positions": parsed_details["positions"],
                "Field": parsed_details["field"],
                "Preference": parsed_details["preference"],
                "Min. Rec. Letters": parsed_details["min_rec_letters"],
                "Max. Rec. Letters": parsed_details["max_rec_letters"],
                "Review Starts": parsed_details["review_starts"],
                "Deadline": parsed_details["deadline"],
                "CS Rankings": "",
                "In List": "",
                "Link": job_info["link"],
                "Additional Material": parsed_details["additional_material"],
                "Notes": parsed_details["notes"]
            }

            # Write the job to CSV immediately after processing
            write_job_to_csv(job_data, args.csv, fieldnames, logger)
            new_jobs_count += 1

        logger.info(f"Scraped {new_jobs_count} new job listings. Results saved to {args.csv}")

    except Exception as e:
        logger.exception(f"An error occurred during execution: {e}")

    finally:
        driver.quit()
        logger.info("CRA Job Crawler finished execution")


if __name__ == "__main__":
    main()
