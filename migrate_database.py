#!/usr/bin/env python3
"""
OpenDeep Database Migration Tool

Usage: python migrate_database.py

This script updates your OpenDeep database to support HITL (Human-in-the-Loop) features.
It safely adds the required columns to your existing tasks table.
"""

import sqlite3
import sys
import os
from pathlib import Path
from datetime import datetime
import shutil

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from config.app_config import app_config
from database.db_manager import get_db_connection

def create_backup():
    """Create a backup of the database before migration"""
    try:
        db_file = app_config.DATABASE_URL.split("sqlite:///./")[-1]
        backup_file = f"{db_file}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        shutil.copy2(db_file, backup_file)
        print(f"✅ Database backup created: {backup_file}")
        return backup_file
    except Exception as e:
        print(f"❌ Failed to create backup: {e}")
        return None

def check_schema():
    """Check current database schema and return migration status"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if tasks table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
        if not cursor.fetchone():
            print("❌ Tasks table not found. Please run main.py first to initialize the database.")
            return False, []
        
        # Check current columns
        cursor.execute("PRAGMA table_info(tasks)")
        columns = [column[1] for column in cursor.fetchall()]
        
        # Required HITL columns
        required_columns = {
            'user_info_request': 'TEXT',
            'user_info_response': 'TEXT', 
            'user_info_timeout': 'INTEGER DEFAULT 10',
            'user_info_requested_at': 'TIMESTAMP'
        }
        
        missing_columns = []
        for col_name, col_def in required_columns.items():
            if col_name not in columns:
                missing_columns.append((col_name, col_def))
        
        print(f"📊 Current tasks table has {len(columns)} columns")
        print(f"🔍 Missing HITL columns: {len(missing_columns)}")
        
        conn.close()
        return True, missing_columns
        
    except Exception as e:
        print(f"❌ Error checking schema: {e}")
        return False, []

def apply_migration(missing_columns):
    """Apply the migration to add missing columns"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        print(f"\n🔧 Adding {len(missing_columns)} missing columns...")
        
        for col_name, col_def in missing_columns:
            print(f"  Adding column: {col_name}")
            alter_sql = f"ALTER TABLE tasks ADD COLUMN {col_name} {col_def}"
            cursor.execute(alter_sql)
        
        conn.commit()
        
        # Verify the migration
        cursor.execute("PRAGMA table_info(tasks)")
        new_columns = [column[1] for column in cursor.fetchall()]
        
        print(f"✅ Migration completed successfully!")
        print(f"📊 Tasks table now has {len(new_columns)} columns")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return False

def main():
    print("🚀 OpenDeep Database Migration Tool")
    print("=" * 50)
    
    # Check if database exists
    db_file = app_config.DATABASE_URL.split("sqlite:///./")[-1]
    if not os.path.exists(db_file):
        print(f"❌ Database not found: {db_file}")
        print("Please run 'python main.py' first to initialize the database.")
        return
    
    print(f"📁 Database file: {db_file}")
    
    # Check current schema
    schema_ok, missing_columns = check_schema()
    if not schema_ok:
        return
    
    if not missing_columns:
        print("✅ Database is already up to date! No migration needed.")
        return
    
    print(f"\n⚠️  Migration Required:")
    print("The following columns will be added to the tasks table:")
    for col_name, col_def in missing_columns:
        print(f"  - {col_name} ({col_def})")
    
    # Ask for confirmation
    print(f"\n🔄 This will modify your database.")
    response = input("Do you want to proceed? (y/N): ").strip().lower()
    
    if response != 'y':
        print("Migration cancelled.")
        return
    
    # Create backup
    print(f"\n📋 Creating backup...")
    backup_file = create_backup()
    if not backup_file:
        print("❌ Could not create backup. Migration cancelled for safety.")
        return
    
    # Apply migration
    print(f"\n🔧 Applying migration...")
    if apply_migration(missing_columns):
        print(f"\n🎉 Migration completed successfully!")
        print(f"💾 Your original database was backed up to: {backup_file}")
        print(f"🚀 You can now run 'python main.py' to use HITL features.")
    else:
        print(f"\n❌ Migration failed!")
        print(f"💾 Your original database is backed up at: {backup_file}")
        print(f"🔄 You can restore it by copying {backup_file} to {db_file}")

if __name__ == "__main__":
    main()