---
name: reverse-engineer
description: Trace how an existing feature works in the repository. Use for codebase onboarding, architecture mapping, behavior investigation, and documenting data flow or dependencies.
---

# Reverse Engineer

1. Identify the feature and scenario to explain. Choose its normal entry point, such as a screen action, API route, command, or background job.
2. Trace the call path through the source. Identify inputs, branching decisions, state changes, persistent storage, external dependencies, and the observable result. Follow interfaces to the implementations selected by configuration.
3. Check relevant tests, fixtures, and configuration to corroborate the trace. Call out differences between platforms or environments when they affect the scenario.
4. Report the path in execution order. Cite repository-relative file paths and symbols or line numbers. Include error handling, known side effects, and any behavior that still needs runtime verification.

Keep the investigation read-only. Use source already available in the repository and avoid exposing credentials or private data in the report.
