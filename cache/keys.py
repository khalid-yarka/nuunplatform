# cache/keys.py
"""
Key generation and parsing utilities.
"""

import re
from typing import Tuple, Optional

# Default separator
SEP = ':'

def make_key(namespace: str, entity: str, identifier: str, sub: Optional[str] = None) -> str:
    """
    Build a cache key.

    Examples:
        make_key('user', 'profile', '123') -> 'user:profile:123'
        make_key('quiz', 'state', '456', 'participants') -> 'quiz:state:456:participants'
    """
    parts = [namespace, entity, identifier]
    if sub is not None:
        parts.append(sub)
    return SEP.join(parts)

def parse_key(key: str) -> Tuple[str, str, str, Optional[str]]:
    """
    Parse a key into its components.

    Returns: (namespace, entity, identifier, sub)
    """
    parts = key.split(SEP)
    if len(parts) >= 3:
        namespace = parts[0]
        entity = parts[1]
        identifier = parts[2]
        sub = parts[3] if len(parts) > 3 else None
        return namespace, entity, identifier, sub
    raise ValueError(f"Invalid key format: {key}")

def get_namespace_from_key(key: str) -> str:
    """Extract namespace from a key."""
    return key.split(SEP)[0]

def pattern_for_namespace(namespace: str) -> str:
    """Get a key pattern for a whole namespace."""
    return f"{namespace}:*"

def pattern_for_entity(namespace: str, entity: str) -> str:
    """Get a key pattern for a specific entity within a namespace."""
    return f"{namespace}:{entity}:*"

def pattern_for_identifier(namespace: str, entity: str, identifier: str) -> str:
    """Get a key pattern for a specific identifier (may include sub-keys)."""
    return f"{namespace}:{entity}:{identifier}:*"

def is_pattern_match(key: str, pattern: str) -> bool:
    """
    Check if a key matches a glob pattern (supports * wildcard).
    """
    # Convert glob to regex
    regex = re.escape(pattern).replace('\\*', '.*')
    return re.fullmatch(regex, key) is not None