#!/usr/bin/env python3
"""
Binary Blob Builder

Creates a blob file containing platform binaries and system libraries.
Supports selective extraction based on application requirements.

Usage:
    # Create new blob
    python3 binary-blob-builder.py create --output binaries.blob

    # Add binary package
    python3 binary-blob-builder.py add \
        --blob binaries.blob \
        --key python-3.11 \
        --source /usr/local/python-3.11/ \
        --provides python3,pip3 \
        --env PATH=/opt/binaries/python-3.11/bin

    # Add from config file
    python3 binary-blob-builder.py build-from-config \
        --config binaries.yaml \
        --output binaries.blob

    # List binaries
    python3 binary-blob-builder.py list --blob binaries.blob

    # Show dependencies
    python3 binary-blob-builder.py deps --blob binaries.blob --key python-3.11
"""

import os
import sys
import json
import zlib
import yaml
import tarfile
import hashlib
import argparse
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set
from io import BytesIO
import struct
import subprocess


class BinaryBlobBuilder:
    """Builds and manages binary blobs."""
    
    MAGIC = b'BINBLOB1'
    VERSION = 1
    
    def __init__(self, output_path: str):
        self.output_path = output_path
        self.binaries: Dict[str, dict] = {}
        self.blob_data = BytesIO()
        self.current_offset = 0
    
    def add_binary(
        self,
        key: str,
        source_path: str,
        provides: List[str],
        version: str = "1.0.0",
        description: str = "",
        env_vars: Optional[Dict[str, str]] = None,
        dependencies: Optional[List[str]] = None,
        architecture: str = "x86_64",
        os_type: str = "linux"
    ):
        """Add a binary package to the blob."""
        
        if key in self.binaries:
            print(f"Warning: Binary '{key}' already exists. Skipping.")
            return False
        
        print(f"\nAdding binary package: {key}")
        print(f"  Source: {source_path}")
        
        if not os.path.exists(source_path):
            print(f"  Error: Source path not found")
            return False
        
        # Validate provides list
        if not provides:
            print(f"  Warning: No executables specified in 'provides'")
        
        # Create tar archive
        tar_buffer = BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
            tar.add(source_path, arcname=key)
        
        tar_data = tar_buffer.getvalue()
        uncompressed_size = len(tar_data)
        
        # Compress
        compressed_data = zlib.compress(tar_data, level=6)
        compressed_size = len(compressed_data)
        
        # Calculate checksum
        checksum = hashlib.sha256(tar_data).hexdigest()
        
        # Detect executables and libraries
        executables = []
        libraries = []
        
        for root, dirs, files in os.walk(source_path):
            for filename in files:
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, source_path)
                
                # Check if executable
                if os.access(full_path, os.X_OK) and not filename.endswith('.so'):
                    executables.append(rel_path)
                
                # Check if library
                if filename.endswith('.so') or '.so.' in filename:
                    libraries.append(rel_path)
        
        # Write compressed data to blob
        self.blob_data.write(compressed_data)
        
        # Create metadata
        metadata = {
            'key': key,
            'version': version,
            'description': description,
            'size': uncompressed_size,
            'compressed_size': compressed_size,
            'offset': self.current_offset,
            'checksum': checksum,
            'provides': provides,
            'executables': executables,
            'libraries': libraries,
            'dependencies': dependencies or [],
            'env_vars': env_vars or {},
            'architecture': architecture,
            'os_type': os_type,
            'created_at': datetime.now().isoformat()
        }
        
        self.binaries[key] = metadata
        self.current_offset += compressed_size
        
        compression_ratio = uncompressed_size / compressed_size if compressed_size > 0 else 0
        print(f"  Size: {uncompressed_size:,} → {compressed_size:,} bytes (ratio: {compression_ratio:.2f}x)")
        print(f"  Provides: {', '.join(provides)}")
        print(f"  Executables: {len(executables)}")
        print(f"  Libraries: {len(libraries)}")
        
        if dependencies:
            print(f"  Dependencies: {', '.join(dependencies)}")
        
        return True
    
    def build(self):
        """Build the final blob file."""
        
        print(f"\n{'='*60}")
        print(f"Building Binary Blob: {self.output_path}")
        print(f"{'='*60}")
        
        # Prepare index
        index = {
            'version': self.VERSION,
            'blob_type': 'binaries',
            'created_at': datetime.now().isoformat(),
            'binary_count': len(self.binaries),
            'binaries': self.binaries
        }
        
        index_json = json.dumps(index, indent=2)
        index_bytes = index_json.encode('utf-8')
        index_size = len(index_bytes)
        
        # Write blob file
        with open(self.output_path, 'wb') as f:
            # Magic bytes
            f.write(self.MAGIC)
            
            # Version
            f.write(struct.pack('H', self.VERSION))
            
            # Index size
            f.write(struct.pack('I', index_size))
            
            # Index
            f.write(index_bytes)
            
            # Blob data
            f.write(self.blob_data.getvalue())
        
        total_size = os.path.getsize(self.output_path)
        
        print(f"\n✓ Blob created successfully!")
        print(f"  Total size: {total_size:,} bytes")
        print(f"  Index size: {index_size:,} bytes")
        print(f"  Binaries: {len(self.binaries)}")
        
        # Show breakdown
        if self.binaries:
            print(f"\n  Binary Breakdown:")
            for key, meta in self.binaries.items():
                provides_str = ', '.join(meta['provides'][:3])
                if len(meta['provides']) > 3:
                    provides_str += f" (+{len(meta['provides'])-3} more)"
                print(f"    {key:20s} {meta['compressed_size']:>12,} bytes  [{provides_str}]")
    
    @classmethod
    def load_existing(cls, blob_path: str) -> 'BinaryBlobBuilder':
        """Load an existing blob."""
        
        print(f"Loading existing blob: {blob_path}")
        
        if not os.path.exists(blob_path):
            raise FileNotFoundError(f"Blob not found: {blob_path}")
        
        builder = cls(blob_path)
        
        with open(blob_path, 'rb') as f:
            # Read magic
            magic = f.read(8)
            if magic != cls.MAGIC:
                raise ValueError("Invalid blob file format")
            
            # Read version
            version = struct.unpack('H', f.read(2))[0]
            if version != cls.VERSION:
                raise ValueError(f"Unsupported version: {version}")
            
            # Read index
            index_size = struct.unpack('I', f.read(4))[0]
            index_bytes = f.read(index_size)
            index_data = json.loads(index_bytes.decode('utf-8'))
            
            builder.binaries = index_data['binaries']
            
            # Read blob data
            blob_data = f.read()
            builder.blob_data = BytesIO(blob_data)
            builder.current_offset = len(blob_data)
        
        print(f"  Loaded {len(builder.binaries)} binaries")
        
        return builder
    
    def list_binaries(self):
        """List all binaries in the blob."""
        
        print(f"\nBinaries in blob: {len(self.binaries)}")
        print(f"{'='*80}")
        
        for key, meta in sorted(self.binaries.items()):
            print(f"\nKey:         {key}")
            print(f"  Version:   {meta['version']}")
            print(f"  Size:      {meta['size']:,} bytes (compressed: {meta['compressed_size']:,})")
            print(f"  Provides:  {', '.join(meta['provides'])}")
            print(f"  Arch:      {meta['architecture']}")
            print(f"  OS:        {meta['os_type']}")
            
            if meta['description']:
                print(f"  Desc:      {meta['description']}")
            
            if meta['dependencies']:
                print(f"  Depends:   {', '.join(meta['dependencies'])}")
    
    def get_binary_info(self, key: str):
        """Get detailed info about a binary."""
        
        if key not in self.binaries:
            print(f"Error: Binary '{key}' not found")
            return None
        
        meta = self.binaries[key]
        
        print(f"\nBinary: {key}")
        print(f"{'='*60}")
        print(f"Version:             {meta['version']}")
        print(f"Size (uncompressed): {meta['size']:,} bytes")
        print(f"Size (compressed):   {meta['compressed_size']:,} bytes")
        print(f"Compression ratio:   {meta['size']/meta['compressed_size']:.2f}x")
        print(f"Checksum (SHA256):   {meta['checksum']}")
        print(f"Architecture:        {meta['architecture']}")
        print(f"OS:                  {meta['os_type']}")
        print(f"Created:             {meta['created_at']}")
        
        if meta['description']:
            print(f"Description:         {meta['description']}")
        
        print(f"\nProvides ({len(meta['provides'])}):")
        for exe in meta['provides']:
            print(f"  - {exe}")
        
        if meta['dependencies']:
            print(f"\nDependencies:")
            for dep in meta['dependencies']:
                print(f"  - {dep}")
        
        if meta['env_vars']:
            print(f"\nEnvironment Variables:")
            for var, value in meta['env_vars'].items():
                print(f"  {var}={value}")
        
        if meta['executables']:
            print(f"\nExecutables ({len(meta['executables'])}):")
            for exe in sorted(meta['executables'])[:20]:
                print(f"  - {exe}")
            if len(meta['executables']) > 20:
                print(f"  ... and {len(meta['executables']) - 20} more")
        
        if meta['libraries']:
            print(f"\nLibraries ({len(meta['libraries'])}):")
            for lib in sorted(meta['libraries'])[:20]:
                print(f"  - {lib}")
            if len(meta['libraries']) > 20:
                print(f"  ... and {len(meta['libraries']) - 20} more")
        
        return meta
    
    def resolve_dependencies(self, keys: List[str]) -> List[str]:
        """Resolve all dependencies for given binaries."""
        
        resolved = set()
        to_process = list(keys)
        
        while to_process:
            key = to_process.pop(0)
            
            if key in resolved:
                continue
            
            if key not in self.binaries:
                print(f"Warning: Binary '{key}' not found")
                continue
            
            resolved.add(key)
            
            # Add dependencies
            deps = self.binaries[key].get('dependencies', [])
            for dep in deps:
                if dep not in resolved:
                    to_process.append(dep)
        
        return list(resolved)
    
    def show_dependency_tree(self, key: str, indent: int = 0):
        """Show dependency tree for a binary."""
        
        if key not in self.binaries:
            print(f"{'  ' * indent}✗ {key} (not found)")
            return
        
        meta = self.binaries[key]
        provides_str = ', '.join(meta['provides'][:2])
        
        print(f"{'  ' * indent}• {key} v{meta['version']} [{provides_str}]")
        
        for dep in meta.get('dependencies', []):
            self.show_dependency_tree(dep, indent + 1)
    
    @classmethod
    def build_from_config(cls, config_path: str, output_path: str):
        """Build blob from YAML config file."""
        
        print(f"Building from config: {config_path}")
        
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        builder = cls(output_path)
        
        for binary_config in config.get('binaries', []):
            key = binary_config['key']
            source = binary_config['source']
            provides = binary_config.get('provides', [])
            version = binary_config.get('version', '1.0.0')
            description = binary_config.get('description', '')
            env_vars = binary_config.get('env', {})
            dependencies = binary_config.get('dependencies', [])
            architecture = binary_config.get('architecture', 'x86_64')
            os_type = binary_config.get('os', 'linux')
            
            builder.add_binary(
                key=key,
                source_path=source,
                provides=provides,
                version=version,
                description=description,
                env_vars=env_vars,
                dependencies=dependencies,
                architecture=architecture,
                os_type=os_type
            )
        
        builder.build()
        
        return builder


