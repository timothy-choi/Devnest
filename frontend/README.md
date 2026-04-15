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
npm run dev
```

## Notes

- Uses mock workspace data only.
- No backend APIs, auth logic, IDE embedding, or SSE polling yet.
- `package.json` targets Node 20+ for modern Next.js support.
