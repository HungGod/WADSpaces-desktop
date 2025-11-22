#!/usr/bin/env python3
"""
Application Blob Builder - All-in-One

Builds and manages application blobs with selective extraction and dependency resolution.
Combines both building and extraction functionality in a single tool.

Usage:
    # Build blob from config
    python3 app-blob-builder.py build --config apps.yaml --output apps.blob

    # Add application to blob
    python3 app-blob-builder.py add \
        --blob apps.blob \
        --key webserver \
        --source ./webserver/ \
        --version 2.1.0

    # List applications
    python3 app-blob-builder.py list --blob apps.blob

    # Extract applications
    python3 app-blob-builder.py extract \
        --blob apps.blob \
        --apps webserver,api-gateway \
        --output ./extracted/

    # Show info
    python3 app-blob-builder.py info --blob apps.blob --app webserver

    # Verify integrity
    python3 app-blob-builder.py verify --blob apps.blob

    # Initialize sample config
    python3 app-blob-builder.py init --output sample-config.yaml
"""

import os
import sys
import json
import yaml
import zlib
import tarfile
import hashlib
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set
from io import BytesIO
from dataclasses import dataclass, asdict
import struct


@dataclass
class AppMetadata:
    """Metadata for an application in the blob."""
    key: str
    name: str
    version: str
    size: int  # Uncompressed size
    compressed_size: int
    offset: int  # Byte offset in blob
    dependencies: List[str]  # Other app keys this depends on
    files: List[str]  # List of files in the app
    checksum: str
    created_at: str


class AppBlobBuilder:
    """Builds application blobs with selective extraction support."""
    
    MAGIC = b'APPBLOB1'
    VERSION = 1
    
    def __init__(self, output_path: str):
        self.output_path = output_path
        self.apps: Dict[str, AppMetadata] = {}
        self.blob_data = BytesIO()
        self.current_offset = 0
    
    def add_application(
        self, 
        app_key: str, 
        app_name: str,
        app_path: str, 
        version: str = "1.0.0",
        dependencies: List[str] = None,
        description: str = ""
    ):
        """Add an application to the blob."""
        dependencies = dependencies or []
        
        print(f"\nAdding application: {app_key} ({app_name} v{version})")
        
        if not os.path.exists(app_path):
            print(f"  Error: Path '{app_path}' not found")
            return False
        
        # Create tar archive of the application
        tar_buffer = BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
            tar.add(app_path, arcname='.')
        
        tar_data = tar_buffer.getvalue()
        uncompressed_size = len(tar_data)
        
        # Compress the tar archive
        compressed_data = zlib.compress(tar_data, level=6)
        compressed_size = len(compressed_data)
        
        # Calculate checksum
        checksum = hashlib.sha256(tar_data).hexdigest()
        
        # Get file list
        files = []
        for root, dirs, filenames in os.walk(app_path):
            for filename in filenames:
                rel_path = os.path.relpath(
                    os.path.join(root, filename), 
                    app_path
                )
                files.append(rel_path)
        
        # Write compressed data to blob
        self.blob_data.write(compressed_data)
        
        # Create metadata
        metadata = AppMetadata(
            key=app_key,
            name=app_name,
            version=version,
            size=uncompressed_size,
            compressed_size=compressed_size,
            offset=self.current_offset,
            dependencies=dependencies,
            files=files,
            checksum=checksum,
            created_at=datetime.now().isoformat()
        )
        
        self.apps[app_key] = metadata
        self.current_offset += compressed_size
        
        compression_ratio = uncompressed_size / compressed_size if compressed_size > 0 else 0
        print(f"  Size: {uncompressed_size:,} → {compressed_size:,} bytes (ratio: {compression_ratio:.2f}x)")
        print(f"  Files: {len(files)}")
        if dependencies:
            print(f"  Dependencies: {', '.join(dependencies)}")
        
        return True
    
    def build(self):
        """Build the final blob file with index."""
        print(f"\n{'='*60}")
        print(f"Building Application Blob: {self.output_path}")
        print(f"{'='*60}")
        
        # Prepare index
        index = {
            'version': self.VERSION,
            'blob_type': 'applications',
            'created_at': datetime.now().isoformat(),
            'apps': {key: asdict(meta) for key, meta in self.apps.items()}
        }
        
        index_json = json.dumps(index, indent=2)
        index_bytes = index_json.encode('utf-8')
        index_size = len(index_bytes)
        
        # Write blob file
        # Format: [MAGIC][VERSION][INDEX_SIZE][INDEX][BLOB_DATA]
        with open(self.output_path, 'wb') as f:
            # Magic bytes
            f.write(self.MAGIC)
            
            # Version (2 bytes)
            f.write(struct.pack('H', self.VERSION))
            
            # Index size (4 bytes)
            f.write(struct.pack('I', index_size))
            
            # Index
            f.write(index_bytes)
            
            # Blob data
            f.write(self.blob_data.getvalue())
        
        total_size = os.path.getsize(self.output_path)
        
        print(f"\n✓ Blob created successfully!")
        print(f"  Total size: {total_size:,} bytes")
        print(f"  Index size: {index_size:,} bytes")
        print(f"  Applications: {len(self.apps)}")
        print(f"  Data size: {self.current_offset:,} bytes")
        
        # Show breakdown
        if self.apps:
            print(f"\n  Application Breakdown:")
            for key, meta in self.apps.items():
                deps = f" → {', '.join(meta.dependencies)}" if meta.dependencies else ""
                print(f"    {key:20s} {meta.compressed_size:>12,} bytes{deps}")
    
    @classmethod
    def load_existing(cls, blob_path: str) -> 'AppBlobBuilder':
        """Load an existing blob for modification."""
        print(f"Loading existing blob: {blob_path}")
        
        if not os.path.exists(blob_path):
            raise FileNotFoundError(f"Blob not found: {blob_path}")
        
        builder = cls(blob_path)
        
        with open(blob_path, 'rb') as f:
            # Read and verify magic bytes
            magic = f.read(8)
            if magic != cls.MAGIC:
                raise ValueError("Invalid blob file format")
            
            # Read version
            version = struct.unpack('H', f.read(2))[0]
            if version != cls.VERSION:
                raise ValueError(f"Unsupported blob version: {version}")
            
            # Read index size
            index_size = struct.unpack('I', f.read(4))[0]
            
            # Read and parse index
            index_bytes = f.read(index_size)
            index_data = json.loads(index_bytes.decode('utf-8'))
            
            # Convert to AppMetadata objects
            for key, meta_dict in index_data['apps'].items():
                builder.apps[key] = AppMetadata(**meta_dict)
            
            # Read blob data
            blob_data = f.read()
            builder.blob_data = BytesIO(blob_data)
            builder.current_offset = len(blob_data)
        
        print(f"  Loaded {len(builder.apps)} applications")
        
        return builder
    
    @classmethod
    def build_from_config(cls, config_path: str, output_path: str):
        """Build blob from YAML config file."""
        print(f"Building from config: {config_path}")
        
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        builder = cls(output_path)
        
        for app_config in config.get('applications', []):
            key = app_config['key']
            name = app_config.get('name', key)
            path = app_config['path']
            version = app_config.get('version', '1.0.0')
            deps = app_config.get('dependencies', [])
            
            if not os.path.exists(path):
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
        
        return builder


