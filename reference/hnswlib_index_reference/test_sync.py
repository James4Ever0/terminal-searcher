#!/usr/bin/env python3
"""
Comprehensive test suite for HNSWIndex (sync.py)
Tests auto expansion, search, CRUD operations, and persistence.
"""

import os
import shutil
import numpy as np
import tempfile
import logging
import sys

# Add current directory to path to import sync module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hnswlib_index_sync import HNSWIndex

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TestHNSWIndex:
    """Test class for HNSWIndex functionality"""
    
    def __init__(self):
        self.test_dir = None
        self.index = None
        self.dim = 64
        self.initial_max_elements = 10
        
    def setup(self):
        """Setup test environment"""
        self.test_dir = tempfile.mkdtemp(prefix="hnsw_test_")
        logger.info(f"Created test directory: {self.test_dir}")
        
        # Create index with small initial size to test expansion
        self.index = HNSWIndex(
            db_path=self.test_dir,
            dim=self.dim,
            space="l2",
            max_elements=self.initial_max_elements,
            min_automatic_expansion_batch=5
        )
        logger.info(f"Created HNSWIndex with dim={self.dim}, max_elements={self.initial_max_elements}")
        
    def teardown(self):
        """Cleanup test environment"""
        if self.test_dir and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
            logger.info(f"Cleaned up test directory: {self.test_dir}")
            
    def test_initial_state(self):
        """Test initial state of the index"""
        logger.info("=== Testing Initial State ===")
        
        # Test empty index
        assert len(self.index) == 0, f"Expected empty index, got {len(self.index)} items"
        logger.info(f"✓ Empty index: {len(self.index)} items")
        
        # Test get_all_ids on empty index
        ids = self.index.get_all_ids()
        assert len(ids) == 0, f"Expected no IDs, got {len(ids)}"
        logger.info(f"✓ get_all_ids() on empty index: {ids}")
        
        # Test contains on empty index
        assert 1 not in self.index, "Empty index should not contain ID 1"
        logger.info("✓ contains() works on empty index")
        
        # Test max_elements
        assert self.index.max_elements == self.initial_max_elements
        logger.info(f"✓ Initial max_elements: {self.index.max_elements}")
        
    def test_insert_batch_basic(self):
        """Test basic batch insertion"""
        logger.info("=== Testing Basic Batch Insertion ===")
        
        # Create test data
        batch_size = 5
        ids = list(range(batch_size))
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        
        logger.info(f"Inserting {batch_size} vectors with IDs {ids}")
        
        # Insert batch
        self.index.insert_batch(ids, vectors)
        
        # Verify insertion
        assert len(self.index) == batch_size, f"Expected {batch_size} items, got {len(self.index)}"
        logger.info(f"✓ Index now contains {len(self.index)} items")
        
        # Verify all IDs are present
        all_ids = self.index.get_all_ids()
        for id in ids:
            assert id in all_ids, f"ID {id} not found in index"
            assert id in self.index, f"ID {id} not found via contains()"
        logger.info(f"✓ All IDs {ids} are present in index")
        
        # Test get_batch
        retrieved = self.index.get_batch(ids)
        assert len(retrieved) == batch_size, f"Expected {batch_size} retrieved vectors, got {len(retrieved)}"
        
        # Verify vector integrity
        for i, id in enumerate(ids):
            original_vector = vectors[i]
            retrieved_vector = retrieved[id]
            np.testing.assert_array_almost_equal(original_vector, retrieved_vector, decimal=6)
            logger.info(f"✓ Vector {id} integrity verified")
            
    def test_auto_expansion(self):
        """Test automatic index expansion"""
        logger.info("=== Testing Auto Expansion ===")
        
        initial_max = self.index.max_elements
        logger.info(f"Initial max_elements: {initial_max}")
        
        # Insert more items than initial capacity to trigger expansion
        batch_size = 15  # More than initial_max_elements (10)
        ids = list(range(100, 100 + batch_size))  # Use different ID range
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        
        logger.info(f"Attempting to insert {batch_size} items (exceeds capacity {initial_max})")
        
        # This should trigger auto expansion
        self.index.insert_batch(ids, vectors)
        
        # Verify expansion occurred
        new_max = self.index.max_elements
        assert new_max > initial_max, f"Expected expansion from {initial_max}, got {new_max}"
        logger.info(f"✓ Index expanded from {initial_max} to {new_max}")
        
        # Verify all items were inserted
        assert len(self.index) == batch_size+5, f"Expected {batch_size+5} items after expansion, got {len(self.index)}"
        logger.info(f"✓ All {batch_size} items successfully inserted after expansion")
        
        # Verify expansion size is at least min_automatic_expansion_batch
        expected_min_expansion = initial_max + 5  # min_automatic_expansion_batch = 5
        assert new_max >= expected_min_expansion, f"Expected at least {expected_min_expansion}, got {new_max}"
        logger.info(f"✓ Expansion size meets minimum requirement (≥{expected_min_expansion})")
        
    def test_search_functionality(self):
        """Test search functionality"""
        logger.info("=== Testing Search Functionality ===")
        
        # Insert test data
        num_items = 20
        ids = list(range(200, 200 + num_items))
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        
        # Insert a known query vector for testing
        query_vector = np.random.randn(self.dim).astype(np.float32)
        vectors[0] = query_vector  # Make first item identical to query
        ids[0] = 999  # Use distinctive ID
        
        self.index.insert_batch(ids, vectors)
        logger.info(f"Inserted {num_items} vectors for search testing")
        
        # Test basic search
        k = 5
        labels, distances = self.index.search(query_vector, k=k)
        
        assert len(labels) == k, f"Expected {k} search results, got {len(labels)}"
        assert len(distances) == k, f"Expected {k} distances, got {len(distances)}"
        logger.info(f"✓ Search returned {k} results")
        
        # The identical vector should be the closest (distance ≈ 0)
        assert labels[0] == 999, f"Expected ID 999 as closest match, got {labels[0]}"
        assert distances[0] < 1e-6, f"Expected near-zero distance for identical vector, got {distances[0]}"
        logger.info(f"✓ Identical vector correctly identified as closest match (ID: {labels[0]}, distance: {distances[0]:.6f})")
        
        # Test search with custom ef
        labels_ef, distances_ef = self.index.search(query_vector, k=k, ef=100)
        assert len(labels_ef) == k, f"Expected {k} results with custom ef, got {len(labels_ef)}"
        logger.info(f"✓ Search with custom ef=100 works")
        
        # Test search with list input
        query_list = query_vector.tolist()
        labels_list, distances_list = self.index.search(query_list, k=k)
        assert len(labels_list) == k, f"Expected {k} results with list input, got {len(labels_list)}"
        logger.info(f"✓ Search with list input works")
        
    def test_delete_operations(self):
        """Test delete operations"""
        logger.info("=== Testing Delete Operations ===")
        
        # Insert test data
        num_items = 10
        ids = list(range(300, 300 + num_items))
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        
        self.index.insert_batch(ids, vectors)
        logger.info(f"Inserted {num_items} vectors for delete testing")
        
        initial_count = len(self.index)
        logger.info(f"Initial count: {initial_count}")
        
        # Test single delete
        delete_id = ids[2]
        deleted_count = self.index.delete(delete_id)
        assert deleted_count == 1, f"Expected to delete 1 item, got {deleted_count}"
        logger.info(f"✓ Deleted single item ID {delete_id}")
        
        # Verify deletion
        try:
            deleted_elem = self.index.get(delete_id)
        except KeyError:
            deleted_elem = None
        assert not deleted_elem, f"ID {delete_id} should not be retrievable after deletion"
        logger.info(f"✓ ID {delete_id} no longer in index")
        
        # Test batch delete
        delete_ids = [ids[0], ids[1], ids[3], ids[4]]
        batch_deleted = self.index.delete_batch(delete_ids)
        assert batch_deleted == len(delete_ids), f"Expected to delete {len(delete_ids)} items, got {batch_deleted}"
        logger.info(f"✓ Batch deleted {batch_deleted} items: {delete_ids}")
        
        # Verify batch deletion
        for delete_id in delete_ids:
            try:
                deleted_item = self.index.get(delete_id)
            except KeyError:
                deleted_item = None
            assert not deleted_item, f"ID {delete_id} should not be in index after batch deletion"
        logger.info("✓ All batch deleted IDs verified")
        
        # Test delete non-existent ID
        non_existent_deleted = self.index.delete(99999)
        assert non_existent_deleted == 0, f"Expected 0 deletions for non-existent ID, got {non_existent_deleted}"
        logger.info("✓ Delete non-existent ID returns 0")
        
        # Test batch delete with mixed existent/non-existent IDs
        mixed_ids = [ids[5], 99998, ids[6], 99999]
        mixed_deleted = self.index.delete_batch(mixed_ids)
        assert mixed_deleted == 2, f"Expected 2 deletions for mixed IDs, got {mixed_deleted}"
        logger.info(f"✓ Mixed batch delete: {mixed_deleted} deletions from {mixed_ids}")
        
    def test_error_handling(self):
        """Test error handling"""
        logger.info("=== Testing Error Handling ===")
        
        # Test mismatched ids and vectors
        try:
            self.index.insert_batch([1, 2], [np.random.randn(self.dim).astype(np.float32)])
            assert False, "Should have raised ValueError for mismatched lengths"
        except ValueError as e:
            assert "same length" in str(e)
            logger.info("✓ Correctly handles mismatched ids/vectors lengths")
        
        # Test duplicate IDs
        try:
            vectors = [np.random.randn(self.dim).astype(np.float32) for _ in range(3)]
            self.index.insert_batch([1, 1, 2], vectors)
            assert False, "Should have raised ValueError for duplicate IDs"
        except ValueError as e:
            assert "Duplicate ids" in str(e)
            logger.info("✓ Correctly handles duplicate IDs")
        
        # Test wrong dimension
        try:
            wrong_vector = np.random.randn(self.dim + 10).astype(np.float32)
            self.index.insert_batch([999], [wrong_vector])
            assert False, "Should have raised ValueError for wrong dimension"
        except ValueError as e:
            assert "dimension mismatch" in str(e)
            logger.info("✓ Correctly handles wrong vector dimensions")
        
        # Test get non-existent ID
        try:
            self.index.get(99999)
            assert False, "Should have raised KeyError for non-existent ID"
        except KeyError as e:
            assert "not found" in str(e)
            logger.info("✓ Correctly handles get non-existent ID")
        
    def test_persistence(self):
        """Test persistence and loading"""
        logger.info("=== Testing Persistence ===")
        
        # Insert test data
        num_items = 8
        ids = list(range(400, 400 + num_items))
        vectors = [np.random.randn(self.dim).astype(np.float32) for _ in ids]
        
        self.index.insert_batch(ids, vectors)
        original_max_elements = self.index.max_elements
        logger.info(f"Inserted {num_items} items, max_elements: {original_max_elements}")
        
        # Delete some items to test deletion persistence
        self.index.delete(ids[2])
        logger.info(f"Deleted item ID {ids[2]}")
        
        # Force save
        self.index._save()
        logger.info("Forced explicit save")
        
        # Delete current index
        del self.index
        logger.info("Deleted current index instance")
        
        # Load from disk
        self.index = HNSWIndex(
            db_path=self.test_dir,
            dim=self.dim,
            space="l2"
        )
        logger.info("Loaded index from disk")

        # getting full index list something we do not want to do.
        
        # # Verify loaded data
        # assert len(self.index) == num_items - 1, f"Expected {num_items-1} items after load, got {len(self.index)}"
        # logger.info(f"✓ Loaded index contains {len(self.index)} items")
        
        # Verify max_elements persisted
        assert self.index.max_elements == original_max_elements, f"Expected max_elements {original_max_elements}, got {self.index.max_elements}"
        logger.info(f"✓ max_elements persisted: {self.index.max_elements}")
        
        # # Verify specific IDs
        # loaded_ids = self.index.get_all_ids()
        # for i, id in enumerate(ids):
        #     if i == 2:  # Deleted item
        #         assert id not in loaded_ids, f"Deleted ID {id} should not be in loaded index"
        #     else:
        #         assert id in loaded_ids, f"ID {id} should be in loaded index"
        # logger.info("✓ All expected IDs present after load")
        
        # Verify vector integrity
        retrieved = self.index.get_batch([ids[0], ids[1]])
        np.testing.assert_array_almost_equal(vectors[0], retrieved[ids[0]], decimal=6)
        np.testing.assert_array_almost_equal(vectors[1], retrieved[ids[1]], decimal=6)
        logger.info("✓ Vector integrity verified after load")
        
    def test_resize_functionality(self):
        """Test manual resize functionality"""
        logger.info("=== Testing Manual Resize ===")
        
        initial_max = self.index.max_elements
        new_max = initial_max + 50
        
        logger.info(f"Resizing from {initial_max} to {new_max}")
        self.index.resize(new_max)
        
        assert self.index.max_elements == new_max, f"Expected max_elements {new_max}, got {self.index.max_elements}"
        logger.info(f"✓ Manual resize successful: {self.index.max_elements}")
        
    def run_all_tests(self):
        """Run all tests"""
        logger.info("Starting comprehensive HNSWIndex tests...")
        
        try:
            self.setup()
            
            # Run all test methods
            test_methods = [
                self.test_initial_state,
                self.test_insert_batch_basic,
                self.test_auto_expansion,
                self.test_search_functionality,
                self.test_delete_operations,
                self.test_error_handling,
                self.test_persistence,
                self.test_resize_functionality,
            ]
            
            for test_method in test_methods:
                try:
                    test_method()
                    logger.info(f"✓ {test_method.__name__} PASSED")
                except Exception as e:
                    logger.error(f"✗ {test_method.__name__} FAILED: {e}")
                    raise
            
            logger.info("🎉 ALL TESTS PASSED!")
            
        except Exception as e:
            logger.error(f"Test suite failed: {e}")
            raise
        finally:
            self.teardown()


def main():
    """Main test runner"""
    tester = TestHNSWIndex()
    tester.run_all_tests()


if __name__ == "__main__":
    main()
