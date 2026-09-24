# Frontend

Related: [API reference](../API/API_REFERENCE.md), [Authentication](../API/AUTHENTICATION.md).

React 19 app in `frontend/`. Package name `agentic-frontend`, version `1.0.0`. Router: `react-router-dom` 7. Bundler: Vite 7. Tests: Vitest and Testing Library (`npm test`).

The UI does not receive `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, or Canva secrets. API calls go through `frontend/src/services/api/client.ts` with credentials so the `access_token` cookie is sent.

## Routes (`frontend/src/App.tsx`)

| Path | Page | Guard |
| --- | --- | --- |
| `/login` | `LoginPage` | public |
| `/register` | `RegisterPage` | public |
| `/` | Redirect to `/dashboard` | |
| `/dashboard` | `DashboardPage` | signed in |
| `/generate` | `GeneratePage` | signed in |
| `/images` | `ImagesPage` | signed in |
| `/posts` | `PostsPage` | signed in |
| `/automation` | `AutomationPage` | signed in |
| `/festivals` | `FestivalsPage` | signed in |
| `/business` | `BusinessPage` | signed in |
| `/brand` | `BrandPage` | signed in |
| `/products` | `ProductsPage` | signed in |
| `/assets` | `AssetsPage` | signed in |
| `/ai-settings` | `AiSettingsPage` | signed in |
| `/mcp` | `McpPage` | signed in |
| `/trends` | `TrendsOverviewPage` | signed in |
| `/trends/account` | `AccountTrendsPage` | signed in |
| `/trends/research` | `ResearchPage` | signed in |
| `/trends/opportunities` | `OpportunitiesPage` | signed in |
| `/trends/reports` | `ReportsPage` | signed in |
| `/campaigns` | `CampaignsPage` | signed in |
| `/instagram` | `InstagramPage` | signed in |
| `/settings` | `SettingsPage` | signed in |
| `/admin` | `AdminPage` | signed in and admin |
| other | `NotFoundPage` | |

`ProtectedRoute` sends anonymous users to login. Users with `must_change_password` are kept on the password form (`ChangePasswordForm`).

## API modules

`frontend/src/services/api/` splits calls by area: `auth`, `business`, `catalog`, `generation`, `posts`, `automation`, `festivals`, `instagram`, `trends`, `tasks`, `settings`, `admin`, `dashboard`, `providers`. Paths match [API reference](../API/API_REFERENCE.md).

## Scripts

| Script | Command |
| --- | --- |
| `dev` | `vite` |
| `build` | `tsc -p tsconfig.app.json && vite build` |
| `preview` | `vite preview --host` |
| `test` | `vitest run` |

Docker: `frontend/Dockerfile` serves the built app. Compose maps host port 3000 to container port 80.

## Tests present

`login`, `protected-routes`, `dashboard`, `generator`, `approval`, `instagram-publish`, `automation`, `festival`, `catalog`, `trends`, `errors`, `loading`, `client`.
