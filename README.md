# CRAJobHarvester

CRAJobHarvester is a Python-based tool designed to scrape and analyze job listings from the Computing Research Association (CRA) website. It utilizes web scraping and OpenAI's language models to extract and structure job information.

## Features

- Scrapes job listings from the CRA website
- Uses OpenAI's GPT models to parse and structure job details
- Saves results in a CSV file
- Avoids duplicate entries

## Prerequisites

- Python 3.7 or higher
- Chrome browser
- ChromeDriver

## Installation

1. Clone this repository:
   ```
   git clone https://github.com/yourusername/CRAJobHarvester.git
   cd CRAJobHarvester
   ```

2. Install the required Python packages:
   ```
   pip install -r requirements.txt
   ```

3. Download ChromeDriver:
   - Visit the [ChromeDriver downloads page](https://googlechromelabs.github.io/chrome-for-testing/)
   - Download the version that matches your Chrome browser version
   - Extract the executable and note its path

## Usage

Run the script with the following command:

```
python cra_job_crawler.py --csv output.csv --api_key your_openai_api_key --chromedriver /path/to/chromedriver
```

### Command-line Arguments

- `--csv`: Path to the CSV file for output and duplicate checking (default: cra_job_listings.csv)
- `--api_key`: Your OpenAI API key
- `--model`: OpenAI model to use (choices: gpt-3.5-turbo, gpt-4; default: gpt-3.5-turbo)
- `--chromedriver`: Path to your ChromeDriver executable (required)
- `--additional_links`: Number of additional links to process per job listing (default: 0)
- `--max_attempts`: Maximum number of attempts for parsing job details (default: 3)

## Note on OpenAI Models

This tool has been primarily tested with the GPT-3.5-turbo model.

## Contributing

Contributions to CRAJobHarvester are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This tool is for educational and research purposes only. Please respect the CRA website's terms of service and use this tool responsibly.
