"""
test_export_endpoint.py

Quick test to verify the plain-text export endpoint would work with
frontend data structure.

This simulates what the frontend MessageBubble would send to the backend.
"""

from agents.plain_text_formatter import format_from_chat_response
from datetime import datetime, timezone


# Simulate the frontend data structure (what MessageBubble passes as `d`)
FRONTEND_MESSAGE_DATA = {
    "text": "What tests should I order for a patient with suspected iron deficiency?",
    "answer": (
        "For suspected iron deficiency, the recommended initial testing includes "
        "serum ferritin measurement [SOURCE 1]. If ferritin is borderline or low-normal, "
        "additional iron studies including transferrin saturation and total iron binding "
        "capacity should be considered [SOURCE 2]."
    ),
    "recommendations": [
        {
            "test_name": "Ferritin",
            "test_code": "0020416",
            "rank": "primary",
            "rationale": (
                "Serum ferritin is the most sensitive and specific initial test for "
                "iron deficiency [SOURCE 1]"
            ),
            "specimen": "Serum, 1 mL",
            "tat": "1 day",
        },
        {
            "test_name": "Iron and Total Iron Binding Capacity",
            "test_code": "0020420",
            "rank": "secondary",
            "rationale": (
                "Provides comprehensive iron status assessment when ferritin is "
                "inconclusive [SOURCE 2]"
            ),
            "specimen": "Serum, 1 mL",
            "tat": "1-2 days",
        },
    ],
    "citations": [
        {
            "number": 1,
            "source_type": "Consult Topic",
            "document": "Iron Deficiency Evaluation",
            "excerpt": (
                "Ferritin is the most sensitive and specific laboratory test for "
                "iron deficiency. Values <15 ng/mL are diagnostic of iron deficiency."
            ),
        },
        {
            "number": 2,
            "source_type": "Test Directory",
            "document": "Iron Studies Panel - Test Details",
            "excerpt": (
                "Iron studies panel includes serum iron, TIBC, and transferrin saturation. "
                "Useful when ferritin results are equivocal or in chronic disease states."
            ),
        },
    ],
    "confidence": {
        "score": 85,
        "tier": "high",
    },
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "session_id": "test_session_12345678",
    "message_index": 2,
}


def test_frontend_data_structure():
    """Test that the formatter correctly handles frontend message data."""
    print("=" * 80)
    print("Testing Plain-Text Export with Frontend Data Structure")
    print("=" * 80)
    print()

    # This is what the frontend will send to the backend
    print("Frontend data structure:")
    print(f"  - Has 'text' field (query): {bool(FRONTEND_MESSAGE_DATA.get('text'))}")
    print(f"  - Has 'answer' field: {bool(FRONTEND_MESSAGE_DATA.get('answer'))}")
    print(f"  - Recommendations count: {len(FRONTEND_MESSAGE_DATA.get('recommendations', []))}")
    print(f"  - Citations count: {len(FRONTEND_MESSAGE_DATA.get('citations', []))}")
    print(f"  - Confidence score: {FRONTEND_MESSAGE_DATA.get('confidence', {}).get('score')}")
    print()

    # Call the formatter
    try:
        report = format_from_chat_response(FRONTEND_MESSAGE_DATA)
        print("✓ Formatter executed successfully!")
        print()
        print("Generated report:")
        print("-" * 80)
        print(report)
        print("-" * 80)
        print()
        print(f"Report length: {len(report)} characters, {len(report.splitlines())} lines")
        print()
        print("✓ Test completed successfully!")
        return True
    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_frontend_data_structure()
    exit(0 if success else 1)
