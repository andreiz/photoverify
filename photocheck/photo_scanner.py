import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

import exifread
from PIL import Image
from tqdm import tqdm

from .database import DatabaseManager
from .models import PhotoMetadata, ScanStats


class PhotoScanner:
    SUPPORTED_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp', '.gif',
        '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', 
        '.rw2', '.pef', '.srw', '.x3f'
    }

    def __init__(self, db_manager: DatabaseManager, calculate_hash: bool = False, num_threads: int = 8, exclude_dirs: List[str] = None):
        self.db = db_manager
        self.calculate_hash = calculate_hash
        self.num_threads = num_threads
        self.exclude_dirs = exclude_dirs or []
        self.stats = ScanStats()

    def scan_directory(self, directory: Path, batch_size: int = 100) -> ScanStats:
        self.stats = ScanStats(start_time=datetime.now())
        directory = Path(directory).resolve()
        
        photo_files = list(self._find_photo_files(directory))
        self.stats.total_files = len(photo_files)
        
        if not photo_files:
            self.stats.end_time = datetime.now()
            return self.stats

        print(f"Found {len(photo_files)} photo files. Starting scan...")
        
        with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
            with tqdm(total=len(photo_files), desc="Scanning photos") as pbar:
                batch = []
                
                for photo_path in photo_files:
                    future = executor.submit(self._extract_metadata, photo_path)
                    batch.append(future)
                    
                    if len(batch) >= batch_size:
                        self._process_batch(batch, pbar)
                        batch = []
                
                if batch:
                    self._process_batch(batch, pbar)

        self.stats.end_time = datetime.now()
        return self.stats

    def _find_photo_files(self, directory: Path) -> Iterator[Path]:
        for root, dirs, files in os.walk(directory):
            # Remove excluded directories from dirs list to skip them
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            
            for file in files:
                if Path(file).suffix.lower() in self.SUPPORTED_EXTENSIONS:
                    yield Path(root) / file

    def _process_batch(self, futures: List, pbar: tqdm):
        photos_to_insert = []
        
        for future in as_completed(futures):
            try:
                metadata = future.result()
                if metadata:
                    photos_to_insert.append(metadata)
                    self.stats.photos_found += 1
                self.stats.processed_files += 1
                pbar.update(1)
            except Exception as e:
                print(f"Error processing photo: {e}")
                self.stats.errors += 1
                pbar.update(1)
        
        if photos_to_insert:
            self.db.insert_photos_batch(photos_to_insert)

    def _extract_metadata(self, photo_path: Path) -> Optional[PhotoMetadata]:
        try:
            if not photo_path.exists():
                return None

            stat = photo_path.stat()
            
            metadata = PhotoMetadata(
                filename=photo_path.name,
                file_path=str(photo_path),
                file_size=stat.st_size,
                last_verified=datetime.now()
            )

            if self.calculate_hash:
                metadata.file_hash = self._calculate_file_hash(photo_path)

            self._extract_exif_data(photo_path, metadata)

            return metadata

        except Exception as e:
            print(f"Error processing {photo_path}: {e}")
            return None

    def _calculate_file_hash(self, file_path: Path) -> str:
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()

    def _extract_exif_data(self, photo_path: Path, metadata: PhotoMetadata):
        try:
            with open(photo_path, 'rb') as f:
                tags = exifread.process_file(f, stop_tag='DateTime')
                
                if 'DateTime' in tags:
                    try:
                        dt_str = str(tags['DateTime'])
                        metadata.capture_datetime = datetime.strptime(dt_str, '%Y:%m:%d %H:%M:%S')
                    except ValueError:
                        pass
                
                if 'Image Make' in tags:
                    metadata.camera_make = str(tags['Image Make']).strip()
                
                if 'Image Model' in tags:
                    metadata.camera_model = str(tags['Image Model']).strip()

            try:
                with Image.open(photo_path) as img:
                    metadata.width, metadata.height = img.size
            except Exception:
                pass

        except Exception as e:
            pass

    def update_existing_photos(self, directory: Path) -> int:
        """Mark existing photos as verified and add any new ones"""
        directory = Path(directory).resolve()
        
        self.db.mark_files_missing(str(directory))
        
        updated_count = 0
        for photo_path in self._find_photo_files(directory):
            if photo_path.exists():
                self.db.mark_file_exists(str(photo_path))
                updated_count += 1
                
                existing = self.db.find_by_metadata(photo_path.name)
                if not existing:
                    metadata = self._extract_metadata(photo_path)
                    if metadata:
                        self.db.insert_photo(metadata)
        
        return updated_count