def detect_binary_metadata(source_path: str) -> dict:
    """Auto-detect metadata about a binary package."""
    
    print(f"\nAnalyzing: {source_path}")
    
    metadata = {
        'executables': [],
        'libraries': [],
        'provides': []
    }
    
    # Find executables
    bin_dirs = ['bin', 'sbin', 'usr/bin', 'usr/sbin']
    for bin_dir in bin_dirs:
        full_path = os.path.join(source_path, bin_dir)
        if os.path.exists(full_path):
            for f in os.listdir(full_path):
                full_file = os.path.join(full_path, f)
                if os.path.isfile(full_file) and os.access(full_file, os.X_OK):
                    metadata['executables'].append(f)
                    metadata['provides'].append(f)
    
    # Find libraries
    lib_dirs = ['lib', 'lib64', 'usr/lib', 'usr/lib64']
    for lib_dir in lib_dirs:
        full_path = os.path.join(source_path, lib_dir)
        if os.path.exists(full_path):
            for root, dirs, files in os.walk(full_path):
                for f in files:
                    if f.endswith('.so') or '.so.' in f:
                        metadata['libraries'].append(f)
    
    print(f"  Found {len(metadata['executables'])} executables")
    print(f"  Found {len(metadata['libraries'])} libraries")
    
    return metadata


