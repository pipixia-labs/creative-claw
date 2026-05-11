+++
name = "CodeGenerationExpert"
enabled = true
input_types = ["text", "code", "markdown"]
output_types = ["code", "html", "text_file"]
routing_keywords = ["code generation", "html artifact", "prototype", "dashboard", "landing page", "mobile app", "deck"]
parameter_examples = [
  "{'prompt': 'Generate a standalone HTML dashboard from this brief', 'language': 'html', 'output_path': 'generated/design/dashboard.html'}",
  "{'prompt': 'Generate a Python helper script', 'language': 'python', 'context_files': ['README.md'], 'constraints': ['no network calls']}",
]
+++

# CodeGenerationExpert

## When to Use

Use this expert to generate exactly one code or text file from a structured brief. It is reusable across product lines, and the Design product line should use it for HTML prototypes, dashboards, mobile app screens, slide decks, small scripts, or configuration files.

## Routing Notes

- Pass a complete `prompt` that includes the user goal, selected resources, output contract, and constraints.
- Use `language` to select the intended file type. Supported common values include `html`, `javascript`, `typescript`, `python`, `markdown`, `json`, `yaml`, and `text`.
- Use `output_path` when the caller needs a stable workspace path. If omitted, the expert writes a generated session file.
- Use `context_files` for selected resource files, such as `src/productions/design/design-systems/claude/DESIGN.md` or a selected task skill.
- Keep context files selected and small. Do not pass the entire design resource library.

## Provider Boundaries

- Current implementation uses the configured project LLM through Google ADK.
- It returns one generated file and records it in `output_files`.
- It can read selected project resource files and workspace files as text context.
- It strips a single surrounding markdown code fence before writing the output file.

## When Not to Use

Do not use this expert for image, video, audio, browser preview validation, PDF/PPTX export, or design critique. Use specialized experts or deterministic tools for those capabilities.
