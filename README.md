
# AgentFlow

**A lightweight workflow automation engine built with Python and FastAPI.**

AgentFlow executes structured workflows through a controlled set of tools and exposes a REST API for execution and monitoring.

## Features

- REST API built with FastAPI
- Sequential multi-step workflow execution
- Calculator, uppercase, and word-count tools
- Request validation with Pydantic
- Interactive API documentation
- Automated tests with pytest

## Tech Stack

Python · FastAPI · Pydantic · pytest

## Getting Started

```bash
git clone https://github.com/hemantkarne70-tech/AgentFlow.git
cd AgentFlow

python -m venv .venv
```

Activate the environment on Windows:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Start the server:

```bash
python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs to explore the API.

## Run Tests

```bash
python -m pytest -v
```

## Project Status

Early development — the core workflow execution API is implemented. AI planning, persistent workflow storage, scheduling, and a web dashboard are planned for future releases.

## License

MIT