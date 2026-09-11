"""
Upstash REST API Wrapper for PythonAnywhere
Uses port 443 (HTTPS) which IS allowed on PythonAnywhere
"""

import requests
import json
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class UpstashRedis:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip('/')
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        self._connected = False
    
    def ping(self) -> bool:
        """Test connection"""
        try:
            response = requests.get(
                f"{self.url}/ping",
                headers=self.headers,
                timeout=10
            )
            self._connected = response.status_code == 200
            return self._connected
        except Exception as e:
            logger.error(f"Ping failed: {e}")
            return False
    
    def set(self, key: str, value: str) -> bool:
        """Set a key"""
        try:
            response = requests.post(
                f"{self.url}/set/{key}/{value}",
                headers=self.headers,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"SET failed: {e}")
            return False
    
    def get(self, key: str) -> Optional[str]:
        """Get a key"""
        try:
            response = requests.get(
                f"{self.url}/get/{key}",
                headers=self.headers,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get('result')
            return None
        except Exception as e:
            logger.error(f"GET failed: {e}")
            return None
    
    def delete(self, key: str) -> bool:
        """Delete a key"""
        try:
            response = requests.delete(
                f"{self.url}/del/{key}",
                headers=self.headers,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"DELETE failed: {e}")
            return False
    
    def hset(self, key: str, field: str, value: str) -> bool:
        """Hash set"""
        try:
            response = requests.post(
                f"{self.url}/hset/{key}/{field}/{value}",
                headers=self.headers,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"HSET failed: {e}")
            return False
    
    def hget(self, key: str, field: str) -> Optional[str]:
        """Hash get"""
        try:
            response = requests.get(
                f"{self.url}/hget/{key}/{field}",
                headers=self.headers,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get('result')
            return None
        except Exception as e:
            logger.error(f"HGET failed: {e}")
            return None
    
    def hgetall(self, key: str) -> Dict:
        """Hash get all"""
        try:
            response = requests.get(
                f"{self.url}/hgetall/{key}",
                headers=self.headers,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get('result', {})
            return {}
        except Exception as e:
            logger.error(f"HGETALL failed: {e}")
            return {}
    
    def hincrby(self, key: str, field: str, increment: int = 1) -> bool:
        """Hash increment"""
        try:
            response = requests.post(
                f"{self.url}/hincrby/{key}/{field}/{increment}",
                headers=self.headers,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"HINCRBY failed: {e}")
            return False
    
    def zadd(self, key: str, score: float, member: str) -> bool:
        """Sorted set add"""
        try:
            response = requests.post(
                f"{self.url}/zadd/{key}/{score}/{member}",
                headers=self.headers,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"ZADD failed: {e}")
            return False
    
    def zincrby(self, key: str, increment: int, member: str) -> bool:
        """Sorted set increment"""
        try:
            response = requests.post(
                f"{self.url}/zincrby/{key}/{increment}/{member}",
                headers=self.headers,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"ZINCRBY failed: {e}")
            return False
    
    def zrevrange(self, key: str, start: int, stop: int) -> List[Dict]:
        """Sorted set reverse range"""
        try:
            response = requests.get(
                f"{self.url}/zrevrange/{key}/{start}/{stop}",
                headers=self.headers,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get('result', [])
            return []
        except Exception as e:
            logger.error(f"ZREVRANGE failed: {e}")
            return []
    
    def zrevrank(self, key: str, member: str) -> Optional[int]:
        """Sorted set reverse rank"""
        try:
            response = requests.get(
                f"{self.url}/zrevrank/{key}/{member}",
                headers=self.headers,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get('result')
            return None
        except Exception as e:
            logger.error(f"ZREVRANK failed: {e}")
            return None
    
    def expire(self, key: str, seconds: int) -> bool:
        """Set expiration"""
        try:
            response = requests.post(
                f"{self.url}/expire/{key}/{seconds}",
                headers=self.headers,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"EXPIRE failed: {e}")
            return False
    
    def keys(self, pattern: str) -> List[str]:
        """Get keys matching pattern"""
        try:
            response = requests.get(
                f"{self.url}/keys/{pattern}",
                headers=self.headers,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get('result', [])
            return []
        except Exception as e:
            logger.error(f"KEYS failed: {e}")
            return []
    
    def delete_keys(self, keys: List[str]) -> bool:
        """Delete multiple keys"""
        try:
            keys_path = "/".join(keys)
            response = requests.delete(
                f"{self.url}/del/{keys_path}",
                headers=self.headers,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"DELETE_KEYS failed: {e}")
            return False
    
    def eval_lua(self, script: str, keys: List[str], args: List[str]) -> Any:
        """
        Execute Lua script via REST API
        Note: Upstash REST API supports Lua scripts
        """
        try:
            payload = {
                "script": script,
                "keys": keys,
                "args": args
            }
            response = requests.post(
                f"{self.url}/eval",
                headers=self.headers,
                json=payload,
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"EVAL failed: {e}")
            return None

    def info(self) -> Dict:
        """Get Redis info"""
        try:
            response = requests.get(
                f"{self.url}/info",
                headers=self.headers,
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
            return {}
        except Exception as e:
            logger.error(f"INFO failed: {e}")
            return {}