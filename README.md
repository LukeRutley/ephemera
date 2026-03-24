# Ephemera

Ephemera is self-evolving and ephemeral software: intent is turned into task-specific browser interfaces instead of forcing everything through a chat box.

It treats structured data as a key pillar of persistence, while still mixing in unstructured context when that is the better fit. The system continuously reshapes its database schema around real usage, renders rich custom HTML with a focus on low latency, and can create interfaces that exist for one moment or become familiar saved pages for repeat work. Those saved surfaces are cleaned up and consolidated over time too.

The result is software that adapts to the user instead of asking the user to adapt to the software: from a simple one-off question, to a rich interactive workflow, to something that gradually becomes a fully custom replacement for traditional software.

This repository is intentionally designed to be the simplest possible proof of concept for evolving and ephemeral software.

---

## Run

```bash
pip install -r requirements.txt
python app.py
```

## OpenAI Usage

This project uses OpenAI models for inference.

To run the application, you must set your OpenAI API key as an environment variable:

### macOS / Linux
```bash
export OPENAI_API_KEY="your_api_key_here"
```

### Windows (PowerShell)

```powershell
setx OPENAI_API_KEY "your_api_key_here"
```