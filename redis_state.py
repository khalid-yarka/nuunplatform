# redis_state.py
"""
Live Quiz Redis State Manager – Full Version
Supports both Redis protocol (local) and Upstash REST API (PythonAnywhere)
Automatic fallback between connection methods
"""

import json
import time
import logging
from typing import Optional, Dict, Any, List, Tuple

try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)

# ============================================
# UPSTASH REST API WRAPPER (for PythonAnywhere)
# ============================================

class UpstashRedis:
    """
    Upstash REST API Wrapper for PythonAnywhere
    Uses port 443 (HTTPS) which IS allowed on PythonAnywhere
    """
    
    def __init__(self, url: str, token: str):
        self.url = url.rstrip('/')
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        self._connected = False
        logger.info(f"Upstash REST API initialized: {url}")
    
    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Any:
        """Make a request to Upstash REST API"""
        try:
            import requests
        except ImportError:
            logger.error("requests library not installed. Run: pip install requests")
            return None
        
        try:
            url = f"{self.url}/{endpoint.lstrip('/')}"
            if method == 'GET':
                response = requests.get(url, headers=self.headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, headers=self.headers, json=data, timeout=10)
            elif method == 'PUT':
                response = requests.put(url, headers=self.headers, json=data, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=self.headers, timeout=10)
            else:
                return None
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                return None
            else:
                logger.error(f"REST API error {response.status_code}: {response.text}")
                return None
        except Exception as e:
            logger.error(f"REST API request failed: {e}")
            return None
    
    def ping(self) -> bool:
        """Test connection"""
        result = self._request('GET', 'ping')
        return result is not None
    
    def set(self, key: str, value: str) -> bool:
        """Set a key"""
        result = self._request('POST', f'set/{key}/{value}')
        return result is not None
    
    def get(self, key: str) -> Optional[str]:
        """Get a key"""
        result = self._request('GET', f'get/{key}')
        if result and 'result' in result:
            return result['result']
        return None
    
    def delete(self, key: str) -> bool:
        """Delete a key"""
        result = self._request('DELETE', f'del/{key}')
        return result is not None
    
    def hset(self, key: str, field: str, value: str) -> bool:
        """Hash set"""
        result = self._request('POST', f'hset/{key}/{field}/{value}')
        return result is not None
    
    def hset_dict(self, key: str, mapping: Dict) -> bool:
        """Hash set multiple fields"""
        try:
            for field, value in mapping.items():
                if not self.hset(key, field, str(value)):
                    return False
            return True
        except Exception as e:
            logger.error(f"HSET dict failed: {e}")
            return False
    
    def hget(self, key: str, field: str) -> Optional[str]:
        """Hash get"""
        result = self._request('GET', f'hget/{key}/{field}')
        if result and 'result' in result:
            return result['result']
        return None
    
    def hgetall(self, key: str) -> Dict:
        """Hash get all"""
        result = self._request('GET', f'hgetall/{key}')
        if result and 'result' in result:
            return result['result']
        return {}
    
    def hincrby(self, key: str, field: str, increment: int = 1) -> bool:
        """Hash increment"""
        result = self._request('POST', f'hincrby/{key}/{field}/{increment}')
        return result is not None
    
    def zadd(self, key: str, score: float, member: str) -> bool:
        """Sorted set add"""
        result = self._request('POST', f'zadd/{key}/{score}/{member}')
        return result is not None
    
    def zincrby(self, key: str, increment: int, member: str) -> bool:
        """Sorted set increment"""
        result = self._request('POST', f'zincrby/{key}/{increment}/{member}')
        return result is not None
    
    def zrevrange(self, key: str, start: int, stop: int) -> List[Dict]:
        """Sorted set reverse range"""
        result = self._request('GET', f'zrevrange/{key}/{start}/{stop}')
        if result and 'result' in result:
            return result['result']
        return []
    
    def zrevrank(self, key: str, member: str) -> Optional[int]:
        """Sorted set reverse rank"""
        result = self._request('GET', f'zrevrank/{key}/{member}')
        if result and 'result' in result:
            return int(result['result'])
        return None
    
    def expire(self, key: str, seconds: int) -> bool:
        """Set expiration"""
        result = self._request('POST', f'expire/{key}/{seconds}')
        return result is not None
    
    def keys(self, pattern: str) -> List[str]:
        """Get keys matching pattern"""
        result = self._request('GET', f'keys/{pattern}')
        if result and 'result' in result:
            return result['result']
        return []
    
    def delete_keys(self, keys: List[str]) -> bool:
        """Delete multiple keys"""
        if not keys:
            return True
        keys_path = "/".join(keys)
        result = self._request('DELETE', f'del/{keys_path}')
        return result is not None
    
    def info(self) -> Dict:
        """Get Redis info"""
        result = self._request('GET', 'info')
        return result or {}
    
    def script(self, script: str, keys: List[str] = None, args: List[str] = None) -> Any:
        """Execute Lua script"""
        try:
            payload = {
                "script": script,
                "keys": keys or [],
                "args": args or []
            }
            result = self._request('POST', 'eval', payload)
            return result
        except Exception as e:
            logger.error(f"Lua script failed: {e}")
            return None

