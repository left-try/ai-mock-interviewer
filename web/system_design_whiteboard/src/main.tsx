import React from "react";
import { createRoot } from "react-dom/client";
import { WhiteboardApp } from "./whiteboard-app";
import "./whiteboard.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WhiteboardApp />
  </React.StrictMode>,
);
