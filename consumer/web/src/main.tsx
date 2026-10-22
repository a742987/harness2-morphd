import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./design.css";
import { AppProvider } from "./state";

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <AppProvider>
      <App />
    </AppProvider>
  </StrictMode>,
);
