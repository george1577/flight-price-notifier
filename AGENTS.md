<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Branches

Work directly on `dev`. Don't create feature branches or agent worktrees. `main` is
production and only receives merges from `dev`.

## Definition of done

Passing unit tests is not "done". A change is done only when:

1. **Tests pass:** `cd lambdas && uv run pytest`, `npx vitest run`, `npx tsc --noEmit`.
2. **Deployed and verified on dev**, if it touches `lambdas/` or `infra/`. Push to `dev` and
   wait for the Deploy workflow to go green (`gh run watch`), or run `infra/deploy.sh dev`.
   Then run the relevant smoke checks from `infra/README.md` against the dev API yourself.
   The workflow already checks `GET /version`.
3. **Every acceptance criterion is accounted for.** Report each one as verified, or as
   pending with the reason (for example, it needs a human with a test card). Never call a
   ticket done, or mark a PR ready, while a criterion is unverified. Say what is not done
   first, not in a footnote.
4. **Backward compatible across the deploy gap.** The backend and frontend deploy at
   different moments. A new backend must still serve the previous frontend, and a new
   frontend must fail visibly, never silently, on the previous backend. Never change an
   existing endpoint's response shape; add a new endpoint instead (see infra/README.md).
5. **No silent failures in the UI.** An unexpected API response shows an error.

Before handing work back, check that the backend the user's local frontend talks to (the dev
stack) matches the code. If it doesn't, say so first.
