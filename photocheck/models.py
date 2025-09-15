from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from pathlib import Path


@dataclass
class PhotoMetadata:
    filename: str
    file_path: str
    file_size: int
    file_hash: Optional[str] = None
    capture_datetime: Optional[datetime] = None
    width: Optional[int] = None
    height: Optional[int] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    created_at: datetime = None
    last_verified: Optional[datetime] = None
    file_exists: bool = True

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        
        if isinstance(self.file_path, str):
            self.file_path = str(Path(self.file_path).resolve())


@dataclass
class VerificationResult:
    sd_photo_path: str
    found_in_nas: bool
    nas_photo_path: Optional[str] = None
    match_type: Optional[str] = None  # 'hash', 'metadata', 'filename'
    confidence: float = 0.0
    warnings: List[str] = None  # Warnings about verification quality

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []
    
    
@dataclass 
class ScanStats:
    total_files: int = 0
    processed_files: int = 0
    photos_found: int = 0
    duplicates_found: int = 0
    errors: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    interrupted: bool = False
    
    @property
    def duration(self) -> Optional[float]:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None