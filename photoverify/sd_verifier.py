import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from tqdm import tqdm

from .database import DatabaseManager
from .models import PhotoMetadata, VerificationResult, FailureDetail
from .photo_scanner import PhotoScanner


class SDCardVerifier:
    def __init__(self, db_manager: DatabaseManager, num_threads: int = 8):
        self.db = db_manager
        self.num_threads = num_threads

    def _detect_incomplete_metadata(self, nas_photo: PhotoMetadata, sd_photo: PhotoMetadata) -> List[str]:
        """Detect if NAS metadata is incomplete compared to SD card metadata"""
        warnings = []

        # Check for missing critical metadata in NAS that's present on SD
        if sd_photo.capture_datetime and not nas_photo.capture_datetime:
            warnings.append("NAS entry missing capture date/time")

        if sd_photo.width and sd_photo.height and (not nas_photo.width or not nas_photo.height):
            warnings.append("NAS entry missing image dimensions")

        if sd_photo.camera_make and not nas_photo.camera_make:
            warnings.append("NAS entry missing camera make/model")

        # Check if only basic file info is available (suggests corrupted metadata during scan)
        nas_has_only_basic = (nas_photo.file_size and
                              not nas_photo.capture_datetime and
                              not nas_photo.width and
                              not nas_photo.camera_make)

        if nas_has_only_basic and (sd_photo.capture_datetime or sd_photo.width or sd_photo.camera_make):
            warnings.append("NAS entry has incomplete metadata (possible file corruption during scan)")

        return warnings

    def _find_photo_files_for_verification(self, directory: Path, scanner: PhotoScanner) -> List[Path]:
        """Find photo files for verification (simple file discovery without processing)"""
        photo_files = []
        for root, dirs, files in os.walk(directory):
            for file in files:
                file_path = Path(root) / file
                if (Path(file).suffix.lower() in scanner.SUPPORTED_EXTENSIONS and
                    not scanner._should_skip_file(file_path)):
                    photo_files.append(file_path)
        return photo_files

    def verify_sd_card(self, sd_path: Path, use_hash: bool = True) -> List[VerificationResult]:
        sd_path = Path(sd_path).resolve()
        
        # Create scanner with appropriate hash setting
        scanner = PhotoScanner(self.db, calculate_hash=use_hash)
        photo_files = self._find_photo_files_for_verification(sd_path, scanner)
        
        if not photo_files:
            print("No photo files found on SD card")
            return []

        print(f"Verifying {len(photo_files)} photos from SD card...")
        
        results = []
        
        with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
            with tqdm(total=len(photo_files), desc="Verifying photos") as pbar:
                
                futures = {}
                for photo_path in photo_files:
                    future = executor.submit(self._verify_single_photo, photo_path, use_hash, scanner)
                    futures[future] = photo_path
                
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                        pbar.update(1)
                    except Exception as e:
                        photo_path = futures[future]
                        print(f"Error verifying {photo_path}: {e}")
                        results.append(VerificationResult(
                            sd_photo_path=str(photo_path),
                            found_in_nas=False,
                            match_type="error"
                        ))
                        pbar.update(1)
        
        return results

    def _verify_single_photo(self, sd_photo_path: Path, use_hash: bool, scanner: PhotoScanner) -> VerificationResult:
        try:
            sd_metadata = scanner.extract_metadata_single_file(sd_photo_path)
            if not sd_metadata:
                return VerificationResult(
                    sd_photo_path=str(sd_photo_path),
                    found_in_nas=False,
                    match_type="metadata_error"
                )

            failure_details = []

            # Tier 0: Hash matching (if available)
            if use_hash and sd_metadata.file_hash:
                nas_photo = self.db.find_by_hash(sd_metadata.file_hash)
                if nas_photo:
                    warnings = self._detect_incomplete_metadata(nas_photo, sd_metadata)
                    return VerificationResult(
                        sd_photo_path=str(sd_photo_path),
                        found_in_nas=True,
                        nas_photo_path=nas_photo.file_path,
                        match_type="hash_match",
                        warnings=warnings
                    )

            # Pass 1: Filename-based matching
            # Try direct filename match first
            filename_matches = self.db.find_by_metadata(sd_metadata.filename, None, None)

            # If no direct match, try fuzzy pattern matching for renamed files
            if not filename_matches and sd_metadata.capture_datetime:
                filename_matches = self.db.find_by_fuzzy_pattern(
                    sd_metadata.filename,
                    sd_metadata.capture_datetime,
                    None  # No file size constraint yet
                )

            # If we found filename matches, confirm with datetime + file_size
            if filename_matches:
                for candidate in filename_matches:
                    if (candidate.capture_datetime == sd_metadata.capture_datetime and
                        candidate.file_size == sd_metadata.file_size):
                        warnings = self._detect_incomplete_metadata(candidate, sd_metadata)
                        return VerificationResult(
                            sd_photo_path=str(sd_photo_path),
                            found_in_nas=True,
                            nas_photo_path=candidate.file_path,
                            match_type="full_match",
                            warnings=warnings
                        )
                    else:
                        # Record this as a failed match for reporting
                        failure_details.append(FailureDetail(
                            nas_photo_path=candidate.file_path,
                            filename_match=True,  # We found it by filename
                            datetime_match=candidate.capture_datetime == sd_metadata.capture_datetime,
                            size_match=candidate.file_size == sd_metadata.file_size
                        ))

            # Pass 2: Metadata-only matching (datetime + file_size, ignoring filename)
            if sd_metadata.capture_datetime and sd_metadata.file_size:
                datetime_size_matches = self.db.find_by_datetime_size(
                    sd_metadata.capture_datetime,
                    sd_metadata.file_size
                )
                if datetime_size_matches:
                    best_match = self._find_best_metadata_match(sd_metadata, datetime_size_matches)
                    if best_match:
                        warnings = self._detect_incomplete_metadata(best_match, sd_metadata)
                        return VerificationResult(
                            sd_photo_path=str(sd_photo_path),
                            found_in_nas=True,
                            nas_photo_path=best_match.file_path,
                            match_type="datetime_size_match",
                            warnings=warnings
                        )

            return VerificationResult(
                sd_photo_path=str(sd_photo_path),
                found_in_nas=False,
                match_type="not_found",
                failure_details=failure_details
            )

        except Exception as e:
            return VerificationResult(
                sd_photo_path=str(sd_photo_path),
                found_in_nas=False,
                match_type="error"
            )

    def _find_best_metadata_match(self, sd_metadata: PhotoMetadata,
                                 candidates: List[PhotoMetadata]) -> Optional[PhotoMetadata]:
        if not candidates:
            return None

        # Just return the first candidate since we now use structured matching
        # and candidates should already be filtered appropriately
        return candidates[0]


    def _relativize_sd_path(self, full_path: str, sd_base_path: str) -> str:
        """Convert full SD card path to relative path from specified folder"""
        if not sd_base_path:
            return full_path

        try:
            from pathlib import Path
            full = Path(full_path)
            base = Path(sd_base_path)
            return str(full.relative_to(base))
        except ValueError:
            # If path is not relative to base, return filename only
            return Path(full_path).name


    def generate_report(self, results: List[VerificationResult], sd_path: str = None) -> str:
        if not results:
            return "No verification results to report."

        found_count = sum(1 for r in results if r.found_in_nas)
        missing_count = len(results) - found_count

        # Get current timestamp
        from datetime import datetime
        import sys
        verification_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        command_line = " ".join(sys.argv)

        report_lines = [
            f"SD Card Verification Report",
            f"=" * 40,
            f"Verification Date: {verification_time}",
            f"Command: {command_line}",
            f"SD Card Path: {sd_path or 'Unknown'}",
            "",
            f"Total photos checked: {len(results)}",
            f"Found in DB: {found_count}",
            f"Missing from DB: {missing_count}",
            f"Success rate: {(found_count/len(results)*100):.1f}%",
            ""
        ]
        
        if missing_count > 0:
            report_lines.extend([
                f"Missing/Failed Files ({missing_count}):",
                "-" * 20
            ])

            # Get missing results and sort by path
            missing_results = [result for result in results if not result.found_in_nas]
            missing_results.sort(key=lambda r: r.sd_photo_path)

            # Group missing results by failure type
            no_matches = [r for r in missing_results if not r.failure_details]
            has_mismatches = [r for r in missing_results if r.failure_details]

            # Show files with no matches at all
            if no_matches:
                report_lines.append(f"  No matches at all ({len(no_matches)}):")
                report_lines.append("  " + "-" * 25)
                for result in no_matches:
                    report_lines.append(f"  {result.sd_photo_path}")
                report_lines.append("")

            # Show files with datetime or size mismatches
            if has_mismatches:
                report_lines.append(f"  Datetime or size mismatch ({len(has_mismatches)}):")
                report_lines.append("  " + "-" * 30)
                for result in has_mismatches:
                    report_lines.append(f"  {result.sd_photo_path}")

                    # Show failure details
                    for detail in result.failure_details:
                        report_lines.append(f"  └─ {detail.nas_photo_path}")

                        # Format the yes/no status
                        filename_status = "yes" if detail.filename_match else "no"
                        datetime_status = "yes" if detail.datetime_match else "no"
                        size_status = "yes" if detail.size_match else "no"

                        report_lines.append(f"     Filename: {filename_status}, datetime: {datetime_status}, size: {size_status}")

                    report_lines.append("")  # Empty line between entries

            report_lines.append("")
        

        # Add successful matches sections by match type
        if found_count > 0:
            successful_results = [result for result in results if result.found_in_nas]
            successful_results.sort(key=lambda r: r.sd_photo_path)

            # Group results by match type
            hash_matches = [r for r in successful_results if r.match_type == "hash_match"]
            full_matches = [r for r in successful_results if r.match_type == "full_match"]
            datetime_size_matches = [r for r in successful_results if r.match_type == "datetime_size_match"]

            # Hash Matched section
            if hash_matches:
                report_lines.extend([
                    f"Hash Matched ({len(hash_matches)}):",
                    "-" * 20
                ])
                for result in hash_matches:
                    relative_path = self._relativize_sd_path(result.sd_photo_path, sd_path)
                    report_lines.append(f"  {relative_path}")
                    report_lines.append(f"  └─ {result.nas_photo_path}")

                    match_info = f"     Method: {result.match_type}"
                    if result.warnings:
                        warnings_text = ", ".join(result.warnings)
                        match_info += f", Warning: {warnings_text}"
                    report_lines.append(match_info)
                    report_lines.append("")
                report_lines.append("")

            # Fully Matched section (filename + datetime + size)
            if full_matches:
                report_lines.extend([
                    f"Fully Matched ({len(full_matches)}):",
                    "-" * 20
                ])
                for result in full_matches:
                    relative_path = self._relativize_sd_path(result.sd_photo_path, sd_path)
                    report_lines.append(f"  {relative_path}")
                    report_lines.append(f"  └─ {result.nas_photo_path}")

                    match_info = f"     Method: {result.match_type}"
                    if result.warnings:
                        warnings_text = ", ".join(result.warnings)
                        match_info += f", Warning: {warnings_text}"
                    report_lines.append(match_info)
                    report_lines.append("")
                report_lines.append("")

            # DateTime/Size Matched section (different filename)
            if datetime_size_matches:
                report_lines.extend([
                    f"DateTime/Size Matched ({len(datetime_size_matches)}):",
                    "-" * 20
                ])
                for result in datetime_size_matches:
                    relative_path = self._relativize_sd_path(result.sd_photo_path, sd_path)
                    report_lines.append(f"  {relative_path}")
                    report_lines.append(f"  └─ {result.nas_photo_path}")

                    match_info = f"     Method: {result.match_type}"
                    if result.warnings:
                        warnings_text = ", ".join(result.warnings)
                        match_info += f", Warning: {warnings_text}"
                    report_lines.append(match_info)
                    report_lines.append("")
                report_lines.append("")

        # Add warnings section
        warning_results = [r for r in results if r.found_in_nas and r.warnings]
        if warning_results:
            report_lines.extend([
                "Verification Warnings:",
                "-" * 30
            ])

            for result in warning_results:
                filename = Path(result.sd_photo_path).name
                report_lines.append(f"  {filename}:")
                for warning in result.warnings:
                    report_lines.append(f"    • {warning}")
                report_lines.append("")

        return "\n".join(report_lines)