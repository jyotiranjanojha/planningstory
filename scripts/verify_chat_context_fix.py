#!/usr/bin/env python3
"""
Chat Context Retention - Integration Verification Script

Verify that the context retention fix is properly installed and working.
Run this after deployment to confirm everything is operational.
"""

import sys
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def check_files_exist():
    """Verify all new/modified files are in place."""
    print("\n📋 Checking files...")
    
    required_files = [
        "webapp/app/services/conversation_manager.py",
        "webapp/app/routes/conversation_history.py",
    ]
    
    modified_files = [
        "webapp/app/context_resolution.py",
        "webapp/app/main.py",
    ]
    
    all_good = True
    
    for file in required_files:
        path = REPO_ROOT / file
        if path.exists():
            size = path.stat().st_size
            print(f"  ✅ {file} ({size:,} bytes)")
        else:
            print(f"  ❌ {file} (MISSING)")
            all_good = False
    
    for file in modified_files:
        path = REPO_ROOT / file
        if path.exists():
            print(f"  ✅ {file} (modified)")
        else:
            print(f"  ❌ {file} (MISSING)")
            all_good = False
    
    return all_good


def check_imports():
    """Verify imports are correct."""
    print("\n📚 Checking imports...")
    
    try:
        from webapp.app.services.conversation_manager import ConversationManager, ChatMessage, ConversationThread
        print("  ✅ ConversationManager imports work")
    except Exception as e:
        print(f"  ❌ ConversationManager import failed: {e}")
        return False
    
    try:
        from webapp.app.routes.conversation_history import router
        print("  ✅ Conversation history routes import works")
    except Exception as e:
        print(f"  ❌ Conversation history routes import failed: {e}")
        return False
    
    try:
        from webapp.app.context_resolution import ContextStore
        print("  ✅ ContextStore import works")
    except Exception as e:
        print(f"  ❌ ContextStore import failed: {e}")
        return False
    
    return True


