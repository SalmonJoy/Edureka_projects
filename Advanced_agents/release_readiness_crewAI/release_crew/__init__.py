"""Four-agent learning example. No optional telemetry exports are needed."""

import os

# Configure this before importing CrewAI; the UI uses our own small activity trace.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
