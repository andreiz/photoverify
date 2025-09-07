# PhotoCheck

A Python tool to verify that all photos from an SD card have been backed up to your NAS before erasing the card.

## Features

- Scan NAS directories and build SQLite database of photo metadata
- Verify SD card photos against the database
- Optional hash-based duplicate detection
- Parallel processing for fast scanning
- Flexible photo matching (filename, date, resolution, hash)
- Database cleanup and maintenance tools
- Configurable database location

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Scan your NAS and build database
python main.py scan /path/to/nas/photos

# Verify SD card photos
python main.py verify /media/sdcard

# Clean up missing files
python main.py cleanup --mark-missing
```

## Usage

See `python main.py --help` for detailed usage instructions.