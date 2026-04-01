#!/usr/bin/env python3
"""
Comprehensive test suite for AsyncHNSWIndex (async.py)
Tests all async functionality, concurrency, and performance.
"""

import asyncio
import os
import shutil
import numpy as np
import tempfile
import logging
import time
import sys

# Add current directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from async import AsyncHNSWIndex, create_async_hnsw_index

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TestAsyncHNSWIndex:
    """Test class for AsyncHNSWIndex functionality"""
    
    def __init__(self):
        self.test_dir = None
        self.index = None
        self.dim = 64
        self.initial_max_elements = 10
        
    async def setup(self):
        """Setup async test environment"""
        self.test_dir = tempfile.mkdtemp(prefix="async_hnsw_test_")
        logger.info(f"Created test directory: {self.test_dir}")
        
        # Create async index
        self.index = AsyncHNSWIndex(max_workers=4)
        await self.index.initialize(
            db_path=self.test_dir,
            dim=self.dim,
            space="l2",
            max_elements=self.initial_max_elements,
            min_automatic_expansion_batch=5
        )
        logger.info(f"Created AsyncHNSWIndex with dim={self.dim}, max_elements={self.initial_max_elements}")
        
    async def teardown(self):
        """Cleanup async test environment"""
        if self.index:
            await self.index.close()
        
        if self.test_dir and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
            logger.info(f"Cleaned up test directory: {self.test_dir}")
            
    async def test_initial_state(self):
        """Test initial state of the async index"""
        logger.info("=== Testing Async Initial State ===")
        
        # Test empty index
        count = await self.index.__len__()
        assert count == 0, f"Expected empty index, got {count} items"
        logger.info(f"✓ Empty async index: {count} items")
        
        # Test get_all_ids on empty index
        ids = await self.index.get_all_ids()
        assert len(ids) == 0, f"Expected no IDs, got {len(ids)}"
        logger.info(f"✓ get_all_ids() on empty async index: {ids}")
        
        # Test contains on empty index
        contains = await self.index.__contains__(1)
        assert not contains, "Empty index should not contain ID 1"
        logger.info("✓ contains() works on empty async index")
        
        # Test properties
        assert self.index.dim == self.dim
        assert self.index.max_elements == self.initial_max_elements
        logger.info(f"✓ Properties accessible: dim={self.index.dim}, max_elements={self.index.max_elements}")
        
    async def test_async_insert_batch(self):
        """Test async batch insertion"""
        logger.info("=== Testing Async Batch Insertion ===")
        
        # Create test data
        batch_size = 20
        ids = list(range(batch_size))
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        
        logger.info(f"Async inserting {batch_size} vectors with IDs {ids}")
        
        start_time = time.time()
        await self.index.insert_batch(ids, vectors)
        insert_time = time.time() - start_time
        
        # Verify insertion
        count = await self.index.__len__()
        assert count == batch_size, f"Expected {batch_size} items, got {count}"
        logger.info(f"✓ Async index now contains {count} items (took {insert_time:.3f}s)")
        
        # Verify all IDs are present
        all_ids = await self.index.get_all_ids()
        for id in ids:
            contains = await self.index.__contains__(id)
            assert contains, f"ID {id} not found in index"
        logger.info(f"✓ All IDs {ids} are present in async index")
        
        # Test async get_batch
        retrieved = await self.index.get_batch(ids)
        assert len(retrieved) == batch_size, f"Expected {batch_size} retrieved vectors, got {len(retrieved)}"
        
        # Verify vector integrity
        for i, id in enumerate(ids):
            original_vector = vectors[i]
            retrieved_vector = retrieved[id]
            np.testing.assert_array_almost_equal(original_vector, retrieved_vector, decimal=6)
            logger.info(f"✓ Vector {id} integrity verified")
            
    async def test_async_auto_expansion(self):
        """Test async automatic index expansion"""
        logger.info("=== Testing Async Auto Expansion ===")
        
        initial_max = self.index.max_elements
        logger.info(f"Initial max_elements: {initial_max}")
        
        # Insert more items than initial capacity to trigger expansion
        batch_size = 25  # More than initial_max_elements (10)
        ids = list(range(100, 100 + batch_size))  # Use different ID range
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        
        logger.info(f"Async attempting to insert {batch_size} items (exceeds capacity {initial_max})")
        
        start_time = time.time()
        await self.index.insert_batch(ids, vectors)
        expansion_time = time.time() - start_time
        
        # Verify expansion occurred
        new_max = self.index.max_elements
        assert new_max > initial_max, f"Expected expansion from {initial_max}, got {new_max}"
        logger.info(f"✓ Async index expanded from {initial_max} to {new_max} (took {expansion_time:.3f}s)")
        
        # Verify all items were inserted
        count = await self.index.__len__()
        assert count == batch_size, f"Expected {batch_size} items after expansion, got {count}"
        logger.info(f"✓ All {batch_size} items successfully inserted after async expansion")
        
    async def test_async_search(self):
        """Test async search functionality"""
        logger.info("=== Testing Async Search ===")
        
        # Insert test data
        num_items = 50
        ids = list(range(200, 200 + num_items))
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        
        # Insert a known query vector for testing
        query_vector = np.random.randn(self.dim).astype(np.float32)
        vectors[0] = query_vector  # Make first item identical to query
        ids[0] = 999  # Use distinctive ID
        
        await self.index.insert_batch(ids, vectors)
        logger.info(f"Async inserted {num_items} vectors for search testing")
        
        # Test basic async search
        k = 5
        start_time = time.time()
        labels, distances = await self.index.search(query_vector, k=k)
        search_time = time.time() - start_time
        
        assert len(labels) == k, f"Expected {k} search results, got {len(labels)}"
        assert len(distances) == k, f"Expected {k} distances, got {len(distances)}"
        logger.info(f"✓ Async search returned {k} results (took {search_time:.3f}s)")
        
        # The identical vector should be the closest (distance ≈ 0)
        assert labels[0] == 999, f"Expected ID 999 as closest match, got {labels[0]}"
        assert distances[0] < 1e-6, f"Expected near-zero distance for identical vector, got {distances[0]}"
        logger.info(f"✓ Identical vector correctly identified as closest match (ID: {labels[0]}, distance: {distances[0]:.6f})")
        
        # Test async search with list input
        query_list = query_vector.tolist()
        labels_list, distances_list = await self.index.search(query_list, k=k)
        assert len(labels_list) == k, f"Expected {k} results with list input, got {len(labels_list)}"
        logger.info(f"✓ Async search with list input works")
        
    async def test_async_batch_search(self):
        """Test async batch search functionality"""
        logger.info("=== Testing Async Batch Search ===")
        
        # Insert test data
        num_items = 100
        ids = list(range(300, 300 + num_items))
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        await self.index.insert_batch(ids, vectors)
        
        # Create multiple query vectors
        num_queries = 10
        query_vectors = [np.random.randn(self.dim).astype(np.float32) for _ in range(num_queries)]
        
        logger.info(f"Async batch searching {num_queries} queries")
        
        start_time = time.time()
        batch_results = await self.index.batch_search(query_vectors, k=3)
        batch_time = time.time() - start_time
        
        assert len(batch_results) == num_queries, f"Expected {num_queries} results, got {len(batch_results)}"
        for i, (labels, distances) in enumerate(batch_results):
            assert len(labels) == 3, f"Expected 3 results for query {i}, got {len(labels)}"
            assert len(distances) == 3, f"Expected 3 distances for query {i}, got {len(distances)}"
        
        logger.info(f"✓ Async batch search completed for {num_queries} queries (took {batch_time:.3f}s)")
        
    async def test_async_delete_operations(self):
        """Test async delete operations"""
        logger.info("=== Testing Async Delete Operations ===")
        
        # Insert test data
        num_items = 15
        ids = list(range(400, 400 + num_items))
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        
        await self.index.insert_batch(ids, vectors)
        logger.info(f"Async inserted {num_items} vectors for delete testing")
        
        initial_count = await self.index.__len__()
        logger.info(f"Initial count: {initial_count}")
        
        # Test async single delete
        delete_id = ids[2]
        start_time = time.time()
        deleted_count = await self.index.delete(delete_id)
        delete_time = time.time() - start_time
        
        assert deleted_count == 1, f"Expected to delete 1 item, got {deleted_count}"
        logger.info(f"✓ Async deleted single item ID {delete_id} (took {delete_time:.3f}s)")
        
        # Test async batch delete
        delete_ids = [ids[0], ids[1], ids[3], ids[4]]
        start_time = time.time()
        batch_deleted = await self.index.delete_batch(delete_ids)
        batch_time = time.time() - start_time
        
        assert batch_deleted == len(delete_ids), f"Expected to delete {len(delete_ids)} items, got {batch_deleted}"
        logger.info(f"✓ Async batch deleted {batch_deleted} items: {delete_ids} (took {batch_time:.3f}s)")
        
    async def test_async_get_operations(self):
        """Test async get operations"""
        logger.info("=== Testing Async Get Operations ===")
        
        # Insert test data
        num_items = 10
        ids = list(range(500, 500 + num_items))
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        
        await self.index.insert_batch(ids, vectors)
        
        # Test async single get
        start_time = time.time()
        retrieved_vector = await self.index.get(ids[5])
        get_time = time.time() - start_time
        
        np.testing.assert_array_almost_equal(vectors[5], retrieved_vector, decimal=6)
        logger.info(f"✓ Async get single vector completed (took {get_time:.3f}s)")
        
        # Test async batch get
        get_ids = ids[:5]
        start_time = time.time()
        batch_retrieved = await self.index.get_batch(get_ids)
        batch_time = time.time() - start_time
        
        assert len(batch_retrieved) == len(get_ids), f"Expected {len(get_ids)} vectors, got {len(batch_retrieved)}"
        for i, id in enumerate(get_ids):
            np.testing.assert_array_almost_equal(vectors[i], batch_retrieved[id], decimal=6)
        logger.info(f"✓ Async batch get completed for {len(get_ids)} vectors (took {batch_time:.3f}s)")
        
        # Test get non-existent ID
        try:
            await self.index.get(99999)
            assert False, "Should have raised KeyError for non-existent ID"
        except KeyError:
            logger.info("✓ Async get correctly handles non-existent ID")
            
    async def test_async_concurrency(self):
        """Test async concurrent operations"""
        logger.info("=== Testing Async Concurrency ===")
        
        # Insert initial data
        num_items = 50
        ids = list(range(600, 600 + num_items))
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        await self.index.insert_batch(ids, vectors)
        
        # Run multiple operations concurrently
        query_vectors = [np.random.randn(self.dim).astype(np.float32) for _ in range(5)]
        
        start_time = time.time()
        
        # Concurrent searches
        search_tasks = [
            self.index.search(query_vector, k=3) 
            for query_vector in query_vectors
        ]
        
        # Concurrent gets
        get_tasks = [
            self.index.get(ids[i]) 
            for i in [0, 10, 20, 30, 40]
        ]
        
        # Concurrent contains checks
        contains_tasks = [
            self.index.__contains__(ids[i]) 
            for i in range(0, 20, 4)
        ]
        
        # Wait for all tasks to complete
        all_tasks = search_tasks + get_tasks + contains_tasks
        results = await asyncio.gather(*all_tasks)
        
        concurrent_time = time.time() - start_time
        
        # Verify results
        search_results = results[:5]
        get_results = results[5:10]
        contains_results = results[10:]
        
        assert all(len(labels) == 3 for labels, _ in search_results), "All searches should return 3 results"
        assert all(len(vec) == self.dim for vec in get_results), "All get results should have correct dimension"
        assert all(isinstance(result, bool) for result in contains_results), "All contains results should be boolean"
        
        logger.info(f"✓ Async concurrency test completed: {len(all_tasks)} operations in {concurrent_time:.3f}s")
        
    async def test_async_persistence(self):
        """Test async persistence and loading"""
        logger.info("=== Testing Async Persistence ===")
        
        # Insert test data
        num_items = 12
        ids = list(range(700, 700 + num_items))
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        
        await self.index.insert_batch(ids, vectors)
        original_max_elements = self.index.max_elements
        logger.info(f"Async inserted {num_items} items, max_elements: {original_max_elements}")
        
        # Delete some items
        await self.index.delete(ids[3])
        logger.info(f"Async deleted item ID {ids[3]}")
        
        # Force save
        await self.index.save()
        logger.info("Async forced explicit save")
        
        # Close current index
        await self.index.close()
        logger.info("Async closed current index instance")
        
        # Create new index and load from disk
        self.index = AsyncHNSWIndex(max_workers=4)
        await self.index.initialize(
            db_path=self.test_dir,
            dim=self.dim,
            space="l2"
        )
        logger.info("Async loaded index from disk")
        
        # Verify loaded data
        count = await self.index.__len__()
        logger.info(f"✓ Async loaded index contains {count} items")
        
        # Verify max_elements persisted
        assert self.index.max_elements == original_max_elements
        logger.info(f"✓ max_elements persisted: {self.index.max_elements}")
        
        # Verify vector integrity
        retrieved = await self.index.get_batch([ids[0], ids[1]])
        np.testing.assert_array_almost_equal(vectors[0], retrieved[ids[0]], decimal=6)
        np.testing.assert_array_almost_equal(vectors[1], retrieved[ids[1]], decimal=6)
        logger.info("✓ Vector integrity verified after async load")
        
    async def test_async_resize(self):
        """Test async manual resize"""
        logger.info("=== Testing Async Resize ===")
        
        initial_max = self.index.max_elements
        new_max = initial_max + 100
        
        start_time = time.time()
        await self.index.resize(new_max)
        resize_time = time.time() - start_time
        
        assert self.index.max_elements == new_max
        logger.info(f"✓ Async resize from {initial_max} to {new_max} completed (took {resize_time:.3f}s)")
        
    async def test_convenience_function(self):
        """Test the convenience function for creating async index"""
        logger.info("=== Testing Convenience Function ===")
        
        test_path = tempfile.mkdtemp(prefix="convenience_test_")
        
        try:
            # Use convenience function
            async_index = await create_async_hnsw_index(
                db_path=test_path,
                dim=32,
                space="l2",
                max_elements=20,
                max_workers=2
            )
            
            # Test basic functionality
            ids = [1, 2, 3]
            vectors = [np.random.randn(32).astype(np.float32) for _ in ids]
            await async_index.insert_batch(ids, vectors)
            
            count = await async_index.__len__()
            assert count == 3, f"Expected 3 items, got {count}"
            
            await async_index.close()
            logger.info("✓ Convenience function works correctly")
            
        finally:
            if os.path.exists(test_path):
                shutil.rmtree(test_path)
                
    async def test_context_manager(self):
        """Test async context manager"""
        logger.info("=== Testing Context Manager ===")
        
        test_path = tempfile.mkdtemp(prefix="context_test_")
        
        try:
            async with AsyncHNSWIndex() as index:
                await index.initialize(
                    db_path=test_path,
                    dim=16,
                    space="l2",
                    max_elements=5
                )
                
                # Test operations
                await index.insert(1, np.random.randn(16).astype(np.float32))
                count = await index.__len__()
                assert count == 1
                
            # Index should be automatically closed after context
            logger.info("✓ Context manager works correctly")
            
        finally:
            if os.path.exists(test_path):
                shutil.rmtree(test_path)
                
    async def run_all_tests(self):
        """Run all async tests"""
        logger.info("Starting comprehensive AsyncHNSWIndex tests...")
        
        try:
            await self.setup()
            
            # Run all test methods
            test_methods = [
                self.test_initial_state,
                self.test_async_insert_batch,
                self.test_async_auto_expansion,
                self.test_async_search,
                self.test_async_batch_search,
                self.test_async_delete_operations,
                self.test_async_get_operations,
                self.test_async_concurrency,
                self.test_async_persistence,
                self.test_async_resize,
                self.test_convenience_function,
                self.test_context_manager,
            ]
            
            for test_method in test_methods:
                try:
                    await test_method()
                    logger.info(f"✓ {test_method.__name__} PASSED")
                except Exception as e:
                    logger.error(f"✗ {test_method.__name__} FAILED: {e}")
                    raise
            
            logger.info("🎉 ALL ASYNC TESTS PASSED!")
            
        except Exception as e:
            logger.error(f"Async test suite failed: {e}")
            raise
        finally:
            await self.teardown()


async def main():
    """Main async test runner"""
    tester = TestAsyncHNSWIndex()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
