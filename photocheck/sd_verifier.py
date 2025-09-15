import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from tqdm import tqdm

from .database import DatabaseManager
from .models import PhotoMetadata, VerificationResult
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
                if Path(file).suffix.lower() in scanner.SUPPORTED_EXTENSIONS:
                    photo_files.append(Path(root) / file)
        return photo_files

    def verify_sd_card(self, sd_path: Path, use_hash: bool = True) -> List[VerificationResult]:
        sd_path = Path(sd_path).resolve()
        
        # Create scanner with appropriate hash setting
        scanner = PhotoScanner(self.db, calculate_hash=use_hash, num_threads=1)
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

            if use_hash and sd_metadata.file_hash:
                nas_photo = self.db.find_by_hash(sd_metadata.file_hash)
                if nas_photo:
                    warnings = self._detect_incomplete_metadata(nas_photo, sd_metadata)
                    return VerificationResult(
                        sd_photo_path=str(sd_photo_path),
                        found_in_nas=True,
                        nas_photo_path=nas_photo.file_path,
                        match_type="hash",
                        confidence=1.0,
                        warnings=warnings
                    )

            metadata_matches = self.db.find_by_metadata(
                sd_metadata.filename,
                sd_metadata.capture_datetime,
                sd_metadata.file_size
            )
            
            if metadata_matches:
                best_match = self._find_best_metadata_match(sd_metadata, metadata_matches)
                if best_match:
                    warnings = self._detect_incomplete_metadata(best_match, sd_metadata)
                    return VerificationResult(
                        sd_photo_path=str(sd_photo_path),
                        found_in_nas=True,
                        nas_photo_path=best_match.file_path,
                        match_type="metadata",
                        confidence=self._calculate_confidence(sd_metadata, best_match),
                        warnings=warnings
                    )

            # Fallback: try filename + file size (more reliable than filename alone)
            filename_size_matches = self.db.find_by_metadata(
                sd_metadata.filename,
                None,  # No datetime requirement
                sd_metadata.file_size
            )
            if filename_size_matches:
                best_match = self._find_best_metadata_match(sd_metadata, filename_size_matches)
                if best_match:
                    warnings = self._detect_incomplete_metadata(best_match, sd_metadata)
                    return VerificationResult(
                        sd_photo_path=str(sd_photo_path),
                        found_in_nas=True,
                        nas_photo_path=best_match.file_path,
                        match_type="filename_size",
                        confidence=self._calculate_confidence(sd_metadata, best_match),
                        warnings=warnings
                    )

            return VerificationResult(
                sd_photo_path=str(sd_photo_path),
                found_in_nas=False,
                match_type="not_found",
                confidence=0.0
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
        
        if len(candidates) == 1:
            return candidates[0]

        scored_candidates = []
        for candidate in candidates:
            confidence = self._calculate_confidence(sd_metadata, candidate)
            scored_candidates.append((confidence, candidate))
        
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        
        return scored_candidates[0][1] if scored_candidates[0][0] > 0.5 else None

    def _calculate_confidence(self, sd_metadata: PhotoMetadata, 
                            nas_metadata: PhotoMetadata) -> float:
        score = 0.0
        total_weight = 0.0
        
        if sd_metadata.filename == nas_metadata.filename:
            score += 0.3
        total_weight += 0.3
        
        if (sd_metadata.capture_datetime and nas_metadata.capture_datetime and 
            sd_metadata.capture_datetime == nas_metadata.capture_datetime):
            score += 0.4
        total_weight += 0.4
        
        if sd_metadata.file_size == nas_metadata.file_size:
            score += 0.2
        total_weight += 0.2
        
        if (sd_metadata.width and nas_metadata.width and 
            sd_metadata.height and nas_metadata.height and
            sd_metadata.width == nas_metadata.width and 
            sd_metadata.height == nas_metadata.height):
            score += 0.1
        total_weight += 0.1
        
        return score / total_weight if total_weight > 0 else 0.0

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
                "Missing Photos:",
                "-" * 20
            ])

            # Sort missing photos alphabetically
            missing_photos = [result.sd_photo_path for result in results if not result.found_in_nas]
            missing_photos.sort()

            for photo_path in missing_photos:
                report_lines.append(f"  {photo_path}")

            report_lines.append("")
        
        match_types = {}
        for result in results:
            if result.found_in_nas and result.match_type:
                match_types[result.match_type] = match_types.get(result.match_type, 0) + 1
        
        if match_types:
            report_lines.extend([
                "Match Types:",
                "-" * 20
            ])

            for match_type, count in match_types.items():
                report_lines.append(f"  {match_type}: {count}")

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