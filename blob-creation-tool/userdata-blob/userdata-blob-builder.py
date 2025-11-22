#!/usr/bin/env python3
"""
User Data Blob Builder

Creates a blob file containing user data with read-write support.
Supports incremental updates, user isolation, and checkpoint/restore.

Usage:
    # Create new blob
    python3 userdata-blob-builder.py create --output userdata.blob

    # Add user data
    python3 userdata-blob-builder.py add-user \
        --blob userdata.blob \
        --user-id user123 \
        --source ./user123_data/

    # Update user data
    python3 userdata-blob-builder.py update-user \
        --blob userdata.blob \
        --user-id user123 \
        --source ./user123_data/ \
        --mode merge

    # List users
    python3 userdata-blob-builder.py list --blob userdata.blob

    # Create checkpoint
    python3 userdata-blob-builder.py checkpoint \
        --blob userdata.blob \
        --output userdata-checkpoint-2024.blob
"""

import os
import sys
import json
import zlib
import tarfile
import hashlib
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from io import BytesIO
import struct


class UserDataBlobBuilder:
    """Builds and manages user data blobs with read-write support."""
    
    MAGIC = b'USERBLOB'
    VERSION = 1
    
    def __init__(self, output_path: str):
        self.output_path = output_path
        self.users: Dict[str, dict] = {}
        self.blob_data = BytesIO()
        self.current_offset = 0
    
    def add_user(
        self, 
        user_id: str,
        source_path: str,
        description: str = "",
        quota_mb: Optional[int] = None
    ):
        """Add a user's data to the blob."""
        
        if user_id in self.users:
            print(f"Warning: User '{user_id}' already exists. Use update-user instead.")
            return False
        
        print(f"\nAdding user: {user_id}")
        print(f"  Source: {source_path}")
        
        if not os.path.exists(source_path):
            print(f"  Error: Source path not found")
            return False
        
        # Create tar archive of user data
        tar_buffer = BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
            tar.add(source_path, arcname='.')
        
        tar_data = tar_buffer.getvalue()
        uncompressed_size = len(tar_data)
        
        # Compress
        compressed_data = zlib.compress(tar_data, level=6)
        compressed_size = len(compressed_data)
        
        # Calculate checksum
        checksum = hashlib.sha256(tar_data).hexdigest()
        
        # Count files
        file_count = sum(1 for root, dirs, files in os.walk(source_path) for f in files)
        
        # Get file list with sizes
        files = []
        for root, dirs, filenames in os.walk(source_path):
            for filename in filenames:
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, source_path)
                size = os.path.getsize(full_path)
                files.append({
                    'path': rel_path,
                    'size': size
                })
        
        # Write compressed data to blob
        self.blob_data.write(compressed_data)
        
        # Create user metadata
        user_metadata = {
            'user_id': user_id,
            'description': description,
            'size': uncompressed_size,
            'compressed_size': compressed_size,
            'offset': self.current_offset,
            'file_count': file_count,
            'files': files,
            'checksum': checksum,
            'quota_mb': quota_mb,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
            'version': 1
        }
        
        self.users[user_id] = user_metadata
        self.current_offset += compressed_size
        
        compression_ratio = uncompressed_size / compressed_size if compressed_size > 0 else 0
        print(f"  Size: {uncompressed_size:,} → {compressed_size:,} bytes (ratio: {compression_ratio:.2f}x)")
        print(f"  Files: {file_count}")
        print(f"  Checksum: {checksum[:16]}...")
        
        return True
    
    def update_user(
        self,
        user_id: str,
        source_path: str,
        mode: str = 'replace'
    ):
        """
        Update a user's data in the blob.
        
        Modes:
            replace: Replace entire user data
            merge: Merge changes (for incremental updates)
        """
        
        if user_id not in self.users:
            print(f"Error: User '{user_id}' not found. Use add-user instead.")
            return False
        
        print(f"\nUpdating user: {user_id} (mode: {mode})")
        
        if mode == 'replace':
            # Remove old entry and add new
            old_offset = self.users[user_id]['offset']
            old_size = self.users[user_id]['compressed_size']
            
            # Remove old data (by not including it in rebuild)
            del self.users[user_id]
            
            # Add new data
            return self.add_user(user_id, source_path)
        
        elif mode == 'merge':
            # For merge mode, we need to track changes
            # This is a simplified version - production would use delta encoding
            print(f"  Warning: Merge mode creates a new version")
            
            old_version = self.users[user_id].get('version', 1)
            del self.users[user_id]
            
            result = self.add_user(user_id, source_path)
            if result:
                self.users[user_id]['version'] = old_version + 1
            
            return result
        
        else:
            print(f"Error: Unknown mode '{mode}'")
            return False
    
    def remove_user(self, user_id: str):
        """Remove a user from the blob."""
        
        if user_id not in self.users:
            print(f"Error: User '{user_id}' not found")
            return False
        
        print(f"\nRemoving user: {user_id}")
        
        # In a rebuild, we'll skip this user's data
        del self.users[user_id]
        
        print(f"  User marked for removal")
        print(f"  Note: Run 'build' to compact the blob")
        
        return True
    
    def build(self):
        """Build the final blob file with index."""
        
        print(f"\n{'='*60}")
        print(f"Building User Data Blob: {self.output_path}")
        print(f"{'='*60}")
        
        # Prepare index
        index = {
            'version': self.VERSION,
            'blob_type': 'userdata',
            'created_at': datetime.now().isoformat(),
            'user_count': len(self.users),
            'users': self.users
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
        data_size = self.current_offset
        
        print(f"\n✓ Blob created successfully!")
        print(f"  Total size: {total_size:,} bytes")
        print(f"  Index size: {index_size:,} bytes")
        print(f"  Data size: {data_size:,} bytes")
        print(f"  Users: {len(self.users)}")
        
        # Show per-user breakdown
        if self.users:
            print(f"\n  User Breakdown:")
            for user_id, meta in self.users.items():
                print(f"    {user_id:20s} {meta['compressed_size']:>12,} bytes  ({meta['file_count']} files)")
    
    @classmethod
    def load_existing(cls, blob_path: str) -> 'UserDataBlobBuilder':
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
            
            builder.users = index_data['users']
            
            # Read blob data
            blob_data = f.read()
            builder.blob_data = BytesIO(blob_data)
            builder.current_offset = len(blob_data)
        
        print(f"  Loaded {len(builder.users)} users")
        
        return builder
    
    def list_users(self):
        """List all users in the blob."""
        
        print(f"\nUsers in blob: {len(self.users)}")
        print(f"{'='*80}")
        
        for user_id, meta in sorted(self.users.items()):
            quota = f"{meta['quota_mb']} MB" if meta.get('quota_mb') else "Unlimited"
            
            print(f"\nUser ID:     {user_id}")
            print(f"  Size:      {meta['size']:,} bytes (compressed: {meta['compressed_size']:,})")
            print(f"  Files:     {meta['file_count']}")
            print(f"  Version:   {meta.get('version', 1)}")
            print(f"  Quota:     {quota}")
            print(f"  Created:   {meta['created_at']}")
            print(f"  Updated:   {meta['updated_at']}")
            
            if meta.get('description'):
                print(f"  Desc:      {meta['description']}")
    
    def get_user_info(self, user_id: str):
        """Get detailed info about a specific user."""
        
        if user_id not in self.users:
            print(f"Error: User '{user_id}' not found")
            return None
        
        meta = self.users[user_id]
        
        print(f"\nUser: {user_id}")
        print(f"{'='*60}")
        print(f"Size (uncompressed): {meta['size']:,} bytes")
        print(f"Size (compressed):   {meta['compressed_size']:,} bytes")
        print(f"Compression ratio:   {meta['size']/meta['compressed_size']:.2f}x")
        print(f"File count:          {meta['file_count']}")
        print(f"Checksum (SHA256):   {meta['checksum']}")
        print(f"Blob offset:         {meta['offset']:,} bytes")
        print(f"Created:             {meta['created_at']}")
        print(f"Updated:             {meta['updated_at']}")
        
        if meta.get('description'):
            print(f"Description:         {meta['description']}")
        
        if meta.get('quota_mb'):
            print(f"Quota:               {meta['quota_mb']} MB")
        
        # Show top 10 largest files
        if meta.get('files'):
            print(f"\nTop 10 Largest Files:")
            sorted_files = sorted(meta['files'], key=lambda x: x['size'], reverse=True)[:10]
            for f in sorted_files:
                print(f"  {f['size']:>12,} bytes  {f['path']}")
        
        return meta
    
    def create_checkpoint(self, checkpoint_path: str):
        """Create a checkpoint (snapshot) of the current blob."""
        
        print(f"\nCreating checkpoint: {checkpoint_path}")
        
        import shutil
        shutil.copy2(self.output_path, checkpoint_path)
        
        checkpoint_size = os.path.getsize(checkpoint_path)
        print(f"  ✓ Checkpoint created: {checkpoint_size:,} bytes")
        
        return True


def cmd_create(args):
    """Create a new empty blob."""
    
    if os.path.exists(args.output) and not args.force:
        print(f"Error: File '{args.output}' already exists. Use --force to overwrite.")
        return 1
    
    builder = UserDataBlobBuilder(args.output)
    
    print(f"Creating new user data blob: {args.output}")
    
    # Create empty blob
    builder.build()
    
    return 0


def cmd_add_user(args):
    """Add a user to the blob."""
    
    # Load existing blob or create new
    if os.path.exists(args.blob):
        builder = UserDataBlobBuilder.load_existing(args.blob)
    else:
        print(f"Blob not found, creating new: {args.blob}")
        builder = UserDataBlobBuilder(args.blob)
    
    # Add user
    success = builder.add_user(
        args.user_id,
        args.source,
        description=args.description or "",
        quota_mb=args.quota
    )
    
    if not success:
        return 1
    
    # Rebuild blob
    builder.build()
    
    return 0


def cmd_update_user(args):
    """Update a user in the blob."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
        return 1
    
    builder = UserDataBlobBuilder.load_existing(args.blob)
    
    success = builder.update_user(
        args.user_id,
        args.source,
        mode=args.mode
    )
    
    if not success:
        return 1
    
    # Rebuild blob
    builder.build()
    
    return 0


def cmd_remove_user(args):
    """Remove a user from the blob."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
        return 1
    
    builder = UserDataBlobBuilder.load_existing(args.blob)
    
    success = builder.remove_user(args.user_id)
    
    if not success:
        return 1
    
    # Rebuild blob
    builder.build()
    
    return 0


def cmd_list(args):
    """List users in the blob."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
        return 1
    
    builder = UserDataBlobBuilder.load_existing(args.blob)
    builder.list_users()
    
    return 0


def cmd_info(args):
    """Show info about a specific user."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
        return 1
    
    builder = UserDataBlobBuilder.load_existing(args.blob)
    result = builder.get_user_info(args.user_id)
    
    return 0 if result else 1


def cmd_checkpoint(args):
    """Create a checkpoint of the blob."""
    
    if not os.path.exists(args.blob):
        print(f"Error: Blob not found: {args.blob}")
        return 1
    
    # Auto-generate checkpoint name if not provided
    if not args.output:
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        base = os.path.splitext(args.blob)[0]
        args.output = f"{base}-checkpoint-{timestamp}.blob"
    
    builder = UserDataBlobBuilder.load_existing(args.blob)
    builder.create_checkpoint(args.output)
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='User Data Blob Builder',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Create command
    create_parser = subparsers.add_parser('create', help='Create new empty blob')
    create_parser.add_argument('--output', '-o', required=True, help='Output blob file')
    create_parser.add_argument('--force', '-f', action='store_true', help='Overwrite if exists')
    
    # Add user command
    add_parser = subparsers.add_parser('add-user', help='Add user to blob')
    add_parser.add_argument('--blob', '-b', required=True, help='Blob file')
    add_parser.add_argument('--user-id', '-u', required=True, help='User ID')
    add_parser.add_argument('--source', '-s', required=True, help='Source directory')
    add_parser.add_argument('--description', '-d', help='User description')
    add_parser.add_argument('--quota', '-q', type=int, help='Quota in MB')
    
    # Update user command
    update_parser = subparsers.add_parser('update-user', help='Update user in blob')
    update_parser.add_argument('--blob', '-b', required=True, help='Blob file')
    update_parser.add_argument('--user-id', '-u', required=True, help='User ID')
    update_parser.add_argument('--source', '-s', required=True, help='Source directory')
    update_parser.add_argument('--mode', '-m', choices=['replace', 'merge'], 
                               default='replace', help='Update mode')
    
    # Remove user command
    remove_parser = subparsers.add_parser('remove-user', help='Remove user from blob')
    remove_parser.add_argument('--blob', '-b', required=True, help='Blob file')
    remove_parser.add_argument('--user-id', '-u', required=True, help='User ID')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List users in blob')
    list_parser.add_argument('--blob', '-b', required=True, help='Blob file')
    
    # Info command
    info_parser = subparsers.add_parser('info', help='Show user info')
    info_parser.add_argument('--blob', '-b', required=True, help='Blob file')
    info_parser.add_argument('--user-id', '-u', required=True, help='User ID')
    
    # Checkpoint command
    checkpoint_parser = subparsers.add_parser('checkpoint', help='Create checkpoint')
    checkpoint_parser.add_argument('--blob', '-b', required=True, help='Blob file')
    checkpoint_parser.add_argument('--output', '-o', help='Checkpoint file (auto-generated if not provided)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Dispatch to command handler
    commands = {
        'create': cmd_create,
        'add-user': cmd_add_user,
        'update-user': cmd_update_user,
        'remove-user': cmd_remove_user,
        'list': cmd_list,
        'info': cmd_info,
        'checkpoint': cmd_checkpoint
    }
    
    return commands[args.command](args)


if __name__ == '__main__':
    sys.exit(main())