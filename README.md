# Ephemera

Ephemera is self-evolving and ephemeral software: intent is turned into task-specific browser interfaces instead of forcing everything through a chat box.

It treats structured data as a key pillar of persistence, while still mixing in unstructured context when that is the better fit. The system continuously reshapes its database schema around real usage, renders rich custom HTML with a focus on low latency, and can create interfaces that exist for one moment or become familiar saved pages for repeat work. Those saved surfaces are cleaned up and consolidated over time too.

The result is software that adapts to the user instead of asking the user to adapt to the software: from a simple one-off question, to a rich interactive workflow, to something that gradually becomes a fully custom replacement for traditional software.

This repository is intentionally designed to be the simplest possible proof of concept for evolving and ephemeral software.

---

## Demo

Try a basic demo at [https://ephemera.azurewebsites.net](https://ephemera.azurewebsites.net). Please note that this is a very limited demo, it is hosted on an F1 Azure App Service so it will take a couple of minutes to startup, and it is exactly this repo deployed so data is not partitioned by user, and is not persistent. You will get a much better experience running yourself!

![Ephemera Demo](https://github.com/user-attachments/assets/2f54d003-eca7-46fd-8474-32cf1ee30516)


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
