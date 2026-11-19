PY := .venv/bin/python
.PHONY: setup run server web seed reset test matrix tunnel

setup:            ## install Python + web deps
	python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd web && pnpm install

server:           ## API + /chat/completions on :8000
	.venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload

web:              ## Next.js client on :3000
	cd web && pnpm dev

run:              ## both, in one terminal
	$(MAKE) -j2 server web

seed reset:       ## restore the demo state (server can stay up)
	@curl -sf -X POST localhost:8000/admin/reset >/dev/null && echo "reset via API" || $(PY) -m server.seed

test:             ## unit + loop tests, no keys needed
	$(PY) -m pytest -q tests

matrix:           ## the 12-utterance test plan through the real model (needs LLM_API_KEY, server running)
	$(PY) scripts/matrix.py --runs 1

tunnel:           ## public HTTPS URL for Agora to reach /chat/completions
	cloudflared tunnel --url http://localhost:8000
