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
    location = job.find('div', class_='location').text.replace(
        company, "").strip()
    job_type = job.find('li', class_='job-type').text.strip()

    title = f"{company} ({location}): {title}"
    if title in existing_jobs:
        logger.info(f"Skipping duplicate job: {title}")
        return None, None, None, None, None, None, None, None

    # Fetch the detailed job page
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
    expiration_date = meta.find_all(
        'li', class_='date-posted')[1].text.replace("Expires on:", "").strip()

    logger.info(f"Successfully extracted details for job: {title} ({company})")
    time.sleep(1)  # Be nice to the server

    return title, link, location, job_type, full_content, posted_date, expiration_date, additional_links


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


def parse_job_details(title, details, max_attempts, model, logger):
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

    3. Position(s) Hiring:
    Choose one or more appropriate options, separated by commas: Postdoc, Assistant Professor, Associate Professor, Full Professor, Lecturer

    4. Submission deadline:
    Format as YYYY-MM-DD. If not specified, write "Not specified"

    5. Hiring areas:
    List the main areas of hiring, prioritizing and selecting from the following options: Security, Software Engineering, Programming Languages, AI, Machine Learning, Data Science, Theory, Systems, Networks, Human-Computer Interaction, Graphics, Robotics. For areas not covered by these options, use "Others". If the areas are general or not specified, write "All areas"

    6. Number of Recommendation Letters or References Required:
    Provide the number only. If not specified, write "Not specified"

    7. Number of positions:
    Provide the number only. If not specified, write "Not specified"

    8. Additional important comments:
    Summarize any other crucial or noteworthy information relevant to the job listing

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
            logger.debug(
                f"Attempting to parse job details (attempt {attempt+1}/{max_attempts})")
            logger.debug(f"Prompt to {model}: {prompt}")
            response = query_openai(prompt, model)
            logger.debug(f"Response from {model}: {response}")
            parsed_json = json.loads(response)
            jsonschema.validate(instance=parsed_json, schema=schema)
            return parsed_json
        except (json.JSONDecodeError, jsonschema.exceptions.ValidationError) as e:
            if attempt == max_attempts - 1:
                logger.error(
                    f"All attempts ({max_attempts}) failed on {title}. Returning default values.")
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
    logger = setup_logging(log_level=getattr(logging, args.log_level))
    logger.info("Starting CRA Job Crawler")

    if args.api_key:
        os.environ["OPENAI_API_KEY"] = args.api_key

    existing_jobs = load_existing_jobs(args.csv)
    logger.info(f"Loaded {len(existing_jobs)} existing jobs from {args.csv}")

    driver = setup_driver(args.chromedriver)
    try:
        jobs = fetch_cra_jobs(driver, logger)
        if len(jobs) == 0:
            logger.error(
                "Crawling failed. No job listings found. Please try again later.")
            raise Exception("No job listings found")

        logger.info(f"Found {len(jobs)} job listings on CRA website")

        for job in jobs:
            crawl_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            title, link, location, job_type, full_content, posted_date, expiration_date, additional_links = extract_job_details(
                driver, existing_jobs, job, args.additional_links, logger)
            if title is None:
                continue

            parsed_details = parse_job_details(
                title, full_content, args.max_attempts, args.model, logger)

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
            logger.info(f"Scraped job: {title}")

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
        logger.info(f"Writing results to {args.csv}")
        with open(args.csv, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=ordered_fieldnames)
            writer.writeheader()
            for job_info in existing_jobs.values():
                writer.writerow(job_info)

        logger.info(
            f"Scraped {len(existing_jobs)} job listings. Results saved to {args.csv}")

    except Exception as e:
        logger.exception(f"An error occurred during execution: {e}")

    finally:
        driver.quit()
        logger.info("CRA Job Crawler finished execution")


if __name__ == "__main__":
    main()
