import { useEffect } from "react";

// Client-side replacement for the per-route <head> tags the SSR setup used to render.
export function usePageMeta(title: string, description: string) {
  useEffect(() => {
    document.title = title;
    document.querySelector('meta[name="description"]')?.setAttribute("content", description);
  }, [title, description]);
}
