"""Tests for module: deployment"""

import pytest
import tempfile
import shutil
from pathlib import Path


class TestDeploymentDocker:
    """Tests for the Docker generator."""

    def test_generate_dockerfile_returns_string(self):
        """generate_dockerfile must return a string."""
        from modules.deployment.tools.generate_docker import generate_dockerfile

        result = generate_dockerfile("my-project")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_generate_dockerfile_has_multistage(self):
        """Dockerfile must use multi-stage build."""
        from modules.deployment.tools.generate_docker import generate_dockerfile

        result = generate_dockerfile("my-project")
        assert "FROM" in result
        assert result.count("FROM") >= 2

    def test_generate_dockerfile_has_nonroot_user(self):
        """Dockerfile must include non-root user."""
        from modules.deployment.tools.generate_docker import generate_dockerfile

        result = generate_dockerfile("my-project")
        assert "10001" in result or "app" in result.lower()

    def test_generate_dockerfile_has_healthcheck(self):
        """Dockerfile must include HEALTHCHECK."""
        from modules.deployment.tools.generate_docker import generate_dockerfile

        result = generate_dockerfile("my-project")
        assert "HEALTHCHECK" in result

    def test_generate_dockerfile_with_gunicorn(self):
        """Dockerfile with gunicorn must use gunicorn command."""
        from modules.deployment.tools.generate_docker import generate_dockerfile

        result = generate_dockerfile("my-project", use_gunicorn=True)
        assert "gunicorn" in result.lower()

    def test_generate_dockerfile_custom_port(self):
        """Dockerfile must respect custom port."""
        from modules.deployment.tools.generate_docker import generate_dockerfile

        result = generate_dockerfile("my-project", port=9000)
        assert "9000" in result

    def test_generate_dockerfile_custom_python_version(self):
        """Dockerfile must use specified Python version."""
        from modules.deployment.tools.generate_docker import generate_dockerfile

        result = generate_dockerfile("my-project", python_version="3.11")
        assert "3.11" in result

    def test_generate_dockerfile_valid_dockerfile(self):
        """Dockerfile must have valid structure."""
        from modules.deployment.tools.generate_docker import generate_dockerfile

        result = generate_dockerfile("my-project")
        lines = result.strip().splitlines()
        # Skip comment lines to find the first instruction
        instruction_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]

        assert instruction_lines[0].startswith("FROM")
        assert any("COPY" in line for line in lines)
        assert any("CMD" in line or "ENTRYPOINT" in line for line in lines)


class TestDeploymentCI:
    """Tests for the CI generator."""

    def test_generate_github_actions_returns_string(self):
        """generate_github_actions must return a string."""
        from modules.deployment.tools.generate_ci import generate_github_actions

        result = generate_github_actions("my-project")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_generate_github_actions_has_test_job(self):
        """CI workflow must include test job."""
        from modules.deployment.tools.generate_ci import generate_github_actions

        result = generate_github_actions("my-project")
        assert "test" in result.lower()

    def test_generate_github_actions_has_docker_jobs(self):
        """CI workflow with docker must include build and push."""
        from modules.deployment.tools.generate_ci import generate_github_actions

        result = generate_github_actions("my-project", with_docker=True)
        assert "build" in result.lower() or "docker" in result.lower()

    def test_generate_github_actions_custom_python(self):
        """CI workflow must use specified Python version."""
        from modules.deployment.tools.generate_ci import generate_github_actions

        result = generate_github_actions("my-project", python_version="3.11")
        assert "3.11" in result

    def test_analyze_github_workflow(self):
        """analyze_github_workflow must return findings list."""
        from modules.deployment.tools.generate_ci import analyze_github_workflow

        findings = analyze_github_workflow("not a valid workflow")
        assert isinstance(findings, list)


