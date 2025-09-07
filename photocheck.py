#!/usr/bin/env python3
import os
from pathlib import Path

import click

from cleanup import DatabaseCleaner
from config import Config
from database import DatabaseManager
from photo_scanner import PhotoScanner
from sd_verifier import SDCardVerifier


@click.group()
@click.option('--config', '-c', help='Path to configuration file')
@click.option('--db', help='Path to SQLite database file (overrides config)')
@click.pass_context
def cli(ctx, config, db):
    """PhotoCheck - Verify SD card photos are backed up to NAS"""
    ctx.ensure_object(dict)
    
    # Load configuration
    ctx.obj['config'] = Config(config)
    
    # Database path: CLI option > config file > default
    if db:
        db_path = str(Path(db).expanduser().resolve())
    else:
        db_path = ctx.obj['config'].get_db_path()
    
    ctx.obj['db_path'] = db_path
    ctx.obj['db_manager'] = DatabaseManager(db_path)


@cli.command()
@click.argument('path', type=click.Path(exists=True, path_type=Path))
@click.option('--hash/--no-hash', default=None, 
              help='Calculate file hashes for duplicate detection (slower)')
@click.option('--threads', type=int, help='Number of worker threads')
@click.option('--rescan', is_flag=True, 
              help='Clear existing entries and rescan from scratch')
@click.option('--update', is_flag=True, 
              help='Update existing entries and add new files')
@click.pass_context
def scan(ctx, path, hash, threads, rescan, update):
    """Scan directory and add photos to database"""
    db_manager = ctx.obj['db_manager']
    config = ctx.obj['config']
    
    # Use config defaults if not specified
    scanning_config = config.get_scanning_config()
    if hash is None:
        hash = scanning_config.get('calculate_hash', False)
    if threads is None:
        threads = scanning_config.get('threads', 8)
    
    if rescan:
        click.echo("Clearing existing database entries...")
        with db_manager.get_connection() as conn:
            conn.execute('DELETE FROM photos')
    
    scanner = PhotoScanner(db_manager, calculate_hash=hash, num_threads=threads)
    
    click.echo(f"Scanning directory: {path}")
    click.echo(f"Hash calculation: {'enabled' if hash else 'disabled'}")
    click.echo(f"Threads: {threads}")
    
    if update:
        updated_count = scanner.update_existing_photos(path)
        click.echo(f"Updated {updated_count} existing photos")
    else:
        stats = scanner.scan_directory(path)
        
        click.echo(f"\nScan completed in {stats.duration:.1f}s")
        click.echo(f"Total files processed: {stats.processed_files}")
        click.echo(f"Photos added to database: {stats.photos_found}")
        click.echo(f"Errors encountered: {stats.errors}")
        
        if stats.duplicates_found > 0:
            click.echo(f"Duplicates found: {stats.duplicates_found}")


@cli.command()
@click.argument('path', type=click.Path(exists=True, path_type=Path))
@click.option('--mode', type=click.Choice(['hash', 'metadata', 'auto']), 
              help='Verification mode')
@click.option('--threads', type=int, help='Number of worker threads')
@click.option('--report', type=click.Path(), 
              help='Save verification report to file')
@click.pass_context
def verify(ctx, path, mode, threads, report):
    """Verify SD card photos against database"""
    db_manager = ctx.obj['db_manager']
    config = ctx.obj['config']
    
    # Use config defaults if not specified
    verification_config = config.get_verification_config()
    if mode is None:
        mode = verification_config.get('mode', 'auto')
    if threads is None:
        threads = verification_config.get('threads', 8)
    
    # Check if database has any photos
    stats = db_manager.get_stats()
    if stats['total_photos'] == 0:
        click.echo("Error: Database is empty. Run 'scan' command first.")
        return
    
    click.echo(f"Database contains {stats['total_photos']} photos")
    click.echo(f"Verifying photos from: {path}")
    
    use_hash = mode == 'hash' or (mode == 'auto' and stats['photos_with_hash'] > 0)
    
    if use_hash:
        click.echo("Using hash-based verification (most accurate)")
    else:
        click.echo("Using metadata-based verification")
    
    verifier = SDCardVerifier(db_manager, num_threads=threads)
    results = verifier.verify_sd_card(path, use_hash=use_hash)
    
    if not results:
        click.echo("No photos found on SD card")
        return
    
    report_text = verifier.generate_report(results)
    click.echo("\n" + report_text)
    
    if report:
        Path(report).write_text(report_text)
        click.echo(f"\nReport saved to: {report}")
    
    # Exit with error code if any photos are missing
    missing_count = sum(1 for r in results if not r.found_in_nas)
    if missing_count > 0:
        click.echo(f"\n⚠️  {missing_count} photos not found in NAS!")
        ctx.exit(1)
    else:
        click.echo("\n✅ All photos verified!")


