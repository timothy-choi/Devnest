# DevNest Frontend

Initial Next.js App Router scaffold for the DevNest UI shell.

## Stack

- Next.js + TypeScript
- Tailwind CSS
- shadcn-style UI primitives
- Lucide React
- React Hook Form + Zod
- TanStack Query

## Routes

- `/` landing page
- `/login` login UI
- `/signup` signup UI
- `/dashboard` workspace dashboard shell
- `/workspace/[id]` IDE opening placeholder

## Run locally

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

## Notes

- Frontend API calls go through same-origin Next API routes under `/api/*`.
- Set `NEXT_PUBLIC_API_BASE_URL` in `.env.local` to your backend, for example `http://127.0.0.1:8000`.
- Auth tokens are stored in `HttpOnly` cookies by the frontend proxy layer.
- Workspace opening is still a placeholder. SSE/live updates are intentionally not wired yet.
