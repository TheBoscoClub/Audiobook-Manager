# Acknowledgements

The maintainer uses the following external services and open-source tools in their personal deployment of Audiobook-Manager. None are required; they're listed here as grateful attribution. Other operators can substitute any equivalent.

## GPU / inference (operator's personal choice)

- **[RunPod](https://runpod.io)** — serverless GPU endpoints for Whisper STT and XTTS narration, used in the maintainer's production.

Audiobook-Manager's STT layer is provider-agnostic — it accepts any Whisper-compatible backend (RunPod, self-hosted `whisper-gpu`, CPU `faster-whisper`, or anything else you can reach from Python).

## Development tooling

- **[Gstack](https://github.com/garrytan/gstack)** — open-source tooling used during development.
