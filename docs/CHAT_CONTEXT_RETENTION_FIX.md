# Chat Assistant Context Retention Fix

## Problem Analysis

The Chat Assistant was unable to remember context across multiple conversation turns due to:

1. **No Server-Side Persistent Conversation Storage** — Chat history was only kept in-memory, per-request
2. **Client-Managed History** — The server expected clients to send history each turn and didn't return updated history
3. **SessionContext Dead Code** — Had a `conversation_history` field that was never properly populated
4. **Short Session Timeout** — 30-minute timeout cleared all implicit context
5. **No History Retrieval** — When a new session started, previous context was lost

## Solution Overview

Implemented a **complete server-side conversation persistence layer** with automatic history management:

### 🎯 Core Components

#### 1. **ConversationManager** (`webapp/app/services/conversation_manager.py`)
- Persistent storage of all chat messages with metadata
- Automatic session creation and lifecycle management
- Thread-safe operations with file-based persistence (JSONL + JSON index)
- Features:
  - Message storage with timestamps, confidence, intent, entities, evidence sources
  - Schema tracking with versioning
  - Context summarization (entities, intents, scope)
  - User conversation search
  - Automatic cleanup of old sessions
  - 24-hour session retention (configurable)

#### 2. **Enhanced ContextStore** (updated `context_resolution.py`)
- Integrated ConversationManager for persistent storage
- Automatically loads conversation history on session creation
- Stores every user question and assistant response
- Maintains conversation continuity across requests
- Updates implicit context (current_item, current_location, etc.)

#### 3. **Chat Endpoint Updates** (updated `main.py`)
- Records user questions to ConversationManager before processing
- Logs assistant responses with confidence and evidence sources
- Returns updated conversation history in response
- Enables client to build future requests with full context

#### 4. **Conversation History API** (`webapp/app/routes/conversation_history.py`)
- `/api/conversation/history/{session_id}` — Retrieve full conversation
- `/api/conversation/context/{session_id}` — Get context summary with entities/intents
- `/api/conversation/user/{user_id}` — Get all user's recent conversations
- `/api/conversation/feedback/{session_id}` — Submit feedback on quality
- `/api/conversation/stats` — System-wide statistics
- `/api/conversation/cleanup` — Clean up old sessions

## How It Works

### Turn 1: Initial Question
```
User: "How much demand for item 100?"
  ↓
1. Chat endpoint receives question
2. ConversationManager creates session and stores user message
3. Run chat assistant with full context
4. Log assistant response with entities, confidence, evidence sources
5. Return response + full conversation history to client
  ↓
Client receives: response + conversation_history = [{user: "How much..."}, {assistant: "..."}]
```

### Turn 2: Follow-Up Question
```
User: "Why is it unmet?" (with session_id from Turn 1)
  ↓
1. Chat endpoint loads session
2. ConversationManager loads all prior messages (automatically)
3. SessionContext is enriched with: current_item=100 (from Turn 1)
4. Context resolver includes previous messages for "it" pronoun resolution
5. Run chat assistant with full context
6. Log response
  ↓
Client receives: response + conversation_history = [{user: "How much..."}, {asst: "..."}, {user: "Why..."}, {asst: "..."}]
```

### Turn 3: Later (After 35 minutes)
```
User: "Can you explain the constraint?" (same session_id)
  ↓
1. ConversationManager loads session from persistent storage
2. Even though 30-min in-memory timeout expired, persistent history is still available
3. Full conversation context restored
4. "the constraint" resolved from Turn 1 and Turn 2
  ↓
Context is fully maintained!
```

## Data Files Generated

```
data/
├── conversations.jsonl          # Append-only log of all conversations
│   └── One JSON object per line, one line per conversation update
│
├── sessions_index.json          # Quick lookup index
│   └── session_id → metadata (user_id, created_at, last_activity, message_count)
│
└── (future) feedback_archive/   # User ratings and corrections (extensible)
```

### Example JSONL Entry
```json
{
  "session_id": "abc123",
  "user_id": "user456",
  "messages": [
    {
      "role": "user",
      "content": "How much demand for item 100?",
      "timestamp": "2026-09-03T10:00:00.123456",
      "confidence": null,
      "intent": "demand_analysis",
      "entities": ["item 100"],
      "evidence_sources": []
    },
    {
      "role": "assistant",
      "content": "The demand for item 100 is 5000 units",
      "timestamp": "2026-09-03T10:00:02.456789",
      "confidence": 0.92,
      "intent": "demand_analysis",
      "entities": ["item 100"],
      "evidence_sources": ["by_output_depdmdstatic", "by_output_inddmdview"]
    }
  ],
  "created_at": "2026-09-03T10:00:00.000000",
  "last_activity": "2026-09-03T10:00:02.456789",
  "scope": {"week_id": "202609", "scenario_id": "scenario1"},
  "active": true
}
```

## Configuration

### Session Timeout
- **Default**: 24 hours (persistent storage keeps conversations longer)
- **In-memory cache**: Conversations active in last 24 hours are kept loaded
- **Update** in `ConversationManager.__init__()`:
  ```python
  self.session_timeout_hours = 24  # Change this value
  ```

### Max History Messages
- **Default**: 50 messages per conversation
- **Why**: Prevents runaway memory usage while preserving context
- **Update** in `ConversationManager.__init__()`:
  ```python
  self.max_history_messages = 50  # Change this value
  ```

