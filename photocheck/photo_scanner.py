import hashlib
import os
import queue
import threading
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
        
        print(f"Discovering photos in {directory}...")
        
        # Use streaming processing with producer-consumer pattern
        photo_queue = queue.Queue(maxsize=1000)  # Buffer for discovered photos
        discovery_done = threading.Event()
        
        # Start discovery thread
        discovery_thread = threading.Thread(
            target=self._discover_photos_thread,
            args=(directory, photo_queue, discovery_done)
        )
        discovery_thread.start()
        
        # Process photos as they are discovered
        with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
            batch = []
            
            # Simple status tracking without progress bars
            photos_processed = 0
            last_current_dir = ""
            
            try:
                while True:
                    try:
                        # Get photo path from queue (timeout to check if discovery is done)
                        photo_path = photo_queue.get(timeout=0.1)
                        
                        # Submit for processing
                        future = executor.submit(self._extract_metadata, photo_path)
                        batch.append(future)
                        
                        # Process batch when full
                        if len(batch) >= batch_size:
                            processed_count = self._process_batch_simple(batch)
                            photos_processed += processed_count
                            batch = []
                            
                    except queue.Empty:
                        # Check if discovery is complete and queue is empty
                        if discovery_done.is_set() and photo_queue.empty():
                            break
                        continue
                
                # Process remaining batch
                if batch:
                    processed_count = self._process_batch_simple(batch)
                    photos_processed += processed_count
            except Exception as e:
                print(f"\nError during processing: {e}")
        
        # Wait for discovery thread to complete
        discovery_thread.join()
        
        self.stats.end_time = datetime.now()
        return self.stats

    def _discover_photos_thread(self, directory: Path, photo_queue: queue.Queue, discovery_done: threading.Event):
        """Discovery thread that finds photos and puts them in queue"""
        self.dirs_processed = 0
        photos_found = 0
        self.current_dir = ""
        
        try:
            for photo_path in self._find_photo_files_simple(directory):
                photo_queue.put(photo_path)
                photos_found += 1
                self.stats.total_files = photos_found  # Update running total
                
        except Exception as e:
            print(f"Error during discovery: {e}")
        finally:
            discovery_done.set()
            print(f"\nDiscovery complete: found {photos_found} photos in {self.dirs_processed} directories")

    def _find_photo_files_simple(self, directory: Path) -> Iterator[Path]:
        """Find photo files with simple status updates"""
        for root, dirs, files in os.walk(directory):
            # Remove excluded directories from dirs list to skip them
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            
            self.dirs_processed += 1
            root_path = Path(root)
            
            # Show current directory with up to 3 levels of context
            parts = []
            current = root_path
            base_path = Path(directory)
            
            # Build path components from current back to base (up to 3 levels)
            while current != base_path and len(parts) < 3:
                parts.append(current.name)
                current = current.parent
                if current == base_path:
                    break
            
            # Reverse to get correct order and join
            self.current_dir = "/".join(reversed(parts)) if parts else root_path.name
            
            # Print status every few directories (clear line to avoid remnants)
            if self.dirs_processed % 10 == 0 or self.dirs_processed == 1:
                photos_so_far = getattr(self.stats, 'total_files', 0)
                status_msg = f"\rScanning: {self.current_dir} | {self.dirs_processed} dirs | {photos_so_far} photos found"
                # Clear line by padding with spaces then carriage return
                print(f"{status_msg:<120}", end="\r", flush=True)
            
            for file in files:
                if Path(file).suffix.lower() in self.SUPPORTED_EXTENSIONS:
                    yield Path(root) / file

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

    def _process_batch_simple(self, futures: List) -> int:
        """Process batch without progress bar, return count of processed photos"""
        photos_to_insert = []
        processed_count = 0
        
        for future in as_completed(futures):
            try:
                metadata = future.result()
                if metadata:
                    photos_to_insert.append(metadata)
                    self.stats.photos_found += 1
                self.stats.processed_files += 1
                processed_count += 1
                
                # Print processing status occasionally  
                if processed_count % 50 == 0:
                    print(f"\rProcessing: {processed_count} photos processed | {self.stats.photos_found} added to database", end="", flush=True)
                    
            except Exception as e:
                print(f"\nError processing photo: {e}")
                self.stats.errors += 1
        
        if photos_to_insert:
            self.db.insert_photos_batch(photos_to_insert)
            
        return processed_count

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