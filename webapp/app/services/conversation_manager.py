"""
Conversation Manager - Persistent chat history storage and retrieval.

This service maintains server-side conversation history for each user session.
It enables the Chat Assistant to remember context across multiple turns and
provides full conversation thread retrieval for grounding.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict, field
from threading import RLock

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_iso_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class ChatMessage:
    """Represents a single message in a conversation."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str = field(default_factory=_utc_now_iso)
    confidence: Optional[float] = None
    intent: Optional[str] = None
    entities: List[str] = field(default_factory=list)
    evidence_sources: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "ChatMessage":
        """Create from dictionary."""
        return ChatMessage(**data)


@dataclass
class ConversationThread:
    """Represents a full conversation thread."""
    session_id: str
    user_id: str
    messages: List[ChatMessage] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now_iso)
    last_activity: str = field(default_factory=_utc_now_iso)
    scope: Dict = field(default_factory=dict)  # week, scenario, product, etc.
    active: bool = True
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "scope": self.scope,
            "active": self.active
        }

    @staticmethod
    def from_dict(data: dict) -> "ConversationThread":
        """Create from dictionary."""
        return ConversationThread(
            session_id=data["session_id"],
            user_id=data["user_id"],
            messages=[ChatMessage.from_dict(m) for m in data.get("messages", [])],
            created_at=data.get("created_at", _utc_now_iso()),
            last_activity=data.get("last_activity", _utc_now_iso()),
            scope=data.get("scope", {}),
            active=data.get("active", True)
        )


