PY := .venv/bin/python

.PHONY: setup data backend frontend test eval

setup:
	python3 -m venv .venv
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r backend/requirements.txt
	cd frontend && npm install

data:
	$(PY) data/prepare.py
	$(PY) data/inject_ring.py

backend:
	cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	$(PY) -m pytest backend/tests -q

eval:
	$(PY) evals/run.py
