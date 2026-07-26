PY := .venv/bin/python

.PHONY: setup data backend frontend test test-live eval verify verify-backend

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

test-live:
	$(PY) -m pytest backend/tests -q -m live

verify:
	$(PY) evals/smoke.py http://localhost:5173

verify-backend:
	$(PY) evals/smoke.py http://localhost:8000

eval:
	$(PY) evals/run.py
	$(PY) evals/baseline.py
