import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Iterator
from .constants import DATABASE_TIMEOUT
from .models import PhotoMetadata


class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self._local = threading.local()
        self._ensure_database()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'connection'):
            self._local.connection = sqlite3.connect(
                self.db_path, 
                check_same_thread=False,
                timeout=DATABASE_TIMEOUT
            )
            self._local.connection.row_factory = sqlite3.Row
        return self._local.connection

    @contextmanager
    def get_connection(self):
        conn = self._get_connection()
        try:
            yield conn
        finally:
            conn.commit()

    def _ensure_database(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        with self.get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    file_size INTEGER NOT NULL,
                    file_hash TEXT,
                    capture_datetime TIMESTAMP,
                    width INTEGER,
                    height INTEGER,
                    camera_make TEXT,
                    camera_model TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_verified TIMESTAMP,
                    file_exists BOOLEAN NOT NULL DEFAULT 1
                )
            ''')
            
            conn.execute('CREATE INDEX IF NOT EXISTS idx_file_hash ON photos(file_hash)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_filename ON photos(filename)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_capture_datetime ON photos(capture_datetime)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_file_exists ON photos(file_exists)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_file_path ON photos(file_path)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_filename_exists ON photos(filename, file_exists)')

    def insert_photo(self, photo: PhotoMetadata) -> bool:
        with self.get_connection() as conn:
            try:
                conn.execute('''
                    INSERT OR REPLACE INTO photos (
                        filename, file_path, file_size, file_hash,
                        capture_datetime, width, height, camera_make, camera_model,
                        created_at, last_verified, file_exists
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    photo.filename, photo.file_path, photo.file_size, photo.file_hash,
                    photo.capture_datetime, photo.width, photo.height,
                    photo.camera_make, photo.camera_model, photo.created_at,
                    photo.last_verified, photo.file_exists
                ))
                return True
            except sqlite3.IntegrityError:
                return False

    def insert_photos_batch(self, photos: List[PhotoMetadata]):
        with self.get_connection() as conn:
            conn.executemany('''
                INSERT OR REPLACE INTO photos (
                    filename, file_path, file_size, file_hash,
                    capture_datetime, width, height, camera_make, camera_model,
                    created_at, last_verified, file_exists
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', [
                (p.filename, p.file_path, p.file_size, p.file_hash,
                 p.capture_datetime, p.width, p.height, p.camera_make, 
                 p.camera_model, p.created_at, p.last_verified, p.file_exists)
                for p in photos
            ])

    def find_by_hash(self, file_hash: str) -> Optional[PhotoMetadata]:
        if not file_hash:
            return None
            
        with self.get_connection() as conn:
            row = conn.execute(
                'SELECT * FROM photos WHERE file_hash = ? AND file_exists = 1 LIMIT 1',
                (file_hash,)
            ).fetchone()
            
            return self._row_to_photo(row) if row else None

    def find_by_metadata(self, filename: str, capture_datetime: Optional[datetime] = None,
                        file_size: Optional[int] = None) -> List[PhotoMetadata]:
        with self.get_connection() as conn:
            query = 'SELECT * FROM photos WHERE filename = ? AND file_exists = 1'
            params = [filename]

            if capture_datetime:
                query += ' AND capture_datetime = ?'
                params.append(capture_datetime)

            if file_size:
                query += ' AND file_size = ?'
                params.append(file_size)

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_photo(row) for row in rows]

    def find_by_datetime_size(self, capture_datetime: Optional[datetime] = None,
                             file_size: Optional[int] = None) -> List[PhotoMetadata]:
        """Find files by capture datetime and file size only (ignoring filename)"""
        if not capture_datetime or not file_size:
            return []

        with self.get_connection() as conn:
            query = 'SELECT * FROM photos WHERE capture_datetime = ? AND file_size = ? AND file_exists = 1'
            params = [capture_datetime, file_size]
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_photo(row) for row in rows]

    def find_by_fuzzy_pattern(self, original_filename: str, capture_datetime: Optional[datetime] = None,
                             file_size: Optional[int] = None, prefix: str = "AZ") -> List[PhotoMetadata]:
        """Find files that may have been renamed following patterns:
        1. PREFIX_YYYYMMDD_HHMMSS_NNNN.EXT (time component first)
        2. PREFIX_YYYYMMDD_0NNNN.EXT (5-digit padded fallback)
        """
        if not capture_datetime:
            return []

        # Extract number from original filename (e.g., DSCF3801.RAF -> 3801)
        import re
        from pathlib import Path

        number_match = re.search(r'(\d{4,7})', original_filename)
        if not number_match:
            return []

        number = number_match.group(1)
        date_str = capture_datetime.strftime('%Y%m%d')
        extension = Path(original_filename).suffix

        with self.get_connection() as conn:
            # Strategy 1: Try time component pattern first (AZ_YYYYMMDD_HHMMSS_NNNN.EXT)
            time_pattern = f"{prefix}_{date_str}_%_{number}{extension}"
            query1 = 'SELECT * FROM photos WHERE filename LIKE ? AND file_exists = 1'
            params1 = [time_pattern]

            if file_size:
                query1 += ' AND file_size = ?'
                params1.append(file_size)

            rows = conn.execute(query1, params1).fetchall()
            if rows:
                return [self._row_to_photo(row) for row in rows]

            # Strategy 2: Try 5-digit padded pattern (AZ_YYYYMMDD_0NNNN.EXT)
            padded_number = number.zfill(5)  # Pad to 5 digits: 3801 -> 03801
            padded_pattern = f"{prefix}_{date_str}_{padded_number}{extension}"
            query2 = 'SELECT * FROM photos WHERE filename = ? AND file_exists = 1'
            params2 = [padded_pattern]

            if file_size:
                query2 += ' AND file_size = ?'
                params2.append(file_size)

            rows = conn.execute(query2, params2).fetchall()
            return [self._row_to_photo(row) for row in rows]

    def mark_files_missing(self, base_path: str) -> int:
        with self.get_connection() as conn:
            cursor = conn.execute('''
                UPDATE photos SET file_exists = 0, last_verified = CURRENT_TIMESTAMP
                WHERE file_path LIKE ? AND file_exists = 1
            ''', (f"{base_path}%",))
            return cursor.rowcount

    def mark_file_exists(self, file_path: str):
        with self.get_connection() as conn:
            conn.execute('''
                UPDATE photos SET file_exists = 1, last_verified = CURRENT_TIMESTAMP
                WHERE file_path = ?
            ''', (file_path,))

    def remove_missing_files(self) -> int:
        with self.get_connection() as conn:
            cursor = conn.execute('DELETE FROM photos WHERE file_exists = 0')
            return cursor.rowcount

    def get_stats(self) -> dict:
        with self.get_connection() as conn:
            stats = conn.execute('''
                SELECT 
                    COUNT(*) as total_photos,
                    COUNT(CASE WHEN file_exists = 1 THEN 1 END) as existing_photos,
                    COUNT(CASE WHEN file_exists = 0 THEN 1 END) as missing_photos,
                    COUNT(CASE WHEN file_hash IS NOT NULL THEN 1 END) as photos_with_hash
                FROM photos
            ''').fetchone()
            
            return dict(stats)

    def _row_to_photo(self, row: sqlite3.Row) -> PhotoMetadata:
        return PhotoMetadata(
            filename=row['filename'],
            file_path=row['file_path'],
            file_size=row['file_size'],
            file_hash=row['file_hash'],
            capture_datetime=datetime.fromisoformat(row['capture_datetime']) if row['capture_datetime'] else None,
            width=row['width'],
            height=row['height'],
            camera_make=row['camera_make'],
            camera_model=row['camera_model'],
            created_at=datetime.fromisoformat(row['created_at']),
            last_verified=datetime.fromisoformat(row['last_verified']) if row['last_verified'] else None,
            file_exists=bool(row['file_exists'])
        )