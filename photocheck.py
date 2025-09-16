#!/usr/bin/env python3
import os
from pathlib import Path

import click

from photocheck import DatabaseCleaner, Config, DatabaseManager, PhotoScanner, SDCardVerifier
from photocheck.constants import MAX_FAILED_FILES_DISPLAY, MAX_ERRORS_DISPLAY


def _show_failed_files(scanner, stats):
    """Show files that failed metadata extraction"""
    if hasattr(scanner, 'failed_files') and scanner.failed_files:
        failed_count = len(scanner.failed_files)
        if stats.processed_files > 0:
            failure_rate = (failed_count / stats.processed_files) * 100
            click.echo(f"\n⚠️  Files that failed metadata extraction ({failed_count}, {failure_rate:.1f}%):")
        else:
            click.echo(f"\n⚠️  Files that failed metadata extraction ({failed_count}):")
        for failed_file in scanner.failed_files[:MAX_FAILED_FILES_DISPLAY]:
            click.echo(f"  {Path(failed_file).name}")
        if len(scanner.failed_files) > MAX_FAILED_FILES_DISPLAY:
            click.echo(f"  ... and {len(scanner.failed_files) - MAX_FAILED_FILES_DISPLAY} more")


def _show_processing_rate(stats):
    """Show processing rate if applicable"""
    if stats.processed_files > 0:
        rate = stats.processed_files / stats.duration if stats.duration > 0 else 0
        click.echo(f"🚀 Processing rate: {rate:.1f} files/sec")


def _show_other_errors(scanner):
    """Show other errors encountered during processing"""
    if hasattr(scanner, 'errors') and scanner.errors:
        click.echo(f"\n⚠️  Other errors encountered ({len(scanner.errors)}):")
        for error in scanner.errors[:MAX_ERRORS_DISPLAY]:
            click.echo(f"  {error}")
        if len(scanner.errors) > MAX_ERRORS_DISPLAY:
            click.echo(f"  ... and {len(scanner.errors) - MAX_ERRORS_DISPLAY} more")


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
@click.option('--method', type=click.Choice(['hash', 'exif']), default=None,
              help='Processing method: "hash" for hash-only (fast), "exif" for full metadata extraction')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose output showing detailed progress')
@click.option('--debug', is_flag=True, help='Enable debug output including timing information')
@click.option('--exclude', multiple=True, help='Directory names to exclude (can be specified multiple times)')
@click.option('--rescan', is_flag=True,
              help='Clear existing entries and rescan from scratch')
@click.option('--update', is_flag=True,
              help='Update existing entries and add new files')
@click.pass_context
def scan(ctx, path, method, verbose, debug, exclude, rescan, update):
    """Scan directory and add photos to database"""
    db_manager = ctx.obj['db_manager']
    config = ctx.obj['config']
    
    # Use config defaults if not specified
    scanning_config = config.get_scanning_config()
    if method is None:
        method = scanning_config.get('method', 'exif')
    # Set processing flags based on method
    calculate_hash = (method == 'hash')
    extract_metadata = (method == 'exif')

    # Combine CLI exclusions with config exclusions
    config_excludes = scanning_config.get('exclude_dirs', [])
    if exclude:
        exclude_dirs = list(exclude)
    else:
        exclude_dirs = config_excludes

    if rescan:
        click.echo("Clearing existing database entries...")
        with db_manager.get_connection() as conn:
            conn.execute('DELETE FROM photos')

    scanner = PhotoScanner(db_manager, calculate_hash=calculate_hash, extract_metadata=extract_metadata, exclude_dirs=exclude_dirs, verbose=verbose, debug=debug)

    click.echo(f"Scanning directory: {path}")
    click.echo(f"Processing method: {method}")
    if exclude_dirs:
        click.echo(f"Excluding directories: {', '.join(exclude_dirs)}")
    
    if update:
        updated_count = scanner.update_existing_photos(path)
        stats = scanner.stats
        
        click.echo(f"\n✅ Update completed successfully!")
        click.echo(f"📁 Total files checked: {stats.processed_files}")
        click.echo(f"💾 New photos added to database: {stats.photos_found}")
        click.echo(f"⏱️  Total time: {stats.duration:.1f}s")
        
        _show_processing_rate(stats)
        _show_failed_files(scanner, stats)

    else:
        try:
            stats = scanner.scan_directory(path)
        except KeyboardInterrupt:
            # Exit cleanly without traceback
            ctx.exit(1)
        
        if getattr(stats, 'interrupted', False):
            click.echo(f"\n❌ Scan was interrupted")
            if stats.processed_files > 0:
                click.echo(f"📁 Files processed before interruption: {stats.processed_files}")
                click.echo(f"💾 Photos added to database: {stats.photos_found}")
        else:
            if stats.errors > 0:
                click.echo(f"\n⚠️ Scan completed with {stats.errors} failed files")
            else:
                click.echo(f"\n✅ Scan completed successfully!")
            click.echo(f"📁 Files processed: {stats.processed_files}")
            click.echo(f"💾 Photos added to database: {stats.photos_found}")
            if stats.errors > 0:
                click.echo(f"❌ Failed files: {stats.errors}")
            click.echo(f"⏱️  Total time: {stats.duration:.1f}s")
            
            _show_processing_rate(stats)

        _show_failed_files(scanner, stats)
        _show_other_errors(scanner)
        
        if stats.duplicates_found > 0:
            click.echo(f"🔄 Duplicates found: {stats.duplicates_found}")


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
    
    report_text = verifier.generate_report(results, str(path))
    click.echo("\n" + report_text)
    
    if report:
        Path(report).write_text(report_text)
        click.echo(f"\nReport saved to: {report}")
    
    # Exit with error code if any photos are missing
    missing_count = sum(1 for r in results if not r.found_in_nas)
    if missing_count > 0:
        click.echo(f"\n⚠️  {missing_count} photos not found in DB!")
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