def cmd_create(args):
    """Create new empty blob."""
    
    if os.path.exists(args.output) and not args.force:
        print(f"Error: File '{args.output}' exists. Use --force to overwrite.")
        return 1
    
    builder = BinaryBlobBuilder(args.output)
    builder.build()
    
    return 0


def cmd_add(args):
    """Add binary to blob."""
    
    # Load existing or create new
    if os.path.exists(args.blob):
        builder = BinaryBlobBuilder.load_existing(args.blob)
    else:
        print(f"Blob not found, creating new: {args.blob}")
        builder = BinaryBlobBuilder(args.blob)
    
    # Parse provides
    provides = [p.strip() for p in args.provides.split(',')] if args.provides else []
    
    # Parse env vars
    env_vars = {}
    if args.env:
        for env_str in args.env:
            if '=' in env_str:
                key, value = env_str.split('=', 1)
                env_vars[key] = value
    
    # Parse dependencies
    dependencies = [d.strip() for d in args.dependencies.split(',')] if args.dependencies else []
    
    # Auto-detect if requested
    if args.auto_detect:
        print("\nAuto-detecting binary metadata...")
        detected = detect_binary_metadata(args.source)
        if not provides:
            provides = detected['provides'][:10]  # Limit to top 10
            print(f"  Detected provides: {', '.join(provides)}")
    
    success = builder.add_binary(
        key=args.key,
        source_path=args.source,
        provides=provides,
        version=args.version,
        description=args.description or "",
        env_vars=env_vars,
        dependencies=dependencies,
        architecture=args.architecture,
        os_type=args.os
    )
    
    if not success:
        return 1
    
    builder.build()
    
    return 0


