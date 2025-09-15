import hashlib
import json
import os
import subprocess
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Dict

# Note: Using exiftool for metadata extraction via subprocess JSON calls

from .database import DatabaseManager
from .models import PhotoMetadata, ScanStats


class PhotoScanner:
    SUPPORTED_EXTENSIONS = {
        # Standard image formats
        '.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp', '.gif',
        # Generic RAW
        '.raw',
        # Canon RAW formats
        '.cr2', '.cr3', '.crw',
        # Nikon RAW formats  
        '.nef',
        # Sony RAW formats
        '.arw', '.sr2', '.srf',
        # Adobe Digital Negative
        '.dng',
        # Olympus RAW formats
        '.orf',
        # Panasonic RAW formats
        '.rw2',
        # Pentax RAW formats
        '.pef', '.ptx', '.pxn',
        # Samsung RAW formats
        '.srw',
        # Sigma RAW formats
        '.x3f',
        # Fujifilm RAW formats
        '.raf',
        # Leica RAW formats
        '.rwl',
        # Hasselblad RAW formats
        '.3fr', '.fff',
        # Phase One RAW formats
        '.iiq', '.cap', '.eip',
        # Kodak RAW formats
        '.dcs', '.dcr', '.drf', '.k25', '.kdc', '.kc2',
        # Minolta/Konica RAW formats
        '.mrw', '.mdc',
        # Mamiya RAW formats
        '.mef',
        # Leaf RAW formats
        '.mos',
        # Epson RAW formats
        '.erf',
        # GoPro RAW formats
        '.gpr',
        # Blackmagic RAW formats
        '.braw',
        # Red Digital Cinema RAW formats
        '.r3d',
        # Casio RAW formats
        '.bay'
    }

    def __init__(self, db_manager: DatabaseManager, calculate_hash: bool = False, extract_exif: bool = True, extract_dimensions: bool = True, num_threads: int = 8, exclude_dirs: List[str] = None, verbose: bool = False, debug: bool = False):
        self.db = db_manager
        self.calculate_hash = calculate_hash
        self.extract_exif = extract_exif
        self.extract_dimensions = extract_dimensions
        self.num_threads = num_threads
        self.exclude_dirs = exclude_dirs or []
        self.verbose = verbose
        self.debug = debug
        self.stats = ScanStats()
        self.errors = []  # Collect errors to show at end
        self.failed_files = []  # Files that failed metadata extraction
        self.progress_counter = 0  # Unified progress counter
        self.print_lock = threading.Lock()  # Thread-safe printing

    def scan_directory(self, directory: Path, batch_size: int = 100) -> ScanStats:
        """Scan directory and add new photos to database"""
        return self._process_directory(directory, mode='scan', batch_size=batch_size)

    def update_existing_photos(self, directory: Path, batch_size: int = 100) -> int:
        """Mark existing photos as verified and add any new ones using threading"""
        stats = self._process_directory(directory, mode='update', batch_size=batch_size)
        return stats.processed_files

    def _process_directory(self, directory: Path, mode: str = 'scan', batch_size: int = 100) -> ScanStats:
        """Unified directory processing for both scan and update modes"""
        self.stats = ScanStats(start_time=datetime.now())
        directory = Path(directory).resolve()
        
        if mode == 'scan':
            print(f"Scanning photos in {directory}...")
        else:  # update mode
            print(f"Updating existing photos in {directory}...")
        
        # Initialize batch metadata storage
        self._batch_metadata = []

        try:
            # Walk the directory tree - processing is now handled in _find_photo_files_simple
            file_count = 0
            for photo_path in self._find_photo_files_simple(directory):
                file_count += 1
                # Note: Both scan and update modes are now handled by _find_photo_files_simple
                # which calls _process_directory_with_exiftool and populates _batch_metadata

            # Handle database operations based on mode
            if hasattr(self, '_batch_metadata') and self._batch_metadata:
                if mode == 'scan':
                    # Scan mode: insert all metadata
                    if self.verbose:
                        print(f"💾 Inserting {len(self._batch_metadata)} photos into database...")
                    self.db.insert_photos_batch(self._batch_metadata)
                    self.stats.photos_found = len(self._batch_metadata)
                    if self.verbose:
                        print(f"✅ Database updated with {len(self._batch_metadata)} new photos")
                else:  # update mode
                    # Update mode: separate new from existing files
                    new_photos = []
                    existing_count = 0

                    for metadata in self._batch_metadata:
                        # Check if this photo already exists in database (by filename and file_size)
                        existing = self.db.find_by_metadata(
                            metadata.filename,
                            None,  # Don't match on datetime (format issues)
                            metadata.file_size
                        )
                        if self.debug:
                            print(f"Debug: Checking {metadata.filename} (size: {metadata.file_size}), found {len(existing)} existing records")
                            if not existing:
                                # Show what IS in the database for this filename
                                all_with_name = self.db.find_by_metadata(metadata.filename, None, None)
                                print(f"Debug: Files with same name in DB: {[(f.filename, f.file_size) for f in all_with_name]}")

                        if existing:  # existing is a list, check if not empty
                            # Mark existing file as verified
                            self.db.mark_file_exists(metadata.file_path)
                            existing_count += 1
                            if self.debug:
                                print(f"Debug: Marked {metadata.filename} as existing")
                        else:
                            # This is a new photo
                            new_photos.append(metadata)
                            if self.debug:
                                print(f"Debug: Added {metadata.filename} as new photo")

                    # Insert only new photos
                    if new_photos:
                        if self.verbose:
                            print(f"💾 Inserting {len(new_photos)} new photos into database...")
                        self.db.insert_photos_batch(new_photos)
                        if self.verbose:
                            print(f"✅ Database updated with {len(new_photos)} new photos")

                    self.stats.photos_found = len(new_photos)
                    if self.verbose and existing_count > 0:
                        print(f"✅ Verified {existing_count} existing photos")

                # Stats are already updated during processing - don't overwrite

        except KeyboardInterrupt:
            operation = "Scan" if mode == 'scan' else "Update"
            print(f"\n\n⚠️  {operation} interrupted by user")
            self.stats.end_time = datetime.now()
            self.stats.interrupted = True
            return self.stats
        except Exception as e:
            print(f"\nError during processing: {e}")
        
        self.stats.end_time = datetime.now()
        return self.stats


    def _find_photo_files_simple(self, directory: Path) -> Iterator[Path]:
        """Find photo files by walking directory tree and process each directory with exiftool"""
        for root, dirs, files in os.walk(directory):
            # Remove excluded directories from dirs list AND sort alphabetically
            dirs[:] = sorted([d for d in dirs if d not in self.exclude_dirs])

            # Check if this directory has any photo files
            photo_files = []
            for file in files:
                if Path(file).suffix.lower() in self.SUPPORTED_EXTENSIONS:
                    photo_files.append(Path(root) / file)

            # If we found photos in this directory, process them with exiftool
            if photo_files:
                metadata_list = self._process_directory_with_exiftool(Path(root), photo_files)
                for metadata in metadata_list:
                    if metadata:  # Only yield successful metadata extractions
                        yield Path(metadata.file_path)



    def _process_update_file_simple(self, photo_path: Path):
        """Process a single file for update mode (simplified)"""
        try:
            if photo_path.exists():
                # Mark file as existing
                self.db.mark_file_exists(str(photo_path))

                # Check if this is a new photo (not in database)
                existing = self.db.find_by_metadata(photo_path.name)
                if not existing:
                    # This is a new photo - it would have been processed by exiftool batch
                    # and added to _batch_metadata, so just update stats
                    self.stats.photos_found += 1

                self.stats.processed_files += 1

        except Exception as e:
            self.errors.append(f"Error updating {photo_path}: {e}")



    def _format_elapsed_time(self, seconds: float) -> str:
        """Format elapsed time with consistent padding"""
        total_seconds = int(seconds)
        minutes = total_seconds // 60
        secs = total_seconds % 60
        
        if minutes > 0:
            return f"{minutes}m {secs:2d}s"
        else:
            return f"   {secs:2d}s"

    def _print_progress_status(self, directory: Optional[str] = None, operation: str = "Processing", count: Optional[int] = None):
        """Unified progress display for both scan and update operations"""
        elapsed = (datetime.now() - self.stats.start_time).total_seconds()
        time_str = self._format_elapsed_time(elapsed)
        
        # Use provided directory or fall back to current_dir or default
        if directory:
            # Show just the last directory name for cleaner display
            dir_path = Path(directory)
            current_dir = dir_path.name if dir_path.name else "photos"
        else:
            current_dir = getattr(self, 'current_dir', 'photos')
        
        # Use provided count or fall back to photos_found (successful DB insertions)
        display_count = count if count is not None else self.stats.photos_found
        
        # Pad photo count to 5 digits and align the opening parenthesis
        count_str = f"{display_count:5d}"
        folder_part = f"{operation} {current_dir}"
        
        # Use consistent padding to align "(" at column 60
        padding = max(1, 60 - len(folder_part))
        status_line = f"{folder_part}{' ' * padding}({count_str} photos, {time_str})"
        print(status_line)

    def _print_processing_status(self, directory: Optional[str] = None):
        """Backward compatibility wrapper for processing status"""
        self._print_progress_status(directory, "Processing")

    def _extract_metadata(self, photo_path: Path) -> Optional[PhotoMetadata]:
        """Extract metadata from single photo file (fallback for non-batch processing)"""
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

            # Note: EXIF extraction now handled by batch processing in _process_directory_with_exiftool
            # This method is mainly for fallback scenarios

            return metadata

        except Exception as e:
            # Collect error instead of printing immediately
            self.errors.append(f"{photo_path}: {e}")
            self.failed_files.append(str(photo_path))
            return None

    def _calculate_file_hash(self, file_path: Path) -> str:
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Use 64KB chunks for better I/O performance
            for chunk in iter(lambda: f.read(65536), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()

    def _process_directory_with_exiftool(self, directory: Path, photo_files: List[Path], chunk_size: int = 500) -> List[Optional[PhotoMetadata]]:
        """Process all photos in a directory using exiftool batch JSON processing"""
        # Show directory name with up to 2 parent directories for context
        dir_parts = directory.parts
        if len(dir_parts) >= 3:
            dir_display = "/".join(dir_parts[-3:])
        elif len(dir_parts) >= 2:
            dir_display = "/".join(dir_parts[-2:])
        else:
            dir_display = directory.name or "photos"

        file_count = len(photo_files)

        # For large directories, process in chunks to avoid timeouts
        if file_count > chunk_size:
            return self._process_directory_chunked(directory, photo_files, chunk_size)

        # Unicode spinner characters
        spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

        # Start spinner in separate thread
        spinner_active = threading.Event()
        spinner_active.set()

        def show_spinner():
            i = 0
            while spinner_active.is_set():
                print(f"\rProcessing {file_count} files in {dir_display}... {spinner_chars[i % len(spinner_chars)]}", end="", flush=True)
                time.sleep(0.1)
                i += 1

        spinner_thread = threading.Thread(target=show_spinner, daemon=True)
        spinner_thread.start()

        try:
            # Build exiftool command for this directory
            cmd_start = time.time()
            cmd = [
                "exiftool", "-json",
                "-DateTimeOriginal", "-ImageWidth", "-ImageHeight",
                "-Make", "-Model", "-FileSize",
                "-ext", "RAF", "-ext", "JPG", "-ext", "JPEG",
                "-ext", "PNG", "-ext", "TIFF", "-ext", "TIF",
                "-ext", "CR2", "-ext", "CR3", "-ext", "NEF", "-ext", "ARW",
                "-ext", "DNG",
                str(directory)
            ]

            # Execute exiftool and capture JSON output
            exec_start = time.time()
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            exec_end = time.time()

            # For exiftool, return code 1 often means "completed with warnings", not total failure
            # Only treat it as an error if return code > 1 or if no stdout was generated
            if result.returncode > 1 or not result.stdout.strip():
                # Stop spinner and show error
                spinner_active.clear()
                spinner_thread.join()
                error_msg = result.stderr.strip() if result.stderr else f"return code {result.returncode}"
                print(f"\rProcessing {file_count} files in {dir_display}... (error: {error_msg})")
                return [None] * len(photo_files)

            # Parse JSON output
            parse_start = time.time()
            try:
                exif_data = json.loads(result.stdout)
            except json.JSONDecodeError:
                spinner_active.clear()
                spinner_thread.join()
                print(f"\rProcessing {file_count} files in {dir_display}... (error: invalid JSON)")
                return [None] * len(photo_files)
            parse_end = time.time()

            # Convert exiftool data to PhotoMetadata objects
            convert_start = time.time()
            metadata_dict = {}
            for item in exif_data:
                if "SourceFile" in item:
                    file_path = Path(item["SourceFile"])
                    metadata = self._convert_exiftool_to_metadata(file_path, item)
                    if metadata:
                        metadata_dict[str(file_path)] = metadata

            # Match metadata to original photo_files list
            result_list = []
            successful = 0
            failed = 0

            for photo_file in photo_files:
                if str(photo_file) in metadata_dict:
                    result_list.append(metadata_dict[str(photo_file)])
                    successful += 1
                else:
                    result_list.append(None)
                    failed += 1
                    # Track failed files for reporting
                    self.failed_files.append(str(photo_file))
            convert_end = time.time()

            # Stop spinner and show completion
            spinner_active.clear()
            spinner_thread.join()

            # Calculate timing breakdown (only show if debug enabled)
            if self.debug:
                total_time = convert_end - cmd_start
                exec_time = exec_end - exec_start
                parse_time = parse_end - parse_start
                convert_time = convert_end - convert_start
                timing_details = f" (exec: {exec_time:.1f}s, parse: {parse_time:.1f}s, convert: {convert_time:.1f}s, total: {total_time:.1f}s)"
            else:
                timing_details = ""

            if failed > 0:
                print(f"\rProcessing {file_count} files in {dir_display}... ({successful} successful, {failed} failed){timing_details}")
            else:
                print(f"\rProcessing {file_count} files in {dir_display}... ({successful} successful, {failed} failed){timing_details}")

            # Update stats
            self.stats.errors += failed
            self.stats.processed_files += successful + failed

            # Store metadata for batch database insertion
            self._batch_metadata = getattr(self, '_batch_metadata', []) + [m for m in result_list if m]

            return result_list

        except subprocess.TimeoutExpired:
            spinner_active.clear()
            spinner_thread.join()
            print(f"\rProcessing {file_count} files in {dir_display}... (error: timeout)")
            return [None] * len(photo_files)
        except Exception as e:
            spinner_active.clear()
            spinner_thread.join()
            print(f"\rProcessing {file_count} files in {dir_display}... (error: {e})")
            return [None] * len(photo_files)

    def _convert_exiftool_to_metadata(self, file_path: Path, exif_item: dict) -> Optional[PhotoMetadata]:
        """Convert exiftool JSON item to PhotoMetadata object"""
        try:
            stat = file_path.stat()

            metadata = PhotoMetadata(
                filename=file_path.name,
                file_path=str(file_path),
                file_size=stat.st_size,
                last_verified=datetime.now()
            )

            # Extract datetime
            if "DateTimeOriginal" in exif_item:
                try:
                    dt_str = exif_item["DateTimeOriginal"]
                    metadata.capture_datetime = datetime.strptime(dt_str, '%Y:%m:%d %H:%M:%S')
                except (ValueError, TypeError):
                    pass

            # Extract camera info
            if "Make" in exif_item:
                metadata.camera_make = str(exif_item["Make"]).strip()
            if "Model" in exif_item:
                metadata.camera_model = str(exif_item["Model"]).strip()

            # Extract dimensions
            if "ImageWidth" in exif_item and "ImageHeight" in exif_item:
                try:
                    metadata.width = int(exif_item["ImageWidth"])
                    metadata.height = int(exif_item["ImageHeight"])
                except (ValueError, TypeError):
                    pass

            # Calculate hash if requested
            if self.calculate_hash:
                metadata.file_hash = self._calculate_file_hash(file_path)

            return metadata

        except Exception as e:
            return None


    def _process_update_file(self, photo_path: Path) -> dict:
        """Process a single file for update mode"""
        result = {
            'updated': 0,
            'new_photo': None,
            'file_path': str(photo_path),
            'directory': str(photo_path.parent)
        }
        
        try:
            if photo_path.exists():
                result['updated'] = 1
                
                # Check if this is a new photo (not in database)
                existing = self.db.find_by_metadata(photo_path.name)
                if not existing:
                    metadata = self._extract_metadata(photo_path)
                    if metadata:
                        result['new_photo'] = metadata
                
        except Exception as e:
            self.errors.append(f"Error updating {photo_path}: {e}")
            
        return result

    def _process_update_batch(self, futures: List):
        """Process batch of update results with progress tracking and batch DB operations"""
        files_to_mark = []  # For batch marking files as existing
        photos_to_insert = []  # For batch inserting new photos
        last_directory = None
        
        for future in as_completed(futures):
            try:
                result = future.result()
                if result['updated']:
                    files_to_mark.append(result['file_path'])
                    last_directory = result['directory']
                    
                    if result['new_photo']:
                        photos_to_insert.append(result['new_photo'])
                    
                    # Update stats in thread-safe manner
                    with self.print_lock:
                        if result['new_photo']:
                            self.stats.photos_found += 1
                        self.stats.processed_files += 1
                        
            except Exception as e:
                self.errors.append(f"Error processing update batch: {e}")
        
        # Batch database operations
        if files_to_mark:
            self._batch_mark_files_exist(files_to_mark)
            
        if photos_to_insert:
            self.db.insert_photos_batch(photos_to_insert)
            
        # Progress is now printed per folder in _find_photo_files_simple

    def _batch_mark_files_exist(self, file_paths: List[str]):
        """Batch mark multiple files as existing"""
        with self.db.get_connection() as conn:
            conn.executemany('''
                UPDATE photos SET file_exists = 1, last_verified = CURRENT_TIMESTAMP
                WHERE file_path = ?
            ''', [(path,) for path in file_paths])

    def _process_directory_chunked(self, directory: Path, photo_files: List[Path], chunk_size: int) -> List[Optional[PhotoMetadata]]:
        """Process large directory in chunks to avoid timeouts"""
        # Show directory name with up to 2 parent directories for context
        dir_parts = directory.parts
        if len(dir_parts) >= 3:
            dir_display = "/".join(dir_parts[-3:])
        elif len(dir_parts) >= 2:
            dir_display = "/".join(dir_parts[-2:])
        else:
            dir_display = directory.name or "photos"

        file_count = len(photo_files)

        print(f"Processing {file_count} files in {dir_display} (in chunks of {chunk_size})...")

        all_results = []
        total_successful = 0
        total_failed = 0

        # Process in chunks
        for i in range(0, file_count, chunk_size):
            chunk = photo_files[i:i + chunk_size]
            chunk_num = (i // chunk_size) + 1
            total_chunks = (file_count + chunk_size - 1) // chunk_size

            print(f"  Chunk {chunk_num}/{total_chunks}: processing {len(chunk)} files...", end=" ", flush=True)

            try:
                # Create temporary directory with only the files in this chunk
                import tempfile
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)

                    # Create symlinks to avoid copying large files
                    for photo_file in chunk:
                        link_path = temp_path / photo_file.name
                        link_path.symlink_to(photo_file)

                    # Process this chunk
                    chunk_results = self._process_single_chunk(temp_path, chunk)
                    all_results.extend(chunk_results)

                    # Count results
                    chunk_successful = sum(1 for r in chunk_results if r is not None)
                    chunk_failed = len(chunk_results) - chunk_successful
                    total_successful += chunk_successful
                    total_failed += chunk_failed

                    print(f"({chunk_successful} successful, {chunk_failed} failed)")

            except Exception as e:
                print(f"(error: {e})")
                all_results.extend([None] * len(chunk))
                total_failed += len(chunk)

        print(f"Completed {dir_display}: {total_successful} successful, {total_failed} failed")

        # Update stats with failed count and processed files
        self.stats.errors += total_failed
        self.stats.processed_files += total_successful + total_failed

        return all_results

    def _process_single_chunk(self, temp_directory: Path, original_files: List[Path]) -> List[Optional[PhotoMetadata]]:
        """Process a single chunk of files"""
        try:
            # Build exiftool command for temp directory
            cmd = [
                "exiftool", "-json",
                "-DateTimeOriginal", "-ImageWidth", "-ImageHeight",
                "-Make", "-Model", "-FileSize",
                "-ext", "RAF", "-ext", "JPG", "-ext", "JPEG",
                "-ext", "PNG", "-ext", "TIFF", "-ext", "TIF",
                "-ext", "CR2", "-ext", "CR3", "-ext", "NEF", "-ext", "ARW",
                "-ext", "DNG",
                str(temp_directory)
            ]

            # Execute exiftool
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            # For exiftool, return code 1 often means "completed with warnings", not total failure
            if result.returncode > 1 or not result.stdout.strip():
                # Debug: print error details for failed chunks
                if self.verbose:
                    error_msg = result.stderr.strip() if result.stderr else f"return code {result.returncode}"
                    print(f"\n    Debug: exiftool failed - {error_msg}")
                return [None] * len(original_files)

            # Parse JSON output
            try:
                exif_data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                if self.verbose:
                    print(f"\n    Debug: JSON parsing failed - {e}")
                    print(f"    Stdout length: {len(result.stdout)}")
                    print(f"    First 200 chars: {result.stdout[:200]}")
                return [None] * len(original_files)

            # Create mapping from filename to metadata
            metadata_by_name = {}
            for item in exif_data:
                if "SourceFile" in item:
                    temp_path = Path(item["SourceFile"])
                    filename = temp_path.name

                    # Find the original file path
                    original_file = None
                    for orig in original_files:
                        if orig.name == filename:
                            original_file = orig
                            break

                    if original_file:
                        metadata = self._convert_exiftool_to_metadata(original_file, item)
                        if metadata:
                            metadata_by_name[filename] = metadata

            # Create result list matching original_files order
            result_list = []
            for original_file in original_files:
                if original_file.name in metadata_by_name:
                    result_list.append(metadata_by_name[original_file.name])
                else:
                    # Track failed files
                    self.failed_files.append(str(original_file))
                    result_list.append(None)

            # Store metadata for batch database insertion
            successful_metadata = [m for m in result_list if m]
            self._batch_metadata = getattr(self, '_batch_metadata', []) + successful_metadata

            return result_list

        except Exception as e:
            return [None] * len(original_files)

    def extract_metadata_single_file(self, file_path: Path) -> Optional[PhotoMetadata]:
        """Extract metadata from a single file using exiftool (for verification)"""
        try:
            # Use exiftool for single file (more reliable for RAF files)
            cmd = [
                "exiftool", "-json",
                "-DateTimeOriginal", "-ImageWidth", "-ImageHeight",
                "-Make", "-Model", "-FileSize",
                str(file_path)
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            # For exiftool, return code 1 often means "completed with warnings"
            if result.returncode > 1 or not result.stdout.strip():
                return None

            # Parse JSON output
            try:
                exif_data = json.loads(result.stdout)
                if exif_data and len(exif_data) > 0:
                    return self._convert_exiftool_to_metadata(file_path, exif_data[0])
            except json.JSONDecodeError:
                return None

            return None

        except Exception as e:
            return None