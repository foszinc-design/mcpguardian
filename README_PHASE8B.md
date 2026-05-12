# MCPGuardian Phase 8B — Document-Specialized Native Tools

## Goal

Phase 8B adds conservative document inspection tools. These tools do not claim perfect semantic parsing. They provide structured metadata and excerpts so binary documents are not handled blindly.

## Added native tools

```text
guardian_inspect_xlsx
guardian_read_docx
guardian_inspect_pdf
```

## Tool behavior

### guardian_inspect_xlsx

Uses `openpyxl` to inspect workbook sheets, dimensions, visibility, and sample rows.

Important boundary:

```text
XLSX inspection is not analytical coverage.
```

It does not set `global_claim_safe=true`. Phase 2 coverage rules still apply.

### guardian_read_docx

Reads `word/document.xml` from DOCX using stdlib zip/xml parsing and returns plain text excerpts.

### guardian_inspect_pdf

Returns file hash, size, estimated page count, PDF Info metadata, and conservative text hints. It does not claim full PDF text extraction.

## Security

All document tools use `path_policy.allowed_roots` and emit trace events through the native tool context.
