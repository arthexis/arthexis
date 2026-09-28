def test_base_watchtower_stage_excludes_public_surface_convergence(watchtower_deploy_workflow: str) -> None:
    workflow = watchtower_deploy_workflow
    assert "default: arthexis" in workflow
    assert "Expose Arthexis publicly through Gway recipe" not in workflow
    assert "Verify public Markdown root" not in workflow
    assert "./deploy/remote-dns.rx" not in workflow
    assert "remote.arthexis.com" not in workflow