def cmd_list(args):
    """List binaries in blob."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
        return 1
    
    builder = BinaryBlobBuilder.load_existing(args.blob)
    builder.list_binaries()
    
    return 0


def cmd_info(args):
    """Show binary info."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
        return 1
    
    builder = BinaryBlobBuilder.load_existing(args.blob)
    result = builder.get_binary_info(args.key)
    
    return 0 if result else 1


def cmd_deps(args):
    """Show dependency tree."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
        return 1
    
    builder = BinaryBlobBuilder.load_existing(args.blob)
    
    print(f"\nDependency tree for: {args.key}")
    print(f"{'='*60}")
    builder.show_dependency_tree(args.key)
    
    # Show flat list
    resolved = builder.resolve_dependencies([args.key])
    print(f"\nResolved dependencies ({len(resolved)} total):")
    for dep in resolved:
        print(f"  - {dep}")
    
    return 0


def cmd_build_from_config(args):
    """Build from YAML config."""
    
    if not os.path.exists(args.config):
        print(f"Error: Config not found: {args.config}")
        return 1
    
    BinaryBlobBuilder.build_from_config(args.config, args.output)
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Binary Blob Builder',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    subparsers = parser.add_subparsers(dest='command')
    
    # Create
    create_parser = subparsers.add_parser('create', help='Create new blob')
    create_parser.add_argument('--output', '-o', required=True)
    create_parser.add_argument('--force', '-f', action='store_true')
    
    # Add
    add_parser = subparsers.add_parser('add', help='Add binary')
    add_parser.add_argument('--blob', '-b', required=True)
    add_parser.add_argument('--key', '-k', required=True)
    add_parser.add_argument('--source', '-s', required=True)
    add_parser.add_argument('--provides', '-p', help='Comma-separated list of executables')
    add_parser.add_argument('--version', '-v', default='1.0.0')
    add_parser.add_argument('--description', '-d')
    add_parser.add_argument('--env', '-e', action='append', help='ENV=value (can be repeated)')
    add_parser.add_argument('--dependencies', help='Comma-separated dependencies')
    add_parser.add_argument('--architecture', default='x86_64')
    add_parser.add_argument('--os', default='linux')
    add_parser.add_argument('--auto-detect', '-a', action='store_true')
    
    # List
    list_parser = subparsers.add_parser('list', help='List binaries')
    list_parser.add_argument('--blob', '-b', required=True)
    
    # Info
    info_parser = subparsers.add_parser('info', help='Show binary info')
    info_parser.add_argument('--blob', '-b', required=True)
    info_parser.add_argument('--key', '-k', required=True)
    
    # Deps
    deps_parser = subparsers.add_parser('deps', help='Show dependencies')
    deps_parser.add_argument('--blob', '-b', required=True)
    deps_parser.add_argument('--key', '-k', required=True)
    
    # Build from config
    config_parser = subparsers.add_parser('build-from-config', help='Build from YAML')
    config_parser.add_argument('--config', '-c', required=True)
    config_parser.add_argument('--output', '-o', required=True)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    commands = {
        'create': cmd_create,
        'add': cmd_add,
        'list': cmd_list,
        'info': cmd_info,
        'deps': cmd_deps,
        'build-from-config': cmd_build_from_config
    }
    
    return commands[args.command](args)


if __name__ == '__main__':
    sys.exit(main())