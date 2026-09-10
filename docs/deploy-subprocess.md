# Deployment: Subprocess Mode

Subprocess mode requires no container runtime. The hook spawns the TRIM worker directly from a local Python venv on each file read. This is the simplest deployment path for a single developer machine.

---

## Prerequisites

- Python 3.10+
- An API key for your chosen LLM provider

---

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/Rahulbiju003/TRIM.git
cd TRIM
cp .env.example .env
```

Edit `.env`. At minimum, set:

```bash
WORKER_MODEL=gemini/gemini-2.5-flash
GEMINI_API_KEY=<your-key>
```

Do not set `WORKER_URL` — leaving it unset activates subprocess mode.

### 2. Create the venv and install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Verify the worker

```bash
source .env
python -m worker bulk-read --file /path/to/any/large/file.py
```

You should see a summary printed to stdout.

### 4. Install hooks into your project

Run this from the TRIM directory:

```bash
./setup.sh --local --install /path/to/your/project
```

This copies the hook scripts into your project's `.claude/hooks/` directory with the TRIM venv path hardcoded, and patches `.claude/settings.json`.

---

## How it works

Each time Claude Code attempts to read a file ≥ 350 lines, the hook runs:

```
Claude → PreToolUse hook → python -m worker bulk-read → LLM API → summary
```

A short-lived Python process is spawned per delegation. This is slightly slower than HTTP mode on the first invocation (venv startup) but has zero infrastructure overhead.

---

## Updating TRIM

```bash
cd TRIM
git pull
.venv/bin/pip install -r requirements.txt

# Re-install hooks to pick up any changes
./setup.sh --local --install /path/to/your/project
```

---

## Uninstalling

```bash
./setup.sh --uninstall /path/to/your/project
```

This removes the hook scripts and patches `settings.json` to remove the TRIM entries. The `.env` and venv are left in place.

---

## Limitations

- One Python process per file read — higher latency than HTTP mode (~200–400 ms overhead)
- Not suitable for team use on a shared machine
- API keys must be present in the TRIM `.env` on the local machine
