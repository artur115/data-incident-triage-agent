.PHONY: test runbook replay record demo
test:
	python -m pytest -q
runbook:
	python -m triage.evaluate --triager runbook
replay:     ## agent, from saved responses (no API key)
	python -m triage.evaluate --triager agent --mode replay
record:     ## agent for real; needs ANTHROPIC_API_KEY. Try LIMIT=5 first to see the cost.
	python -m triage.evaluate --triager agent --mode record $(if $(LIMIT),--limit $(LIMIT),)
demo: test runbook replay
