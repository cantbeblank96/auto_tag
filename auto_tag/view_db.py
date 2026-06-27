import os
import json
import argparse
from pprint import pprint
from auto_tag.core.vector_db import VectorDB
from auto_tag.core.config import settings

def view_database(output_path=None):
    print(f"Connecting to vector index at: {settings.db_path}...")
    db = VectorDB(db_path=settings.db_path, collection_name=settings.collection_name)
    
    total_count = db.count()
    print(f"Total documents in database: {total_count}")
    
    if total_count == 0:
        print("Database is empty.")
        return

    # Fetch all records
    results = db.collection.get(
        include=["metadatas"]
    )
    
    metadatas = results.get("metadatas", [])
    
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(metadatas, f, ensure_ascii=False, indent=2)
        print(f"Successfully exported {total_count} records to {output_path}")
    else:
        print("\n" + "="*50)
        print("📋 Current Database Records:")
        print("="*50)
        
        for i, meta in enumerate(metadatas):
            print(f"\n[{i+1}/{total_count}] Document metadata:")
            pprint(meta)
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="View or export database contents")
    parser.add_argument("--output_path", type=str, help="Path to export results as a JSON file")
    args = parser.parse_args()
    view_database(args.output_path)
