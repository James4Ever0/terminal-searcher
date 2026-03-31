#!/usr/bin/env python3
"""Test script for BM25SQLiteIndexAsync functionality."""

import asyncio
import tempfile
import shutil
from pathlib import Path

from flashback_terminal.bm25_index import BM25SQLiteIndexAsync


async def test_basic_functionality():
    """Test basic BM25 index functionality."""
    # Test database path
    db_path = "./test_bm25_index.db"
    
    # Clean up any existing test database
    if Path(db_path).exists():
        Path(db_path).unlink()
    
    print("Testing BM25SQLiteIndexAsync...")
    
    # Test 1: Initialize index
    print("\n1. Testing index initialization...")
    async with BM25SQLiteIndexAsync(db_path) as index:
        assert index.num_docs == 0
        print("✓ Index initialized successfully")
        
        # Test 2: Add single document
        print("\n2. Testing single document addition...")
        await index.add_document("doc1", "hello world this is a test")
        assert index.num_docs == 1
        assert index.exists("doc1")
        print("✓ Single document added successfully")
        
        # Test 3: Add multiple documents
        print("\n3. Testing batch document addition...")
        documents = [
            ("doc2", "the quick brown fox jumps over the lazy dog"),
            ("doc3", "python programming language tutorial"),
            ("doc4", "machine learning algorithms and data science"),
            ("doc5", "web development with html css javascript")
        ]
        await index.add_documents(documents)
        assert index.num_docs == 5
        assert all(index.exists(doc_id) for doc_id, _ in documents)
        print("✓ Batch documents added successfully")
        
        # Test 4: Test basic queries
        print("\n4. Testing basic search queries...")
        
        # Query for "python"
        results = await index.query("python", top_n=3)
        assert len(results) > 0
        assert results[0][0] == "doc3"  # Should find doc3 first
        print(f"✓ Query 'python': {results}")
        
        # Query for "fox"
        results = await index.query("fox", top_n=3)
        assert len(results) > 0
        assert results[0][0] == "doc2"  # Should find doc2 first
        print(f"✓ Query 'fox': {results}")
        
        # Query for "test"
        results = await index.query("test", top_n=3)
        assert len(results) > 0
        assert results[0][0] == "doc1"  # Should find doc1 first
        print(f"✓ Query 'test': {results}")
        
        # Test 5: Test multi-term queries
        print("\n5. Testing multi-term queries...")
        results = await index.query("machine learning", top_n=3)
        assert len(results) > 0
        assert results[0][0] == "doc4"  # Should find doc4 first
        print(f"✓ Query 'machine learning': {results}")
        
        # Test 6: Test duplicate prevention
        print("\n6. Testing duplicate document prevention...")
        original_count = index.num_docs
        await index.add_document("doc1", "this should not be added")
        await index.add_documents([("doc2", "neither should this")])
        assert index.num_docs == original_count
        print("✓ Duplicate documents prevented successfully")
        
        # Test 7: Test custom tokenizer
        print("\n7. Testing custom tokenizer...")
        custom_index = BM25SQLiteIndexAsync("./test_custom_tokenizer.db", tokenizer=lambda x: x.lower().split())
        await custom_index.initialize()
        await custom_index.add_document("custom1", "Hello WORLD Test")
        results = await custom_index.query("hello", top_n=5)
        assert len(results) > 0
        print(f"✓ Custom tokenizer works: {results}")
        await custom_index.close()
        
        print("\n✅ All tests passed successfully!")


async def test_persistence():
    """Test that index persists across sessions."""
    print("\n" + "="*50)
    print("Testing persistence...")
    
    db_path = "./test_persistence.db"
    
    # Clean up any existing test database
    if Path(db_path).exists():
        Path(db_path).unlink()
    
    # Session 1: Add data
    print("\n1. Session 1: Adding data...")
    async with BM25SQLiteIndexAsync(db_path) as index:
        await index.add_document("persist1", "persistent data test")
        await index.add_document("persist2", "another persistent document")
        assert index.num_docs == 2
        print("✓ Data added in session 1")
    
    # Session 2: Verify data persists
    print("\n2. Session 2: Verifying persistence...")
    async with BM25SQLiteIndexAsync(db_path) as index:
        assert index.num_docs == 2
        assert index.exists("persist1")
        assert index.exists("persist2")
        
        results = await index.query("persistent", top_n=5)
        assert len(results) == 2
        print("✓ Data persisted across sessions")
    
    print("\n✅ Persistence test passed!")


async def test_clear_functionality():
    """Test clearing all documents."""
    print("\n" + "="*50)
    print("Testing clear functionality...")
    
    db_path = "./test_clear.db"
    
    # Clean up any existing test database
    if Path(db_path).exists():
        Path(db_path).unlink()
    
    # Add data and clear
    async with BM25SQLiteIndexAsync(db_path) as index:
        await index.add_documents([
            ("clear1", "document to clear"),
            ("clear2", "another document to clear"),
            ("clear3", "third document to clear")
        ])
        assert index.num_docs == 3
        
        # Test clear
        await index.clear_all()
        assert index.num_docs == 0
        assert not index.exists("clear1")
        assert not index.exists("clear2")
        assert not index.exists("clear3")
        
        # Test query after clear
        results = await index.query("document", top_n=5)
        assert len(results) == 0
        
        print("✓ Clear functionality works correctly")


async def main():
    """Run all tests."""
    print("Starting BM25SQLiteIndexAsync tests...")
    
    try:
        await test_basic_functionality()
        await test_persistence()
        await test_clear_functionality()
        
        print("\n" + "="*50)
        print("🎉 ALL TESTS PASSED! 🎉")
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    finally:
        # Clean up test databases
        test_dbs = [
            "./test_bm25_index.db",
            "./test_custom_tokenizer.db", 
            "./test_persistence.db",
            "./test_clear.db"
        ]
        
        for db in test_dbs:
            if Path(db).exists():
                Path(db).unlink()
                print(f"Cleaned up {db}")
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
