
# FlowPilot

FlowPilot analyzes changes to GitHub Actions workflows, identifies potentially
unintended behavioral changes, explains the evidence, and helps developers
validate workflow changes before merging.

## Current status

Repository foundation in progress.

The initial milestone establishes the Django application, PostgreSQL development
environment, health endpoints, automated tests, and GitHub Actions CI.

## Technology stack

- Python 3.13
- Django 5.2
- PostgreSQL 17
- Docker Compose
- pytest and pytest-django
- Ruff
- GitHub Actions

## Local setup

1. Create and activate a Python 3.13 virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and review the development settings.
4. Start PostgreSQL and the web service with `docker compose up --build`.
5. Apply database migrations with `docker compose exec web python manage.py migrate`.
6. Open `http://localhost:8000/health/` and `http://localhost:8000/ready/`.

## Tests and quality checks

Run these commands in the activated virtual environment:

```powershell
ruff format --check .
ruff check .
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest
```

## Product boundaries

FlowPilot is a workflow-change analysis tool, not a CI simulator. It will analyze
real connected repositories and pull requests. It will not execute pull-request
scripts or claim that static analysis proves a workflow correct.

## Security

Never commit `.env`, GitHub tokens, application private keys, or production
credentials.
Testing FlowPilot pull request webhook integration.
