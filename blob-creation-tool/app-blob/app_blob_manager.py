#!/usr/bin/env python3
"""
Application Blob Manager

Manages a compressed blob of applications that can be mounted to containers
and selectively extracted based on application keys.

Design Goals:
- Single blob file mountable as read-only volume
- Fast selective extraction of specific apps
- Minimal container startup overhead
- Support for shared dependencies
"""

import os
import sys
import json
import zlib
import tarfile
import hashlib
from pathlib import Path
from typing import List, Dict, Set, Optional
from dataclasses import dataclass, asdict
from io import BytesIO
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


class AppBlobBuilder:
    """Builds a blob file containing multiple applications."""
    
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
        dependencies: List[str] = None
    ):
        """Add an application directory to the blob."""
        dependencies = dependencies or []
        
        print(f"Adding application: {app_key} ({app_name} v{version})")
        
        # Create a tar archive of the application
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
            checksum=checksum
        )
        
        self.apps[app_key] = metadata
        self.current_offset += compressed_size
        
        compression_ratio = uncompressed_size / compressed_size if compressed_size > 0 else 0
        print(f"  Compressed: {uncompressed_size:,} → {compressed_size:,} bytes "
              f"(ratio: {compression_ratio:.2f}x)")
        print(f"  Files: {len(files)}")
    
    def build(self):
        """Build the final blob file with index."""
        print(f"\nBuilding blob file: {self.output_path}")
        
        # Prepare index
        index = {
            'version': '1.0',
            'apps': {key: asdict(meta) for key, meta in self.apps.items()}
        }
        
        index_json = json.dumps(index, indent=2)
        index_bytes = index_json.encode('utf-8')
        index_size = len(index_bytes)
        
        # Write blob file
        # Format: [MAGIC][INDEX_SIZE][INDEX][BLOB_DATA]
        with open(self.output_path, 'wb') as f:
            # Magic bytes
            f.write(b'APPBLOB1')
            
            # Index size (4 bytes)
            f.write(struct.pack('I', index_size))
            
            # Index
            f.write(index_bytes)
            
            # Blob data
            f.write(self.blob_data.getvalue())
        
        total_size = os.path.getsize(self.output_path)
        print(f"Blob created successfully!")
        print(f"  Total size: {total_size:,} bytes")
        print(f"  Index size: {index_size:,} bytes")
        print(f"  Applications: {len(self.apps)}")
        print(f"  Data size: {self.current_offset:,} bytes")


class AppBlobExtractor:
    """Extracts applications from a blob file."""
    
    def __init__(self, blob_path: str):
        self.blob_path = blob_path
        self.index: Dict[str, AppMetadata] = {}
        self.blob_file = None
        self.data_offset = 0
        
        self._load_index()
    
    def _load_index(self):
        """Load the index from the blob file."""
        with open(self.blob_path, 'rb') as f:
            # Read and verify magic bytes
            magic = f.read(8)
            if magic != b'APPBLOB1':
                raise ValueError("Invalid blob file format")
            
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
    
    def extract_to_memory(self, app_key: str) -> Optional[bytes]:
        """Extract application data to memory without writing to disk."""
        if app_key not in self.index:
            return None
        
        metadata = self.index[app_key]
        
        with open(self.blob_path, 'rb') as f:
            f.seek(self.data_offset + metadata.offset)
            compressed_data = f.read(metadata.compressed_size)
        
        return zlib.decompress(compressed_data)