class ConversationManager:
    """Manages persistent conversation history storage and retrieval."""

    def __init__(self, data_dir: str = "data"):
        """Initialize conversation manager.
        
        Args:
            data_dir: Directory to store conversation history files
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        self.conversations_file = self.data_dir / "conversations.jsonl"
        self.sessions_index = self.data_dir / "sessions_index.json"
        
        # In-memory cache for active sessions
        self.active_conversations: Dict[str, ConversationThread] = {}
        self.lock = RLock()
        
        # Configuration
        self.session_timeout_hours = 24  # Keep conversations for 24 hours
        self.max_history_messages = 50  # Limit messages per conversation
        
        # Load existing conversations
        self._load_conversations()
        
        logger.info(f"ConversationManager initialized with data_dir={data_dir}")

    def _load_conversations(self) -> None:
        """Load existing conversations from storage."""
        try:
            if self.sessions_index.exists():
                with open(self.sessions_index, 'r', encoding='utf-8') as f:
                    index = json.load(f)
                    for session_id, meta in index.items():
                        # Only load active sessions within timeout
                        last_raw = meta.get("last_activity", "")
                        if not last_raw:
                            continue
                        try:
                            last_activity = _parse_iso_utc(last_raw)
                        except ValueError:
                            continue
                        if _utc_now() - last_activity < timedelta(hours=self.session_timeout_hours):
                            self._load_session_from_file(session_id)
        except Exception as e:
            logger.error(f"Error loading conversations: {e}")

    def _load_session_from_file(self, session_id: str) -> Optional[ConversationThread]:
        """Load a specific session from file (uses the most recent valid entry)."""
        try:
            if self.conversations_file.exists():
                latest: Optional[dict] = None
                with open(self.conversations_file, 'r', encoding='utf-8') as f:
                    for line_no, line in enumerate(f, 1):
                        raw = line.strip()
                        if not raw:
                            continue
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning(f"Skipping malformed JSONL line {line_no} in {self.conversations_file}")
                            continue
                        if data.get("session_id") == session_id:
                            latest = data
                if latest is not None:
                    conv = ConversationThread.from_dict(latest)
                    self.active_conversations[session_id] = conv
                    return conv
        except Exception as e:
            logger.error(f"Error loading session {session_id}: {e}")
        return None

    def start_conversation(self, session_id: str, user_id: str, scope: Dict = None) -> ConversationThread:
        """Start a new conversation thread.
        
        Args:
            session_id: Unique session identifier
            user_id: User making the request
            scope: Planning scope (week, scenario, product, etc.)
            
        Returns:
            New ConversationThread
        """
        with self.lock:
            if session_id in self.active_conversations:
                thread = self.active_conversations[session_id]
                thread.last_activity = _utc_now_iso()
                return thread
            
            thread = ConversationThread(
                session_id=session_id,
                user_id=user_id,
                scope=scope or {}
            )
            self.active_conversations[session_id] = thread
            self._save_conversation(thread)
            logger.info(f"Started conversation: session={session_id}, user={user_id}")
            return thread

    def add_message(self, session_id: str, role: str, content: str, 
                   intent: Optional[str] = None, entities: List[str] = None,
                   confidence: Optional[float] = None,
                   evidence_sources: List[str] = None,
                   metadata: Dict = None) -> bool:
        """Add a message to a conversation.
        
        Args:
            session_id: Session to add message to
            role: "user" or "assistant"
            content: Message content
            intent: Detected intent for the message
            entities: Extracted entities
            confidence: Confidence score (for assistant messages)
            evidence_sources: Evidence sources used (for assistant messages)
            metadata: Additional metadata
            
        Returns:
            True if message was added
        """
        with self.lock:
            if session_id not in self.active_conversations:
                logger.warning(f"Session {session_id} not found, creating new")
                self.start_conversation(session_id, "unknown")
            
            thread = self.active_conversations[session_id]
            
            # Create message
            message = ChatMessage(
                role=role,
                content=content,
                intent=intent,
                entities=entities or [],
                confidence=confidence,
                evidence_sources=evidence_sources or [],
                metadata=metadata or {}
            )
            
            # Add to thread
            thread.messages.append(message)
            thread.last_activity = _utc_now_iso()
            
            # Trim old messages if exceeding max
            if len(thread.messages) > self.max_history_messages:
                thread.messages = thread.messages[-self.max_history_messages:]
            
            # Save to disk
            self._save_conversation(thread)
            
            return True

    def get_conversation_history(self, session_id: str, 
                                limit: Optional[int] = None) -> List[ChatMessage]:
        """Get conversation history for a session.
        
        Args:
            session_id: Session to retrieve
            limit: Maximum number of messages to return (most recent)
            
        Returns:
            List of ChatMessages
        """
        with self.lock:
            thread = self.active_conversations.get(session_id)
            if not thread:
                return []
            
            messages = thread.messages
            if limit:
                messages = messages[-limit:]
            return messages

    def get_conversation_thread(self, session_id: str) -> Optional[ConversationThread]:
        """Get full conversation thread.
        
        Args:
            session_id: Session to retrieve
            
        Returns:
            ConversationThread or None
        """
        with self.lock:
            return self.active_conversations.get(session_id)

    def get_recent_messages(self, session_id: str, num_messages: int = 10) -> List[ChatMessage]:
        """Get most recent N messages for context building.
        
        Args:
            session_id: Session to retrieve
            num_messages: Number of recent messages to return
            
        Returns:
            List of ChatMessages
        """
        return self.get_conversation_history(session_id, limit=num_messages)

    def get_context_summary(self, session_id: str) -> Dict:
        """Get a summary of conversation context.
        
        Args:
            session_id: Session to analyze
            
        Returns:
            Dictionary with context summary
        """
        with self.lock:
            thread = self.active_conversations.get(session_id)
            if not thread:
                return {}
            
            # Extract last mentioned entities
            entities = {}
            intents = []
            for msg in reversed(thread.messages):
                if msg.role == "assistant" and msg.entities:
                    for entity in msg.entities:
                        if entity not in entities:
                            entities[entity] = msg.timestamp
                if msg.intent and msg.intent not in intents:
                    intents.append(msg.intent)
            
            return {
                "session_id": session_id,
                "message_count": len(thread.messages),
                "duration_minutes": self._get_duration_minutes(thread),
                "scope": thread.scope,
                "recent_entities": list(entities.keys()),
                "recent_intents": intents[:3],  # Last 3 distinct intents
                "last_activity": thread.last_activity
            }

    def _get_duration_minutes(self, thread: ConversationThread) -> int:
        """Calculate conversation duration in minutes."""
        try:
            created = _parse_iso_utc(thread.created_at)
            last = _parse_iso_utc(thread.last_activity)
            return int((last - created).total_seconds() / 60)
        except Exception:
            return 0

    def update_scope(self, session_id: str, scope: Dict) -> bool:
        """Update planning scope for a session.
        
        Args:
            session_id: Session to update
            scope: New scope values (week, scenario, product, etc.)
            
        Returns:
            True if updated
        """
        with self.lock:
            thread = self.active_conversations.get(session_id)
            if thread:
                thread.scope.update(scope)
                thread.last_activity = _utc_now_iso()
                self._save_conversation(thread)
                return True
            return False

    def close_conversation(self, session_id: str) -> bool:
        """Close and archive a conversation.
        
        Args:
            session_id: Session to close
            
        Returns:
            True if closed
        """
        with self.lock:
            thread = self.active_conversations.get(session_id)
            if thread:
                thread.active = False
                thread.last_activity = _utc_now_iso()
                self._save_conversation(thread)
                del self.active_conversations[session_id]
                logger.info(f"Closed conversation: {session_id}")
                return True
            return False

    def _save_conversation(self, thread: ConversationThread) -> None:
        """Save conversation to disk (append-only log).
        
        Args:
            thread: ConversationThread to save
        """
        try:
            # Append to JSONL log
            with open(self.conversations_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(thread.to_dict()) + '\n')
            
            # Update index
            self._update_index(thread.session_id, thread)
        except Exception as e:
            logger.error(f"Error saving conversation: {e}")

    def _update_index(self, session_id: str, thread: ConversationThread) -> None:
        """Update sessions index atomically for quick lookup.
        
        Args:
            session_id: Session to index
            thread: ConversationThread data
        """
        try:
            index = {}
            if self.sessions_index.exists():
                with open(self.sessions_index, 'r', encoding='utf-8') as f:
                    index = json.load(f)
            
            index[session_id] = {
                "user_id": thread.user_id,
                "created_at": thread.created_at,
                "last_activity": thread.last_activity,
                "message_count": len(thread.messages),
                "active": thread.active
            }
            
            tmp_path = self.sessions_index.with_suffix(".json.tmp")
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(index, f, indent=2)
            os.replace(tmp_path, self.sessions_index)
        except Exception as e:
            logger.error(f"Error updating index: {e}")

    def search_conversations(self, user_id: str, limit: int = 10) -> List[Dict]:
        """Get user's recent conversations.
        
        Args:
            user_id: User to search for
            limit: Maximum conversations to return
            
        Returns:
            List of conversation summaries
        """
        with self.lock:
            conversations = [
                {
                    "session_id": t.session_id,
                    "created_at": t.created_at,
                    "last_activity": t.last_activity,
                    "message_count": len(t.messages),
                    "scope": t.scope
                }
                for t in self.active_conversations.values()
                if t.user_id == user_id
            ]
            
            # Sort by last activity
            conversations.sort(key=lambda x: x["last_activity"], reverse=True)
            return conversations[:limit]

    def cleanup_old_sessions(self) -> int:
        """Remove sessions older than timeout period.
        
        Returns:
            Number of sessions cleaned up
        """
        with self.lock:
            cutoff = _utc_now() - timedelta(hours=self.session_timeout_hours)
            to_remove = []
            
            for session_id, thread in self.active_conversations.items():
                last_activity = _parse_iso_utc(thread.last_activity)
                if last_activity < cutoff:
                    to_remove.append(session_id)
            
            for session_id in to_remove:
                del self.active_conversations[session_id]
            
            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} old sessions")
            
            return len(to_remove)

    def get_statistics(self) -> Dict:
        """Get conversation manager statistics.
        
        Returns:
            Dictionary with statistics
        """
        with self.lock:
            active_count = len(self.active_conversations)
            total_messages = sum(len(t.messages) for t in self.active_conversations.values())
            avg_messages = total_messages / active_count if active_count > 0 else 0
            
            return {
                "active_sessions": active_count,
                "total_messages": total_messages,
                "average_messages_per_session": round(avg_messages, 2),
                "storage_file": str(self.conversations_file),
                "index_file": str(self.sessions_index)
            }
