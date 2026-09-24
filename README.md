# Instagram Agentic AI

Multi-user Instagram still-image studio. The Instagram Agent is the only component that publishes to Meta.

Documentation: [md/README.md](md/README.md).

## Install

Python 3.11+ and Node 20+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
cd frontend
npm install
```

Use the project virtualenv for tests. The base Anaconda interpreter in this workspace does not have the app dependencies.

```bash
.venv\Scripts\python.exe -m pytest
cd frontend
npm test
```

Do not commit `.env`.
