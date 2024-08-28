# Changelog
All notable changes to this project will be documented in this file.

## [0.1.1] - 2024-08-27
### Added
- Colored console logging for improved readability
- Line numbers and component names in log messages for easier debugging
- README.md file with comprehensive project documentation

### Changed
- Enhanced logging system with different levels for console and file output
- Improved handling of additional links in job descriptions
- Updated Selenium usage to scroll through dynamically loaded content

### Fixed
- Prevented processing of "mailto:" links to avoid errors
- Suppressed excessive logging from dependencies (Selenium, OpenAI, BeautifulSoup, etc.)

## [0.1.0] - 2024-08-27
### Added
- Initial release of CRAJobHarvester
- Web scraping functionality for CRA job listings
- Integration with OpenAI for job detail parsing
- CSV output for scraped job data