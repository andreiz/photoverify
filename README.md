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
python photocheck.py scan /path/to/nas/photos

# Verify SD card photos
python photocheck.py verify /media/sdcard

# Clean up missing files
python photocheck.py cleanup --mark-missing
```

## Usage

## Configuration

Copy `config.yaml.example` to `config.yaml` and customize:

```yaml
database:
  path: "~/photocheck.db"
scanning:
  threads: 8
  calculate_hash: false
verification:
  mode: "auto"
  threads: 8
```

## Usage

```bash
# Use default config file (config.yaml)
python photocheck.py scan /nas/photos

# Specify custom config file  
python photocheck.py --config /path/to/config.yaml scan /nas/photos

# Override database location
python photocheck.py --db /tmp/photos.db scan /nas/photos
```

See `python photocheck.py --help` for detailed usage instructions.