class AppBlobExtractor:
    """Extracts applications from a blob file."""
    
    def __init__(self, blob_path: str):
        self.blob_path = blob_path
        self.index: Dict[str, AppMetadata] = {}
        self.data_offset = 0
        
        self._load_index()
    
    def _load_index(self):
        """Load the index from the blob file."""
        with open(self.blob_path, 'rb') as f:
            # Read and verify magic bytes
            magic = f.read(8)
            if magic != AppBlobBuilder.MAGIC:
                raise ValueError("Invalid blob file format")
            
            # Read version
            version = struct.unpack('H', f.read(2))[0]
            if version != AppBlobBuilder.VERSION:
                raise ValueError(f"Unsupported version: {version}")
            
            # Read index size
            index_size = struct.unpack('I', f.read(4))[0]
            
            # Read and parse index
            index_bytes = f.read(index_size)
            index_data = json.loads(index_bytes.decode('utf-8'))
            
            # Convert to AppMetadata objects
            for key, meta_dict in index_data['apps'].items():
                self.index[key] = AppMetadata(**meta_dict)
            
            # Store data offset
            self.data_offset = f.tell()
    
    def list_applications(self) -> List[str]:
        """List all available application keys."""
        return list(self.index.keys())
    
    def get_metadata(self, app_key: str) -> Optional[AppMetadata]:
        """Get metadata for an application."""
        return self.index.get(app_key)
    
    def resolve_dependencies(self, app_keys: List[str]) -> List[str]:
        """Resolve all dependencies for the given app keys."""
        resolved = set()
        to_process = list(app_keys)
        
        while to_process:
            key = to_process.pop(0)
            
            if key in resolved:
                continue
            
            if key not in self.index:
                print(f"Warning: Application '{key}' not found in blob")
                continue
            
            resolved.add(key)
            
            # Add dependencies
            deps = self.index[key].dependencies
            for dep in deps:
                if dep not in resolved:
                    to_process.append(dep)
        
        return list(resolved)
    
    def extract_application(
        self, 
        app_key: str, 
        target_dir: str,
        verify_checksum: bool = True
    ) -> bool:
        """Extract a single application to the target directory."""
        if app_key not in self.index:
            print(f"Error: Application '{app_key}' not found")
            return False
        
        metadata = self.index[app_key]
        
        print(f"Extracting {app_key} ({metadata.name} v{metadata.version})...")
        
        # Read compressed data
        with open(self.blob_path, 'rb') as f:
            f.seek(self.data_offset + metadata.offset)
            compressed_data = f.read(metadata.compressed_size)
        
        # Decompress
        try:
            tar_data = zlib.decompress(compressed_data)
        except zlib.error as e:
            print(f"Error: Failed to decompress {app_key}: {e}")
            return False
        
        # Verify checksum
        if verify_checksum:
            checksum = hashlib.sha256(tar_data).hexdigest()
            if checksum != metadata.checksum:
                print(f"Error: Checksum mismatch for {app_key}")
                return False
        
        # Create target directory
        app_dir = os.path.join(target_dir, app_key)
        os.makedirs(app_dir, exist_ok=True)
        
        # Extract tar archive
        tar_buffer = BytesIO(tar_data)
        with tarfile.open(fileobj=tar_buffer, mode='r') as tar:
            tar.extractall(app_dir)
        
        print(f"  Extracted to: {app_dir}")
        print(f"  Size: {metadata.size:,} bytes ({len(metadata.files)} files)")
        
        return True
    
    def extract_applications(
        self, 
        app_keys: List[str], 
        target_dir: str,
        resolve_deps: bool = True,
        verify_checksums: bool = True
    ) -> Dict[str, bool]:
        """Extract multiple applications with optional dependency resolution."""
        
        # Resolve dependencies
        if resolve_deps:
            keys_to_extract = self.resolve_dependencies(app_keys)
            if len(keys_to_extract) > len(app_keys):
                print(f"Resolved {len(app_keys)} apps to {len(keys_to_extract)} (with dependencies)")
        else:
            keys_to_extract = app_keys
        
        # Extract each application
        results = {}
        for key in keys_to_extract:
            success = self.extract_application(key, target_dir, verify_checksums)
            results[key] = success
        
        # Summary
        successful = sum(1 for v in results.values() if v)
        print(f"\nExtraction complete: {successful}/{len(results)} successful")
        
        return results
    
    def verify_blob(self) -> bool:
        """Verify integrity of all applications in the blob."""
        print(f"Verifying blob: {self.blob_path}")
        
        all_valid = True
        
        for key in self.index.keys():
            metadata = self.index[key]
            
            try:
                # Read compressed data
                with open(self.blob_path, 'rb') as f:
                    f.seek(self.data_offset + metadata.offset)
                    compressed_data = f.read(metadata.compressed_size)
                
                # Decompress
                tar_data = zlib.decompress(compressed_data)
                
                # Verify checksum
                checksum = hashlib.sha256(tar_data).hexdigest()
                if checksum == metadata.checksum:
                    print(f"  ✓ {key}: Valid")
                else:
                    print(f"  ✗ {key}: Checksum mismatch")
                    all_valid = False
            except Exception as e:
                print(f"  ✗ {key}: {e}")
                all_valid = False
        
        return all_valid


