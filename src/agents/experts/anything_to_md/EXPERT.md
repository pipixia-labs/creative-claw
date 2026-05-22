+++
name = "AnythingToMD"
enabled = true
input_types = ["document", "spreadsheet", "presentation", "text"]
output_types = ["markdown", "text"]
routing_keywords = ["convert to markdown", "anything to md", "source to md", "extract markdown", "document markdown"]
parameter_examples = [
  "{'input_path': 'workspace/path/source.pptx'}",
  "{'input_path': 'workspace/path/report.xlsx', 'max_rows': 200, 'max_cols': 40}",
  "{'input_path': 'workspace/path/document.pdf', 'output_path': 'generated/document.md'}",
  "{'input_path': 'workspace/path/document.pdf', 'doc2x_api_key': 'optional-key', 'formula_level': 1}",
]
+++

# AnythingToMD

## When to Use

Use this expert to convert one local workspace source file into Markdown. It is best for extracting reusable text, slide outlines, workbook tables, HTML content, and document source material before creative planning or generation.

## Routing Notes

- Pass `input_path` for a local workspace file.
- Supported primary paths include PDF, PowerPoint, Excel, DOCX, HTML, Markdown, and plain text-like files.
- PDF conversion first tries Doc2X v3 through `pdfdeal` when `DOC2X_API_KEY` or `doc2x_api_key` is available.
- Optional `output_path` controls where the Markdown file is saved inside the workspace.
- Optional `max_rows` and `max_cols` limit spreadsheet export size.
- Optional `formula_level` or `doc2x_formula_level` controls Doc2X v3 formula degradation for PDF export: `0` keeps formulas, `1` converts inline formulas to text, and `2` converts all formulas to text.
- Non-PDF primary converters follow the local `source_to_md` style; MarkItDown is used only as a fallback when available.

## Provider Boundaries

- This is a deterministic local conversion expert and does not call an LLM.
- File inputs must already be available in the runtime workspace.
- URL inputs are intentionally unsupported. Product managers should download remote files into the workspace first, then pass `input_path`.
- Doc2X PDF conversion requires `pdfdeal` installed from source and a `DOC2X_API_KEY` environment variable or explicit `doc2x_api_key` parameter.
- Some fallback formats require optional Python packages such as PyMuPDF, mammoth, openpyxl, beautifulsoup4, markdownify, or markitdown.

## When Not to Use

Do not use this expert to summarize, rewrite, translate, or design from the extracted Markdown. Use `TextTransformExpert`, `KnowledgeAgent`, or a generation expert after conversion when semantic transformation is needed.
