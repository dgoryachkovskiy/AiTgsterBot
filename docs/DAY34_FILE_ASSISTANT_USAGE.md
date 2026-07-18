# Day34 File Assistant Usage Guide

The Day34 File Assistant is a CLI tool that helps you manage and understand your project files using RAG context from source files, MCP file system operations, and optional DeepSeek integration.

## Prerequisites

- Python virtual environment activated: `.venv\Scripts\activate`
- Environment variables configured (see `.env.example`):
  - `DEEPSEEK_API_KEY` (optional, for AI-powered features)
  - `DAY34_STORE_DIR`
  - `DAY34_MCP_URL`
  - `DAY34_DEEPSEEK_MODEL`

## Available Commands

### 1. List MCP Tools

Display all available MCP tools that the assistant can use:

```powershell
.\.venv\Scripts\python.exe day34_file_assistant.py mcp-tools
```

**Expected output:** A list of MCP tools with descriptions, including file system operations and git status tools.

---

### 2. Find Usages of a Symbol

Search for all occurrences of a specific symbol (variable, function, class, etc.) across the project:

```powershell
.\.venv\Scripts\python.exe day34_file_assistant.py find-usages "DEEPSEEK_API_KEY"
```

**Expected output:** A list of files and line numbers where `DEEPSEEK_API_KEY` is referenced, including:
- `.env.example`
- `README.md`
- `docs/PROJECT_COMMANDS.md`
- Any Python files that use this environment variable

---

### 3. Update Project Documentation

Automatically update a documentation file based on real source code analysis:

```powershell
.\.venv\Scripts\python.exe day34_file_assistant.py update-docs --target docs/PROJECT_COMMANDS.md --apply
```

**What it does:**
- Reads the current `docs/PROJECT_COMMANDS.md`
- Scans project source files for commands, environment variables, and usage patterns
- Updates the documentation with accurate, current information
- `--apply` writes changes directly to the file

**Verification:** Open `docs/PROJECT_COMMANDS.md` and check that the Day34 section now includes all commands listed in this guide.

---

### 4. Generate a New File

Create a new file from a template or specification:

```powershell
.\.venv\Scripts\python.exe day34_file_assistant.py generate-file --kind readme --target docs/DAY34_FILE_ASSISTANT_USAGE.md --apply
```

**What it does:**
- Generates a README-style usage guide for the Day34 assistant
- `--kind readme` specifies the template type
- `--target` defines the output file path
- `--apply` writes the generated content to the file

**Verification:** The file `docs/DAY34_FILE_ASSISTANT_USAGE.md` should now exist with content similar to this guide.

---

### 5. Check Project Rules

Validate that the project follows defined conventions and rules:

```powershell
.\.venv\Scripts\python.exe day34_file_assistant.py check-rules
```

**What it checks:**
- File naming conventions
- Directory structure compliance
- Environment variable usage patterns
- Code style consistency

**Expected output:** A report showing which rules pass and which need attention.

---

### 6. Prepare a Diff for Review

Generate a structured diff of recent changes for code review:

```powershell
.\.venv\Scripts\python.exe day34_file_assistant.py prepare-diff
```

**What it does:**
- Captures git status and recent changes
- Formats the diff with context for review
- Outputs a structured summary suitable for code review

**Expected output:** A formatted diff report showing changed files, line-by-line changes, and contextual information.

## Verification Checklist

After running all commands, verify:

1. **MCP Tools:** Command lists available tools without errors
2. **Find Usages:** Returns at least 3 files referencing `DEEPSEEK_API_KEY`
3. **Update Docs:** `docs/PROJECT_COMMANDS.md` contains updated Day34 section
4. **Generate File:** `docs/DAY34_FILE_ASSISTANT_USAGE.md` exists and is readable
5. **Check Rules:** Returns a pass/fail report (no crashes)
6. **Prepare Diff:** Returns a structured diff (even if empty)

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| `DAY34_MCP_URL` not set | Copy `.env.example` to `.env` and configure |
| MCP server not running | Start the Day34 MCP server first |
| DeepSeek errors | Check `DEEPSEEK_API_KEY` is valid and has credits |

## Architecture

The Day34 assistant uses:
- **RAG context** from project source files (`bot.py`, `README.md`, `docs/*.md`)
- **MCP context** from file system operations and git status
- **DeepSeek** (optional) for AI-powered analysis when `DEEPSEEK_API_KEY` is configured