### Data Directory
- **Default**: `data/` (in project root)
- **Customizable** when creating ConversationManager:
  ```python
  conv_manager = ConversationManager(data_dir="/custom/path")
  ```

## Integration Points

### 1. **ContextStore** — Automatic History Loading
```python
# When a session context is retrieved, it now loads history:
context_store = ContextStore(conversation_manager=conv_manager)
ctx = context_store.get_or_create(session_id)
# ctx.conversation_history now populated from persistent storage!
```

### 2. **Chat Endpoint** — Automatic Logging
```python
@app.post("/api/chat")
def chat(req: ChatRequest):
    # Conversation manager auto-creates session
    conv_manager = ConversationManager()
    conv_manager.start_conversation(session_id, req.user_id, scope)
    
    # Process question...
    
    # Auto-log response
    conv_manager.add_message(
        session_id, "assistant", response_text,
        intent=intent, entities=entities,
        confidence=confidence, evidence_sources=sources
    )
    
    # Return with history
    response["conversation_history"] = [
        {"role": msg.role, "content": msg.content}
        for msg in conv_manager.get_conversation_history(session_id)
    ]
```

### 3. **Client Usage**
```javascript
// No longer need to manually track history!
// Server returns it in response:

const response = await fetch("/api/chat", {
  method: "POST",
  body: JSON.stringify({
    session_id: sessionId,
    question: "Why is demand unmet?",
    // No need to send history - server has it!
  })
});

const data = await response.json();
// data.conversation_history contains full thread
// Use it for next turn, UI display, etc.
```

## Key Benefits

✅ **Automatic Context Retention** — No need to manually track history  
✅ **Persistent Storage** — Conversations survive application restarts  
✅ **Full Pronoun Resolution** — "it", "that", "the constraint" now work reliably  
✅ **Extended Session Life** — 24-hour retention vs 30-min timeout  
✅ **Evidence Tracking** — Every answer logs what sources were used  
✅ **Confidence Calibration** — Tracks confidence scores for quality analysis  
✅ **User Search** — Find all conversations by user ID  
✅ **Cleanup Automation** — Old sessions automatically cleaned up  
✅ **Extensible** — Ready for adding feedback, ratings, corrections  

## Monitoring

### Check Conversation Stats
```bash
curl http://localhost:8000/api/conversation/stats
```

Response:
```json
{
  "timestamp": "2026-09-03T10:15:30.123456",
  "statistics": {
    "active_sessions": 12,
    "total_messages": 156,
    "average_messages_per_session": 13.0,
    "storage_file": "data/conversations.jsonl",
    "index_file": "data/sessions_index.json"
  }
}
```

### Retrieve Full Conversation
```bash
curl http://localhost:8000/api/conversation/history/abc123?limit=20
```

### Get Conversation Context
```bash
curl http://localhost:8000/api/conversation/context/abc123
```

Response:
```json
{
  "session_id": "abc123",
  "message_count": 8,
  "duration_minutes": 15,
  "scope": {"week_id": "202609", "scenario_id": "scenario1"},
  "recent_entities": ["item 100", "location SAL1", "resource RES1"],
  "recent_intents": ["demand_analysis", "root_cause", "constraint_analysis"],
  "last_activity": "2026-09-03T10:15:30.123456"
}
```

## Troubleshooting

### Context Still Not Retained?

1. **Check that session_id is consistent**
   ```javascript
   // Turn 1
   const resp1 = await chat({session_id: "abc123", question: "..."});
   
   // Turn 2 - MUST use same session_id
   const resp2 = await chat({session_id: "abc123", question: "..."});
   ```

2. **Verify conversation is being stored**
   ```bash
   curl http://localhost:8000/api/conversation/history/abc123
   ```

3. **Check data directory exists and is writable**
   ```bash
   ls -la data/conversations.jsonl
   ls -la data/sessions_index.json
   ```

4. **Review logs for errors**
   ```bash
   grep ConversationManager logs/*.log
   ```

### Performance Issues?

- Check number of messages per conversation (default 50, can tune)
- Review `get_statistics()` to see active session count
- Consider archiving old conversations after 30 days
- Run `POST /api/conversation/cleanup` to remove expired sessions

## Testing

### Manual Test Flow

1. **Start conversation**
   ```bash
   curl -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"session_id":"test1","question":"How much demand for item 100?","week_id":"202609"}'
   ```

2. **Follow-up question** (same session_id)
   ```bash
   curl -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"session_id":"test1","question":"Why is it unmet?","week_id":"202609"}'
   ```

3. **Verify context was used**
   ```bash
   curl http://localhost:8000/api/conversation/context/test1
   # Should show "item 100" in recent_entities
   ```

4. **View full history**
   ```bash
   curl http://localhost:8000/api/conversation/history/test1
   # Should show both user questions and both assistant responses
   ```

## Next Steps

1. **Optional**: Add feedback collection UI to capture ratings/corrections
2. **Optional**: Implement analytics on conversation patterns
3. **Optional**: Export conversations for audit trail
4. **Optional**: Add multi-turn conversation templates for common workflows
5. **Optional**: Implement conversation search across all users (admin feature)

---

**The Chat Assistant now fully remembers context across all conversation turns!**