def cmd_build(args):
    """Build blob from config file."""
    
    if not os.path.exists(args.config):
        print(f"Error: Config file '{args.config}' not found")
        return 1
    
    AppBlobBuilder.build_from_config(args.config, args.output)
    
    return 0


def cmd_add(args):
    """Add application to blob."""
    
    # Load existing or create new
    if os.path.exists(args.blob):
        builder = AppBlobBuilder.load_existing(args.blob)
    else:
        print(f"Blob not found, creating new: {args.blob}")
        builder = AppBlobBuilder(args.blob)
    
    # Parse dependencies
    dependencies = [d.strip() for d in args.dependencies.split(',')] if args.dependencies else []
    
    success = builder.add_application(
        app_key=args.key,
        app_name=args.name or args.key,
        app_path=args.source,
        version=args.version,
        dependencies=dependencies
    )
    
    if not success:
        return 1
    
    builder.build()
    
    return 0


def cmd_list(args):
    """List applications in blob."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
        return 1
    
    extractor = AppBlobExtractor(args.blob)
    
    print(f"\nApplications in {args.blob}:\n")
    
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


def cmd_info(args):
    """Show detailed info about an application."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
        return 1
    
    extractor = AppBlobExtractor(args.blob)
    meta = extractor.get_metadata(args.app)
    
    if not meta:
        print(f"Error: Application '{args.app}' not found")
        return 1
    
    print(f"\nApplication: {args.app}")
    print(f"{'='*60}")
    print(f"Name:               {meta.name}")
    print(f"Version:            {meta.version}")
    print(f"Size (original):    {meta.size:,} bytes")
    print(f"Size (compressed):  {meta.compressed_size:,} bytes")
    print(f"Compression ratio:  {meta.size / meta.compressed_size:.2f}x")
    print(f"Offset in blob:     {meta.offset:,} bytes")
    print(f"Checksum (SHA256):  {meta.checksum}")
    print(f"Created:            {meta.created_at}")
    
    if meta.dependencies:
        print(f"\nDependencies:")
        for dep in meta.dependencies:
            print(f"  - {dep}")
    
    print(f"\nFiles ({len(meta.files)}):")
    for f in sorted(meta.files)[:20]:
        print(f"  - {f}")
    
    if len(meta.files) > 20:
        print(f"  ... and {len(meta.files) - 20} more")
    
    return 0


