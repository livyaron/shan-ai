# מערכת לקחים — UI

The React app of the lessons module, served by Shan-AI at `/lessons/`
(PLAN-lessons-module.md phase A). Login is Shan-AI's; data goes through the
`/lessons/api` gateway (`app/routers/lessons_gateway.py`) to PostgREST over
schema `lessons`. See `docs/lessons-module.md` in the repo root.

```sh
npm ci
npm run build     # → dist/, copied into the image as static/lessons
npm test
```

Built in the Docker image (`Dockerfile`, stage `ui`); nothing here is served
from source.
