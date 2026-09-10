from pathlib import Path

workflow_path = Path('.github/workflows/install-health.yml')
text = workflow_path.read_text(encoding='utf-8')
text = text.replace("""          - os_flavor: debian
            container_image: debian:13-slim
            python_version: '3.13'
            python_cache_version: python3.13
            db_backend: postgres
            test_shard: smoke
            pytest_args: \"\"
            full_pytest: false
          - os_flavor: ubuntu
            container_image: ubuntu:24.04
            python_version: '3.13'
            python_cache_version: python3.13
            db_backend: sqlite
            test_shard: smoke
            pytest_args: \"\"
            full_pytest: false
          - os_flavor: ubuntu
            container_image: ubuntu:24.04
            python_version: '3.11'
            python_cache_version: python3.11
            db_backend: sqlite
            test_shard: smoke
            pytest_args: \"\"
            full_pytest: false
""", """          - os_flavor: ubuntu22
            container_image: ubuntu:22.04
            python_version: '3.13'
            python_cache_version: python3.13
            db_backend: sqlite
            test_shard: smoke
            pytest_args: \"\"
            full_pytest: false
          - os_flavor: ubuntu22
            container_image: ubuntu:22.04
            python_version: '3.13'
            python_cache_version: python3.13
            db_backend: postgres
            test_shard: smoke
            pytest_args: \"\"
            full_pytest: false
""")
text = text.replace('test_shard: rest\n            pytest_args: --ignore=apps/ocpp/tests', 'test_shard: extra\n            pytest_args: --ignore=apps/ocpp/tests')
text = text.replace("matrix.test_shard == 'rest'", "matrix.test_shard == 'extra'")
workflow_path.write_text(text, encoding='utf-8')

reg_path = Path('apps/core/tests/reports/release_publish_regressions.py')
reg = reg_path.read_text(encoding='utf-8')
start = reg.index('def test_install_health_workflow_is_manual_only_not_scheduled() -> None:')
end = reg.index('\n\n@pytest.mark.parametrize(', start)
block = reg[start:end]
old_matrix_start = block.index('    assert [\n        (\n            entry["os_flavor"]')
old_matrix_end = block.index('\n\n    assert (\n        install_job["name"]', old_matrix_start)
new_matrix = '''    assert [
        (
            entry["os_flavor"],
            entry["container_image"],
            entry["python_version"],
            entry["db_backend"],
            entry["test_shard"],
            entry["pytest_args"],
            entry["full_pytest"],
        )
        for entry in matrix_entries
    ] == [
        ("debian", "debian:13-slim", "3.13", "sqlite", "ocpp", "apps/ocpp/tests", True),
        ("debian", "debian:13-slim", "3.13", "sqlite", "extra", "--ignore=apps/ocpp/tests", True),
        ("ubuntu22", "ubuntu:22.04", "3.13", "sqlite", "smoke", "", False),
        ("ubuntu22", "ubuntu:22.04", "3.13", "postgres", "smoke", "", False),
    ]'''
block = block[:old_matrix_start] + new_matrix + block[old_matrix_end:]
reg = reg[:start] + block + reg[end:]
reg_path.write_text(reg, encoding='utf-8')
