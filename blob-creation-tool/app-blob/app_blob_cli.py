#!/usr/bin/env python3
"""
App Blob CLI Tool

Command-line interface for building and managing application blobs.

Usage:
    blob-cli build --config apps.yaml --output apps.blob
    blob-cli list apps.blob
    blob-cli extract apps.blob --apps app1,app2 --output /opt/apps
    blob-cli info apps.blob app1
    blob-cli verify apps.blob
"""

import argparse
import sys
import yaml
from pathlib import Path
from app_blob_manager import AppBlobBuilder, AppBlobExtractor


def cmd_build(args):
    """Build a blob from configuration file."""
    
    if not Path(args.config).exists():
        print(f"Error: Config file '{args.config}' not found")
        return 1
    
    # Load config
    with open(args.config, 'r') as f:
        config = yaml.safe_load(f)
    
    print(f"Building blob from config: {args.config}")
    print(f"Output: {args.output}\n")
    
    builder = AppBlobBuilder(args.output)
    
    # Process apps from config
    for app_config in config.get('applications', []):
        key = app_config['key']
        name = app_config.get('name', key)
        path = app_config['path']
        version = app_config.get('version', '1.0.0')
        deps = app_config.get('dependencies', [])
        
        if not Path(path).exists():
            print(f"Warning: Path '{path}' not found, skipping {key}")
            continue
        
        builder.add_application(
            app_key=key,
            app_name=name,
            app_path=path,
            version=version,
            dependencies=deps
        )
    
    builder.build()
    return 0


def cmd_list(args):
    """List applications in a blob."""
    
    if not Path(args.blob).exists():
        print(f"Error: Blob file '{args.blob}' not found")
        return 1
    
    extractor = AppBlobExtractor(args.blob)
    
    print(f"Applications in {args.blob}:\n")
    
    for key in sorted(extractor.list_applications()):
        meta = extractor.get_metadata(key)
        deps = ''
        if meta.dependencies:
            deps = f" → depends on: {', '.join(meta.dependencies)}"
        
        print(f"  {key}")
        print(f"    Name: {meta.name}")
        print(f"    Version: {meta.version}")
        print(f"    Files: {len(meta.files)}")
        print(f"    Size: {meta.size:,} bytes (compressed: {meta.compressed_size:,})")
        if deps:
            print(f"    Dependencies: {deps}")
        print()
    
    return 0


def cmd_extract(args):
    """Extract applications from blob."""
    
    if not Path(args.blob).exists():
        print(f"Error: Blob file '{args.blob}' not found")
        return 1
    
    app_keys = [k.strip() for k in args.apps.split(',')]
    
    extractor = AppBlobExtractor(args.blob)
    
    results = extractor.extract_applications(
        app_keys,
        args.output,
        resolve_deps=not args.no_deps,
        verify_checksums=not args.no_verify
    )
    
    # Return error if any extraction failed
    if not all(results.values()):
        return 1
    
    return 0


def cmd_info(args):
    """Show detailed info about an application."""
    
    if not Path(args.blob).exists():
        print(f"Error: Blob file '{args.blob}' not found")
        return 1
    
    extractor = AppBlobExtractor(args.blob)
    meta = extractor.get_metadata(args.app)
    
    if not meta:
        print(f"Error: Application '{args.app}' not found")
        return 1
    
    print(f"Application: {args.app}")
    print(f"{'=' * 60}")
    print(f"Name:               {meta.name}")
    print(f"Version:            {meta.version}")
    print(f"Size (original):    {meta.size:,} bytes")
    print(f"Size (compressed):  {meta.compressed_size:,} bytes")
    print(f"Compression ratio:  {meta.size / meta.compressed_size:.2f}x")
    print(f"Offset in blob:     {meta.offset:,} bytes")
    print(f"Checksum (SHA256):  {meta.checksum}")
    
    if meta.dependencies:
        print(f"\nDependencies:")
        for dep in meta.dependencies:
            print(f"  - {dep}")
    
    print(f"\nFiles ({len(meta.files)}):")
    for f in sorted(meta.files)[:20]:  # Show first 20
        print(f"  - {f}")
    
    if len(meta.files) > 20:
        print(f"  ... and {len(meta.files) - 20} more")
    
    return 0


def cmd_verify(args):
    """Verify blob integrity."""
    
    if not Path(args.blob).exists():
        print(f"Error: Blob file '{args.blob}' not found")
        return 1
    
    print(f"Verifying blob: {args.blob}")
    
    try:
        extractor = AppBlobExtractor(args.blob)
        print(f"✓ Blob format valid")
        print(f"✓ Index loaded ({len(extractor.list_applications())} apps)")
        
        # Verify each application's checksum
        all_valid = True
        for key in extractor.list_applications():
            try:
                # This will verify checksum
                extractor.extract_to_memory(key)
                print(f"✓ {key}: checksum valid")
            except Exception as e:
                print(f"✗ {key}: {e}")
                all_valid = False
        
        if all_valid:
            print(f"\n✓ All applications verified successfully")
            return 0
        else:
            print(f"\n✗ Some applications failed verification")
            return 1
    
    except Exception as e:
        print(f"✗ Blob verification failed: {e}")
        return 1


def cmd_init(args):
    """Initialize a sample config file."""
    
    config = {
        'applications': [
            {
                'key': 'my-app',
                'name': 'My Application',
                'path': './apps/my-app',
                'version': '1.0.0',
                'dependencies': []
            },
            {
                'key': 'shared-lib',
                'name': 'Shared Library',
                'path': './apps/shared-lib',
                'version': '1.0.0',
                'dependencies': []
            }
        ]
    }
    
    output_file = args.output or 'blob-config.yaml'
    
    with open(output_file, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    print(f"Created sample config: {output_file}")
    print("\nEdit this file and then build with:")
    print(f"  blob-cli build --config {output_file} --output apps.blob")
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Application Blob Management Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Build command
    build_parser = subparsers.add_parser('build', help='Build a blob from config')
    build_parser.add_argument('--config', '-c', required=True, help='YAML config file')
    build_parser.add_argument('--output', '-o', default='apps.blob', help='Output blob file')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List apps in blob')
    list_parser.add_argument('blob', help='Blob file path')
    
    # Extract command
    extract_parser = subparsers.add_parser('extract', help='Extract apps from blob')
    extract_parser.add_argument('blob', help='Blob file path')
    extract_parser.add_argument('--apps', '-a', required=True, help='Comma-separated app keys')
    extract_parser.add_argument('--output', '-o', default='./extracted', help='Output directory')
    extract_parser.add_argument('--no-deps', action='store_true', help='Do not resolve dependencies')
    extract_parser.add_argument('--no-verify', action='store_true', help='Skip checksum verification')
    
    # Info command
    info_parser = subparsers.add_parser('info', help='Show app info')
    info_parser.add_argument('blob', help='Blob file path')
    info_parser.add_argument('app', help='Application key')
    
    # Verify command
    verify_parser = subparsers.add_parser('verify', help='Verify blob integrity')
    verify_parser.add_argument('blob', help='Blob file path')
    
    # Init command
    init_parser = subparsers.add_parser('init', help='Create sample config')
    init_parser.add_argument('--output', '-o', help='Output config file')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Dispatch to command handler
    commands = {
        'build': cmd_build,
        'list': cmd_list,
        'extract': cmd_extract,
        'info': cmd_info,
        'verify': cmd_verify,
        'init': cmd_init
    }
    
    return commands[args.command](args)


if __name__ == '__main__':
    sys.exit(main())