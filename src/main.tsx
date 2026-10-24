import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router";

import "./styles.css";
import { BackendVersionBanner } from "./components/BackendVersionBanner";
import { routes } from "./routes";

const router = createBrowserRouter(routes);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {import.meta.env.DEV && <BackendVersionBanner />}
    <RouterProvider router={router} />
  </StrictMode>,
);
