[project]
name = "sap-health-check-agent"
version = "0.1.0"
description = "SAP HANA Health Check Cloud Run streaming service"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "python-dotenv>=1.0.1",
    "pydantic>=2.11.0",
    "google-cloud-storage>=2.18.0",
    "google-cloud-aiplatform[adk,agent-engines]>=1.93.0",
    "google-genai>=1.0.0",
    "google-adk>=1.5.0",
    "requests>=2.32.0",
    "numpy>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.5",
    "pytest-asyncio>=0.26.0",
    "ruff>=0.6.0",
]

[build-system]
requires = ["setuptools>=75.0.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["sap_hc_agent*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-vv -s"
log_cli = true
log_level = "INFO"

[tool.ruff]
line-length = 100
target-version = "py312"
