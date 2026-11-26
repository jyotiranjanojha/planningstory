"""
Conversation History API - endpoints for managing and retrieving chat history.
"""

from datetime import datetime, timezone
import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from ..services.conversation_manager import ConversationManager
from ..context_resolution import get_conversation_manager as _shared_conversation_manager

logger = logging.getLogger(__name__)

# Global conversation manager instance (defaults to the app-wide shared instance)
_conversation_manager: Optional[ConversationManager] = None


def get_conversation_manager() -> ConversationManager:
    """Get the shared conversation manager (same instance used by ContextStore and /api/chat)."""
    global _conversation_manager
    if _conversation_manager is None:
        return _shared_conversation_manager()
    return _conversation_manager


def set_conversation_manager(manager: ConversationManager) -> None:
    """Set conversation manager for dependency injection."""
    global _conversation_manager
    _conversation_manager = manager


# Create router
router = APIRouter(prefix="/api/conversation", tags=["conversation"])


@router.get("/history/{session_id}")
def get_conversation_history(
    session_id: str,
    limit: Optional[int] = Query(50, ge=1, le=1000)
):
    """Get conversation history for a session.
    
    Args:
        session_id: Session identifier
        limit: Maximum number of messages to return
        
    Returns:
        List of messages in the conversation
    """
    try:
        manager = get_conversation_manager()
        messages = manager.get_recent_messages(session_id, num_messages=limit)
        
        return {
            "session_id": session_id,
            "message_count": len(messages),
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "confidence": msg.confidence,
                    "intent": msg.intent,
                    "entities": msg.entities,
                    "evidence_sources": msg.evidence_sources
                }
                for msg in messages
            ]
        }
    except Exception as e:
        logger.error(f"Error retrieving history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/context/{session_id}")
def get_conversation_context(session_id: str):
    """Get conversation context summary.
    
    Args:
        session_id: Session identifier
        
    Returns:
        Context summary with entities, intents, duration
    """
    try:
        manager = get_conversation_manager()
        context = manager.get_context_summary(session_id)
        
        if not context:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return context
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clear/{session_id}")
def close_conversation(session_id: str):
    """Close and archive a conversation.
    
    Args:
        session_id: Session identifier
        
    Returns:
        Confirmation message
    """
    try:
        manager = get_conversation_manager()
        success = manager.close_conversation(session_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return {
            "status": "closed",
            "session_id": session_id,
            "message": "Conversation closed and archived"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/{user_id}")
def get_user_conversations(
    user_id: str,
    limit: Optional[int] = Query(10, ge=1, le=100)
):
    """Get user's recent conversations.
    
    Args:
        user_id: User identifier
        limit: Maximum conversations to return
        
    Returns:
        List of conversation summaries
    """
    try:
        manager = get_conversation_manager()
        conversations = manager.search_conversations(user_id, limit=limit)
        
        return {
            "user_id": user_id,
            "conversation_count": len(conversations),
            "conversations": conversations
        }
    except Exception as e:
        logger.error(f"Error retrieving user conversations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/feedback/{session_id}")
def submit_conversation_feedback(session_id: str, feedback: Dict):
    """Submit feedback on conversation quality.
    
    Args:
        session_id: Session identifier
        feedback: Feedback data with rating, corrections, etc.
        
    Returns:
        Confirmation message
    """
    try:
        manager = get_conversation_manager()
        thread = manager.get_conversation_thread(session_id)
        
        if not thread:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Could store feedback in a separate file for analysis
        # For now, just confirm receipt
        logger.info(f"Feedback received for session {session_id}: {feedback}")
        
        return {
            "status": "feedback_received",
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting feedback: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
def get_conversation_stats():
    """Get conversation manager statistics.
    
    Returns:
        System-wide statistics
    """
    try:
        manager = get_conversation_manager()
        stats = manager.get_statistics()
        
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "statistics": stats
        }
    except Exception as e:
        logger.error(f"Error retrieving stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cleanup")
def cleanup_old_sessions():
    """Clean up sessions older than timeout period.
    
    Returns:
        Cleanup result
    """
    try:
        manager = get_conversation_manager()
        removed_count = manager.cleanup_old_sessions()
        
        return {
            "status": "cleanup_completed",
            "sessions_removed": removed_count,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        raise HTTPException(status_code=500, detail=str(e))
