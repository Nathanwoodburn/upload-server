# Python Flask Webserver Template

Python3 website template including git actions

## Requirements
- UV

## Development
1. Install project requirements
```bash
uv sync
```
2. Run the dev server
```bash
uv run python3 server.py
```
3. Alternatively use the virtual environment
```bash
source .venv/bin/activate
```
You can exit the environment with `deactivate`

For best development setup, you should install the git hook for pre-commit
```bash
uv run pre-commit install
```


## Production
Run using the main.py file
```bash
python3 main.py
```
