from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="crajobharvester",
    version="0.1.0",
    author="Zhuo Zhang",
    author_email="zhan3299@purdue.edu",
    description="A tool to scrape and analyze job listings from the CRA website",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/ZhangZhuoSJTU/CRAJobHarvester",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.7',
    install_requires=[
        "beautifulsoup4>=4.12.3",
        "jsonschema>=4.23.0",
        "openai>=1.42.0",
        "requests>=2.32.3",
        "selenium>=4.23.1",
    ],
    entry_points={
        'console_scripts': [
            'crajobharvester=crajobharvester.main:main',
        ],
    },
)
