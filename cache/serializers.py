# cache/serializers.py
"""
Serialization/deserialization utilities for cache values.
"""

import json
import pickle
from typing import Any, Union

# Try to import msgpack for faster serialization
try:
    import msgpack
    HAS_MSGPACK = True
except ImportError:
    HAS_MSGPACK = False

def serialize(value: Any, format: str = 'json') -> bytes:
    """
    Serialize a Python object to bytes.

    Supported formats: 'json', 'pickle', 'msgpack'.
    """
    if format == 'json':
        return json.dumps(value, default=str).encode('utf-8')
    elif format == 'pickle':
        return pickle.dumps(value)
    elif format == 'msgpack' and HAS_MSGPACK:
        return msgpack.packb(value, use_bin_type=True)
    else:
        raise ValueError(f"Unsupported serialization format: {format}")

def deserialize(data: bytes, format: str = 'json') -> Any:
    """
    Deserialize bytes back to a Python object.

    Supported formats: 'json', 'pickle', 'msgpack'.
    """
    if not data:
        return None
    if format == 'json':
        return json.loads(data.decode('utf-8'))
    elif format == 'pickle':
        return pickle.loads(data)
    elif format == 'msgpack' and HAS_MSGPACK:
        return msgpack.unpackb(data, raw=False)
    else:
        raise ValueError(f"Unsupported deserialization format: {format}")