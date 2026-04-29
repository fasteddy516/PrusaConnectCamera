# Development Workflow

This document covers local development and test usage.

## 1. Clone and enter the repository

```bash
git clone https://github.com/fasteddy516/PrusaConnectCamera.git
cd PrusaConnectCamera
```

## 2. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. Generate a local config template

If `config.json` does not exist, the app will generate it and exit:

```bash
.venv/bin/python prusaconnectcamera.py
```

Then edit it:

```bash
nano ./config.json
```

Default local paths used by the generated template:

- Config: `./config.json`
- State: `./state`

## 4. Run locally

```bash
.venv/bin/python prusaconnectcamera.py --config ./config.json
```

## 5. Install development dependencies

```bash
.venv/bin/pip install -r requirements-dev.txt
```

## 6. Run tests

```bash
.venv/bin/pytest tests/ -v
```
