import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from tqdm import tqdm

from database import DatabaseManager
from models import PhotoMetadata, VerificationResult
from photo_scanner import PhotoScanner


class SDCardVerifier:
    def __init__(self, db_manager: DatabaseManager, num_threads: int = 8):
        self.db = db_manager
        self.num_threads = num_threads

    def verify_sd_card(self, sd_path: Path, use_hash: bool = True) -> List[VerificationResult]:
        sd_path = Path(sd_path).resolve()
        
        # Create scanner with appropriate hash setting
        scanner = PhotoScanner(self.db, calculate_hash=use_hash, num_threads=1)
        photo_files = list(scanner._find_photo_files(sd_path))
        
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
            sd_metadata = scanner._extract_metadata(sd_photo_path)
            if not sd_metadata:
                return VerificationResult(
                    sd_photo_path=str(sd_photo_path),
                    found_in_nas=False,
                    match_type="metadata_error"
                )

            if use_hash and sd_metadata.file_hash:
                nas_photo = self.db.find_by_hash(sd_metadata.file_hash)
                if nas_photo:
                    return VerificationResult(
                        sd_photo_path=str(sd_photo_path),
                        found_in_nas=True,
                        nas_photo_path=nas_photo.file_path,
                        match_type="hash",
                        confidence=1.0
                    )

            metadata_matches = self.db.find_by_metadata(
                sd_metadata.filename,
                sd_metadata.capture_datetime,
                sd_metadata.file_size
            )
            
            if metadata_matches:
                best_match = self._find_best_metadata_match(sd_metadata, metadata_matches)
                if best_match:
                    return VerificationResult(
                        sd_photo_path=str(sd_photo_path),
                        found_in_nas=True,
                        nas_photo_path=best_match.file_path,
                        match_type="metadata",
                        confidence=self._calculate_confidence(sd_metadata, best_match)
                    )

            filename_matches = self.db.find_by_metadata(sd_metadata.filename)
            if filename_matches:
                best_match = self._find_best_metadata_match(sd_metadata, filename_matches)
                if best_match:
                    return VerificationResult(
                        sd_photo_path=str(sd_photo_path),
                        found_in_nas=True,
                        nas_photo_path=best_match.file_path,
                        match_type="filename",
                        confidence=self._calculate_confidence(sd_metadata, best_match)
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

    def generate_report(self, results: List[VerificationResult]) -> str:
        if not results:
            return "No verification results to report."

        found_count = sum(1 for r in results if r.found_in_nas)
        missing_count = len(results) - found_count
        
        report_lines = [
            f"SD Card Verification Report",
            f"=" * 40,
            f"Total photos checked: {len(results)}",
            f"Found in NAS: {found_count}",
            f"Missing from NAS: {missing_count}",
            f"Success rate: {(found_count/len(results)*100):.1f}%",
            ""
        ]
        
        if missing_count > 0:
            report_lines.extend([
                "Missing Photos:",
                "-" * 20
            ])
            
            for result in results:
                if not result.found_in_nas:
                    report_lines.append(f"  {result.sd_photo_path}")
            
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
        
        return "\n".join(report_lines)