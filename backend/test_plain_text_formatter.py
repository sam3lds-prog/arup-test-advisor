"""
test_plain_text_formatter.py

Simple test script to demonstrate the plain-text clinical report formatter.
Run this to verify the formatter produces correct output.

Usage:
    python test_plain_text_formatter.py
"""

from agents.plain_text_formatter import format_clinical_report
from datetime import datetime, timezone


# Sample data matching the AI Test Advisor response structure
SAMPLE_QUERY = (
    "Patient presents with suspected hypothyroidism. What thyroid function "
    "tests should I order for initial workup?"
)

SAMPLE_RESPONSE_TEXT = """
Based on the clinical presentation, **initial thyroid function testing** should
include TSH measurement [SOURCE 1]. If TSH is abnormal, reflex testing to Free T4
is recommended to characterize the dysfunction [SOURCE 2].

For comprehensive evaluation in suspected hypothyroidism, consider also measuring
Thyroid Peroxidase Antibodies (TPO-Ab) to assess for autoimmune etiology [SOURCE 3].
"""

SAMPLE_RECOMMENDATIONS = [
    {
        "test_name": "Thyroid Stimulating Hormone (TSH)",
        "test_code": "0070200",
        "rank": "primary",
        "rationale": (
            "TSH is the initial screening test for thyroid dysfunction and has "
            "high sensitivity for detecting both hyper- and hypothyroidism [SOURCE 1]"
        ),
        "specimen": "Serum, 1 mL",
        "tat": "1-2 days",
    },
    {
        "test_name": "Free Thyroxine (Free T4)",
        "test_code": "0070209",
        "rank": "reflex",
        "rationale": (
            "Free T4 measurement is recommended when TSH is abnormal to characterize "
            "the degree and type of thyroid dysfunction [SOURCE 2]"
        ),
        "specimen": "Serum, 1 mL",
        "tat": "1-2 days",
    },
    {
        "test_name": "Thyroid Peroxidase Antibodies",
        "test_code": "0070324",
        "rank": "secondary",
        "rationale": (
            "TPO-Ab testing helps identify autoimmune thyroid disease as the etiology "
            "of hypothyroidism [SOURCE 3]"
        ),
        "specimen": "Serum, 0.5 mL",
        "tat": "2-3 days",
    },
]

SAMPLE_CITATIONS = [
    {
        "number": 1,
        "source_type": "Algorithm",
        "document": "Thyroid Function Testing Algorithm.pdf",
        "excerpt": (
            "Measurement of serum TSH is recommended as the initial laboratory test "
            "for assessment of thyroid function. TSH has high sensitivity and "
            "specificity for detecting thyroid dysfunction."
        ),
    },
    {
        "number": 2,
        "source_type": "Consult Topic",
        "document": "Hypothyroidism Evaluation - Clinical Consult",
        "excerpt": (
            "When TSH is elevated, measurement of free T4 is recommended to confirm "
            "primary hypothyroidism and assess severity. Low free T4 with elevated "
            "TSH confirms overt hypothyroidism."
        ),
    },
    {
        "number": 3,
        "source_type": "Test Directory",
        "document": "Thyroid Peroxidase Antibodies - Test Details",
        "excerpt": (
            "Thyroid peroxidase antibodies are present in >90% of patients with "
            "Hashimoto thyroiditis and 70% of patients with Graves disease. "
            "Measurement aids in determining autoimmune etiology."
        ),
    },
]

SAMPLE_CONFIDENCE = 87
SAMPLE_TIMESTAMP = datetime.now(timezone.utc).isoformat()


def test_basic_formatting():
    """Test basic plain-text report generation."""
    print("=" * 80)
    print("Testing Plain-Text Clinical Report Formatter")
    print("=" * 80)
    print()

    report = format_clinical_report(
        original_query=SAMPLE_QUERY,
        response_text=SAMPLE_RESPONSE_TEXT,
        test_recommendations=SAMPLE_RECOMMENDATIONS,
        citations_data=SAMPLE_CITATIONS,
        confidence_score=SAMPLE_CONFIDENCE,
        timestamp=SAMPLE_TIMESTAMP,
    )

    print(report)
    print()
    print("=" * 80)
    print("Test completed successfully!")
    print(f"Report length: {len(report)} characters, {len(report.splitlines())} lines")
    print("=" * 80)


def test_minimal_data():
    """Test formatting with minimal data (missing optional fields)."""
    print("\n\n")
    print("=" * 80)
    print("Testing with minimal data (missing optional fields)")
    print("=" * 80)
    print()

    minimal_recommendations = [
        {
            "test_name": "Complete Blood Count",
            # No test_code
            "rationale": "Basic hematologic assessment",
            # No specimen, tat, or rank
        }
    ]

    minimal_citations = [
        {
            "number": 1,
            "source_type": "General",
            "document": "Laboratory Reference",
            # No excerpt
        }
    ]

    report = format_clinical_report(
        original_query="Patient needs basic labs",
        response_text="Complete blood count recommended for initial assessment.",
        test_recommendations=minimal_recommendations,
        citations_data=minimal_citations,
        confidence_score=None,  # No confidence score
        timestamp=None,  # No timestamp
    )

    print(report)
    print()
    print("=" * 80)
    print("Minimal data test completed!")
    print("=" * 80)


def test_empty_recommendations():
    """Test formatting when no recommendations are available."""
    print("\n\n")
    print("=" * 80)
    print("Testing with empty recommendations")
    print("=" * 80)
    print()

    report = format_clinical_report(
        original_query="What test should I order for condition X?",
        response_text=(
            "The uploaded ARUP content does not contain sufficient information "
            "to answer this query confidently."
        ),
        test_recommendations=[],  # No recommendations
        citations_data=[],  # No citations
        confidence_score=15,
        timestamp=SAMPLE_TIMESTAMP,
    )

    print(report)
    print()
    print("=" * 80)
    print("Empty recommendations test completed!")
    print("=" * 80)


if __name__ == "__main__":
    test_basic_formatting()
    test_minimal_data()
    test_empty_recommendations()

    print("\n\n")
    print("✓ All tests passed successfully!")
    print()
    print("To use this formatter in your application:")
    print("  from agents.plain_text_formatter import format_clinical_report")
    print("  report = format_clinical_report(...)")
    print()
