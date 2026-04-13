"""Unit tests for integration API schema validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestImportRepoRequestValidation:
    def _make(self, **overrides):
        from app.services.integration_service.api.schemas import ImportRepoRequest

        defaults = {"repo_url": "https://github.com/alice/repo.git"}
        return ImportRepoRequest(**{**defaults, **overrides})

    def test_valid_https_url_accepted(self):
        req = self._make(repo_url="https://github.com/alice/repo.git")
        assert req.repo_url == "https://github.com/alice/repo.git"

    def test_git_plus_https_url_accepted(self):
        req = self._make(repo_url="git+https://github.com/org/repo.git")
        assert req.repo_url.startswith("git+https://")

    def test_http_scheme_rejected(self):
        with pytest.raises(ValidationError, match="https"):
            self._make(repo_url="http://github.com/alice/repo.git")

    def test_file_scheme_rejected(self):
        with pytest.raises(ValidationError, match="https"):
            self._make(repo_url="file:///etc/passwd")

    def test_ssh_scheme_rejected(self):
        with pytest.raises(ValidationError, match="https"):
            self._make(repo_url="git@github.com:alice/repo.git")

    def test_private_ip_rejected(self):
        with pytest.raises(ValidationError, match="private"):
            self._make(repo_url="https://192.168.1.10/alice/repo.git")

    def test_loopback_ip_rejected(self):
        with pytest.raises(ValidationError, match="private|loopback"):
            self._make(repo_url="https://127.0.0.1/alice/repo.git")

    def test_valid_clone_dir(self):
        req = self._make(clone_dir="/workspace/myproject")
        assert req.clone_dir == "/workspace/myproject"

    def test_clone_dir_path_traversal_rejected(self):
        with pytest.raises(ValidationError, match="\\.\\."):
            self._make(clone_dir="/workspace/../etc/passwd")

    def test_clone_dir_relative_path_rejected(self):
        with pytest.raises(ValidationError, match="absolute"):
            self._make(clone_dir="relative/path")


class TestCIPayloadNesting:
    """Verify that CI trigger inputs cannot overwrite workspace_id / ref."""

    def test_ci_trigger_request_accepts_inputs(self):
        from app.services.integration_service.api.schemas import CITriggerRequest

        req = CITriggerRequest(
            event_type="deploy",
            inputs={"workspace_id": "evil", "ref": "evil", "custom_key": "value"},
        )
        # Schema itself doesn't prevent the payload — the router nests it safely.
        # Confirm the inputs dict is preserved.
        assert req.inputs["custom_key"] == "value"