def create_sample_apps():
    """Create sample application directories for testing."""
    
    # App 1: Web Server
    os.makedirs('sample_apps/webserver/bin', exist_ok=True)
    os.makedirs('sample_apps/webserver/config', exist_ok=True)
    
    with open('sample_apps/webserver/bin/server.py', 'w') as f:
        f.write("""#!/usr/bin/env python3
import http.server
import socketserver

PORT = 8080
Handler = http.server.SimpleHTTPServer

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Server running on port {PORT}")
    httpd.serve_forever()
""")
    
    with open('sample_apps/webserver/config/server.conf', 'w') as f:
        f.write("port=8080\nhost=0.0.0.0\n")
    
    with open('sample_apps/webserver/README.md', 'w') as f:
        f.write("# Web Server\n\nSimple HTTP server application.\n")
    
    # App 2: Database Client (depends on shared-lib)
    os.makedirs('sample_apps/db-client/bin', exist_ok=True)
    
    with open('sample_apps/db-client/bin/client.py', 'w') as f:
        f.write("""#!/usr/bin/env python3
import sqlite3

def main():
    conn = sqlite3.connect(':memory:')
    print("Database client initialized")
    conn.close()

if __name__ == '__main__':
    main()
""")
    
    with open('sample_apps/db-client/README.md', 'w') as f:
        f.write("# Database Client\n\nConnects to databases.\n")
    
    # App 3: Shared Library
    os.makedirs('sample_apps/shared-lib/lib', exist_ok=True)
    
    with open('sample_apps/shared-lib/lib/utils.py', 'w') as f:
        f.write("""def log(message):
    print(f"[LOG] {message}")

def validate(data):
    return data is not None
""")
    
    with open('sample_apps/shared-lib/README.md', 'w') as f:
        f.write("# Shared Library\n\nCommon utilities.\n")
    
    # App 4: API Gateway (depends on shared-lib)
    os.makedirs('sample_apps/api-gateway/bin', exist_ok=True)
    
    with open('sample_apps/api-gateway/bin/gateway.py', 'w') as f:
        f.write("""#!/usr/bin/env python3
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "API Gateway"

if __name__ == '__main__':
    app.run(port=8000)
""")
    
    with open('sample_apps/api-gateway/README.md', 'w') as f:
        f.write("# API Gateway\n\nRoutes API requests.\n")


def main():
    """Demo the blob system."""
    
    print("=" * 70)
    print("Application Blob Manager - Demo")
    print("=" * 70)
    
    # Create sample apps
    print("\n1. Creating sample applications...")
    create_sample_apps()
    
    # Build blob
    print("\n2. Building application blob...")
    builder = AppBlobBuilder('apps.blob')
    
    builder.add_application(
        'shared-lib',
        'Shared Library',
        'sample_apps/shared-lib',
        version='1.0.0'
    )
    
    builder.add_application(
        'webserver',
        'Web Server',
        'sample_apps/webserver',
        version='2.1.0'
    )
    
    builder.add_application(
        'db-client',
        'Database Client',
        'sample_apps/db-client',
        version='1.5.2',
        dependencies=['shared-lib']
    )
    
    builder.add_application(
        'api-gateway',
        'API Gateway',
        'sample_apps/api-gateway',
        version='3.0.0',
        dependencies=['shared-lib']
    )
    
    builder.build()
    
    # Extract specific apps
    print("\n3. Extracting applications...")
    extractor = AppBlobExtractor('apps.blob')
    
    print("\nAvailable applications:")
    for key in extractor.list_applications():
        meta = extractor.get_metadata(key)
        deps = f" (depends on: {', '.join(meta.dependencies)})" if meta.dependencies else ""
        print(f"  - {key}: {meta.name} v{meta.version}{deps}")
    
    print("\n4. Extracting 'db-client' and 'webserver' with dependencies...")
    os.makedirs('extracted_apps', exist_ok=True)
    
    results = extractor.extract_applications(
        ['db-client', 'webserver'],
        'extracted_apps',
        resolve_deps=True
    )
    
    print("\n5. Verifying extracted files...")
    for app_key, success in results.items():
        if success:
            app_path = os.path.join('extracted_apps', app_key)
            file_count = sum(len(files) for _, _, files in os.walk(app_path))
            print(f"  ✓ {app_key}: {file_count} files extracted")


if __name__ == '__main__':
    main()