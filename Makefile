.PHONY: install dev mock test key chat health models

# Install dependencies
install:
	uv sync

# Start backend pointed at Ollama (real local LLM)
dev:
	VLLM_BASE_URL=http://localhost:11434 uvicorn app.main:app --reload --port 8000

# Start backend pointed at mock vLLM (canned responses, no LLM needed)
mock:
	uvicorn tests.mocks.fake_vllm:app --port 8001 &
	sleep 1
	VLLM_BASE_URL=http://localhost:8001 uvicorn app.main:app --reload --port 8000

# Run all tests
test:
	python -m pytest --tb=short -q

# Create an admin API key
key:
	python -m app.cli create-key --label "dev" --scope admin

# Quick smoke test — chat (requires API key as arg: make chat KEY=vault_sk_...)
chat:
	@curl -sN -X POST http://localhost:8000/v1/chat/completions \
		-H "Authorization: Bearer $(KEY)" \
		-H "Content-Type: application/json" \
		-d '{"model": "tinyllama", "messages": [{"role": "user", "content": "Say hello in one sentence."}], "stream": true}'

# Check health
health:
	@curl -s http://localhost:8000/vault/health | python -m json.tool

# List models from inference backend
models:
	@curl -s http://localhost:8000/v1/models -H "Authorization: Bearer $(KEY)" | python -m json.tool