class TestDeploymentK6:
    """Tests for the K6 load test generator."""

    def test_generate_k6_script_returns_string(self):
        """generate_k6_script must return a string."""
        from modules.deployment.tools.generate_k6 import generate_k6_script

        endpoints = [{"method": "GET", "path": "/healthz", "name": "health"}]
        result = generate_k6_script("http://localhost:8000", endpoints)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_generate_k6_script_has_thresholds(self):
        """K6 script must include performance thresholds."""
        from modules.deployment.tools.generate_k6 import generate_k6_script

        endpoints = [{"method": "GET", "path": "/healthz", "name": "health"}]
        result = generate_k6_script("http://localhost:8000", endpoints)
        assert "threshold" in result.lower()

    def test_generate_smoke_test(self):
        """generate_smoke_test must return a valid script."""
        from modules.deployment.tools.generate_k6 import generate_smoke_test

        result = generate_smoke_test("http://localhost:8000")
        assert isinstance(result, str)
        assert "healthz" in result

    def test_generate_stress_test(self):
        """generate_stress_test must return a valid script."""
        from modules.deployment.tools.generate_k6 import generate_stress_test

        endpoints = [{"method": "GET", "path": "/healthz", "name": "health"}]
        result = generate_stress_test("http://localhost:8000", endpoints)
        assert isinstance(result, str)


class TestDeploymentK8s:
    """Tests for the K8s manifest generator."""

    def test_deployment_yaml_returns_string(self):
        """_deployment_yaml must return a string."""
        from modules.deployment.tools.generate_k8s import _deployment_yaml

        result = _deployment_yaml(
            app_name="my-app",
            image="my-app:latest",
            replicas=3,
            port=8000,
            env_vars={"DB_URL": "postgres://localhost"},
            cpu_request="100m",
            cpu_limit="500m",
            memory_request="128Mi",
            memory_limit="512Mi",
            prestop_sleep=5,
            termination_grace=30,
            graceful_shutdown_timeout=30,
        )
        assert isinstance(result, str)
        assert "Deployment" in result

    def test_service_yaml_returns_string(self):
        """_service_yaml must return a string."""
        from modules.deployment.tools.generate_k8s import _service_yaml

        result = _service_yaml("my-app", 8000)
        assert isinstance(result, str)
        assert "Service" in result

    def test_hpa_yaml_returns_string(self):
        """_hpa_yaml must return a string."""
        from modules.deployment.tools.generate_k8s import _hpa_yaml

        result = _hpa_yaml("my-app", min_replicas=2, max_replicas=10, cpu_target=70, memory_target=80)
        assert isinstance(result, str)
        assert "autoscaling" in result or "HPA" in result or "HorizontalPodAutoscaler" in result

    def test_pdb_yaml_returns_string(self):
        """_pdb_yaml must return a string."""
        from modules.deployment.tools.generate_k8s import _pdb_yaml

        result = _pdb_yaml("my-app", min_available=1)
        assert isinstance(result, str)
        assert "PodDisruptionBudget" in result or "pdb" in result.lower()

    def test_k8s_manifests_have_probes(self):
        """K8s deployment must include liveness/readiness probes."""
        from modules.deployment.tools.generate_k8s import _deployment_yaml

        result = _deployment_yaml(
            app_name="my-app",
            image="my-app:latest",
            replicas=3,
            port=8000,
            env_vars=None,
            cpu_request="100m",
            cpu_limit="500m",
            memory_request="128Mi",
            memory_limit="512Mi",
            prestop_sleep=5,
            termination_grace=30,
            graceful_shutdown_timeout=30,
        )
        assert "livenessProbe" in result
        assert "readinessProbe" in result


class TestDeploymentMCPTool:
    """Tests for MCP tool registration."""

    def test_docker_mcp_tool_registered(self):
        """Docker generator must have MCP_TOOL dict."""
        from modules.deployment.tools.generate_docker import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "generate_dockerfile"

    def test_ci_mcp_tool_registered(self):
        """CI generator must have MCP_TOOL dict."""
        from modules.deployment.tools.generate_ci import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "generate_github_actions"

    def test_k6_mcp_tool_registered(self):
        """K6 generator must have MCP_TOOL dict."""
        from modules.deployment.tools.generate_k6 import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "generate_k6_script"

    def test_k8s_mcp_tool_registered(self):
        """K8s generator must have MCP_TOOL dict."""
        from modules.deployment.tools.generate_k8s import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