@cli.command()
@click.option('--mark-missing', is_flag=True, 
              help='Mark missing files without removing them')
@click.option('--remove-missing', is_flag=True, 
              help='Remove entries for missing files')
@click.option('--verify-paths', is_flag=True, 
              help='Check all file paths and mark missing ones')
@click.option('--remove-duplicates', is_flag=True, 
              help='Remove duplicate entries based on hash')
@click.option('--base-path', multiple=True, 
              help='Base paths to check (can be specified multiple times)')
@click.pass_context
def cleanup(ctx, mark_missing, remove_missing, verify_paths, remove_duplicates, base_path):
    """Clean up database entries"""
    db_manager = ctx.obj['db_manager']
    cleaner = DatabaseCleaner(db_manager)
    
    if not any([mark_missing, remove_missing, verify_paths, remove_duplicates]):
        # Show cleanup stats by default
        stats = cleaner.get_cleanup_stats()
        click.echo("Database Statistics:")
        click.echo(f"Total photos: {stats['total_photos']}")
        click.echo(f"Existing photos: {stats['existing_photos']}")
        click.echo(f"Missing photos: {stats['missing_photos']}")
        click.echo(f"Photos with hash: {stats['photos_with_hash']}")
        click.echo(f"Recently verified: {stats.get('recently_verified', 0)}")
        click.echo(f"Never verified: {stats.get('never_verified', 0)}")
        return
    
    if verify_paths:
        click.echo("Verifying file paths...")
        result = cleaner.verify_file_existence()
        click.echo(f"Checked {result['total_checked']} files")
        click.echo(f"Found {result['existing_files']} existing files")
        click.echo(f"Marked {result['missing_files']} as missing")
    
    if mark_missing and base_path:
        click.echo(f"Marking missing files in {len(base_path)} base paths...")
        results = cleaner.mark_missing_files(list(base_path))
        for path, count in results.items():
            click.echo(f"  {path}: {count} files marked missing")
    
    if remove_missing:
        missing_files = cleaner.get_missing_files()
        if missing_files:
            click.echo(f"Found {len(missing_files)} missing file entries")
            if click.confirm("Remove these entries from database?"):
                removed_count = cleaner.remove_missing_files()
                click.echo(f"Removed {removed_count} entries")
        else:
            click.echo("No missing files to remove")
    
    if remove_duplicates:
        click.echo("Removing duplicate entries...")
        result = cleaner.cleanup_duplicates()
        click.echo(f"Found {result['duplicate_groups']} groups of duplicates")
        click.echo(f"Removed {result['entries_removed']} duplicate entries")


@cli.command()
@click.pass_context
def stats(ctx):
    """Show database statistics"""
    db_manager = ctx.obj['db_manager']
    cleaner = DatabaseCleaner(db_manager)
    
    stats = cleaner.get_cleanup_stats()
    
    click.echo("PhotoCheck Database Statistics")
    click.echo("=" * 35)
    click.echo(f"Total photos: {stats['total_photos']}")
    click.echo(f"Existing photos: {stats['existing_photos']}")
    click.echo(f"Missing photos: {stats['missing_photos']}")
    click.echo(f"Photos with hash: {stats['photos_with_hash']}")
    
    if stats['total_photos'] > 0:
        existence_rate = (stats['existing_photos'] / stats['total_photos']) * 100
        hash_rate = (stats['photos_with_hash'] / stats['total_photos']) * 100
        click.echo(f"\nFile existence rate: {existence_rate:.1f}%")
        click.echo(f"Hash coverage: {hash_rate:.1f}%")
    
    click.echo(f"\nDatabase location: {ctx.obj['db_path']}")


if __name__ == '__main__':
    cli()