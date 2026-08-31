from pathlib import Path


def test_ubuntu_launcher_preserves_existing_origin_without_explicit_override():
    script = Path("scripts/launch/ubuntu.sh").read_text(encoding="utf-8")

    assert 'local origin_explicit="$4"' in script
    assert 'if [[ "$origin_explicit" == "1" ]]; then' in script
    assert 'git -C "$repo_dir" remote set-url origin "$origin_url"' in script
    assert (
        'ubuntu_launch_git_for_origin "$active_origin_url" clone --origin origin "$active_origin_url" "$repo_dir"'
        in script
    )
    assert 'local ssh_command="${GIT_SSH_COMMAND:-}"' in script
    assert 'ssh_command="$(git -C "$repo_dir" config --get core.sshCommand' in script
    assert 'ssh_command="ssh"' in script
    assert "StrictHostKeyChecking=accept-new" not in script
    assert 'GIT_SSH_COMMAND="$ssh_command" git "$@"' in script
    assert (
        'ubuntu_launch_git_for_origin "$active_origin_url" -C "$repo_dir" fetch origin'
        in script
    )
    assert (
        'ubuntu_launch_git_for_origin "$active_origin_url" -C "$repo_dir" pull --rebase origin "$branch"'
        in script
    )
    assert (
        'ubuntu_launch_prepare_repo "$repo_dir" "$origin_url" "$branch" "$origin_explicit"'
        in script
    )
