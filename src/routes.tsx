import type { RouteObject } from "react-router";

import { RootLayout, RootError, NotFound } from "@/components/RootLayout";
import { RequireAuth } from "@/components/RequireAuth";
import { AuthForm } from "@/components/AuthForm";
import Index from "@/pages/Index";
import AppPage from "@/pages/App";

export const routes: RouteObject[] = [
  {
    element: <RootLayout />,
    errorElement: <RootError />,
    children: [
      { path: "/", element: <Index /> },
      { path: "/signin", element: <AuthForm key="signin" mode="signin" /> },
      { path: "/signup", element: <AuthForm key="signup" mode="signup" /> },
      {
        element: <RequireAuth />,
        children: [{ path: "/app", element: <AppPage /> }],
      },
      { path: "*", element: <NotFound /> },
    ],
  },
];
