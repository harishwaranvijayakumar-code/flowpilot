# FlowPilot [Try FlowPilot](https://flowpilot-642g.onrender.com)

**A GitHub-connected workflow analysis platform for inspecting repository automation, processing workflow-related events, and tracking analysis jobs through a web dashboard.**

[![Python](https://img.shields.io/badge/Python-3.13.16-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![PostgreSQL](https://img.shields.io/badge/Database-PostgreSQL-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)

---

## Table of Contents

- [Overview](#overview)
- [The Problem](#the-problem)
- [Project Goals](#project-goals)
- [Features](#features)
- [Architecture](#architecture)
- [Technology Stack](#technology-stack)
- [Repository Structure](#repository-structure)
- [Getting Started](#getting-started)
- [Environment Configuration](#environment-configuration)
- [Running FlowPilot](#running-flowpilot)
- [Background Job Processing](#background-job-processing)
- [GitHub Integration](#github-integration)
- [Database and Migrations](#database-and-migrations)
- [Testing and Code Quality](#testing-and-code-quality)
- [Continuous Integration](#continuous-integration)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)
- [Security Considerations](#security-considerations)
- [Current Limitations](#current-limitations)
- [Engineering Principles](#engineering-principles)
- [Future Improvements](#future-improvements)
- [Contributing](#contributing)

---

## Overview

FlowPilot is a developer-tooling project built around GitHub integration, workflow parsing, asynchronous analysis jobs, webhook handling, and a web-based dashboard.

Repository automation involves more than defining a workflow file. Events must be received and processed, workflow definitions must be interpreted, potentially longer-running work must be managed, and the resulting state must be made visible to developers.

FlowPilot brings these concerns into a single Django application with separate application modules for the major responsibilities.

The project emphasizes practical backend engineering: clear component boundaries, persistent application state, integration with external systems, automated testing, continuous integration, and deployment to a publicly accessible environment.

---

## The Problem

Developer workflows frequently span several independent systems:

- A repository stores source code and automation definitions.
- GitHub emits events when repository activity occurs.
- Webhooks deliver those events to external applications.
- Workflow definitions must be parsed before their structure can be analyzed.
- Analysis jobs may take longer than a typical HTTP request.
- Users need a way to inspect application state without relying exclusively on logs or database queries.

These components introduce several engineering concerns.

### 1. External event handling

An application receiving events from an external platform must account for network failures, repeated deliveries, invalid requests, and events that arrive independently of a user's browser session.

### 2. Separation of request handling and background work

Long-running operations should not unnecessarily hold open web requests. They need an explicit lifecycle and a mechanism for tracking whether work is queued, processing, completed, or failed.

### 3. Workflow interpretation

Workflow definitions are structured configuration, not merely text files. Parsing them in a dedicated component makes the logic easier to maintain and test independently from the user interface.

### 4. Operational visibility

A backend process is more useful when its state is observable. Developers need an application surface for inspecting relevant repository, integration, and job information.

### FlowPilot's approach

FlowPilot organizes these concerns into dedicated Django applications rather than placing GitHub integration, parsing, background processing, and dashboard logic into one undifferentiated module.

The goal is to provide a maintainable foundation for repository workflow analysis while keeping the major responsibilities independently testable.

---

## Project Goals

FlowPilot is designed around the following goals:

1. **Modular backend architecture:** separate integration, parsing, job processing, and presentation responsibilities.
2. **GitHub connectivity:** organize repository integration and event handling within the application.
3. **Explicit job lifecycle:** represent asynchronous analysis work separately from normal HTTP requests.
4. **Testable parsing logic:** keep workflow interpretation isolated from dashboard code.
5. **Operational visibility:** expose useful application state through a web interface.
6. **Repeatable delivery:** use automated tests, linting, CI, and a documented deployment process.
7. **Maintainability:** centralize configuration and avoid embedding environment-specific secrets in source code.

These goals describe the design direction. The exact behavior currently supported is determined by the implementation and its tests.

---

## Features

### 1. GitHub integration

The `github_integration` application contains the GitHub-facing domain logic.

Its responsibilities include organizing GitHub installation and repository information and providing the application-side foundation for interacting with GitHub.

Integration behavior depends on the configured GitHub application, credentials, permissions, and callback settings.

### 2. Workflow parsing

The `workflow_parser` application isolates workflow-definition parsing from the web-facing components.

This separation helps keep parsing logic independently testable and provides a natural place to evolve workflow inspection and analysis rules.

The exact syntax supported and the rules applied during analysis should be confirmed against the current implementation.

### 3. Background analysis jobs

The `jobs` application manages analysis-job-related behavior.

Separating jobs from normal request handling provides a foundation for processing work independently and tracking its lifecycle.

A worker management command is available in the development workflow:

```powershell
python manage.py run_worker --poll-interval 1 --lease-seconds 120
```

See [Background Job Processing](#background-job-processing) for how to run it.

### 4. Webhook handling

The `webhooks` application provides a dedicated boundary for incoming webhook requests and delivery-related logic.

Keeping webhook behavior separate makes it easier to reason about external event handling, validation, persistence, and tests without coupling those concerns directly to dashboard views.

### 5. Web dashboard

The `dashboard` application contains dashboard-facing views and membership-related behavior.

It provides the user-facing surface for working with FlowPilot rather than requiring users to inspect internal application state directly.

### 6. Persistent application state

PostgreSQL is used as the relational database for the hosted application and database-backed local development.

Database migrations provide a repeatable way to apply model changes as the application evolves.

### 7. Automated quality checks

The project uses pytest, Ruff, Django's system checks, and a GitHub Actions workflow to support a repeatable development process.

---

## Architecture

FlowPilot uses Django as its application framework and divides its responsibilities into separate applications.

### High-level conceptual architecture

```text
                         GitHub
                            |
                Repository activity / events
                            |
                            v
                +------------------------+
                |  GitHub Integration    |
                +------------+-----------+
                             |
                             v
                +------------------------+
                |   Webhook Handling     |
                +------------+-----------+
                             |
                             v
                +------------------------+
                |   Analysis Jobs        |
                +------------+-----------+
                             |
                             v
                +------------------------+
                |   Workflow Parser      |
                +------------+-----------+
                             |
                             v
                +------------------------+
                |      PostgreSQL        |
                +------------------------+

                +------------------------+
                |    Django Dashboard    |
                +------------+-----------+
                             |
                             v
                          Browser
```

This diagram illustrates the main application responsibilities, not a guaranteed sequence of calls for every operation. The actual execution path depends on the relevant view, event handler, job implementation, and database interactions.

### Application responsibilities

| Component | Responsibility |
|---|---|
| `config` | Django configuration, routing, and application entry points |
| `github_integration` | GitHub integration and repository-related domain logic |
| `webhooks` | Incoming webhook and delivery-related behavior |
| `jobs` | Analysis-job lifecycle and worker processing |
| `workflow_parser` | Workflow-definition parsing |
| `dashboard` | Dashboard views and membership-related behavior |

### Design rationale

**Separation of concerns**

Each Django application has a defined area of responsibility. This reduces the tendency for unrelated functionality to accumulate in a single module.

**Independent testing**

Parsing, webhook handling, job management, and dashboard behavior can be tested at their respective boundaries.

**Asynchronous processing**

Analysis jobs can be managed independently of the HTTP request that initiated or exposed the relevant operation.

**External integration boundaries**

GitHub communication and incoming events are treated as integration concerns rather than being mixed into unrelated presentation logic.

---

## Technology Stack

| Area | Technology | Purpose |
|---|---|---|
| Language | Python | Application and domain logic |
| Web framework | Django | Request handling, routing, models, and application structure |
| Application server | Gunicorn | Serving the Django WSGI application in deployment |
| Database | PostgreSQL | Persistent relational application state |
| Database hosting | Neon | Hosted PostgreSQL instance |
| Workflow infrastructure | OpenSearch | Included in local infrastructure configuration; exact runtime usage should be verified |
| GitHub integration | GitHub App / GitHub API | Repository integration and event-related functionality |
| Static assets | Django static files and WhiteNoise | Static asset collection and serving |
| Containers | Docker and Docker Compose | Local infrastructure and development services |
| Testing | pytest and pytest-django | Automated tests |
| Formatting | Ruff | Python formatting |
| Linting | Ruff | Static code-quality checks |
| CI | GitHub Actions | Automated validation |
| Hosting | Render | Public web application deployment |

The dependency manifests and configuration files in the repository are the authoritative sources for exact package versions and enabled services.

---

## Repository Structure

The following is a guide to the main project areas. Verify the actual file tree in the repository before treating every entry as exhaustive.

```text
flowpilot/
├── .github/
│   └── workflows/
│       └── ci.yml
│
├── config/
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
│
├── dashboard/
│   ├── models.py
│   ├── views.py
│   └── ...
│
├── github_integration/
│   ├── models.py
│   └── ...
│
├── jobs/
│   ├── models.py
│   └── ...
│
├── webhooks/
│   ├── models.py
│   └── ...
│
├── workflow_parser/
│   └── ...
│
├── tests/
│   └── ...
│
├── manage.py
├── requirements.txt
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .python-version
└── README.md
```

Some directories, including `staticfiles`, may be generated during development or deployment and should not be treated as source code.

### Important files

- `manage.py` — Django management commands.
- `requirements.txt` — Python dependencies.
- `pyproject.toml` — project and tooling configuration.
- `docker-compose.yml` — local infrastructure configuration.
- `.env.example` — example environment configuration, if present.
- `.github/workflows/ci.yml` — automated CI workflow.
- `.python-version` — Python runtime version used by supported environments.

---

## Getting Started

These instructions describe a Windows and PowerShell-oriented development setup.

### Prerequisites

Install the following:

- Python 3.13.16, matching the configured deployment runtime.
- Git.
- Docker Desktop with Docker Compose.
- A code editor such as Visual Studio Code.
- A GitHub account if testing GitHub integration.
- PostgreSQL access through the local Compose setup or a configured hosted database.

### 1. Clone the repository

```powershell
git clone https://github.com/harishwaranvijayakumar-code/flowpilot.git
cd flowpilot
```

### 2. Create a virtual environment

```powershell
py -3.13 -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

If activation is blocked by the current PowerShell execution policy, use an appropriate local policy for the current session or invoke the environment's Python executable directly.

### 3. Upgrade pip and install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Configure environment variables

If `.env.example` exists in the repository, copy it:

```powershell
Copy-Item .env.example .env
```

Populate `.env` with valid development configuration.

Do not commit `.env` or include real secrets in issues, screenshots, logs, or documentation.

### 5. Start local infrastructure

If the Compose file defines services named `postgres` and `opensearch`, start them with:

```powershell
docker compose up -d postgres opensearch
```

Check their status:

```powershell
docker compose ps
```

To see the actual service names defined by your Compose configuration:

```powershell
docker compose config --services
```

If the service names differ, use the names shown by that command.

### 6. Apply database migrations

```powershell
python manage.py migrate
```

### 7. Run Django system checks

```powershell
python manage.py check
```

A successful check should report no configuration issues detected by Django's system-check framework.

### 8. Start the development server

```powershell
python manage.py runserver
```

Open the local address printed in the terminal. Django commonly uses:

```text
http://127.0.0.1:8000/
```

The actual page and functionality available depend on the current URL configuration and environment.

---

## Environment Configuration

Environment variables keep deployment-specific configuration outside the source code.

The names below reflect the project's known configuration. Check `.env.example` and the settings code before assuming every variable is required or consumed by the current version.

### Django configuration

| Variable | Purpose |
|---|---|
| `DJANGO_DEBUG` | Enables or disables Django debug mode |
| `DJANGO_SECRET_KEY` | Secret key used by Django |
| `DJANGO_ALLOWED_HOSTS` | Allowed hostnames for incoming requests |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Trusted origins for CSRF-protected requests |

For a deployed application, debug mode should be disabled and the secret key should remain private.

### PostgreSQL configuration

| Variable | Purpose |
|---|---|
| `DB_ENGINE` | Database configuration selector |
| `POSTGRES_DB` | Database name |
| `POSTGRES_USER` | Database username |
| `POSTGRES_PASSWORD` | Database password |
| `POSTGRES_HOST` | Database server hostname |
| `POSTGRES_PORT` | Database server port |
| `POSTGRES_SSLMODE` | PostgreSQL connection SSL mode |

For the hosted Neon configuration, use the connection values supplied by Neon. Do not copy credentials into this document.

If the application expects `DB_ENGINE=postgres`, use that value as specified by the current project settings.

### GitHub integration configuration

The project context includes the following GitHub-related configuration names:

| Variable | Purpose |
|---|---|
| `GITHUB_CLIENT_ID` | GitHub client identifier |
| `GITHUB_CLIENT_SECRET` | GitHub client secret |
| `GITHUB_APP_ID` | GitHub App identifier |
| `GITHUB_APP_PRIVATE_KEY` | GitHub App private key |
| `GITHUB_WEBHOOK_SECRET` | Secret used for webhook verification, where implemented |
| `GITHUB_API_URL` | GitHub API endpoint configuration |

Only configure variables that the current code actually reads.

The exact credentials, callback URLs, permissions, and webhook configuration depend on the integration flow enabled in the application.

### Production checklist

Before deploying, verify:

- [ ] `DJANGO_DEBUG` is set to `false`.
- [ ] `DJANGO_SECRET_KEY` is strong and private.
- [ ] `DJANGO_ALLOWED_HOSTS` includes the deployed hostname.
- [ ] `DJANGO_CSRF_TRUSTED_ORIGINS` contains the correct HTTPS origin.
- [ ] Database credentials are correct.
- [ ] Hosted database connections use the required SSL configuration.
- [ ] GitHub callback and webhook URLs match the deployed hostname.
- [ ] Secrets are stored in the hosting provider's environment settings.
- [ ] No secret values are committed to Git.

---

## Running FlowPilot

### Start the web application

With the virtual environment active:

```powershell
python manage.py runserver
```

This starts Django's development server. It is intended for local development, not production hosting.

### Run Django checks

```powershell
python manage.py check
```

### Collect static files

```powershell
python manage.py collectstatic --noinput
```

This collects configured static assets into the destination specified by Django's static-file settings.

### Inspect database migrations

```powershell
python manage.py showmigrations
```

Apply pending migrations:

```powershell
python manage.py migrate
```

---

## Background Job Processing

Some analysis operations are better handled outside a normal HTTP request. FlowPilot's `jobs` application provides the application boundary for this work.

### Start the worker

Open a second PowerShell terminal in the project directory, activate the virtual environment, and run:

```powershell
python manage.py run_worker --poll-interval 1 --lease-seconds 120
```

Keep the web server and worker running in separate terminals for the separate-process development setup.

### Why separate the worker?

A separate worker provides several engineering benefits:

- Web requests do not need to perform all longer-running work synchronously.
- Job processing can be observed independently of page rendering.
- Worker failures can be investigated separately from web-server failures.
- The job lifecycle can be tested independently from the dashboard.

### When a job does not progress

Check the following:

1. Confirm the database is reachable.
2. Confirm the web application can create or retrieve the relevant job.
3. Confirm the worker process is running.
4. Inspect the worker's terminal output for exceptions.
5. Inspect the stored job state and any associated error fields.
6. Check whether the deployed environment actually runs a worker.

**Deployment note:** A running web service does not automatically prove that background jobs are being processed. A separate worker must be configured, or the application must explicitly implement another execution path.

The Render free web-service configuration used for the demo does not provide a dedicated always-on worker. Verify the actual job-processing behavior in the hosted environment before relying on it.

---

## GitHub Integration

FlowPilot separates GitHub-related application logic from its dashboard and job-processing components.

### Integration configuration

To test GitHub-dependent features:

1. Configure the GitHub application or client required by the implemented integration flow.
2. Add the necessary credentials to local environment variables.
3. Configure callback URLs for the local or hosted environment.
4. Configure webhook delivery URLs and secrets if the feature requires them.
5. Grant only the repository permissions needed by the enabled functionality.
6. Test using a repository and data that are safe to use for development.

### Webhook considerations

Webhook handlers interact with an external event-delivery system. The implementation should be checked for:

- Request and payload validation.
- Signature verification where required.
- Appropriate handling of repeated deliveries.
- Persistent recording of delivery or processing state where implemented.
- Clear error handling when downstream processing fails.

These are important verification points; their inclusion here should not be interpreted as proof that every case is already implemented.

---

## Database and Migrations

FlowPilot uses Django's model and migration system to manage relational application state.

### Apply migrations

```powershell
python manage.py migrate
```

### Generate migrations after model changes

```powershell
python manage.py makemigrations
```

Review the generated migration files before applying them.

### Development practices

- Keep model changes and their migrations together.
- Run migrations against the intended database.
- Avoid using production data for experiments when test data will suffice.
- Verify database connectivity before investigating application-level failures.
- Avoid manually editing database schema objects outside the migration process unless there is a documented reason.

---

## Testing and Code Quality

Automated tests help verify that application behavior remains consistent as the project changes.

### Run the test suite

```powershell
python -m pytest
```

### Run Django's system checks

```powershell
python manage.py check
```

### Check formatting

```powershell
ruff format --check .
```

### Run linting

```powershell
ruff check .
```

### Apply formatting

To format files after making changes:

```powershell
ruff format .
```

Then run the linter again:

```powershell
ruff check .
```

### What should be tested?

The following are useful test categories for FlowPilot's architecture:

| Area | Useful cases |
|---|---|
| Workflow parser | Valid definitions, malformed input, unsupported structures |
| GitHub integration | Missing configuration, invalid responses, permission failures |
| Webhooks | Invalid payloads, signature failures, repeated deliveries |
| Jobs | Job creation, state transitions, processing failures |
| Dashboard | Authentication, permissions, expected response behavior |
| Database | Model constraints, migration consistency, transaction behavior |
| Configuration | Missing environment variables and invalid settings |

The test suite should reflect actual supported behavior and should not assume that an unimplemented feature exists.

---

## Continuous Integration

FlowPilot includes a GitHub Actions workflow at:

```text
.github/workflows/ci.yml
```

The workflow is intended to automate project validation.

The exact commands, service dependencies, Python version, and trigger conditions are defined by the workflow file itself.

### Recommended validation sequence

```powershell
ruff format --check .
ruff check .
python manage.py check
python manage.py migrate --noinput
python -m pytest
```

Run these commands locally before pushing changes. If CI uses additional services or environment variables, configure the local environment accordingly.

### Why CI matters

Continuous integration helps detect problems before changes are merged. It makes formatting, linting, configuration checks, and automated tests repeatable instead of relying entirely on manual verification.

A passing CI run is evidence that the configured checks passed; it does not prove that every deployment path or external integration works in production.

---

## Deployment

### Live application

**https://flowpilot-642g.onrender.com**

The current hosted setup uses Render for the Django web service and Neon for PostgreSQL.

### Render configuration

The service was configured to deploy from the `main` branch using Render's native Python runtime.

**Build command**

```bash
pip install -r requirements.txt && python manage.py collectstatic --noinput
```

**Start command**

```bash
python manage.py migrate --noinput && gunicorn config.wsgi:application
```

The repository's `.python-version` file pins Python to `3.13.16` to keep the runtime compatible with the project's dependencies.

### Database configuration

The hosted database is PostgreSQL on Neon.

Supply the database connection values through the hosting provider's environment-variable configuration. Do not store production database credentials in the repository.

### Verify a deployment

After a deployment:

1. Confirm the build completes successfully.
2. Confirm static-file collection completes.
3. Confirm migrations finish without errors.
4. Confirm Gunicorn starts successfully.
5. Open the public URL.
6. Check the main page and the expected user-facing flow.
7. Test GitHub-dependent functionality only after integration configuration is verified.
8. Trigger an analysis action and inspect its resulting state.
9. Check deployment logs if a request returns an error or a job does not progress.

### Free-tier behavior

The Render free web-service tier can spin down after inactivity. The first request after a period of inactivity may take longer while the service starts again.

The public demo is useful for reviewing the hosted application, but its availability and background-processing behavior are constrained by the hosting configuration.

---

## Troubleshooting

### Django returns HTTP 400 on the hosted URL

Check:

- `DJANGO_ALLOWED_HOSTS` contains the exact deployed hostname.
- `DJANGO_CSRF_TRUSTED_ORIGINS` includes the correct HTTPS origin when required.
- The deployment is using the expected environment variables.
- The service has restarted or redeployed after configuration changes.

### Database connection fails

Check:

- Database host, name, username, and password.
- Network connectivity to the database.
- Required SSL settings.
- Whether the hosted database is available.
- Whether the application is reading the expected environment variables.

Do not print database passwords when debugging connection errors.

### Migrations fail during deployment

Check the deployment logs and confirm that:

- The database connection succeeds.
- The expected migration files are committed.
- The configured database user has the necessary permissions.
- No migration depends on a missing application or dependency.

### Static assets are missing

Run locally:

```powershell
python manage.py collectstatic --noinput
```

Check Django's static-file settings and the deployment logs to confirm that collection completed successfully.

### Analysis jobs remain pending

Check:

- Whether the job was created successfully.
- Whether the worker is running.
- Whether the job is in a valid state for processing.
- Whether worker logs show exceptions.
- Whether the hosted environment actually runs the worker process.

Do not assume that a working dashboard means asynchronous processing is also operational.

### GitHub integration fails

Check:

- Client or App identifiers.
- Private key and secret configuration.
- Callback URL and webhook URL.
- GitHub App permissions.
- Webhook secret and request validation.
- Application logs, while ensuring tokens and secret-bearing headers are not exposed.

---

## Security Considerations

FlowPilot integrates with an external platform and stores application state. Its configuration should be handled accordingly.

- Never commit `.env` files, database passwords, GitHub private keys, webhook secrets, or Django secret keys.
- Disable Django debug mode in production.
- Configure allowed hosts and trusted origins explicitly.
- Use HTTPS for the deployed application.
- Grant GitHub applications only the permissions required by the enabled functionality.
- Verify webhook signatures where required by the implementation.
- Avoid logging credentials, tokens, private keys, or secret-bearing request headers.
- Use test repositories and non-sensitive data when demonstrating the application.
- Review authentication, authorization, and membership checks before allowing broader access.
- Keep dependencies updated and review security-relevant changes.

Security requirements should be verified against the actual code and deployment configuration rather than inferred from the presence of a configuration variable alone.

---

## Current Limitations

The current project has several constraints worth documenting transparently.

### Hosting constraints

The free Render web service may sleep after inactivity, causing slower initial responses.

### Background processing

A separate always-on worker is not included in the configured free web-service setup. The actual hosted job execution path must be verified independently.

### GitHub configuration

GitHub-dependent functionality requires the correct application credentials, permissions, callback URLs, and webhook settings.

### Workflow support

The workflow syntax and analysis rules supported by FlowPilot are determined by the current parser implementation and tests.

### Implementation status

This README documents the architecture and development workflow based on the project context available. The repository's source code, settings, dependency manifests, migrations, tests, and deployment configuration remain authoritative.

---

## Engineering Principles

FlowPilot is intended to demonstrate engineering beyond assembling a collection of framework features.

### 1. Clear component boundaries

GitHub integration, webhook handling, parsing, job processing, and dashboard logic should remain separated according to their responsibilities.

### 2. Explicit state

Long-running work should have inspectable state and predictable transitions. Error conditions should be represented in a way that can be investigated.

### 3. Testable domain logic

Workflow parsing and other application logic should be testable without requiring every test to run through the full web interface.

### 4. External-system awareness

GitHub integration introduces network, configuration, permission, and event-delivery failure modes. Those boundaries need explicit handling.

### 5. Repeatable validation

Formatting, linting, system checks, and tests should be reproducible locally and in CI.

### 6. Deployment discipline

A successful build is only one part of deployment verification. Database connectivity, migrations, application startup, static assets, integrations, and job processing require separate checks.

### 7. Evidence over assumptions

A feature should be described as implemented only when the code and tests support that claim. A passing test suite should be distinguished from a successful end-to-end hosted workflow.

---

## Future Improvements

The following are potential directions for further development, not claims that these features already exist.

- **Stronger job observability:** expose job state transitions, failure reasons, and processing duration.
- **Webhook resilience:** test repeated deliveries and downstream processing failures, and make delivery outcomes easier to inspect.
- **Parser coverage:** expand tests for malformed workflow definitions and supported syntax.
- **Integration diagnostics:** provide clearer feedback for missing GitHub credentials and permission errors.
- **Reliability testing:** introduce controlled failures to verify recovery and state consistency.
- **Deployment validation:** add repeatable checks for the hosted application and its integration boundaries.
- **Performance measurement:** establish a baseline for parsing and job-processing latency before attempting optimization.
- **Operational documentation:** document actual state transitions, supported workflow formats, and failure-handling behavior.
- **UI refinement:** continue improving the dashboard's usability and consistency as the backend capabilities evolve.

Prioritize improvements based on demonstrated problems, test evidence, and measurable outcomes rather than adding features solely to increase the size of the project.

---

## Contributing

For a focused change:

1. Create a branch from `main`.
2. Make the smallest change that addresses the problem.
3. Add or update tests for the affected behavior.
4. Run the relevant Django checks, tests, formatting, and linting.
5. Review the changes for accidental secrets or generated files.
6. Open a pull request describing the problem, implementation, and verification evidence.

Example:

```powershell
git switch main
git pull
git switch -c feat/your-change
```

After implementing and testing the change:

```powershell
ruff format .
ruff check .
python manage.py check
python -m pytest
```

Commit and push the changes:

```powershell
git add .
git commit -m "Describe the change"
git push -u origin feat/your-change
```

Only stage files intended for the change. Avoid committing virtual environments, local databases, credentials, or generated artifacts.

---

## Summary

FlowPilot is a Django-based developer-tooling project that brings together GitHub integration, webhook handling, workflow parsing, background analysis jobs, and a web dashboard.

Its engineering focus is the organization and operation of these components as a maintainable application, supported by persistent state, automated validation, and a repeatable deployment workflow.