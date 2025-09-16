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
- **Clean display format**: Visual chunk indicators (✅/🟡) and tree-style summary output

## Verification Features
- **Four-tier matching strategy**: Hash → Full metadata → Filename+size → Fuzzy pattern
- **Fuzzy pattern matching**: Handles renamed files (e.g., `DSCF3801.RAF` → `AZ_20190715_095220_3801.RAF`)
- **Detailed failure analysis**: Shows exactly what matching criteria failed and why
- **Enhanced reporting**: Summary → Missing files → Successful matches with full paths
- **Intelligent file detection**: Automatically skips AppleDouble system files (._filename)

## Current Matching Criteria (may need updating)
Verification uses these criteria in sequence until a match is found:

1. **Hash Match**: Exact file hash (if available)
2. **Full Metadata Match**: filename + capture_datetime + file_size (all must match exactly)
3. **Filename + Size Match**: filename + file_size (ignores datetime)
4. **Fuzzy Pattern Match**: Renamed file detection using:
   - Pattern: `PREFIX_YYYYMMDD_HHMMSS_NNNN.EXT` or `PREFIX_YYYYMMDD_NNNNN.EXT`
   - Requires: capture_datetime + file_size + extracted number from filename
   - Example: `DSCF3801.JPG` → `AZ_20190715_095220_3801.JPG`

**Note**: All methods currently require exact file_size matching, which may cause issues with recompressed JPEGs that have slight size differences but are the same photo.

## Update Mode Features
- **New vs existing tracking**: Shows count of new files added vs existing files verified
- **Streaming processing**: Processes directories immediately without batching
- **Progress indication**: Real-time file processing with timing per directory/chunk

Don't scan SD cards.
You don't need to comment on commit messages and remaining files.