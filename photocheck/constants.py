"""
Configuration constants for PhotoCheck.

Centralizes all magic numbers and configuration values used throughout the application.
"""

# Processing constants
DEFAULT_CHUNK_SIZE = 500  # Files per chunk for large directories
DEFAULT_THREADS = 8       # Default number of threads (kept for config compatibility)

# Timeout values (in seconds)
EXIFTOOL_TIMEOUT_FULL = 600      # Full directory processing
EXIFTOOL_TIMEOUT_CHUNK = 120     # Single chunk processing
EXIFTOOL_TIMEOUT_SINGLE = 30     # Single file processing
DATABASE_TIMEOUT = 30.0          # Database connection timeout

# Display constants
SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"     # Unicode spinner characters
SPINNER_DELAY = 0.1               # Seconds between spinner updates
MAX_FAILED_FILES_DISPLAY = 15    # Maximum failed files to show in output
MAX_ERRORS_DISPLAY = 10          # Maximum other errors to show in output

# File processing constants
MAX_SYMLINK_FILES = 500          # Maximum files to symlink in temp directory for chunking