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
# Scan NAS (full scan)
python photocheck.py scan /path/to/nas --db photos.db

# Update scan (faster - marks existing as verified, adds new)
python photocheck.py scan --update /path/to/nas --db photos.db

# Exclude specific directories
python photocheck.py scan --exclude iCloud-sync --exclude iPhone-sync /path/to/nas

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
- exiftool: External tool for EXIF metadata and image dimension extraction (supports all RAW formats)
- tqdm: Progress bars
- pyyaml: Configuration files

## Supported File Formats
PhotoCheck supports 45+ image formats including:

### Standard Formats
- JPEG (.jpg, .jpeg)
- PNG (.png) 
- TIFF (.tiff, .tif)
- BMP (.bmp)
- GIF (.gif)

### RAW Formats
- **Canon**: .cr2, .cr3, .crw
- **Nikon**: .nef
- **Sony**: .arw, .sr2, .srf
- **Fujifilm**: .raf
- **Olympus**: .orf
- **Panasonic**: .rw2
- **Pentax**: .pef, .ptx, .pxn
- **Leica**: .rwl
- **Hasselblad**: .3fr, .fff
- **Phase One**: .iiq, .cap, .eip
- **Adobe**: .dng
- And many more...

## Performance Features
- **Multi-threaded processing**: 8 threads by default for fast scanning
- **Efficient metadata extraction**: exiftool batch processing handles EXIF data and dimensions for all formats
- **Chunked processing**: Large directories automatically split into manageable chunks
- **Progress display**: Shows current folder being processed with running totals and timing
- **Database optimization**: Proper indexing and batch operations
- **Memory efficient**: Streaming processing prevents memory issues with large directories
- **Robust verification**: Requires filename + file size minimum for reliable photo matching