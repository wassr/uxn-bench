import json
import fnmatch
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional
from .logger import get_logger

logger = get_logger(__name__)

@dataclass
class TestNode:
    id: str  # e.g., 'assembler/instructions/add'
    group: str  # e.g., 'assembler/instructions'
    name: str
    path: Path
    config: dict

class DiscoveryEngine:
    def __init__(self, tests_dir: str = "tests"):
        self.tests_dir = Path(tests_dir)
        self.tests: List[TestNode] = []
        
    def discover(self, includes: List[str] = None, excludes: List[str] = None) -> List[TestNode]:
        if not self.tests_dir.exists():
            logger.warning(f"Tests directory '{self.tests_dir}' not found.")
            return []
            
        logger.debug(f"Discovering tests in {self.tests_dir}")
        self.tests = []
        
        for path in self.tests_dir.rglob("*.test.json"):
            # Rel path without the '.test.json' suffix
            rel_path = path.relative_to(self.tests_dir)
            # Remove the suffix
            test_id = str(rel_path).replace(".test.json", "")
            # The group is the parent directory structure
            group = str(rel_path.parent) if rel_path.parent.name else ""
            
            # Apply filters
            if not self._matches_filters(test_id, includes, excludes):
                logger.debug(f"Skipping {test_id} due to filters.")
                continue
                
            try:
                with open(path, "r") as f:
                    config = json.load(f)
            except Exception as e:
                logger.error(f"Failed to read test config {path}: {e}")
                continue
                
            name = config.get("name", test_id)
            node = TestNode(id=test_id, group=group, name=name, path=path, config=config)
            self.tests.append(node)
            
        # Sort for stable output
        self.tests.sort(key=lambda t: t.id)
        return self.tests
        
    def _matches_filters(self, test_id: str, includes: List[str], excludes: List[str]) -> bool:
        # If there are includes, it MUST match at least one
        if includes:
            if not any(fnmatch.fnmatch(test_id, inc) for inc in includes):
                return False
                
        # If there are excludes, it MUST NOT match any
        if excludes:
            if any(fnmatch.fnmatch(test_id, exc) for exc in excludes):
                return False
                
        return True
