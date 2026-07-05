import React from "react";
import ReactDOM from "react-dom/client";
import { BaseStyles, ThemeProvider } from "@primer/react";
// Primer v38 component colors come from CSS custom properties defined by
// @primer/primitives, scoped per color mode; without these the components fall
// back to light-mode values on our dark canvas.
import "@primer/primitives/dist/css/primitives.css";
import "@primer/primitives/dist/css/functional/themes/light.css";
import "@primer/primitives/dist/css/functional/themes/dark.css";
import { App } from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider colorMode="night">
      <BaseStyles>
        <App />
      </BaseStyles>
    </ThemeProvider>
  </React.StrictMode>,
);
