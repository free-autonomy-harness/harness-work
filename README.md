<<<<<<< HEAD
# harness-work

Hermes worker: получает issue, запускает модель, отдаёт патч artifact-ом.

- `scripts/run_agent.sh` / `run_chain.py` — Docker + model chain + fallback.
- `scripts/model_chain.json` — три маршрута.
- `scripts/make_patch.sh` — патч из workspace без `.git`.
- `scripts/fetch_issue.py` — prompt только из title/body issue.
- `scripts/notify.py` — dispatch `core/publish`.
- `scripts/smoke_model.py` — smoke одного маршрута.

Локально:

```bash
bash tests/test_runner.sh
```

Секреты: `NVIDIA_API_KEY`, `GROQ_API_KEY`.
=======
# work
here we writing code
>>>>>>> b626e9513aa01587a36c3280bd9c5bf987f36120
