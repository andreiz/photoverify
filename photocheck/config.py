import os
from pathlib import Path
from typing import Dict, Any, Optional

import yaml


class Config:
    def __init__(self, config_path: Optional[str] = None):
        self.config = self._load_config(config_path)
    
    def _load_config(self, config_path: Optional[str]) -> Dict[str, Any]:
        """Load configuration from file, with fallback to defaults"""
        default_config = {
            'database': {
                'path': 'photos.db'
            },
            'scanning': {
                'threads': 8,
                'batch_size': 100,
                'calculate_hash': False,
                'exclude_dirs': ['.git', '.svn', '__pycache__', '.thumbnails', '@eaDir', 'thumbs']
            },
            'verification': {
                'mode': 'auto',
                'threads': 8
            }
        }
        
        if not config_path:
            # Try to find config file in common locations
            possible_paths = [
                'config.yaml',
                '~/.photocheck/config.yaml',
                '~/.config/photocheck/config.yaml'
            ]
            
            for path in possible_paths:
                expanded_path = Path(path).expanduser()
                if expanded_path.exists():
                    config_path = str(expanded_path)
                    break
        
        if config_path and Path(config_path).exists():
            try:
                with open(config_path, 'r') as f:
                    file_config = yaml.safe_load(f) or {}
                
                # Merge with defaults
                merged_config = default_config.copy()
                for key, value in file_config.items():
                    if key in merged_config and isinstance(merged_config[key], dict):
                        merged_config[key].update(value)
                    else:
                        merged_config[key] = value
                
                return merged_config
            except Exception as e:
                print(f"Warning: Failed to load config from {config_path}: {e}")
        
        return default_config
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., 'database.path')"""
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def get_db_path(self) -> str:
        """Get database path with environment variable expansion"""
        db_path = self.get('database.path', 'photos.db')
        return str(Path(db_path).expanduser().resolve())
    
    def get_scanning_config(self) -> Dict[str, Any]:
        """Get scanning configuration"""
        return self.get('scanning', {})
    
    def get_verification_config(self) -> Dict[str, Any]:
        """Get verification configuration"""
        return self.get('verification', {})