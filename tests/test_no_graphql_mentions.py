import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _paths_containing_text(paths):
    offending = []
    for path in paths:
        content = path.read_text(encoding="utf-8").lower()
        if "graphql" in content:
            offending.append(path)
    return offending


def _fixture_files_without_graphql(prefix: str):
    fixtures_dir = BASE_DIR / "nodes" / "fixtures"
    fixture_files = fixtures_dir.glob(prefix)

    offending = []
    for fixture_path in fixture_files:
        content = fixture_path.read_text(encoding="utf-8")
        if "graphql" in content.lower():
            offending.append(fixture_path)
            continue

        data = json.loads(content)
        for entry in data:
            has_graphql = False
            for value in entry.get("fields", {}).values():
                if isinstance(value, str) and "graphql" in value.lower():
                    offending.append(fixture_path)
                    has_graphql = True
                    break

            if has_graphql:
                break

    return offending


def test_nodefeature_fixtures_have_no_graphql_mentions():
    offending = _fixture_files_without_graphql("node_features__*.json")

    assert offending == [], f"Remove GraphQL mentions from fixtures: {offending}"


def test_noderole_fixtures_have_no_graphql_mentions():
    offending = _fixture_files_without_graphql("node_roles__*.json")

    assert offending == [], f"Remove GraphQL mentions from fixtures: {offending}"


def test_nodefeature_docs_have_no_graphql_mentions():
    doc_paths = [
        BASE_DIR / "docs" / "cookbooks" / "node-features.md",
        BASE_DIR / "docs" / "architecture_manifest.md",
        BASE_DIR / "docs" / "development" / "pytest-role-markers.md",
    ]

    offending = _paths_containing_text(doc_paths)

    assert offending == [], f"Remove GraphQL mentions from docs: {offending}"
