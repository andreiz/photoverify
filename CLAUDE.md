# Claude Configuration for PhotoCheck

## Project Overview
PhotoCheck is a Python tool for verifying photo backups between SD cards and NAS storage.

## Development Commands

### Testing
```bash
python -m pytest tests/
```

### Code Quality
```bash
python -m flake8 .
python -m black .
```

### Running the Application
```bash
# Scan NAS
python photocheck.py scan /path/to/nas --db photos.db

# Verify SD card  
python photocheck.py verify /media/sdcard --db photos.db

# Clean up database
python photocheck.py cleanup --mark-missing --db photos.db

# Use config file
python photocheck.py --config config.yaml scan /nas/photos
```

## Project Structure
- `photocheck.py` - CLI interface
- `photocheck/` - Main module directory
  - `__init__.py` - Package initialization and exports
  - `photo_scanner.py` - NAS scanning and metadata extraction
  - `sd_verifier.py` - SD card verification logic
  - `database.py` - SQLite database operations
  - `config.py` - Configuration management
  - `models.py` - Data models and schemas
  - `cleanup.py` - Database maintenance operations

## Key Dependencies
- click: CLI framework
- Pillow: Image processing
- exifread: EXIF metadata extraction
- tqdm: Progress bars
- pyyaml: Configuration files