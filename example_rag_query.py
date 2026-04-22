"""Example script demonstrating RAG query functionality."""

import os
import django


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from scrapes.rag import rag_query  # noqa: E402
from scrapes.models import Website  # noqa: E402


def main():
    """Example usage of RAG query system."""

    print("=" * 80)
    print("RAG Query Example")
    print("=" * 80)

    indexed_count = Website.objects.exclude(indexed_with_model="").count()
    total_count = Website.objects.count()

    print(f"\nIndexed websites: {indexed_count}/{total_count}")

    if indexed_count == 0:
        print("\n⚠️  No indexed websites found!")
        print("Please run: python manage.py index_websites")
        print("\nOr index websites manually:")
        print("  from scrapes.models import Website")
        print("  from scrapes.rag import index_website")
        print("  for website in Website.objects.all():")
        print("      index_website(website)")
        return

    queries = [
        "Gimana kalo ada barang hilang di bagasi pesawat?",
    ]

    for query in queries:
        print(f"\n{'-' * 80}")
        print(f"Query: {query}")
        print(f"{'-' * 80}")

        result = rag_query(query, top_k=3)

        print(f"\nAnswer:\n{result['answer']}")

        print(f"\nSources ({len(result['sources'])} found):")
        for i, source in enumerate(result["sources"], 1):
            print(f"\n  [{i}] {source['website_url']}")
            print(f"      Similarity: {source['similarity_score']:.3f}")
            print(f"      Chunk preview: {source['chunk'][:100]}...")


if __name__ == "__main__":
    main()
