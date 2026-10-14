PY ?= .venv/bin/python
ifeq ($(OS),Windows_NT)
PY = .venv/Scripts/python.exe
endif

setup:            ## local venv for scripts, evals, pipeline (the stack itself runs in compose); generates AGENT_API_TOKEN in .env
	python -m venv .venv
	$(PY) -m pip install -r requirements.lock
	$(PY) -m pip install -e ".[dev]"
	$(MAKE) token

token:            ## write generated AGENT_API_TOKEN and SESSION_SECRET into .env when the lines are empty
	$(PY) -c "import pathlib,re,secrets; p=pathlib.Path('.env'); t=p.read_text(encoding='utf-8') if p.exists() else ''; n=re.sub(r'^AGENT_API_TOKEN=\s*$$', 'AGENT_API_TOKEN='+secrets.token_urlsafe(32), t, flags=re.M); n=re.sub(r'^SESSION_SECRET=\s*$$', 'SESSION_SECRET='+secrets.token_urlsafe(48), n, flags=re.M); n=n if re.search(r'^SESSION_SECRET=', n, re.M) else n+'\nSESSION_SECRET='+secrets.token_urlsafe(48)+'\nCOOKIE_SECURE=0\n'; p.write_text(n, encoding='utf-8') if t else print('no .env yet: cp .env.example .env first'); print('secrets set' if n!=t else 'AGENT_API_TOKEN and SESSION_SECRET already set')"

web-dev:          ## producer UI against the compose agent on :8010 (http://localhost:3000); reads the secrets from .env
	cd web && set -a && . ../.env && set +a && AGENT_URL=http://localhost:8010 npm run dev

web-e2e:          ## Playwright end-to-end against the running stack (web on :3010)
	cd web && npm run e2e

producer:         ## rotate or add a producer account: make producer NAME=sam (prints the password once, stores the bcrypt hash)
	cd web && node scripts/rotate-producer.mjs $(NAME)

up:               ## full stack live (builds images, seeds, ingests)
	docker compose up -d --build

down:             ## stop and remove the stack (keeps the postgres volume)
	docker compose down

logs:
	docker compose logs -f --tail=100

bridge:           ## interim option-3 model door on the host (token = the LangSmith key the app presents)
	BRIDGE_TOKEN=$$(grep -E '^LANGSMITH_API_KEY=' .env | cut -d= -f2-) $(PY) bridge/claude_cli_bridge.py

lock:             ## regenerate requirements.lock from pyproject on the image's own platform (python:3.11-slim)
	MSYS_NO_PATHCONV=1 docker run --rm -v "$(CURDIR):/src" -w /src python:3.11-slim sh -c "pip install -q pip-tools && pip-compile -q --strip-extras --resolver=backtracking -o requirements.lock pyproject.toml"

prompts-push:     ## push ANCHOR prompts to the Hub and pin commits
	$(PY) scripts/prompts_push.py

langsmith-setup:  ## annotation queue + online-eval config echo (idempotent)
	$(PY) scripts/langsmith_setup.py

lint:
	$(PY) -m ruff check producery scripts tests evals/evaluators
	$(PY) -m ruff format --check producery scripts tests evals/evaluators

typecheck:
	$(PY) -m mypy

test:
	$(PY) -m pytest -q

evals:            ## offline eval suite + gate (needs the model door)
	$(PY) -m producery.evals.run --all
	$(PY) -m producery.evals.gate

prove-gate:       ## proves the gate can go RED: the committed proof rows must FAIL the gate (exit 1)
	$(PY) scripts/pipeline.py prove-gate

smoke:            ## smoke eval against the running stack (the CD stage's golden subset)
	$(PY) -m producery.evals.run --suite smoke

online:           ## online scoring over recent traces → feedback + annotation queue
	$(PY) -m producery.evals.online

pipeline-ci:
	$(PY) scripts/pipeline.py ci
pipeline-cd:
	$(PY) scripts/pipeline.py cd
pipeline-post:
	$(PY) scripts/pipeline.py post

help:
	@grep -E '^[a-z-]+:.*##' Makefile | sed 's/:.*##/ —/'
