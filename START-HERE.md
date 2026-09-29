# Agentic AI & MCP: Chapter 1 package

## Do this, in order

| # | What | File | Time |
|---|---|---|---|
| 1 | Set up your workstation | `lab-00-environment.html` | ~1 h |
| 2 | Read the four lessons | `chapter-01-llm-essentials.html` | ~2 h |
| 3 | Run Lab 1 (steps are in the chapter) | `lab01/` (`compare.py`, `.env.example`) | ~2-3 h |
| 4 | Take the self-check quiz (end of the chapter file) | | 15 min |
| 5 | Bring your deliverable back to the chat and say "review Lab 1" | | |

## How to view the lessons

`chapter-01-llm-essentials.html` and `lab-00-environment.html` are plain, single, self-contained HTML files — open either one by double-clicking it, or right-click → Open With → your browser. Everything (text, tables, styling) is in that one file; nothing else needs to load.

## Optional: disposable OpenShift cluster + OpenRouter (GPU-free inference)

Only needed for cluster-based labs (9, 14, 15) or the Lab 1 cluster variant. Read `lab-env/README.md`.
Chapter 1 itself works fully on a laptop with Ollama + an OpenRouter (or other) API key.

## Layout

```
agentic-labs/
├── START-HERE.md
├── .gitignore                       # keeps keys, results and artifacts out of git
├── lab-00-environment.html          # <-- open in a browser
├── chapter-01-llm-essentials.html   # <-- open in a browser
├── lab01/
│   ├── compare.py                   # Lab 1 script
│   └── .env.example                 # copy to .env (never commit .env)
└── lab-env/                         # cluster + OpenRouter toolkit (optional for Chapter 1)
    ├── README.md
    ├── labenv.sh
    ├── labenv.env.example
    └── manifests/
```

Suggested first commands:

```bash
unzip agentic-labs-chapter01.zip -d ~ && cd ~/agentic-labs
git init && git add -A && git commit -m "Chapter 1 package"
```
