+++
name = "AnythingToMD"
enabled = true
input_types = ["document", "web", "spreadsheet", "presentation", "text"]
output_types = ["markdown", "text"]
routing_keywords = ["convert to markdown", "anything to md", "source to md", "extract markdown", "document markdown", "web markdown"]
parameter_examples = [
  "{'input_path': 'workspace/path/source.pptx'}",
  "{'input_path': 'workspace/path/report.xlsx', 'max_rows': 200, 'max_cols': 40}",
  "{'input_path': 'workspace/path/document.pdf', 'output_path': 'generated/document.md'}",
  "{'url': 'https://example.com/article', 'output_path': 'generated/article.md'}",
]
+++

# AnythingToMD

## When to Use

Use this expert to convert one workspace source file or one web page into Markdown. It is best for extracting reusable text, slide outlines, workbook tables, HTML content, and document source material before creative planning or generation.

## Routing Notes

- Pass either `input_path` for a workspace file or `url` for a web page.
- Supported primary paths include PDF, PowerPoint, Excel, DOCX, HTML, Markdown, and plain text-like files.
- Optional `output_path` controls where the Markdown file is saved inside the workspace.
- Optional `max_rows` and `max_cols` limit spreadsheet export size.
- The primary converter follows the local `source_to_md` style; MarkItDown is used only as a fallback when available.

## Provider Boundaries

- This is a deterministic local conversion expert and does not call an LLM.
- File inputs must already be available in the runtime workspace.
- Web conversion may download page content and images when network access is available in the runtime environment.
- Some formats require optional Python packages such as PyMuPDF, mammoth, openpyxl, beautifulsoup4, markdownify, or markitdown.

## When Not to Use

Do not use this expert to summarize, rewrite, translate, or design from the extracted Markdown. Use `TextTransformExpert`, `KnowledgeAgent`, or a generation expert after conversion when semantic transformation is needed.