def cmd_extract(args):
    """Extract applications from blob."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
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


def cmd_verify(args):
    """Verify blob integrity."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
        return 1
    
    extractor = AppBlobExtractor(args.blob)
    
    if extractor.verify_blob():
        print(f"\n✓ All applications verified successfully")
        return 0
    else:
        print(f"\n✗ Some applications failed verification")
        return 1


def cmd_init(args):
    """Initialize a sample config file."""
    
    config = {
        'applications': [
            {
                'key': 'webserver',
                'name': 'Web Server',
                'path': './apps/webserver',
                'version': '2.1.0',
                'dependencies': []
            },
            {
                'key': 'api-gateway',
                'name': 'API Gateway',
                'path': './apps/api-gateway',
                'version': '1.5.0',
                'dependencies': ['shared-lib']
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
    
    output_file = args.output or 'app-blob-config.yaml'
    
    with open(output_file, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    print(f"Created sample config: {output_file}")
    print("\nEdit this file and then build with:")
    print(f"  python3 app-blob-builder.py build --config {output_file} --output apps.blob")
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Application Blob Builder - All-in-One',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Build command
    build_parser = subparsers.add_parser('build', help='Build blob from config')
    build_parser.add_argument('--config', '-c', required=True, help='YAML config file')
    build_parser.add_argument('--output', '-o', default='apps.blob', help='Output blob file')
    
    # Add command
    add_parser = subparsers.add_parser('add', help='Add application to blob')
    add_parser.add_argument('--blob', '-b', required=True, help='Blob file')
    add_parser.add_argument('--key', '-k', required=True, help='Application key')
    add_parser.add_argument('--name', '-n', help='Application name (defaults to key)')
    add_parser.add_argument('--source', '-s', required=True, help='Source directory')
    add_parser.add_argument('--version', '-v', default='1.0.0', help='Version')
    add_parser.add_argument('--dependencies', '-d', help='Comma-separated dependencies')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List applications in blob')
    list_parser.add_argument('--blob', '-b', required=True, help='Blob file')
    
    # Info command
    info_parser = subparsers.add_parser('info', help='Show application info')
    info_parser.add_argument('--blob', '-b', required=True, help='Blob file')
    info_parser.add_argument('--app', '-a', required=True, help='Application key')
    
    # Extract command
    extract_parser = subparsers.add_parser('extract', help='Extract applications')
    extract_parser.add_argument('--blob', '-b', required=True, help='Blob file')
    extract_parser.add_argument('--apps', '-a', required=True, help='Comma-separated app keys')
    extract_parser.add_argument('--output', '-o', default='./extracted', help='Output directory')
    extract_parser.add_argument('--no-deps', action='store_true', help='Do not resolve dependencies')
    extract_parser.add_argument('--no-verify', action='store_true', help='Skip checksum verification')
    
    # Verify command
    verify_parser = subparsers.add_parser('verify', help='Verify blob integrity')
    verify_parser.add_argument('--blob', '-b', required=True, help='Blob file')
    
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
        'add': cmd_add,
        'list': cmd_list,
        'info': cmd_info,
        'extract': cmd_extract,
        'verify': cmd_verify,
        'init': cmd_init
    }
    
    return commands[args.command](args)


if __name__ == '__main__':
    sys.exit(main())