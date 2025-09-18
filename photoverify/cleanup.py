from datetime import datetime
from pathlib import Path
from typing import List

import click
from tqdm import tqdm

from .database import DatabaseManager


class DatabaseCleaner:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def mark_missing_files(self, base_paths: List[str]) -> dict:
        """Mark files as missing if they don't exist in the specified base paths"""
        results = {}
        
        for base_path in base_paths:
            base_path = str(Path(base_path).resolve())
            count = self.db.mark_files_missing(base_path)
            results[base_path] = count
            
        return results

    def verify_file_existence(self, base_paths: List[str] = None) -> dict:
        """Check database entries and mark missing files"""
        # Build query based on whether base paths are specified
        if base_paths:
            # Only check files under the specified base paths
            base_conditions = []
            params = []
            for base_path in base_paths:
                base_path = str(Path(base_path).resolve())
                base_conditions.append('file_path LIKE ?')
                params.append(f"{base_path}%")

            where_clause = f"file_exists = 1 AND ({' OR '.join(base_conditions)})"
        else:
            where_clause = "file_exists = 1"
            params = []

        with self.db.get_connection() as conn:
            cursor = conn.execute(f'SELECT COUNT(*) FROM photos WHERE {where_clause}', params)
            total_count = cursor.fetchone()[0]

            if total_count == 0:
                return {'existing_files': 0, 'missing_files': 0, 'total_checked': 0}

        click.echo(f"Checking {total_count:,} files for existence...")

        # Process in batches to avoid memory issues
        batch_size = 1000
        existing_count = 0
        missing_count = 0

        with tqdm(total=total_count, desc="Verifying files", unit="files") as pbar:
            offset = 0
            while offset < total_count:
                with self.db.get_connection() as conn:
                    cursor = conn.execute(f'''
                        SELECT file_path FROM photos
                        WHERE {where_clause}
                        ORDER BY file_path
                        LIMIT ? OFFSET ?
                    ''', params + [batch_size, offset])
                    batch_paths = [row['file_path'] for row in cursor.fetchall()]

                if not batch_paths:
                    break

                # Check existence for this batch
                batch_updates = []
                for file_path in batch_paths:
                    if Path(file_path).exists():
                        existing_count += 1
                    else:
                        batch_updates.append(file_path)
                        missing_count += 1
                    pbar.update(1)

                # Update missing files in batch
                if batch_updates:
                    with self.db.get_connection() as conn:
                        conn.executemany('''
                            UPDATE photos SET file_exists = 0, last_verified = CURRENT_TIMESTAMP
                            WHERE file_path = ?
                        ''', [(path,) for path in batch_updates])

                offset += batch_size

        return {
            'existing_files': existing_count,
            'missing_files': missing_count,
            'total_checked': total_count
        }

    def remove_missing_files(self) -> int:
        """Permanently remove entries for missing files"""
        return self.db.remove_missing_files()

    def get_missing_files(self) -> List[str]:
        """Get list of files marked as missing"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                'SELECT file_path FROM photos WHERE file_exists = 0 ORDER BY file_path'
            )
            return [row['file_path'] for row in cursor.fetchall()]

    def restore_found_files(self, base_paths: List[str]) -> int:
        """Restore files that were marked missing but now exist"""
        restored_count = 0
        
        missing_files = self.get_missing_files()
        
        for file_path in missing_files:
            path = Path(file_path)
            if path.exists():
                # Verify it's under one of our base paths
                for base_path in base_paths:
                    try:
                        path.resolve().relative_to(Path(base_path).resolve())
                        self.db.mark_file_exists(file_path)
                        restored_count += 1
                        break
                    except ValueError:
                        continue
        
        return restored_count

    def cleanup_duplicates(self) -> dict:
        """Remove duplicate entries based on file hash"""
        with self.db.get_connection() as conn:
            # Find duplicates by hash
            cursor = conn.execute('''
                SELECT file_hash, COUNT(*) as count
                FROM photos 
                WHERE file_hash IS NOT NULL AND file_exists = 1
                GROUP BY file_hash 
                HAVING COUNT(*) > 1
            ''')
            
            duplicate_hashes = [row['file_hash'] for row in cursor.fetchall()]
            
        removed_count = 0
        for file_hash in duplicate_hashes:
            with self.db.get_connection() as conn:
                # Keep the oldest entry, remove the rest
                cursor = conn.execute('''
                    SELECT id FROM photos 
                    WHERE file_hash = ? AND file_exists = 1
                    ORDER BY created_at ASC
                ''', (file_hash,))
                
                ids = [row['id'] for row in cursor.fetchall()]
                if len(ids) > 1:
                    # Remove all but the first (oldest)
                    ids_to_remove = ids[1:]
                    conn.executemany(
                        'DELETE FROM photos WHERE id = ?',
                        [(id_,) for id_ in ids_to_remove]
                    )
                    removed_count += len(ids_to_remove)
        
        return {
            'duplicate_groups': len(duplicate_hashes),
            'entries_removed': removed_count
        }

    def get_cleanup_stats(self) -> dict:
        """Get overall cleanup statistics"""
        stats = self.db.get_stats()
        
        # Add some additional cleanup-specific stats
        with self.db.get_connection() as conn:
            # Count files by verification status
            recent_cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            cursor = conn.execute('''
                SELECT 
                    COUNT(CASE WHEN last_verified >= ? THEN 1 END) as recently_verified,
                    COUNT(CASE WHEN last_verified IS NULL THEN 1 END) as never_verified,
                    COUNT(CASE WHEN file_hash IS NOT NULL AND file_exists = 1 THEN 1 END) as with_hash
                FROM photos
            ''', (recent_cutoff,))
            
            additional_stats = dict(cursor.fetchone())
            stats.update(additional_stats)
        
        return stats