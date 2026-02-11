# Canvas LMS Conversational AI Agent

An agentic AI pipeline that connects Claude to your Canvas LMS, letting you ask about upcoming assignments, quizzes, and deadlines in plain English.
Deployed at: [Streamlit App](https://canvas-gpt.streamlit.app/)

## Architecture

```
You (natural language)
  → Claude (Anthropic API, tool-use)
    → Canvas LMS REST API
      • get_courses
      • get_upcoming_assignments
      • get_upcoming_quizzes
      • get_all_upcoming_deadlines
      • get_assignment_detail
    → Structured data
  → Natural language answer
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|---|---|
| `CANVAS_API_URL` | Your Canvas instance URL (e.g. `https://myschool.instructure.com`) |
| `CANVAS_API_TOKEN` | A Canvas API access token ([how to generate one](https://community.canvaslms.com/t5/Admin-Guide/How-do-I-obtain-an-API-access-token-in-the-Canvas-Data-Portal/ta-p/596)) |
| `ANTHROPIC_API_KEY` | Your Anthropic API key from [console.anthropic.com](https://console.anthropic.com/) |

### 3. Run

**CLI** (with animated spinner):

```bash
python main.py
```

**Web UI** (Streamlit):

```bash
streamlit run streamlit_app.py
```

## Example queries

- "What assignments do I have due this week?"
- "Show me all upcoming quizzes across my courses."
- "When is the next deadline for my CS 101 class?"
- "Give me details on assignment 12345 in course 67890."

## Project structure

```
canvas_agent/
  __init__.py
  canvas_client.py   # Canvas LMS REST API client
  tools.py           # Tool schemas exposed to Claude
  tool_handler.py    # Dispatches tool calls → Canvas client
  agent.py           # Agentic conversation loop (Claude ↔ tools)
main.py              # Interactive CLI entrypoint (rich spinner)
streamlit_app.py     # Web chat UI (Streamlit)
```