def check_conversation_manager():
    """Verify ConversationManager functionality."""
    print("\n🔍 Checking ConversationManager...")
    
    try:
        from webapp.app.services.conversation_manager import ConversationManager
        
        # Create temporary manager
        mgr = ConversationManager(data_dir="data_test")
        
        # Test basic operations
        conv = mgr.start_conversation("test_session", "test_user")
        print(f"  ✅ Session creation works (session_id: {conv.session_id})")
        
        success = mgr.add_message(
            "test_session",
            "user",
            "Test question",
            intent="test",
            entities=["test_entity"],
            metadata={"test": True}
        )
        if success:
            print("  ✅ Message addition works")
        else:
            print("  ❌ Message addition failed")
            return False
        
        history = mgr.get_conversation_history("test_session")
        if len(history) == 1:
            print(f"  ✅ History retrieval works ({len(history)} message)")
        else:
            print(f"  ❌ History retrieval returned wrong count: {len(history)}")
            return False
        
        context = mgr.get_context_summary("test_session")
        if "session_id" in context:
            print(f"  ✅ Context summary works")
        else:
            print(f"  ❌ Context summary missing required fields")
            return False
        
        stats = mgr.get_statistics()
        if "active_sessions" in stats:
            print(f"  ✅ Statistics work (active_sessions: {stats['active_sessions']})")
        else:
            print(f"  ❌ Statistics missing required fields")
            return False
        
        # Cleanup
        mgr.close_conversation("test_session")
        print("  ✅ Session closure works")
        
        return True
        
    except Exception as e:
        print(f"  ❌ ConversationManager test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_context_store_integration():
    """Verify ContextStore integration with ConversationManager."""
    print("\n🔗 Checking ContextStore integration...")
    
    try:
        from webapp.app.context_resolution import ContextStore
        from webapp.app.services.conversation_manager import ConversationManager
        
        # Create test manager
        mgr = ConversationManager(data_dir="data_test2")
        
        # Create context store with manager
        store = ContextStore(conversation_manager=mgr)
        print("  ✅ ContextStore can be created with ConversationManager")
        
        # Test context creation
        ctx = store.get_or_create("test_session_2")
        if ctx.session_id == "test_session_2":
            print("  ✅ Context creation works")
        else:
            print("  ❌ Context creation failed")
            return False
        
        # Test update after query (should log to conversation manager)
        success = store.update_after_query(
            "test_session_2",
            entities={"item": "100"},
            last_intent="test_intent",
            user_query="Test question",
            response_summary="Test response"
        )
        if success is None or success:
            print("  ✅ Query update works")
        else:
            print("  ❌ Query update failed")
            return False
        
        # Verify message was logged
        history = mgr.get_conversation_history("test_session_2")
        if len(history) >= 1:
            print(f"  ✅ ConversationManager received messages ({len(history)} logged)")
        else:
            print(f"  ⚠️  No messages logged to ConversationManager")
            return False
        
        return True
        
    except Exception as e:
        print(f"  ❌ ContextStore integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_data_directory():
    """Verify data directory structure."""
    print("\n💾 Checking data directory...")
    
    data_dir = Path("data")
    
    if data_dir.exists():
        print(f"  ✅ {data_dir}/ exists")
        
        # Check for conversation files
        conv_file = data_dir / "conversations.jsonl"
        index_file = data_dir / "sessions_index.json"
        
        if conv_file.exists():
            size = conv_file.stat().st_size
            print(f"  ✅ conversations.jsonl exists ({size:,} bytes)")
        else:
            print(f"  ⓘ  conversations.jsonl not yet created (will be on first use)")
        
        if index_file.exists():
            try:
                with open(index_file) as f:
                    index = json.load(f)
                print(f"  ✅ sessions_index.json exists ({len(index)} sessions)")
            except Exception as e:
                print(f"  ⚠️  sessions_index.json exists but invalid JSON: {e}")
        else:
            print(f"  ⓘ  sessions_index.json not yet created (will be on first use)")
    else:
        print(f"  ⓘ  {data_dir}/ will be created on first request")
    
    return True


def check_documentation():
    """Verify documentation files exist."""
    print("\n📖 Checking documentation...")
    
    docs = [
        "docs/CHAT_CONTEXT_RETENTION_FIX.md",
        "docs/CHAT_CONTEXT_FIX_SUMMARY.md",
        "docs/CHAT_CONTEXT_BEFORE_AFTER.md",
    ]
    
    all_found = True
    for doc in docs:
        path = Path(doc)
        if path.exists():
            size = path.stat().st_size
            print(f"  ✅ {doc} ({size:,} bytes)")
        else:
            print(f"  ⚠️  {doc} (MISSING)")
            all_found = False
    
    return all_found


def print_summary(results):
    """Print verification summary."""
    print("\n" + "="*60)
    print("INTEGRATION VERIFICATION SUMMARY")
    print("="*60)
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    failed = total - passed
    
    if failed == 0:
        print(f"\n✅ All {total} checks PASSED!")
        print("\n🚀 Chat context retention is properly installed.")
        print("\nNext steps:")
        print("  1. Restart the FastAPI application")
        print("  2. Test with POST /api/chat endpoint")
        print("  3. Verify conversation_history in response")
        print("  4. Check /api/conversation/history/{session_id}")
        return 0
    else:
        print(f"\n⚠️  {passed}/{total} checks passed, {failed} failed")
        print("\nPlease review failures above and fix before deployment.")
        return 1


def main():
    """Run all verification checks."""
    print("\n" + "="*60)
    print("CHAT CONTEXT RETENTION - INTEGRATION VERIFICATION")
    print("="*60)
    
    results = {
        "Files": check_files_exist(),
        "Imports": check_imports(),
        "ConversationManager": check_conversation_manager(),
        "ContextStore Integration": check_context_store_integration(),
        "Data Directory": check_data_directory(),
        "Documentation": check_documentation(),
    }
    
    exit_code = print_summary(results)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