# ============================================
# LIVE QUIZ STATE MANAGER
# ============================================

class LiveQuizState:
    """
    Manager for Redis-based Live Quiz state.
    Supports both Redis protocol and REST API.
    """
    
    PREFIX = "livequiz:"
    
    # Lua scripts (for atomic operations on Redis protocol)
    SUBMIT_ANSWER_LUA = """
        local quiz_id = ARGV[1]
        local user_id = ARGV[2]
        local question_id = ARGV[3]
        local answer = ARGV[4]
        local correct_answer = ARGV[5]
        local key_prefix = 'livequiz:participant:' .. quiz_id .. ':' .. user_id

        -- Check if already answered this question
        local answers_json = redis.call('HGET', key_prefix, 'answers')
        local answers = {}
        if answers_json then
            answers = cjson.decode(answers_json)
        end
        if answers[question_id] then
            return {-1, 'already answered'}
        end

        -- Determine correctness
        local is_correct = (answer == correct_answer)
        local points = is_correct and 2 or 0

        -- Update score, counts
        redis.call('HINCRBY', key_prefix, 'score', points)
        redis.call('HINCRBY', key_prefix, 'correct_count', is_correct and 1 or 0)
        redis.call('HINCRBY', key_prefix, 'wrong_count', is_correct and 0 or 1)

        -- Store answer
        answers[question_id] = {answer = answer, correct = is_correct}
        redis.call('HSET', key_prefix, 'answers', cjson.encode(answers))

        -- Update leaderboard
        local lb_key = 'livequiz:leaderboard:' .. quiz_id
        redis.call('ZINCRBY', lb_key, points, user_id)

        return {is_correct and 1 or 0, correct_answer}
    """
    
    SKIP_QUESTION_LUA = """
        local quiz_id = ARGV[1]
        local user_id = ARGV[2]
        local question_id = ARGV[3]
        local key_prefix = 'livequiz:participant:' .. quiz_id .. ':' .. user_id

        local answers_json = redis.call('HGET', key_prefix, 'answers')
        local answers = {}
        if answers_json then
            answers = cjson.decode(answers_json)
        end
        if answers[question_id] then
            return {-1, 'already answered'}
        end

        answers[question_id] = {answer = nil, correct = false, skipped = true}
        redis.call('HSET', key_prefix, 'answers', cjson.encode(answers))
        redis.call('HINCRBY', key_prefix, 'skipped_count', 1)
        local new_index = redis.call('HINCRBY', key_prefix, 'current_question_index', 1)

        return {1, new_index}
    """
    
    SUBMIT_RATING_LUA = """
        local quiz_id = ARGV[1]
        local user_id = ARGV[2]
        local question_id = ARGV[3]
        local rating = ARGV[4]
        local key_prefix = 'livequiz:participant:' .. quiz_id .. ':' .. user_id

        local ratings_json = redis.call('HGET', key_prefix, 'ratings')
        local ratings = {}
        if ratings_json then
            ratings = cjson.decode(ratings_json)
        end
        if ratings[question_id] then
            return {-1, 'already rated'}
        end

        ratings[question_id] = rating
        redis.call('HSET', key_prefix, 'ratings', cjson.encode(ratings))
        local new_index = redis.call('HINCRBY', key_prefix, 'current_question_index', 1)

        return {1, new_index}
    """

    def __init__(self, redis_client=None):
        """
        Initialize with Redis client (protocol) or use REST API fallback
        """
        self.redis = redis_client
        self.rest_client = None
        self.use_rest_api = False
        self._redis_script_cache = {}
        
        # If no redis_client provided, try REST API
        if self.redis is None:
            self._init_rest_api()
        
        # If we have Redis client, check if it works
        if self.redis and not self._check_redis_connection():
            self._init_rest_api()
        
        # If still no connection, log warning
        if self.redis is None and self.rest_client is None:
            logger.warning("No Redis connection available (neither protocol nor REST API)")
    
    def _init_rest_api(self):
        """Initialize REST API client from config"""
        try:
            from config import Config
            
            rest_url = getattr(Config, 'UPSTASH_REST_URL', None)
            rest_token = getattr(Config, 'UPSTASH_REST_TOKEN', None)
            
            if rest_url and rest_token:
                self.rest_client = UpstashRedis(rest_url, rest_token)
                if self.rest_client.ping():
                    self.use_rest_api = True
                    logger.info("Using Upstash REST API")
                    # Set redis to None since we're using REST
                    self.redis = None
                    return
                else:
                    logger.warning("Upstash REST API connection failed")
                    self.rest_client = None
            else:
                logger.debug("UPSTASH_REST_URL/TOKEN not configured")
        except Exception as e:
            logger.error(f"Failed to initialize REST API: {e}")
    
    def _check_redis_connection(self) -> bool:
        """Check if Redis protocol connection works"""
        if not self.redis:
            return False
        try:
            self.redis.ping()
            return True
        except Exception:
            return False
    
    def _key(self, *parts: str) -> str:
        """Generate Redis key"""
        return self.PREFIX + ":".join(str(p) for p in parts)
    
    # ============================================
    # PARTICIPANT STATE OPERATIONS
    # ============================================
    
    def get_participant(self, quiz_id: int, user_id: int) -> Optional[Dict]:
        """Get participant state"""
        key = self._key("participant", quiz_id, user_id)
        
        if self.use_rest_api and self.rest_client:
            data = self.rest_client.hgetall(key)
            if not data:
                return None
            
            # Decode fields
            decoded = {}
            for k, v in data.items():
                if k in ('answers', 'ratings'):
                    try:
                        decoded[k] = json.loads(v)
                    except:
                        decoded[k] = {}
                elif k in ('score', 'current_question_index', 'correct_count', 'wrong_count', 'skipped_count'):
                    try:
                        decoded[k] = int(v)
                    except:
                        decoded[k] = 0
                else:
                    decoded[k] = v
            return decoded
        
        elif self.redis:
            data = self.redis.hgetall(key)
            if not data:
                return None
            
            decoded = {}
            for k, v in data.items():
                k_str = k.decode('utf-8')
                v_str = v.decode('utf-8')
                if k_str in ('answers', 'ratings'):
                    try:
                        decoded[k_str] = json.loads(v_str)
                    except:
                        decoded[k_str] = {}
                elif k_str in ('score', 'current_question_index', 'correct_count', 'wrong_count', 'skipped_count'):
                    try:
                        decoded[k_str] = int(v_str)
                    except:
                        decoded[k_str] = 0
                else:
                    decoded[k_str] = v_str
            return decoded
        
        return None
    
    def update_participant(self, quiz_id: int, user_id: int, updates: Dict) -> bool:
        """Update participant state"""
        key = self._key("participant", quiz_id, user_id)
        
        # Serialize JSON fields
        serialized = {}
        for k, v in updates.items():
            if k in ('answers', 'ratings'):
                serialized[k] = json.dumps(v)
            else:
                serialized[k] = str(v)
        
        if self.use_rest_api and self.rest_client:
            success = self.rest_client.hset_dict(key, serialized)
            if success:
                self.rest_client.expire(key, 3600)
            return success
        
        elif self.redis:
            self.redis.hset(key, mapping=serialized)
            self.redis.expire(key, 3600)
            return True
        
        return False
    
    def init_participant(self, quiz_id: int, user_id: int, question_ids: List[int],
                         initial_data: Optional[Dict] = None) -> bool:
        """Initialize a new participant"""
        if initial_data is None:
            initial_data = {
                'score': 0,
                'current_question_index': 0,
                'correct_count': 0,
                'wrong_count': 0,
                'skipped_count': 0,
                'answers': {},
                'ratings': {},
                'status': 'active',
                'is_ready': False,
                'name': 'Unknown'
            }
        
        # Include shuffled question IDs
        if question_ids:
            import random
            shuffled = question_ids[:]
            random.shuffle(shuffled)
            if 'answers' not in initial_data:
                initial_data['answers'] = {}
            initial_data['answers']['__shuffled_ids'] = shuffled
        
        return self.update_participant(quiz_id, user_id, initial_data)
    
    # ============================================
    # ATOMIC OPERATIONS
    # ============================================
    
    def submit_answer(self, quiz_id: int, user_id: int, question_id: int,
                      answer: str, correct_answer: str) -> Tuple[bool, Dict]:
        """Atomically submit an answer"""
        
        # Try Redis protocol first (with Lua script)
        if self.redis:
            try:
                script = self.redis.register_script(self.SUBMIT_ANSWER_LUA)
                result = script(keys=[], args=[
                    str(quiz_id), str(user_id), str(question_id),
                    answer, correct_answer
                ])
                if result[0] == -1:
                    return False, {'error': result[1]}
                is_correct = bool(result[0])
                correct_ans = result[1]
                from db import get_question_by_id
                q = get_question_by_id(question_id)
                explanation = q.get('explanation', '') if q else ''
                return True, {
                    'correct': is_correct,
                    'correct_answer': correct_ans,
                    'explanation': explanation
                }
            except Exception as e:
                logger.error(f"Lua submit_answer failed: {e}")
                # Fall through to REST API or manual
        
        # Fallback to REST API (without Lua - need to implement manually)
        if self.use_rest_api and self.rest_client:
            return self._submit_answer_rest(quiz_id, user_id, question_id, answer, correct_answer)
        
        return False, {'error': 'No Redis connection available'}
    
    def _submit_answer_rest(self, quiz_id: int, user_id: int, question_id: int,
                            answer: str, correct_answer: str) -> Tuple[bool, Dict]:
        """Submit answer using REST API (manual atomicity)"""
        key = self._key("participant", quiz_id, user_id)
        
        # Get current state
        participant = self.get_participant(quiz_id, user_id)
        if not participant:
            return False, {'error': 'Participant not found'}
        
        # Check if already answered
        answers = participant.get('answers', {})
        if str(question_id) in answers:
            return False, {'error': 'Already answered'}
        
        # Determine correctness
        is_correct = (answer == correct_answer)
        points = 2 if is_correct else 0
        
        # Update state
        answers[str(question_id)] = {'answer': answer, 'correct': is_correct}
        participant['answers'] = answers
        participant['score'] = participant.get('score', 0) + points
        participant['correct_count'] = participant.get('correct_count', 0) + (1 if is_correct else 0)
        participant['wrong_count'] = participant.get('wrong_count', 0) + (0 if is_correct else 1)
        
        # Save state
        if self.update_participant(quiz_id, user_id, participant):
            # Update leaderboard
            lb_key = self._key("leaderboard", quiz_id)
            self.rest_client.zincrby(lb_key, points, str(user_id))
            
            from db import get_question_by_id
            q = get_question_by_id(question_id)
            explanation = q.get('explanation', '') if q else ''
            return True, {
                'correct': is_correct,
                'correct_answer': correct_answer,
                'explanation': explanation
            }
        
        return False, {'error': 'Failed to save state'}
    
    def skip_question(self, quiz_id: int, user_id: int, question_id: int) -> Tuple[bool, str]:
        """Skip a question"""
        
        if self.redis:
            try:
                script = self.redis.register_script(self.SKIP_QUESTION_LUA)
                result = script(keys=[], args=[str(quiz_id), str(user_id), str(question_id)])
                if result[0] == -1:
                    return False, result[1]
                return True, 'skipped'
            except Exception as e:
                logger.error(f"Lua skip_question failed: {e}")
        
        # Fallback to REST API
        if self.use_rest_api and self.rest_client:
            participant = self.get_participant(quiz_id, user_id)
            if not participant:
                return False, 'Participant not found'
            
            answers = participant.get('answers', {})
            if str(question_id) in answers:
                return False, 'Already answered'
            
            answers[str(question_id)] = {'answer': None, 'correct': False, 'skipped': True}
            participant['answers'] = answers
            participant['skipped_count'] = participant.get('skipped_count', 0) + 1
            participant['current_question_index'] = participant.get('current_question_index', 0) + 1
            
            if self.update_participant(quiz_id, user_id, participant):
                return True, 'skipped'
        
        return False, 'No Redis connection available'
    
    def submit_rating(self, quiz_id: int, user_id: int, question_id: int, rating: str) -> Tuple[bool, str]:
        """Submit a rating"""
        
        if self.redis:
            try:
                script = self.redis.register_script(self.SUBMIT_RATING_LUA)
                result = script(keys=[], args=[str(quiz_id), str(user_id), str(question_id), rating])
                if result[0] == -1:
                    return False, result[1]
                return True, 'rated'
            except Exception as e:
                logger.error(f"Lua submit_rating failed: {e}")
        
        # Fallback to REST API
        if self.use_rest_api and self.rest_client:
            participant = self.get_participant(quiz_id, user_id)
            if not participant:
                return False, 'Participant not found'
            
            ratings = participant.get('ratings', {})
            if str(question_id) in ratings:
                return False, 'Already rated'
            
            ratings[str(question_id)] = rating
            participant['ratings'] = ratings
            participant['current_question_index'] = participant.get('current_question_index', 0) + 1
            
            if self.update_participant(quiz_id, user_id, participant):
                return True, 'rated'
        
        return False, 'No Redis connection available'
    
    # ============================================
    # READY STATUS
    # ============================================
    
    def set_participant_ready(self, quiz_id: int, user_id: int, is_ready: bool) -> bool:
        """Set participant ready status"""
        return self.update_participant(quiz_id, user_id, {'is_ready': is_ready})
    
    # ============================================
    # GET ALL PARTICIPANTS
    # ============================================
    
    def get_all_participants(self, quiz_id: int) -> List[Dict]:
        """Get all participants for a quiz"""
        pattern = self._key("participant", quiz_id, "*")
        participants = []
        user_ids = []
        
        if self.use_rest_api and self.rest_client:
            keys = self.rest_client.keys(pattern)
            for key in keys:
                parts = key.split(':')
                if len(parts) >= 4:
                    user_id = int(parts[-1])
                    user_ids.append(user_id)
        
        elif self.redis:
            keys = list(self.redis.scan_iter(match=pattern))
            for key in keys:
                parts = key.decode('utf-8').split(':')
                if len(parts) >= 4:
                    user_id = int(parts[-1])
                    user_ids.append(user_id)
        
        for user_id in user_ids:
            p = self.get_participant(quiz_id, user_id)
            if p:
                p['user_id'] = user_id
                if 'name' not in p:
                    from db import get_student_by_id
                    student = get_student_by_id(user_id)
                    p['name'] = f"{student.get('first_name', '')} {student.get('last_name', '')}".strip() or 'Unknown'
                    p['public_id'] = student.get('public_id', '----')
                participants.append(p)
        
        return participants
    
    def get_active_participant_count(self, quiz_id: int) -> int:
        """Get number of active participants"""
        all_p = self.get_all_participants(quiz_id)
        return sum(1 for p in all_p if p.get('status') != 'left')
    
    # ============================================
    # LEADERBOARD
    # ============================================
    
    def get_leaderboard(self, quiz_id: int, limit: int = 10) -> List[Dict]:
        """Get leaderboard"""
        lb_key = self._key("leaderboard", quiz_id)
        
        if self.use_rest_api and self.rest_client:
            results = self.rest_client.zrevrange(lb_key, 0, limit - 1)
            leaderboard = []
            for item in results:
                # Upstash REST API returns list of [member, score]
                if isinstance(item, list) and len(item) >= 2:
                    user_id = int(item[0])
                    score = float(item[1])
                    p = self.get_participant(quiz_id, user_id)
                    name = p.get('name', 'Unknown') if p else 'Unknown'
                    leaderboard.append({
                        'user_id': user_id,
                        'name': name,
                        'score': int(score)
                    })
            return leaderboard
        
        elif self.redis:
            results = self.redis.zrevrange(lb_key, 0, limit - 1, withscores=True)
            leaderboard = []
            for user_id_bytes, score in results:
                user_id = int(user_id_bytes.decode('utf-8'))
                p = self.get_participant(quiz_id, user_id)
                name = p.get('name', 'Unknown') if p else 'Unknown'
                leaderboard.append({
                    'user_id': user_id,
                    'name': name,
                    'score': int(score)
                })
            return leaderboard
        
        # Fallback to DB
        from db import get_live_quiz_participants_with_names
        parts = get_live_quiz_participants_with_names(quiz_id)
        sorted_parts = sorted(parts, key=lambda x: x.get('score', 0), reverse=True)
        return [{
            'user_id': p['student_id'],
            'name': f"{p.get('student', {}).get('first_name', '')} {p.get('student', {}).get('last_name', '')}".strip() or 'Unknown',
            'score': p.get('score', 0)
        } for p in sorted_parts[:limit]]
    
    def get_user_rank(self, quiz_id: int, user_id: int) -> Optional[int]:
        """Get user's rank"""
        lb_key = self._key("leaderboard", quiz_id)
        
        if self.use_rest_api and self.rest_client:
            rank = self.rest_client.zrevrank(lb_key, str(user_id))
            return rank + 1 if rank is not None else None
        
        elif self.redis:
            rank = self.redis.zrevrank(lb_key, str(user_id))
            return rank + 1 if rank is not None else None
        
        # Fallback to DB
        from db import get_live_quiz_participants_with_names
        parts = get_live_quiz_participants_with_names(quiz_id)
        sorted_parts = sorted(parts, key=lambda x: x.get('score', 0), reverse=True)
        for i, p in enumerate(sorted_parts, 1):
            if p['student_id'] == user_id:
                return i
        return None
    
    # ============================================
    # QUIZ OPERATIONS
    # ============================================
    
    def start_quiz(self, quiz_id: int) -> bool:
        """Initialize all participants for active quiz"""
        from db import get_live_quiz_participants, get_question_ids_for_quiz, get_student_by_id
        
        participants = get_live_quiz_participants(quiz_id)
        question_ids = get_question_ids_for_quiz(quiz_id)
        
        for p in participants:
            self.init_participant(quiz_id, p['student_id'], question_ids, {
                'score': 0,
                'current_question_index': 0,
                'correct_count': 0,
                'wrong_count': 0,
                'skipped_count': 0,
                'answers': {},
                'ratings': {},
                'status': 'active',
                'is_ready': False
            })
            student = get_student_by_id(p['student_id'])
            if student:
                self.update_participant(quiz_id, p['student_id'], {
                    'name': f"{student.get('first_name', '')} {student.get('last_name', '')}".strip() or 'Unknown',
                    'public_id': student.get('public_id', '----')
                })
        
        # Reset leaderboard
        lb_key = self._key("leaderboard", quiz_id)
        if self.use_rest_api and self.rest_client:
            self.rest_client.delete(lb_key)
        elif self.redis:
            self.redis.delete(lb_key)
        
        return True
    
    def delete_quiz(self, quiz_id: int) -> bool:
        """Delete all Redis keys for a quiz"""
        pattern = self._key("participant", quiz_id, "*")
        
        if self.use_rest_api and self.rest_client:
            keys = self.rest_client.keys(pattern)
            if keys:
                self.rest_client.delete_keys(keys)
            lb_key = self._key("leaderboard", quiz_id)
            self.rest_client.delete(lb_key)
            return True
        
        elif self.redis:
            keys = list(self.redis.scan_iter(match=pattern))
            if keys:
                self.redis.delete(*keys)
            lb_key = self._key("leaderboard", quiz_id)
            self.redis.delete(lb_key)
            return True
        
        return False
    
    # ============================================
    # CHECKPOINT
    # ============================================
    
    def checkpoint(self, quiz_id: int) -> bool:
        """Write current state to SQLite"""
        participants = self.get_all_participants(quiz_id)
        if not participants:
            return False
        
        from db import update_live_quiz_participant, get_live_quiz_participant
        
        for p in participants:
            db_p = get_live_quiz_participant(quiz_id, p['user_id'])
            if db_p:
                update_data = {
                    'score': p.get('score', 0),
                    'current_question_index': p.get('current_question_index', 0),
                    'correct_count': p.get('correct_count', 0),
                    'wrong_count': p.get('wrong_count', 0),
                    'skipped_count': p.get('skipped_count', 0),
                    'answers': p.get('answers', {}),
                    'ratings': p.get('ratings', {}),
                    'status': p.get('status', 'active'),
                    'is_ready': 1 if p.get('is_ready', False) else 0,
                }
                update_live_quiz_participant(db_p['id'], update_data)
        
        return True